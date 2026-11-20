#!/usr/bin/env python3
"""只读国家站 ZIP，核验并去重为 raw/nmic 可导入的 TXT；输出逐时次覆盖表。"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from station_common import ROOT, UTC, atomic_json, in_cmagd, iso, read_json
from nmic_obs import FIELDS, read_records


def prepare(source, metadata, output, baseline=None):
    if output.exists():
        raise ValueError('输出目录已存在，请指定新的目录，以免混入上一次产物')
    meta = {str(s['stationId']): s for s in read_json(metadata)['data']}
    core = set(read_json(ROOT/'nmic_provinces.json'))
    expected = {sid for sid, s in meta.items() if in_cmagd(float(s['lon']), float(s['lat']))}
    outer = expected-core
    records = {}; header = None; files = []; duplicates = conflicts = 0; examples = []
    for archive in sorted(source.glob('*.zip')):
        with zipfile.ZipFile(archive) as z:
            for name in sorted(z.namelist()):
                if not name.lower().endswith('.txt'):
                    continue
                lines = z.read(name).decode('utf-8-sig').splitlines()
                fields = lines[0].split() if lines else []
                if not FIELDS <= set(fields):
                    if fields and fields[0] == 'Station_Id_C':
                        raise ValueError(f'表头缺要素：{archive.name}/{name}')
                    continue  # 官方说明文件
                if len(set(fields)) != len(fields):
                    raise ValueError(f'重复列名：{archive.name}/{name}')
                if header is None: header = fields
                if set(fields) != set(header):
                    raise ValueError(f'表头不一致：{archive.name}/{name}')
                count = 0
                for lineno, line in enumerate(lines[1:], 2):
                    if not line.strip(): continue
                    cells = line.split()
                    if len(cells) != len(fields):
                        raise ValueError(f'列数错误：{archive.name}/{name}:{lineno}')
                    item = dict(zip(fields, cells)); sid = item['Station_Id_C']
                    at = datetime(*(int(item[k]) for k in ('Year', 'Mon', 'Day', 'Hour')), tzinfo=UTC)
                    if not re.fullmatch(r'\d{5}', sid) or at.hour % 3:
                        raise ValueError(f'站号或三小时时次错误：{archive.name}/{name}:{lineno}')
                    # Do not treat a malformed measurement as valid coverage.
                    for key in set(fields)-{'Station_Id_C', 'Year', 'Mon', 'Day', 'Hour'}:
                        float(item[key])
                    key = sid, at
                    if key in records:
                        duplicates += 1
                        if records[key] != item:
                            conflicts += 1
                            if len(examples)<20: examples.append(dict(station=sid,time=iso(at),winner=archive.name+'/'+name))
                    records[key] = item; count += 1
                files.append(dict(archive=archive.name,member=name,records=count,
                                  sha256=hashlib.sha256(z.read(name)).hexdigest()))
    if not records:
        raise ValueError('没有国家站记录')
    core_changes = []
    if baseline:
        previous, _ = read_records(baseline)
        for key, item in previous.items():
            if key in records and records[key] != item:
                core_changes.append(dict(station=key[0],time=iso(key[1])))
        if core_changes:
            raise ValueError(f'现有记录有 {len(core_changes)} 处变化，必须先核实；示例 {core_changes[:5]}')
    by_time = defaultdict(set)
    for sid, at in records: by_time[at].add(sid)
    coverage = []; at = min(by_time)
    while at <= max(by_time):
        ids = by_time.get(at, set()); missing = outer-ids
        coverage.append(dict(time=iso(at),core=len(ids&core),outer=len(ids&outer),
                             outer_expected=len(outer),outer_missing=sorted(missing)))
        at += timedelta(hours=3)
    output.mkdir(parents=True)
    # Uppercase C sorts before original S exports; existing core reports keep
    # their established filename precedence when the supervisor installs this.
    txt = output/'CMAGD_SURF_CHN_MUL_HOR.txt'
    with txt.open('w', encoding='utf-8') as f:
        f.write(' '.join(header)+'\n')
        for key, item in sorted(records.items()): f.write(' '.join(item[k] for k in header)+'\n')
    checked, diag = read_records(output)
    if checked != records: raise AssertionError('整理后记录不等价')
    report = dict(expected_stations=len(expected),core_expected=len(core),outer_expected=len(outer),
                  stations=len({sid for sid, at in records}),unique_reports=len(records),duplicates=duplicates,
                  conflicts=conflicts,conflict_examples=examples,missing_metadata=sorted({sid for sid,at in records}-set(meta)),
                  outside_cmagd=sorted({sid for sid,at in records}-expected),baseline_conflicts=len(core_changes),
                  first=iso(min(by_time)),last=iso(max(by_time)),inputs=files,coverage=coverage,
                  sha256=hashlib.sha256(txt.read_bytes()).hexdigest(),roundtrip=diag,
                  coverage_distribution=dict(Counter(c['outer'] for c in coverage)))
    atomic_json(output/'coverage.json',report)
    with (output/'coverage.csv').open('w') as f:
        f.write('time_utc,core,outer,outer_expected,outer_missing\n')
        for c in coverage: f.write(f"{c['time']},{c['core']},{c['outer']},{c['outer_expected']},{len(c['outer_missing'])}\n")
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--metadata',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--baseline-nmic',type=Path)
    a = p.parse_args(); result=prepare(a.source,a.metadata,a.output,a.baseline_nmic)
    print(json.dumps({k:v for k,v in result.items() if k not in ('inputs','coverage')},ensure_ascii=False,indent=2))
