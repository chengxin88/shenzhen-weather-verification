"""Immutable, compressed per-dataset views of the existing catalog.

Only transport changes here: row values and their original order are retained.
Each generation owns its temporary files until its last request releases it.
"""
from collections import defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import tempfile
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


class CatalogSnapshot:
    def __init__(self, value, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(prefix='generation-', dir=directory)
        self.snapshot_id = uuid.uuid4().hex
        self.files = {}
        groups = defaultdict(list)
        for row in value['rows']:
            groups[row['dataset']].append(row)
        datasets = []
        metrics = ('ME', 'MAE', 'RMSE', 'VRMSE', 'CC')
        for dataset in value['datasets']:
            ident = dataset['id']
            rows = groups.get(ident, [])
            finite = lambda v: isinstance(v, (int, float)) and math.isfinite(v)
            datasets.append(dict(dataset, row_count=len(rows),
                score_has_n=any(finite(row.get('n')) for row in rows),
                score_metrics=list(dict.fromkeys(dataset.get('score_metrics',[])+[metric for metric in metrics
                               if any(finite(row.get(metric)) for row in rows)]))))
            filename = hashlib.sha256(ident.encode()).hexdigest() + '.json.gz'
            path = Path(self._temporary.name) / filename
            payload = dict(dataset=ident, snapshot_id=self.snapshot_id, rows=rows)
            with gzip.open(path, 'wb', compresslevel=6) as handle:
                handle.write(encode(payload))
            self.files[ident] = path
        metadata = {key: item for key, item in value.items() if key != 'rows'}
        metadata.update(datasets=datasets, snapshot_id=self.snapshot_id)
        self.catalog_bytes = encode(metadata)

    def rows_file(self, dataset):
        return self.files[dataset]

    def __del__(self):
        temporary = getattr(self, '_temporary', None)
        if temporary is not None:
            temporary.cleanup()
