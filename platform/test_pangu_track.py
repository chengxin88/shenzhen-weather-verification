"""Cyclone following on regional Pangu fields."""
import unittest

import numpy as np

import pangu_track as P


def frame(centers):
    lon = np.arange(110., 131., .25); lat = np.arange(10., 31., .25); x, y = np.meshgrid(lon, lat)
    msl = []; wind = []
    for cx, cy in centers:
        d = np.hypot(x-cx, y-cy); msl.append(1012-30*np.exp(-(d/2)**2)); wind.append(25*np.exp(-(d/2)**2))
    return dict(lon=lon, lat=lat, leads=np.arange(len(centers))*6, msl=np.array(msl), u10=np.array(wind), v10=np.zeros_like(wind))


class Track(unittest.TestCase):
    def test_follows_moving_low(self):
        rows = P.track(frame([(120, 18), (120, 19), (121, 20)]), '2026110300', 120.5, 18.2, 12)
        self.assertEqual([(r['lon'], r['lat']) for r in rows], [(120, 18), (120, 19), (121, 20)])
        self.assertLess(rows[0]['pressure'], 985); self.assertGreater(rows[0]['wind'], 20)
        self.assertEqual(rows[1]['time'].hour, 6)

    def test_stops_at_lead_limit(self):
        self.assertEqual(len(P.track(frame([(120, 18)]*5), '2026110300', 120, 18, 12)), 3)

    def test_stops_when_no_closed_low_remains(self):
        rows = P.track(frame([(120, 18), (128, 28)]), '2026110300', 120, 18, 6)
        self.assertEqual(len(rows), 1)


if __name__ == '__main__': unittest.main()
