import gzip
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import server
import station_api
from catalog_http import CatalogSnapshot


class CatalogHTTPTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.value = dict(version=2, generated='fixture', status={'accepted': 1}, datasets=[
            dict(id='气温', kind='气温'), dict(id='降水', kind='降水')], rows=[
            dict(dataset='气温', model='EC', valid='2026-10-28', lead=24, RMSE=1.2345678901234567, n=3),
            dict(dataset='降水', model='GFS', H=2, M=1, F=0, n=None),
            dict(dataset='气温', model='GFS', valid='2026-10-29', lead=48, RMSE=None, n=4)])
        self.prepared = CatalogSnapshot(self.value, self.temporary.name)
        server.snapshot = json.dumps(self.value, ensure_ascii=False).encode()
        server.http_snapshot = self.prepared
        server.recent_snapshots = {self.prepared.snapshot_id: self.prepared}
        server.loaded = time.monotonic()
        server.refreshing = False
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}/Verification/test/api/'

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        server.recent_snapshots = {}
        server.http_snapshot = None
        self.temporary.cleanup()

    def rows_url(self, **changes):
        params = dict(dataset='气温', snapshot=self.prepared.snapshot_id)
        params.update(changes)
        return self.base + 'rows?' + urlencode(params)

    def test_metadata_and_legacy_contract(self):
        with urlopen(self.base + 'catalog?format=metadata') as response:
            metadata = json.load(response)
        self.assertNotIn('rows', metadata)
        self.assertEqual(metadata['generated'], self.value['generated'])
        self.assertEqual(metadata['datasets'][0]['row_count'], 2)
        self.assertEqual(metadata['datasets'][0]['score_metrics'], ['RMSE'])
        self.assertTrue(metadata['datasets'][0]['score_has_n'])
        with urlopen(self.base + 'catalog') as response:
            self.assertEqual(json.load(response), self.value)

    def test_rows_preserve_values_order_and_compression(self):
        expected = [r for r in self.value['rows'] if r['dataset'] == '气温']
        for encoding in ('gzip', 'identity'):
            with urlopen(Request(self.rows_url(), headers={'Accept-Encoding': encoding})) as response:
                payload = response.read()
                if encoding == 'gzip':
                    self.assertEqual(response.headers['Content-Encoding'], 'gzip')
                    payload = gzip.decompress(payload)
                self.assertEqual(json.loads(payload)['rows'], expected)
                self.assertEqual(response.headers['Cache-Control'], 'private, max-age=300')

    def test_validation_and_generation_isolation(self):
        for url, status in ((self.rows_url(dataset='missing'), 404),
                            (self.rows_url(snapshot='f' * 32), 409),
                            (self.rows_url(snapshot='../other'), 400),
                            (self.rows_url() + '&dataset=other', 400),
                            (self.rows_url() + '&unknown=1', 400)):
            with self.assertRaises(HTTPError) as error:
                urlopen(url)
            self.assertEqual(error.exception.code, status)
        newer = CatalogSnapshot(dict(self.value, rows=[]), self.temporary.name)
        server.http_snapshot = newer
        server.recent_snapshots[newer.snapshot_id] = newer
        with urlopen(self.rows_url()) as response:
            self.assertEqual(json.load(response)['snapshot_id'], self.prepared.snapshot_id)

    def test_station_date_range_preserves_exact_rows(self):
        path = Path(self.temporary.name) / 'grid-rows-t.json.gz'
        rows = self.value['rows'][::2]
        with gzip.open(path, 'wt', encoding='utf-8') as handle:
            json.dump(rows, handle)
        dataset = station_api.GRID_DATASETS['t']
        with patch.object(station_api, 'snapshot', return_value=Path(self.temporary.name)):
            self.assertEqual(station_api.grid_rows({'dataset': dataset}), path)
            selected = station_api.dispatch('grid-rows', urlencode(dict(
                dataset=dataset, start='2026-10-29', end='2026-10-29')))
            self.assertEqual(selected, rows[1:])
            self.assertEqual(station_api.grid_rows(dict(dataset=dataset, start='2026-10-28', end='2026-10-29', leads='24')), rows[:1])
            with self.assertRaises(ValueError):
                station_api.grid_rows(dict(dataset=dataset, start='2026-10-30', end='2026-10-29'))

    def test_versioned_station_range_cache(self):
        generation = '20261029T012208988881'
        directory = Path(self.temporary.name) / generation
        directory.mkdir()
        with gzip.open(directory / 'grid-rows-t.json.gz', 'wt', encoding='utf-8') as handle:
            json.dump(self.value['rows'][::2], handle)
        with patch.object(station_api, 'snapshot', return_value=directory):
            params = dict(dataset=station_api.GRID_DATASETS['t'], generation=generation,
                          start='2026-10-29', end='2026-10-29')
            with urlopen(self.base + 'stations/grid-rows?' + urlencode(params)) as response:
                self.assertEqual(json.load(response), self.value['rows'][2:])
                self.assertEqual(response.headers['Cache-Control'], 'private, max-age=300')
            params['generation'] = '../other'
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base + 'stations/grid-rows?' + urlencode(params))
            self.assertEqual(error.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
