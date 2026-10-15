import copy
from dataclasses import replace
import importlib
import json
from pathlib import Path
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import model_interfaces as mi


class Contracts(unittest.TestCase):
    def setUp(self):
        mi._ADAPTERS.clear()

    def tearDown(self):
        mi._ADAPTERS.clear()

    def request(self):
        return mi.GridRequest('feed-qpf', '2026-10-13T00:00:00Z', 30, 'precipitation', 15)

    def field(self):
        return dict(product_id='feed-qpf', run_utc='2026-10-13T00:00:00Z',
                    valid_utc='2026-10-13T00:30:00Z', variable='precipitation', units='mm',
                    source='test fixture only', accumulation_minutes=15,
                    interval_start_utc='2026-10-13T00:15:00Z',
                    lon=[113.5, 114.0], lat=[22.0, 22.5], values=[[0.0, None], [2.0, 4.0]])

    def test_all_products_and_existing_source(self):
        data = mi.catalog()
        self.assertEqual(len(data['products']), 14)
        self.assertEqual(len({p['id'] for p in data['products']}), 14)
        self.assertEqual([sum(p['group'] == group for p in data['products']) for group in mi.GROUPS], [4, 7, 3])
        self.assertEqual(sum(p['status'] == 'reserved' for p in data['products']), 13)
        ec = mi.contract('ecmwf-typhoon')
        self.assertEqual(ec['existing_source'], 'ECMWF-IFS')
        self.assertNotIn('endpoint', ec)
        for p in data['products']:
            c = mi.contract(p['id'])
            self.assertIsNone(c['source'])
            self.assertIsNone(c['run_schedule_utc'])
            json.dumps(c, allow_nan=False)

    def test_distinct_resolutions_and_product_types(self):
        self.assertEqual([mi.product(x).resolution_m for x in ['cma-gd-ec-mt-500m', 'cma-gd-ec-mt-1km', 'cma-gd-ec-mt-3km']], [500, 1000, 3000])
        self.assertNotEqual(mi.product('pangu-short-range').group, mi.product('pangu-typhoon').group)
        with self.assertRaises(ValueError):
            replace(self.request(), product_id='cma-trams')

    def test_pending_has_no_forecast_data(self):
        with self.assertRaises(mi.InterfacePending):
            mi.fetch(self.request())
        with self.assertRaises(ValueError):
            mi.product('../app.py')

    def test_minute_precipitation_and_missing_cells(self):
        out = mi.validate_field(self.field(), self.request())
        self.assertEqual(out['values'][0][1], None)
        self.assertEqual(out['interval_start_utc'], '2026-10-13T00:15:00Z')

    def test_quarter_hour_labels_are_not_hours(self):
        # The business plot labels slot 12 as +012, but 12 x 15 min = 3 h.
        lead = mi.lead_minutes_from_valid_time('2026-10-13T00:00:00Z', '2026-10-13T03:00:00Z')
        self.assertEqual(lead, 180)
        self.assertNotEqual(lead, 12 * 60)
        with self.assertRaises(ValueError):
            mi.lead_minutes_from_valid_time('2026-10-13T03:00:00Z', '2026-10-13T00:00:00Z')
        with self.assertRaises(ValueError):
            mi.lead_minutes_from_valid_time('2026-10-13T00:00:00Z', '2026-10-13T00:00:30Z')

    def test_ui_metadata_kept_separate_from_source_contract(self):
        reference = mi.ui_reference()
        if reference is None:
            self.skipTest('UI observation file not installed in this stage')
        self.assertEqual(reference['timezone'], 'Asia/Shanghai')
        self.assertEqual(reference['time_basis_confirmed_by'], 'user')
        self.assertEqual(len(reference['models']), 14)
        row = next(x for x in reference['models'] if x['ui_model'] == 'CMA 500MT')
        temp = next(x for x in row['fields'] if x['field'] == '2米温度')
        self.assertEqual(temp['lead_minutes'][0], 0)
        self.assertEqual(temp['lead_minutes'][-1], 420)
        self.assertEqual(temp['lead_minutes'][12], 180)
        self.assertEqual(temp['label_step_minutes'], 15)
        self.assertIsNone(mi.contract('cma-gd-ec-mt-500m')['run_schedule_utc'])
        self.assertIsNone(mi.contract('cma-gd-ec-mt-500m')['supported_variables'])
        aifs = next(x for x in reference['models'] if x['ui_model'] == 'AIFS')
        self.assertEqual(aifs['run_hour_choices_utc'], ['00', '06', '12', '18'])
        self.assertEqual(aifs['fields'][0]['sampled_run_utc'], '2026-10-12T18:00:00Z')
        mt = mi.product_ui_reference('cma-gd-ggf-mt-3km')
        self.assertEqual(mt['models'][0]['ui_model'], 'CMA 3KMT')
        self.assertEqual(mt['models'][0]['mapping_state'], 'reference_only')

    def test_no_cross_run_or_wrong_accumulation(self):
        for overrides in [dict(run_utc='2026-10-13T08:00:00+08:00'), dict(lead_minutes=True),
                          dict(accumulation_minutes=0), dict(accumulation_minutes=60),
                          dict(variable='temperature_2m'), dict(level_hpa=500)]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                replace(self.request(), **overrides)
        for overrides in [dict(run_utc='2026-10-12T00:00:00Z'), dict(valid_utc='2026-10-13T01:00:00Z'),
                          dict(interval_start_utc='2026-10-13T00:00:00Z'), dict(units='m'),
                          dict(product_id='unicast'), dict(accumulation_minutes=30)]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                mi.validate_field({**self.field(), **overrides}, self.request())

    def test_grid_shape_and_nonfinite_values(self):
        for overrides in [dict(values=[[1, 2]]), dict(values=[[1, float('nan')], [3, 4]]),
                          dict(lon=[114, 113.5]), dict(lat=[22, 22]), dict(values=[[True, 2], [3, 4]])]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                mi.validate_field({**self.field(), **overrides}, self.request())

    def test_pressure_level_and_surface_units(self):
        request = mi.GridRequest('zhiji', '2026-10-13T00:00:00+00:00', 180, 'u', level_hpa=850)
        self.assertEqual(request.level_hpa, 850)
        with self.assertRaises(ValueError):
            replace(request, level_hpa=None)
        temp = mi.GridRequest('unicast', '2026-10-13T00:00:00Z', 10, 'temperature_2m')
        self.assertIsNone(temp.accumulation_minutes)

    def test_adapter_dispatch_and_no_extra_payload_leak(self):
        fixture = {**self.field(), 'internal_path': '/private/file', 'token': 'test-only'}
        seen = []
        class Adapter:
            def fetch_field(self, request):
                seen.append(request)
                return fixture
        mi.register_adapter('feed-qpf', Adapter())
        result = mi.fetch(self.request())
        self.assertEqual(seen, [self.request()])
        self.assertNotIn('token', result)
        self.assertNotIn('internal_path', result)
        with self.assertRaises(ValueError):
            mi.register_adapter('feed-qpf', Adapter())
        with self.assertRaises(ValueError):
            mi.register_adapter('cma-trams', Adapter())

    def test_track_times_and_wind_convention(self):
        request = mi.TrackRequest('cma-trams', '2026-10-13T00:00:00Z', '202613')
        fixture = dict(product_id=request.product_id, run_utc=request.run_utc, storm_id=request.storm_id,
                       source='test fixture only', wind_convention='模式瞬时10米风',
                       points=[dict(lead_minutes=360, valid_utc='2026-10-13T06:00:00Z', lon=130,
                                    lat=20, pressure_hpa=None, wind_ms=20)])
        self.assertIsNone(mi.validate_track(fixture, request)['points'][0]['pressure_hpa'])
        bad = copy.deepcopy(fixture);bad['points'][0]['valid_utc']='2026-10-13T12:00:00Z'
        for payload in [bad, {**fixture, 'wind_convention': None}]:
            with self.assertRaises(ValueError):
                mi.validate_track(payload, request)
        with self.assertRaises(ValueError):
            mi.TrackRequest('ecmwf-typhoon', request.run_utc, request.storm_id)


class HTTPContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import the staged server with the installed platform dependencies.
        server = importlib.import_module('server')
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()

    def read(self, path, method='GET'):
        request = Request(self.base + path, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def test_catalog_and_every_contract(self):
        status, body = self.read(mi.PREFIX)
        self.assertEqual(status, 200)
        for p in json.loads(body)['products']:
            status, body = self.read(p['contract_url'])
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['product_id'], p['id'])

    def test_pending_and_bad_requests(self):
        status, body = self.read(mi.PREFIX + '/feed-qpf/field?run_utc=2026-10-13T00:00:00Z&lead_minutes=30&variable=precipitation&accumulation_minutes=15')
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)['status'], 'reserved')
        self.assertNotIn('values', json.loads(body))
        for suffix in ['?run_utc=x', '?run_utc=x&run_utc=y', '?source_url=http://example.com']:
            self.assertEqual(self.read(mi.PREFIX+'/feed-qpf/field'+suffix)[0], 400)
        self.assertEqual(self.read(mi.PREFIX+'/missing/contract')[0], 404)
        self.assertEqual(self.read(mi.PREFIX+'/feed-qpf/field', 'POST')[0], 404)

    def test_static_page_assets_and_health(self):
        for path in ['/Verification/test/', '/Verification/test/model-interfaces.js',
                     '/Verification/test/model-interfaces.css', '/Verification/test/health']:
            self.assertEqual(self.read(path)[0], 200)
        status, body = self.read('/Verification/test/')
        page = body.decode()
        self.assertIn('id="interfaces-workspace"', page)
        self.assertLess(page.index('model-interfaces.js'), page.index('./modules.js'))


if __name__ == '__main__':
    unittest.main()
