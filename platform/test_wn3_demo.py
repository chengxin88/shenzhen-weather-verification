"""Exercise real cached sample identity, scientific pairing and HTTP separation."""
from dataclasses import replace
from datetime import timedelta
import importlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import model_interfaces as mi
import wn3_demo as demo


class RealSample(unittest.TestCase):
    def setUp(self):
        self.sample = demo.manifest()
        self.request = mi.GridRequest('cma-gd-ec-mt-500m', self.sample['run_utc'],
                                      self.sample['lead_minutes'], 'temperature_2m')

    def test_all_grid_interfaces_preserve_wn3_identity_and_original_values(self):
        original = demo.read_data(self.sample, 'temperature_2m')
        data = mi.catalog()
        targets = [p for p in data['products'] if p['demo_available']]
        self.assertEqual(len(targets), 11)
        self.assertEqual(sum(p['status']=='reserved' for p in data['products']), 13)
        for product in targets:
            with self.subTest(product=product['id']):
                request = replace(self.request, product_id=product['id'])
                field = demo.field(request)
                self.assertIs(field['is_demo'], True)
                self.assertIs(field['excluded_from_official_scores'], True)
                self.assertEqual(field['source_model'], demo.MODEL)
                self.assertEqual(field['target_product_id'], product['id'])
                self.assertEqual(field['native_grid_degrees'], .1)
                for key in ('values', 'lon', 'lat', 'run_utc', 'valid_utc'):
                    self.assertEqual(field[key], original[key])
                with self.assertRaises(mi.InterfacePending):
                    mi.fetch(request)
                with self.assertRaises(ValueError):
                    mi.validate_field(field, request)

    def test_no_fabricated_minute_fields_different_batch_or_window(self):
        for overrides in [dict(lead_minutes=15), dict(run_utc='2026-10-12T00:00:00Z'),
                          dict(variable='u', level_hpa=850),
                          dict(variable='precipitation', accumulation_minutes=60)]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                demo.field(replace(self.request, **overrides))
        rain = demo.field(replace(self.request, variable='precipitation', accumulation_minutes=180))
        self.assertEqual(mi.utc(rain['valid_utc'])-mi.utc(rain['interval_start_utc']), timedelta(hours=3))
        self.assertEqual(rain['units'], 'mm')
        for product in mi.PRODUCTS:
            if product.group == 'typhoon':
                self.assertFalse(next(p for p in mi.catalog()['products'] if p['id']==product.id)['demo_available'])

    def test_corrupt_sample_rejected_and_missing_sample_graceful(self):
        saved = demo.DEMO_ROOT
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(saved/self.sample['sample_id'], root/self.sample['sample_id'])
            shutil.copyfile(saved/'current.json', root/'current.json')
            try:
                demo.DEMO_ROOT = root
                with (root/self.sample['sample_id']/'temperature_2m.json').open('ab') as handle:
                    handle.write(b' ')
                with self.assertRaisesRegex(ValueError, '校验失败'):
                    demo.field(self.request)
                (root/'current.json').unlink()
                self.assertIsNone(demo.summary())
                self.assertFalse(any(p['demo_available'] for p in mi.catalog()['products']))
            finally:
                demo.DEMO_ROOT = saved

    def test_real_source_and_same_run_trailing_rain_sum(self):
        import native_archive as native
        root = demo.DEMO_ROOT/self.sample['sample_id']
        provenance = json.loads((root/'provenance.json').read_text())
        manifest_path = Path(provenance['manifest'])
        run, directory, committed = native.wn3_manifest(manifest_path)
        lead = self.sample['lead_minutes']//60
        frame, issues = native.load_wn3_frame(manifest_path.parent, run, lead, directory, committed)
        self.assertEqual(issues, [])
        for variable, values in [('temperature_2m', frame['layers']['surface']['t']), ('precipitation', frame['rain3h'])]:
            field = demo.read_data(self.sample, variable)
            np.testing.assert_array_equal(field['lon'], frame['lon'])
            np.testing.assert_array_equal(field['lat'], frame['lat'])
            np.testing.assert_array_equal(field['values'], np.asarray(values).reshape(len(frame['lat']), len(frame['lon'])))
        self.assertNotIn('manifest', demo.summary())
        self.assertNotIn('source_signature', demo.detail())

    def test_scores_match_real_paired_arrays(self):
        detail = demo.detail()
        pair = detail['pairs']['temperature_2m']
        forecast, observed = np.array(pair['forecast']), np.array(pair['observed'])
        self.assertEqual(len(forecast), detail['scores']['temperature_2m']['n'])
        self.assertGreater(len(forecast), 0)
        delta = forecast-observed
        expected = dict(ME=delta.mean(), MAE=np.abs(delta).mean(), RMSE=np.sqrt((delta*delta).mean()), CC=np.corrcoef(forecast, observed)[0,1])
        for key, value in expected.items():
            self.assertAlmostEqual(detail['scores']['temperature_2m'][key], value, places=10)
        pair = detail['pairs']['precipitation']
        forecast, observed = np.array(pair['forecast']), np.array(pair['observed'])
        for score in detail['scores']['precipitation']:
            f, o = forecast>=score['threshold'], observed>=score['threshold']
            h, m, false = int((f&o).sum()), int((~f&o).sum()), int((f&~o).sum())
            self.assertEqual([score['n'], score['H'], score['M'], score['F']], [len(forecast), h, m, false])
            self.assertEqual(score['TS'], h/(h+m+false) if h+m+false else None)
            self.assertEqual(score['POD'], h/(h+m) if h+m else None)
            self.assertEqual(score['FAR'], false/(h+false) if h+false else None)


class HTTPDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server = importlib.import_module('server')
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()

    def read(self, path):
        try:
            with urlopen(self.base+path, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_all_demo_routes_and_formal_routes_remain_pending(self):
        sample = demo.manifest()
        for product in mi.catalog()['products']:
            if not product['demo_available']:
                continue
            for variable in ('temperature_2m', 'precipitation'):
                params = dict(run_utc=sample['run_utc'], lead_minutes=sample['lead_minutes'], variable=variable)
                if variable=='precipitation':
                    params['accumulation_minutes'] = 180
                suffix = '?'+urlencode(params)
                status, field = self.read(product['demo_endpoint']+suffix)
                self.assertEqual(status, 200)
                self.assertIs(field['is_demo'], True)
                self.assertEqual(field['product_id'], product['id'])
                status, pending = self.read(mi.PREFIX+'/'+product['id']+'/field'+suffix)
                self.assertEqual(status, 503)
                self.assertEqual(pending['status'], 'reserved')
                self.assertNotIn('values', pending)
        status, detail = self.read(mi.PREFIX+'/demo/detail')
        self.assertEqual(status, 200)
        self.assertEqual(detail['sample_id'], sample['sample_id'])
        for suffix in ('?run_utc=x', '?run_utc=x&run_utc=y', '?source_url=http://example.com'):
            self.assertEqual(self.read(mi.PREFIX+'/unicast/demo-field'+suffix)[0], 400)


if __name__=='__main__':
    unittest.main()
