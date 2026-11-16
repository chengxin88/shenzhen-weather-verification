"""EC ensemble-mean (ecens) regional frames are discovered and sampled like GEFS."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from station_common import GRID_MODELS, MODELS, UTC
import station_forecasts


def write_frame(directory, run, lead, interval, west=108, nx=49):
    lon = [west + .25 * i for i in range(nx)]; lat = [18 + .25 * j for j in range(37)]
    name = f'ecens_{run:%Y%m%d%H}_f{lead:03}_global1'
    meta = dict(schema=2, model='ECMWF-ENSEMBLES平均', run=run.strftime('%Y-%m-%dT%H:%M:%SZ'),
                valid=(run + timedelta(hours=lead)).strftime('%Y-%m-%dT%H:%M:%SZ'), lead=lead,
                storage='ecens-regional-v1', bounds=[float(west), 18., lon[-1], 27.], native_grid_degrees=.25,
                native_grid_shape=[37, nx], lon=lon, lat=lat, interval_hours=interval,
                rain_start=(run + timedelta(hours=lead - interval)).strftime('%Y-%m-%dT%H:%M:%SZ') if lead else None,
                members=50, statistic='mean', **{'global': False})
    (directory / (name + '.json')).write_text(json.dumps(meta))
    fields = directory / (name + '.fields'); fields.mkdir()
    np.save(fields / 'surface_t.npy', np.full((37, nx), 20. + lead, np.float32))
    if lead:
        np.save(fields / f'rain{interval}h.npy', np.full((37, nx), 1.5, np.float32))
    return directory / (name + '.json')


class EnsembleMean(unittest.TestCase):
    def test_model_tables(self):
        self.assertEqual(MODELS['ecens'], 'EC 集合平均')
        self.assertEqual(GRID_MODELS['ecens'], 'ECMWF-ENSEMBLES平均')
        self.assertEqual(GRID_MODELS['gefs'], 'GEFS集合平均')

    def test_discover_and_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); ecens = base / 'local/cache/native-backfill/ecens'; ecens.mkdir(parents=True)
            cache = base / 'stations'; cache.mkdir()
            run = datetime(2026, 11, 1, tzinfo=UTC)
            f144 = write_frame(ecens, run, 144, 3); f150 = write_frame(ecens, run, 150, 6)
            with patch.object(station_forecasts, 'WEATHER', base / 'weather'), \
                 patch.object(station_forecasts, 'PRODUCTION', base / 'production'), \
                 patch.object(station_forecasts, 'ROOT', base / 'local'):
                groups = station_forecasts.discover(cache, run + timedelta(days=1))
            self.assertEqual(groups['ecens', '2026-11-01T00:00:00Z'][150], f150)
            targets = [(114.1, 22.55), (105.0, 22.0)]
            a = station_forecasts.read_native_frame(f144, 'ecens', run, 144, targets)
            b = station_forecasts.read_native_frame(f150, 'ecens', run, 150, targets)
            self.assertAlmostEqual(a['t'][0], 164.); self.assertTrue(np.isnan(a['t'][1]))
            self.assertEqual((a['rain_start'], b['rain_start']), (141, 144))
            self.assertAlmostEqual(b['rain'][0], 1.5)
            self.assertNotIn('td', b)

    def test_widened_box_reaches_western_stations(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); run = datetime(2026, 11, 2, tzinfo=UTC)
            frame = write_frame(directory, run, 24, 3, west=104, nx=69)
            r = station_forecasts.read_native_frame(frame, 'ecens', run, 24, [(105.0, 22.0), (121.5, 22.0)])
            self.assertAlmostEqual(r['t'][0], 44.); self.assertTrue(np.isnan(r['t'][1]))
            self.assertAlmostEqual(r['rain'][0], 1.5)


    def test_grid_reader_admits_widened_box(self):
        import native_archive
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); run = datetime(2026, 11, 2, tzinfo=UTC)
            frame = write_frame(directory, run, 24, 3, west=104, nx=69)
            meta = json.loads(frame.read_text()); meta.update(model='GEFS集合平均', storage='gefs-regional-v1')
            out = native_archive.load_global(frame, 'gefs', meta)
            self.assertEqual((out['lon'][0], out['lon'][-1]), (110.0, 119.0))
            self.assertEqual(len(out['layers']['surface']['t']), 37 * 29)


    def test_dew_point_mean_and_analysis_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); run = datetime(2026, 11, 2, tzinfo=UTC)
            frame = write_frame(directory, run, 24, 3, west=104, nx=69)
            fields = frame.with_suffix('.fields')
            np.save(fields / 'surface_td.npy', np.full((37, 69), 18., np.float32))
            np.save(fields / 'surface_msl.npy', np.full((37, 69), 1012., np.float32))
            r = station_forecasts.read_native_frame(frame, 'ecens', run, 24, [(114.1, 22.55)])
            self.assertAlmostEqual(r['td'][0], 18.); self.assertAlmostEqual(r['msl'][0], 1012.)
            signature = station_forecasts.native_signature(frame)[0]
            self.assertIn('surface_td', [name for name, _ in signature[2]])
        import analysis_verification
        self.assertIn('ECMWF-ENSEMBLES平均', analysis_verification.SURFACE_MODELS)
        self.assertEqual(analysis_verification.NATIVE_MODELS['ecens'][0], 'ECMWF-ENSEMBLES平均')


if __name__ == '__main__':
    unittest.main()
