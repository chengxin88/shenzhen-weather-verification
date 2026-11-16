"""A silent station that reports again must reach the archive, or it can never become active."""
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sz_district_obs
from build_stations import active_ids
from station_obs import load_observations


class StationReturn(unittest.TestCase):
    def test_inactive_station_is_archived_but_not_scored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'raw/iem').mkdir(parents=True)
            (root / 'raw/iem/a.csv').write_text(
                'station,valid,tmpf,dwpf,sknt\n'
                'ZGGG,2026-11-05 00:00,68,50,5\n'
                'ZSCN,2026-11-05 00:00,59,41,4\n')
            active = dict(id='ZGGG', type='metar')
            silent = dict(id='ZSCN', type='metar')
            end = datetime(2026, 11, 5, 6, tzinfo=timezone.utc)
            with patch.object(sz_district_obs, 'observation_rows', return_value=[]):
                selected, summary, _ = load_observations(root, [active], end, [silent])
            self.assertEqual({key[0] for key in selected}, {'ZGGG'})
            self.assertEqual(summary['iem']['stations'], 1)
            with gzip.open(root / 'obs/2026-11-05.jsonl.gz', 'rt') as handle:
                archived = {json.loads(line)['station'] for line in handle}
            self.assertEqual(archived, {'ZGGG', 'ZSCN'})
            self.assertEqual(active_ids(root / 'obs', minimum=1), {'ZGGG', 'ZSCN'})


if __name__ == '__main__':
    unittest.main()
