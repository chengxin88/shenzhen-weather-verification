#!/usr/bin/env python3
"""Offline counter migration from persistent pairs; uses the refresh worker's flock/publication protocol."""
import fcntl
import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import guifan_scores as guifan
import station_score as score
from station_common import (cache_root, read_json, atomic_json, now, stamp, sync_directory,
                            VARIABLES, THRESHOLDS)
from station_obs import select_observations


def migrate(root):
    root=Path(root)
    with (root/'refresh.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        previous=root/'snapshots'/read_json(root/'current.json')['generation']
        meta=read_json(previous/'meta.json')
        # Archived QC/source flags are sufficient. No HTTP clients or forecast samplers.
        observations=[]
        for path in sorted((root/'obs').glob('*.jsonl.gz')):
            with gzip.open(path,'rt') as handle:
                for line in handle:
                    if line.strip():
                        item=json.loads(line)
                        if item.get('var','').startswith('rain'):observations.append(item)
        observations=select_observations(observations,meta['stations'])
        with closing(score.database(root/'working.sqlite')) as db:
            if not db.execute('SELECT COUNT(*) FROM pairs').fetchone()[0]:
                # An exported snapshot directory may have been copied without a working DB.
                with sqlite3.connect((previous/'pairs.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True) as source:source.backup(db)
            score.migrate_trace(db,observations)
            generation=now().strftime('%Y%m%dT%H%M%S%f')
            directory=root/'snapshots'/('.incomplete-'+generation);directory.mkdir()
            end=stamp(meta['window_end'])
            score.aggregates(db,directory,end)
            for name in ('common-report.json','shenzhen-sanity.json'):
                value=read_json(previous/name,{})
                atomic_json(directory/name,value)
            meta.update(score_revision=guifan.REVISION,generated=now().strftime('%Y-%m-%dT%H:%M:%SZ'),
                variables=[dict(id=k,label=v[0],unit=v[1],thresholds=THRESHOLDS.get(k,[]),
                                **guifan.metadata(k,int(k[4:]) if k in THRESHOLDS else None)) for k,v in VARIABLES.items()])
            atomic_json(directory/'meta.json',meta)
            days={r[0] for r in db.execute('SELECT day FROM dirty_days')}
            score.export_days(db,root,days)
            db.commit()
            with closing(sqlite3.connect(directory/'pairs.sqlite')) as target:db.backup(target)
            for path in directory.iterdir():
                with path.open('rb') as handle:os.fsync(handle.fileno())
            sync_directory(directory)
            directory.rename(root/'snapshots'/generation);sync_directory(root/'snapshots')
            atomic_json(root/'current.json',dict(generation=generation),durable=True)
            with db:db.execute('DELETE FROM dirty_days')
            score.prune_snapshots(root)
            print('PASS migration',generation,'pairs',db.execute('SELECT COUNT(*) FROM pairs').fetchone()[0])
    return generation


if __name__=='__main__':
    migrate(cache_root())
