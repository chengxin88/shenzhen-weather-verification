#!/usr/bin/env python3
"""Freeze a completed worker's certified station inputs for full pairing replay.

Reads only local cache files. The replay uses the same point values as the
incremental worker, including lossless legacy-cache migration; it never copies
fc/ob from the result into the replay. Pickles are for locally generated inputs.
"""
import argparse
from datetime import timedelta
import gzip
import json
from pathlib import Path
import pickle
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from station_common import read_json,stamp,iso
from station_forecasts import load_samples,sample_state


def freeze(root, output):
    output.mkdir(parents=True,exist_ok=True)
    generation=read_json(root/'current.json')['generation']
    meta=read_json(root/'snapshots'/generation/'meta.json')
    end=stamp(meta['window_end']);cutoff=end-timedelta(days=31)
    report=dict(generation=generation,end=iso(end),runs=0,cached_runs=0,leads=0,
                input='certified station NPZ arrays; all pair columns are produced by the full baseline pairer')
    with sqlite3.connect((root/'working.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        db.execute('PRAGMA cache_size=-4000')
        for model,run_text,source in db.execute('SELECT model,run,source FROM job_state ORDER BY model,run'):
            run=stamp(run_text)
            valids=list(db.execute('SELECT valid,signature FROM job_valid WHERE model=? AND run=?',(model,run_text)))
            available={at for at,_ in valids}
            key=model+'_'+run.strftime('%Y%m%d%H');path=root/'forecast'/(key+'.npz')
            samples={}
            if path.is_file() and any(signature is not None for _,signature in valids):
                # Within the scoring window, honor exactly the descriptor's
                # available leads. Retain the preceding 24h accumulation tiles.
                state,_=sample_state(path,path.with_suffix('.sample-state.json'))
                if not state.get('station_signature'):raise ValueError(f'Uncertified sample cache: {path}')
                for lead,values in load_samples(path).items():
                    at=run+timedelta(hours=lead)
                    if cutoff-timedelta(hours=24)<=at<cutoff or iso(at) in available:
                        samples[lead]=values
                report['cached_runs']+=1
            name=model+'_'+run_text.replace(':','')+'.pkl.gz'
            temporary=output/(name+'.next')
            with gzip.open(temporary,'wb') as handle:pickle.dump((model,run,samples,source,[]),handle)
            temporary.replace(output/name)
            report['runs']+=1;report['leads']+=len(samples)
    (output/'provenance.json').write_text(json.dumps(report,indent=2)+'\n')
    (output/'.incomplete').unlink(missing_ok=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();print(json.dumps(freeze(args.root.resolve(),args.output.resolve()),indent=2))
