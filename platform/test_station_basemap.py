"""Regression coverage for the missing Fujian/Guangxi station-map boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shapely.geometry import LineString, box
from shapely.ops import unary_union

import station_api
from station_common import BOX, ROOT


class StationBasemapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.basemap = json.loads((ROOT / 'station-basemap.json').read_text())

    def test_full_map_coverage_and_real_linework(self):
        self.assertEqual(self.basemap['station_bounds'], list(BOX))
        west, south, east, north = self.basemap['map_bounds']
        self.assertLessEqual(west, 104.4)  # include Guangxi's western edge
        self.assertGreaterEqual(east, 120.7)  # include Fujian's eastern edge
        self.assertEqual(north, 27)
        layers = {}
        for key in ('coastline', 'country_boundaries', 'province_boundaries'):
            coordinates = self.basemap[key]
            self.assertTrue(coordinates)
            for line in coordinates:
                self.assertGreaterEqual(len(line), 2)
                for lon, lat in line:
                    self.assertTrue(west <= lon <= east and south <= lat <= north)
            layers[key] = unary_union([LineString(line) for line in coordinates])
        # Check Fujian mainland, rather than accepting a Taiwan island at 120 E.
        self.assertGreater(layers['coastline'].intersection(box(118.2, 24.4, 119.9, 27)).length, 1)
        self.assertGreater(layers['coastline'].intersection(box(108, 21, 110, 22)).length, 1)
        self.assertGreater(layers['country_boundaries'].intersection(box(106, 21.4, 108, 23.5)).length, 1)
        self.assertGreater(layers['province_boundaries'].intersection(box(110, 24, 116, 27)).length, 5)
        # A polygon clipped before extracting its boundary creates false frame edges.
        frame = box(west, south, east, north).boundary
        for layer in layers.values():
            self.assertLess(layer.intersection(frame).length, .001)

    def test_old_score_snapshot_gets_new_map_without_being_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            previous = json.dumps(dict(schema=1, generated='2026-10-25T00:00:00Z',
                                       stations=[], coastline=[[[108, 21], [118, 24]]]))
            (directory / 'meta.json').write_text(previous)
            (directory / 'scores-7.json').write_text('[]')
            with patch.object(station_api, 'snapshot', return_value=directory):
                metadata = station_api.meta()
            self.assertEqual(metadata['generated'], '2026-10-25T00:00:00Z')
            self.assertEqual(metadata['windows'], [7])
            self.assertEqual(metadata['coastline'], self.basemap['coastline'])
            self.assertTrue(metadata['province_boundaries'])
            self.assertTrue(metadata['country_boundaries'])
            self.assertEqual((directory / 'meta.json').read_text(), previous)


if __name__ == '__main__':
    unittest.main()
