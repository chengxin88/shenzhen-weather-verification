"""Expanded station selection, source coverage, and historical cache migration."""
from datetime import datetime, timedelta
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import numpy as np
from build_stations import build
from station_common import BOX, ROOT, UTC, atomic_json, iso
from station_forecasts import forecast_runs, read_native_frame, native, interpolate
from station_sources import iem
import station_score


class StationRegionTests(unittest.TestCase):
    def test_wn3_reader_samples_western_stations_with_shared_27_north_cap(self):
        run = datetime(2026,10,25,tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'f003_surface_t.npz'
            bounds = [95.,10.,130.,35.]
            x = np.arange(950,1301)/10; y = np.arange(100,351)/10
            meta = dict(schema='weathernext3-native-southern-china-v2',run=iso(run),lead=3,
                        valid=iso(run+timedelta(hours=3)),field='surface_t',
                        source_variable='temperature_2m_mean',unit='degC',grid_degrees=.1,
                        bounds=bounds,statistic='mean',source_kind='earth-engine-region',source_sha256='0'*64)
            np.savez_compressed(path,lon=x,lat=y,values=np.full((len(y),len(x)),21,np.float32),meta=json.dumps(meta))
            lon,lat,values = native().load_wn3_field(path,run,3,'surface_t','native-southern-china-v2')
            self.assertEqual((lon[0],lon[-1],lat[0],lat[-1]),(104,121,18,27))
            self.assertEqual(interpolate(values,lon,lat,[(106.6,23.9)])[0],21)

    def test_station_builder_includes_west_and_caps_at_27_north(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            points = [('CHM00059211', 23.9, 106.6), ('CHM00057602', 28.8, 105.4),
                      ('CHM00058850', 26.9, 120.1), ('CHM00059152', 24.8, 121.5)]
            lines = [f'{ident:11} {lat:8.4f} {lon:9.4f} {175:6.1f}    TEST STATION'
                     for ident, lat, lon in points]
            (root/'ghcnh-station-list.txt').write_text('\n'.join(lines))
            (root/'ghcnh-inventory.xml').write_text('<ListBucketResult/>')
            atomic_json(root/'CN__ASOS.geojson', {'features': []})
            table = build(root, root/'stations.json')
            self.assertEqual({s['id'] for s in table['stations']}, {'59211', '58850'})
            self.assertEqual(table['box'], [104., 18., 121., 27.])
            basemap = json.loads((ROOT/'station-basemap.json').read_text())
            self.assertEqual(basemap['map_bounds'], list(BOX))
            self.assertEqual(basemap['station_bounds'], list(BOX))

    def test_sample_new_western_station_and_retain_pruned_old_values(self):
        run = datetime(2026, 10, 25, tzinfo=UTC)
        end = run+timedelta(hours=3)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root/'ecmwf_2026102500_f003_global1.json'
            directory = path.with_suffix('.fields'); directory.mkdir()
            x = np.arange(60, 150.01, .25); y = np.arange(0, 60.01, .25)
            metadata = dict(schema=2, model='ECMWF-IFS', run=iso(run), valid=iso(end), lead=3,
                            storage='ecmwf-regional-v1', native_grid_degrees=.25,
                            bounds=[60., 0., 150., 60.], lon=x.tolist(), lat=y.tolist(),
                            interval_hours=3, rain_start=iso(run))
            path.write_text(json.dumps(metadata))
            for name, value in [('surface_t', 21), ('surface_rh', 50), ('rain3h', 1.5)]:
                np.save(directory/(name+'.npy'), np.full((len(y), len(x)), value, np.float32))
            old = [dict(id='old', lon=114., lat=23.)]
            expanded = [dict(id='new', lon=106.6, lat=23.9), *old]
            cache = root/'cache'; cache.mkdir()
            groups = {('ecmwf', iso(run)): {3: path}}
            with patch('station_forecasts.discover', return_value=groups):
                first = next(forecast_runs(cache, old, end))
                self.assertEqual(first[2][3]['t'][0], 21)
                atomic_json(cache/'current.json', {'generation': '20261025T000000'})
                atomic_json(cache/'snapshots/20261025T000000/meta.json', {'stations': old})
                path.unlink()
                retained = next(forecast_runs(cache, expanded, end))[2][3]
                self.assertTrue(np.isnan(retained['t'][0]))
                self.assertEqual(retained['t'][1], 21)
                path.write_text(json.dumps(metadata))
                refreshed = next(forecast_runs(cache, expanded, end))[2][3]
                np.testing.assert_equal(refreshed['t'], [21, 21])
                np.testing.assert_equal(refreshed['rain'], [1.5, 1.5])
                self.assertTrue(np.all(np.isfinite(refreshed['td'])))

    def test_iem_expanded_roster_does_not_reuse_smaller_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = MagicMock()
            client.get.return_value.text = 'station,valid,tmpf\n'
            client.get.return_value.content = b'station,valid,tmpf\n'
            start = datetime(2026, 10, 25, tzinfo=UTC); end = start+timedelta(days=1)
            a = dict(id='ZGHC', type='metar'); b = dict(id='ZUGY', type='metar')
            for stations in ([a], [a, b], [a, b]):
                self.assertEqual(iem(client, Path(temporary), stations, start, end), [])
            self.assertEqual(client.get.call_count, 2)

    def test_narrow_source_reuses_old_values_and_keeps_new_sites_missing(self):
        run = datetime(2026, 10, 25, tzinfo=UTC); end = run+timedelta(hours=3)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root/'ecmwf_2026102500_f003_global1.json'
            directory = path.with_suffix('.fields'); directory.mkdir()
            x = np.arange(108, 120.01, .25); y = np.arange(18, 27.01, .25)
            metadata = dict(schema=2, model='ECMWF-IFS', run=iso(run), valid=iso(end), lead=3,
                            storage='ecmwf-regional-v1', native_grid_degrees=.25,
                            bounds=[108.,18.,120.,27.], lon=x.tolist(), lat=y.tolist())
            path.write_text(json.dumps(metadata))
            np.save(directory/'surface_t.npy', np.full((len(y),len(x)), 21, np.float32))
            old = [dict(id='old', lon=114., lat=23.)]
            expanded = [dict(id='new', lon=106.6, lat=23.9), *old]
            cache = root/'cache'; cache.mkdir()
            with patch('station_forecasts.discover', return_value={('ecmwf',iso(run)):{3:path}}):
                next(forecast_runs(cache,old,end))
                atomic_json(cache/'current.json', {'generation':'20261025T000000'})
                atomic_json(cache/'snapshots/20261025T000000/meta.json', {'stations':old})
                with patch('station_forecasts.read_native_frame',side_effect=AssertionError('unnecessary source read')):
                    values = next(forecast_runs(cache,expanded,end))[2][3]['t']
                self.assertTrue(np.isnan(values[0])); self.assertEqual(values[1],21)

    def test_publishing_expanded_roster_retains_old_elevation_flags(self):
        end = datetime(2026,10,25,tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = dict(id='old', lon=114., lat=23., elevation=20, type='synop', sources=[],
                       model_elevations={'ecmwf':800.})
            new = dict(id='new', lon=106.6, lat=23.9, elevation=100, type='synop', sources=[])
            atomic_json(root/'stations.json', {'stations':[new,old]})
            atomic_json(root/'current.json', {'generation':'20261024T000000'})
            atomic_json(root/'snapshots/20261024T000000/meta.json', {'stations':[old]})
            with patch.object(station_score,'load_observations',return_value=({}, {}, {})), \
                 patch.object(station_score,'forecast_runs',return_value=iter([])), \
                 patch.object(station_score,'shenzhen_sanity',return_value={'n':0}):
                result = station_score.refresh(root,end)
            metadata = json.loads((root/'snapshots'/result['generation']/'meta.json').read_text())
            sites = {s['id']:s for s in metadata['stations']}
            self.assertEqual(sites['old']['model_elevations'], {'ecmwf':800.})
            self.assertEqual(sites['old']['elevation_flags'], ['ecmwf'])
            self.assertEqual(sites['new']['model_elevations'], {})


if __name__ == '__main__':
    unittest.main()
