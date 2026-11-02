#!/usr/bin/env python3
"""Compare complete offline station generations with bounded pair-table memory.

Usage: python tools/compare_station_runs.py CACHE_A CACHE_B [--output result.json]
Pair rows (including fc/ob) are exact. Derived floating scores/sums use rel=1e-9,
abs=1e-12. Publication timestamps, compressed byte sizes and timing reports are
excluded; all score keys, sample counts and contingency counts are checked.
"""
import argparse
import gzip
import json
import math
from pathlib import Path
import sqlite3


class Comparison:
    def __init__(self):
        self.errors = []
        self.mismatches = 0
        self.checked = {}
        self.max_relative_error = 0.

    def fail(self, at, a, b):
        self.mismatches += 1
        if len(self.errors) < 20:
            self.errors.append(dict(location=at, a=repr(a)[:500], b=repr(b)[:500]))

    def value(self, a, b, at):
        if isinstance(a, dict) and isinstance(b, dict):
            if a.keys() != b.keys():
                self.fail(at+'.keys', sorted(a), sorted(b)); return
            for key in a:
                self.value(a[key], b[key], at+'.'+key)
        elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                self.fail(at+'.length', len(a), len(b)); return
            for i, (left, right) in enumerate(zip(a, b)):
                self.value(left, right, at+f'[{i}]')
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if isinstance(a, int) and isinstance(b, int):
                if a != b: self.fail(at, a, b)
            elif not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12):
                self.fail(at, a, b)
            if a != b:
                self.max_relative_error = max(self.max_relative_error, abs(a-b)/max(abs(a),abs(b),1e-300))
        elif a != b:
            self.fail(at, a, b)

    def sqlite(self, a, b, table, order, exact=False, json_columns=()):
        with sqlite3.connect(a.as_uri()+'?mode=ro&immutable=1', uri=True) as da, \
             sqlite3.connect(b.as_uri()+'?mode=ro&immutable=1', uri=True) as db:
            for connection in (da, db):
                connection.execute('PRAGMA cache_size=-4000')
                connection.execute('PRAGMA temp_store=FILE')
            ca=da.execute(f'SELECT * FROM {table} ORDER BY {order}')
            cb=db.execute(f'SELECT * FROM {table} ORDER BY {order}')
            if ca.description != cb.description: self.fail(table+'.schema',ca.description,cb.description)
            json_indexes = [i for i,column in enumerate(ca.description) if column[0] in json_columns]
            names=[c[0] for c in ca.description]
            key_indexes=[names.index(key.strip()) for key in order.split(',')]
            def batches(cursor):
                while True:
                    batch=cursor.fetchmany(2000)
                    if not batch:return
                    yield from batch
            def key(row):
                return tuple((0,None) if row[i] is None else (1,row[i]) for i in key_indexes)
            left,right=batches(ca),batches(cb)
            x,y=next(left,None),next(right,None)
            counts=dict(a_rows=0,b_rows=0,matched=0,added=0,removed=0,modified=0)
            while x is not None or y is not None:
                if y is None or (x is not None and key(x)<key(y)):
                    self.fail(table+'.removed',x,None);counts['a_rows']+=1;counts['removed']+=1
                    x=next(left,None);continue
                if x is None or key(y)<key(x):
                    self.fail(table+'.added',None,y);counts['b_rows']+=1;counts['added']+=1
                    y=next(right,None);continue
                before=self.mismatches
                if json_indexes:
                    x,y=list(x),list(y)
                    for i in json_indexes:x[i],y[i]=json.loads(x[i]),json.loads(y[i])
                if exact:
                    if x!=y:self.fail(f'{table}[{counts["matched"]}]',x,y)
                else:self.value(x,y,f'{table}[{counts["matched"]}]')
                counts['modified']+=int(self.mismatches>before)
                counts['a_rows']+=1;counts['b_rows']+=1;counts['matched']+=1
                x,y=next(left,None),next(right,None)
            self.checked[table]=counts


def snapshot(root):
    return root/'snapshots'/json.loads((root/'current.json').read_text())['generation']


def json_data(path):
    if path.suffix=='.gz':
        with gzip.open(path,'rt') as handle:return json.load(handle)
    return json.loads(path.read_text())


def canonical(rows, ignored=()):
    if isinstance(rows,list):
        rows=[{k:v for k,v in row.items() if k not in ignored} for row in rows]
        return sorted(rows,key=lambda r:json.dumps({k:v for k,v in r.items() if k in (
            'dataset','id','station','model','var','run','lead','valid','threshold','lead_min','lead_max','day')},sort_keys=True))
    return rows


def compare(a,b):
    result=Comparison(); sa,sb=snapshot(a),snapshot(b)
    result.sqlite(sa/'pairs.sqlite',sb/'pairs.sqlite','pairs','model,run,lead,station,var',exact=True)
    result.sqlite(sa/'pairs.sqlite',sb/'pairs.sqlite','detail_stats',
                  'station,model,var,lead,days,threshold',json_columns=('stats',))
    for days in (1,3,7,30):
        for name in (f'scores-{days}.json',f'station-scores-{days}.json'):
            left,right=canonical(json_data(sa/name)),canonical(json_data(sb/name))
            result.value(left,right,name);result.checked[name]=len(left)
    for var in ('t','rain6','rain24'):
        name=f'grid-rows-{var}.json.gz'
        left=canonical(json_data(sa/name),('created','updated'))
        right=canonical(json_data(sb/name),('created','updated'))
        result.value(left,right,name);result.checked[name]=len(left)
    result.sqlite(sa/'grid-map.sqlite',sb/'grid-map.sqlite','daily','model,var,day,lead,station')
    result.value(canonical(json_data(sa/'grid-datasets.json'),('updated','gzip_bytes')),
                 canonical(json_data(sb/'grid-datasets.json'),('updated','gzip_bytes')),'grid-datasets.json')
    result.value(canonical(json_data(a/'common-report.json')),canonical(json_data(b/'common-report.json')),'common-report.json')
    return dict(equal=result.mismatches==0, mismatches=result.mismatches, errors=result.errors,
                checked=result.checked,max_relative_error=result.max_relative_error,
                tolerance=dict(relative=1e-9,absolute=1e-12),
                ignored=['publication created/updated timestamps','grid-datasets gzip_bytes'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('a',type=Path);parser.add_argument('b',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(); report=compare(args.a.resolve(),args.b.resolve())
    data=json.dumps(report,ensure_ascii=False,indent=2);print(data)
    if args.output:args.output.write_text(data+'\n')
    raise SystemExit(0 if report['equal'] else 1)
