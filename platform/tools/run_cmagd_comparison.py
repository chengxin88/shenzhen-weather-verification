#!/usr/bin/env python3
"""Spark 第四步：只读冻结输入，在指定私有目录重放完整新旧流程和常规轮。

不部署、不发通知、不联网。输入必须由主管冻结；预报大帧只读共享。
--snapshot 指向已经发布的不可变快照；--input-cache 提供同一批冻结原料。
"""
import argparse
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import resource
import shutil
import socket
import sqlite3
import subprocess
import sys
import time


ROOT=Path(__file__).resolve().parents[1]


def inside(path,parent):
    return path==parent or parent in path.parents


def measure_inputs(root):
    # Stat certificates/raw files, never hash the multi-GB frame archive.
    return [(str(p.relative_to(root)),p.stat().st_size,p.stat().st_mtime_ns)
            for folder in ('raw','forecast','obs','wn3-xj','wn3ml')
            for p in sorted((root/folder).rglob('*')) if p.is_file()]


def worker(a):
    root=a.worker_cache.resolve()
    if not inside(root,a.output.resolve()):raise ValueError('worker 缓存必须位于输出目录')
    native_cache=(a.output/'native-cache' if not a.output.stat().st_mode & 0o022 else
                  Path('/tmp')/('station-aggregate-'+str(os.getuid())))
    os.environ.update(STATIONS_CACHE_DIR=str(root),STATIONS_NMIC_DIR=str(root/'raw/nmic'),
                      STATIONS_NMIC_METADATA=str(root/'raw/nmic/A0012_stations_api.json'),
                      STATIONS_ALERTS='0',PYTHONDONTWRITEBYTECODE='1',
                      STATIONS_WEATHER_ROOT=str(a.weather_root.resolve()),STATIONS_NATIVE_ROOT=str(a.native_root.resolve()),
                      STATIONS_AGG_NATIVE_CACHE=str(native_cache))
    # Newly sampled numerical frames and all supplemental input readers are
    # local. Any accidental request, including notifications, fails closed.
    def offline(*args,**kwargs):raise RuntimeError('真实规模对照禁止联网')
    socket.socket.connect=offline;socket.socket.connect_ex=offline;socket.create_connection=offline
    sys.path.insert(0,str(a.worker_code.resolve()))
    sys.path.insert(0,str(a.weather_root.resolve().parent/'weather_backend'))
    import station_score
    from station_common import stamp
    started=time.monotonic()
    result=station_score.refresh(root,stamp(a.end))
    result.update(wall_seconds=time.monotonic()-started,
                  peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
    (root/(a.phase+'-measurement.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--input-cache',type=Path,required=True)
    p.add_argument('--baseline-code',type=Path,required=True)
    p.add_argument('--candidate-code',type=Path,default=ROOT)
    p.add_argument('--nmic-dir',type=Path,required=True)
    p.add_argument('--weather-root',type=Path,required=True)
    p.add_argument('--native-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--end',help='默认使用正式快照 window_end，固定 UTC 时刻')
    p.add_argument('--warm-hours',type=int,default=3,help='首轮核对后双方同时推进此小时数，默认三小时')
    p.add_argument('--worker-cache',type=Path,help=argparse.SUPPRESS)
    p.add_argument('--worker-code',type=Path,help=argparse.SUPPRESS)
    p.add_argument('--phase',default='first',help=argparse.SUPPRESS)
    a=p.parse_args()
    a.output=a.output.resolve()
    if a.worker_cache:return worker(a)
    sources=[getattr(a,n).resolve() for n in ('snapshot','input_cache','baseline_code','candidate_code','nmic_dir','weather_root','native_root')]
    if a.output.exists():p.error('输出目录必须不存在')
    if any(inside(a.output,s) or inside(s,a.output) for s in sources):p.error('输出目录不能包含输入，也不能位于输入目录内')
    if a.warm_hours<0:p.error('warm-hours 不能为负')
    metadata=json.loads((a.snapshot/'meta.json').read_text())
    a.end=a.end or metadata['window_end']
    database=a.snapshot/'pairs.sqlite';size=database.stat().st_size
    # Working+published pairs, detail/day caches, dirty-day CSVs and comparison
    # sort spill: intentionally conservative until real expanded sizes exist.
    estimate=size*16+20*2**30
    free=shutil.disk_usage(a.output.parent).free
    print(json.dumps(dict(input_GiB=size/2**30,free_GiB=free/2**30,reserve_GiB=estimate/2**30),ensure_ascii=False),flush=True)
    if free<estimate:p.error('空间不足；请先在 Spark 的独立大盘安排输出，不要删正式资料')
    if 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower() and not str(a.output).startswith('/mnt/e/'):
        p.error('WSL 的大文件必须位于 E 盘')
    a.output.mkdir();temporary=a.output/'tmp';temporary.mkdir()
    os.environ.update(TMPDIR=str(temporary),SQLITE_TMPDIR=str(temporary),PYTHONDONTWRITEBYTECODE='1')
    # The producer's highest-priority archive is ROOT/cache/native-backfill-south.
    # Isolate code too, then expose that input by a read-only usage symlink;
    # neither supplied checkout needs to be changed to reproduce precedence.
    for label in ('baseline','candidate'):
        supplied=getattr(a,label+'_code').resolve();code=a.output/(label+'-code');code.mkdir()
        for path in supplied.iterdir():
            if path.is_file() and path.suffix in ('.py','.json','.c','.js','.html','.css'):
                shutil.copy2(path,code/path.name)
        if (supplied/'tools').is_dir():shutil.copytree(supplied/'tools',code/'tools',ignore=shutil.ignore_patterns('__pycache__'))
        south=a.native_root/'cache/native-backfill-south'
        if south.is_dir():
            (code/'cache').mkdir();(code/'cache/native-backfill-south').symlink_to(south.resolve(),target_is_directory=True)
        setattr(a,label+'_code',code)
    original=measure_inputs(a.input_cache)
    before_stat=(database.stat().st_size,database.stat().st_mtime_ns)
    base_table=json.loads((a.input_cache/'stations.json').read_text())
    base_table['stations']=metadata['stations']
    for label in ('baseline','candidate'):
        root=a.output/label;root.mkdir()
        for folder in ('raw','forecast','obs','wn3-xj','wn3ml'):
            if (a.input_cache/folder).is_dir():shutil.copytree(a.input_cache/folder,root/folder)
        for name in ('discovery.json','ogimet-journal.json'):
            if (a.input_cache/name).is_file():shutil.copy2(a.input_cache/name,root/name)
        (root/'stations.json').write_text(json.dumps(base_table,ensure_ascii=False))
        # Forecast remapping/elevation reuse reads the frozen old roster.
        old=root/'snapshots'/a.snapshot.name;old.mkdir(parents=True)
        shutil.copy2(a.snapshot/'meta.json',old/'meta.json')
        (root/'current.json').write_text(json.dumps(dict(generation=a.snapshot.name)))
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as source, \
             closing(sqlite3.connect(root/'working.sqlite')) as target:
            source.backup(target)
        if label=='candidate':
            for path in a.nmic_dir.glob('*.txt'):
                dest=root/'raw/nmic'/path.name
                if dest.exists():raise ValueError('新资料不能覆盖原料文件：'+str(path))
                shutil.copy2(path,dest)
    if original!=measure_inputs(a.input_cache) or before_stat!=(database.stat().st_size,database.stat().st_mtime_ns):
        raise RuntimeError('输入在准备期间变化；请主管冻结后重跑')
    def run(label,phase,end):
        command=[sys.executable,str(Path(__file__).resolve()),
                 '--worker-cache',str(a.output/label),'--worker-code',str(getattr(a,label+'_code')),
                 '--phase',phase,'--end',end,'--snapshot',str(a.snapshot),
                 '--input-cache',str(a.input_cache),'--baseline-code',str(a.baseline_code),
                 '--nmic-dir',str(a.nmic_dir),'--weather-root',str(a.weather_root),
                 '--native-root',str(a.native_root),'--output',str(a.output)]
        with (a.output/f'{label}-{phase}.log').open('w') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    def check(phase):
        subprocess.run([sys.executable,str(a.candidate_code/'tools/check_cmagd_equivalence.py'),
                        str(a.output/'baseline'),str(a.output/'candidate'),'--output',str(a.output/f'{phase}-comparison.json')],check=True)
    for label in ('baseline','candidate'):run(label,'first',a.end)
    check('first')
    if a.warm_hours:
        from datetime import datetime,timezone
        end=datetime.fromisoformat(a.end.replace('Z','+00:00'))+timedelta(hours=a.warm_hours)
        advanced=end.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        for label in ('baseline','candidate'):run(label,'warm',advanced)
        check('warm')
    if original!=measure_inputs(a.input_cache) or before_stat!=(database.stat().st_size,database.stat().st_mtime_ns):
        raise RuntimeError('输入在运行期间变化；本轮结果不能作为冻结验收')
    print('对照完成：仅私有输出目录更新 current.json；正式站、正式快照与原料未写入。',flush=True)


if __name__=='__main__':main()
