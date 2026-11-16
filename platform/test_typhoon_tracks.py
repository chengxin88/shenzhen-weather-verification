"""Single-issue track helpers: truth interpolation, errors, archive identity."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json, tempfile, unittest

import numpy as np

import typhoon_tracks as K

T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)


def fix(hours, lon, lat, pressure=980., wind=30.):
    return dict(stamp=T0+timedelta(hours=hours), lon=lon, lat=lat, pressure=pressure, wind=wind)


class Truth(unittest.TestCase):
    def test_interpolates_inside_and_refuses_outside(self):
        obs = [fix(0, 120, 20, 980, 30), fix(12, 122, 22, 960, 40)]
        point = K.interpolate(obs, T0+timedelta(hours=6))
        self.assertEqual((point['lon'], point['lat'], point['pressure'], point['wind']), (121, 21, 970, 35))
        self.assertIsNone(K.interpolate(obs, T0+timedelta(hours=13)))

    def test_errors_only_where_truth_exists(self):
        obs = [fix(0, 120, 20), fix(48, 120, 20)]
        rows = [dict(lead=24, stamp=T0+timedelta(hours=24), lon=121, lat=20, pressure=990, wind=None),
                dict(lead=72, stamp=T0+timedelta(hours=72), lon=121, lat=20, pressure=990, wind=20)]
        result = K.errors(rows, obs)
        self.assertEqual(set(result), {24})
        self.assertAlmostEqual(result[24]['km'], 104, delta=2)
        self.assertEqual(result[24]['pressure'], 10); self.assertNotIn('wind', result[24])


class Files(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.old = K.WN3_ROOTS, K.CACHE; K.WN3_ROOTS = (self.root,); K.CACHE = self.root/'cache'
        self.addCleanup(lambda: (setattr(K, 'WN3_ROOTS', self.old[0]), setattr(K, 'CACHE', self.old[1])))

    def wn3(self, run='20261020_00hr_01_preds', units=('scalar:hPa(mslp)', 'u:m/s(10m)', 'v:m/s(10m)')):
        folder = self.root/'20261020_00hr_01_preds'; folder.mkdir(exist_ok=True)
        lon = np.arange(100., 131., .25); lat = np.arange(5., 41., .25); field = np.ones((len(lat), len(lon)))
        np.savez(folder/'sfc_006h.npz', scalar=field*1000, u=field, v=field, lon=lon, lat=lat, lead_h=np.array(6), run=np.array(run), units=np.array(units))

    def test_wn3_frame(self):
        self.wn3(); frame = K.wn3_fields(T0, 120)
        self.assertEqual(list(frame['leads']), [6]); self.assertEqual(frame['msl'].shape[0], 1)

    def test_wn3_wrong_run_rejected(self):
        self.wn3(run='20261021_00hr_01_preds')
        with self.assertRaises(ValueError): K.wn3_fields(T0, 120)

    def test_missing_run_is_none(self):
        self.assertIsNone(K.wn3_fields(T0+timedelta(days=9), 120))

    def test_only_nonempty_tracks_are_stored(self):
        self.assertEqual(K.stored('WN3', T0, 120, 20, lambda: []), [])
        self.assertFalse(K.CACHE.exists())
        rows = K.stored('WN3', T0, 120, 20, lambda: [dict(lead=6, time=T0, lon=1., lat=2., pressure=3., wind=4.)])
        self.assertEqual(rows[0]['time'], '2026-10-20T08:00:00+08:00')
        self.assertEqual(K.stored('WN3', T0, 120, 20, lambda: self.fail('should use the stored track')), rows)


if __name__ == '__main__': unittest.main()
