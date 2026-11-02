"""Forecast discovery must keep finding new runs in directories without catalog.json."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from station_common import UTC
import station_forecasts


class DiscoverWithoutCatalog(unittest.TestCase):
    def test_new_runs_found_after_first_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); gefs = base / 'local/cache/native-backfill/gefs'; gefs.mkdir(parents=True)
            cache = base / 'stations'; cache.mkdir()
            (gefs / 'gefs_2026101800_f024_global1.json').write_text('{}')
            end = datetime(2026, 10, 19, tzinfo=UTC)
            with patch.object(station_forecasts, 'WEATHER', base / 'weather'), \
                 patch.object(station_forecasts, 'PRODUCTION', base / 'production'), \
                 patch.object(station_forecasts, 'ROOT', base / 'local'):
                groups = station_forecasts.discover(cache, end)
                self.assertIn(('gefs', '2026-10-18T00:00:00Z'), groups)
                (gefs / 'gefs_2026110100_f024_global1.json').write_text('{}')
                groups = station_forecasts.discover(cache, end + timedelta(days=14))
                self.assertIn(('gefs', '2026-11-01T00:00:00Z'), groups)


if __name__ == '__main__':
    unittest.main()
