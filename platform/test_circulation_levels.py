"""WN3 and GEFS pressure-level frames feed circulation verification."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile, unittest

import numpy as np

import analysis_verification as A

UTC = timezone.utc
RUN = datetime(2026, 10, 20, 12, tzinfo=UTC)


def grid(step):
    return np.arange(100., 125.01, step), np.arange(10., 35.01, step)


def wn3_frame(path, run='20261020_12hr_01_preds', units=('scalar:gpm', 'u:m/s', 'v:m/s')):
    lon, lat = grid(.25); levels = np.array(A.LEVELS)
    height = np.array([800., 1500., 3100., 5800., 9600., 12400.])[:, None, None]*np.ones((6, len(lat), len(lon)))
    wind = np.ones_like(height)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, scalar=height, u=wind, v=2*wind, levels_hpa=levels, units=np.array(units), lat=lat, lon=lon,
                        lead_h=np.array(24), run=np.array(run), n_members=np.array(64))


def gefs_frame(path):
    lon, lat = grid(.5); levels = np.array(A.LEVELS)
    height = np.array([800., 1500., 3100., 5800., 9600., 12400.])[:, None, None]*np.ones((6, len(lat), len(lon)))
    np.savez_compressed(path, gh=height, u=height*0+3, v=height*0+4, levels_hpa=levels, lat=lat, lon=lon,
                        lead_h=np.array(24), run=np.array('2026102012'))


def job(path, archive, level=500):
    return dict(path=path, model=A.WN3, run=RUN, lead=24, valid=RUN+timedelta(hours=24), level=level, archive=archive)


class LevelFrames(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_wn3_frame_is_cropped_and_keeps_units(self):
        path = self.root/'20261020_12hr_01_preds/lead_024h.npz'; wn3_frame(path)
        lon, lat, (height, u, v) = A.archive_forecast(job(path, 'wn3-levels'), 500)
        self.assertEqual((lon[0], lon[-1], lat[0], lat[-1]), (107., 121., 17., 28.))
        self.assertTrue(np.all(height == 5800) and np.all(u == 1) and np.all(v == 2))

    def test_gefs_frame_reads_requested_level(self):
        path = self.root/'gefs_levels_2026102012_f024.npz'; gefs_frame(path)
        _, _, (height, u, v) = A.archive_forecast(job(path, 'gefs-levels', 200), 200)
        self.assertTrue(np.all(height == 12400) and np.all(u == 3) and np.all(v == 4))

    def test_wrong_identity_or_units_rejected(self):
        path = self.root/'20261020_12hr_01_preds/lead_024h.npz'
        wn3_frame(path, run='20261021_12hr_01_preds')
        with self.assertRaises(ValueError): A.archive_forecast(job(path, 'wn3-levels'), 500)
        wn3_frame(path, units=('scalar:m2s2', 'u:m/s', 'v:m/s'))
        with self.assertRaises(ValueError): A.archive_forecast(job(path, 'wn3-levels'), 500)

    def test_only_00_12_ensemble_runs_are_candidates(self):
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        day = (now-timedelta(days=5)).strftime('%Y%m%d')
        wn3 = self.root/'wn3'; gefs = self.root/'gefs'; gefs.mkdir()
        wn3_frame(wn3/f'{day}_12hr_01_preds/lead_024h.npz')
        for hour in ('06', '12'): gefs_frame(gefs/f'gefs_levels_{day}{hour}_f024.npz')
        names = ('WN3_LEVEL_ROOTS', 'GEFS_LEVEL_ROOT', 'NATIVE_MODELS', 'APP_CACHE', 'WEATHER')
        old = [getattr(A, name) for name in names]
        for name, value in zip(names, ((wn3,), gefs, {}, self.root/'none', self.root/'none')): setattr(A, name, value)
        self.addCleanup(lambda: [setattr(A, name, value) for name, value in zip(names, old)])
        found = [j for j in A.candidates(now) if j.get('archive')]
        self.assertEqual({(j['model'], j['run'].hour) for j in found}, {(A.WN3, 12), ('GEFS集合平均', 12)})
        self.assertEqual({j['level'] for j in found}, set(A.LEVELS))


if __name__ == '__main__': unittest.main()
