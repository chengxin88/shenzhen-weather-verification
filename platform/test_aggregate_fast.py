"""Raw-order equivalence, cache invalidation and killed-writer recovery."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import station_aggregate as fast
import station_aggregate_daily as daily
import station_score as score
from station_common import UTC, iso, atomic_json

sys.path.insert(0,str(Path(__file__).parent/'tools'))
from compare_aggregates import compare, AggregateComparison


class FastAggregateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'snapshots').mkdir()
        self.db=score.database(self.root/'working.sqlite');self.addCleanup(self.db.close)
        self.end=datetime(2026,11,12,3,17,tzinfo=UTC)
        rows=[]
        for age in (0,1,3,7,30,31):
            for offset in (-1,0,1):
                at=self.end-timedelta(days=age)+timedelta(seconds=offset)
                for i,var in enumerate(('t','msl','rain6','rain12','rain24')):
                    lead=(3,27,81,180)[(age+offset)%4]
                    fc,ob=(1000.+offset*1e-5,1000.+offset*1.1e-5) if var=='msl' else (float(i+offset),float(i))
                    rows.append(('ec01',iso(at-timedelta(hours=lead)),lead,iso(at),
                                 '52112' if age==7 else 'a',var,fc,ob,'test',int(offset==0),iso(at)))
        # A two-point continuous group must keep CC for stations, but not grids.
        for station,fc,ob in (('b',1.,2.),('c',3.,6.)):
            rows.append(('gfs',iso(self.end-timedelta(hours=3)),3,iso(self.end),station,'t',fc,ob,'test',0,iso(self.end)))
        self.db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',rows);self.db.commit()
        self.serial=0

    def generate(self,legacy=False,end=None,ends=None):
        self.serial+=1;directory=self.root/'snapshots'/str(self.serial);directory.mkdir()
        db=self.db
        if legacy:
            db=sqlite3.connect(directory/'legacy-working.sqlite');self.db.backup(db)
        with patch.dict(os.environ,STATIONS_AGGFAST='0' if legacy else '1'):
            score.aggregates(db,directory,end or self.end,ends=ends)
        # Preserve detail stats independently, since subsequent runs update them.
        with sqlite3.connect(directory/'detail.sqlite') as target:
            target.execute('CREATE TABLE detail_stats AS SELECT 1 AS unused')
            target.execute('DROP TABLE detail_stats')
            target.execute('CREATE TABLE detail_stats (station TEXT,model TEXT,var TEXT,lead INTEGER,days INTEGER,threshold REAL,stats TEXT)')
            target.executemany('INSERT INTO detail_stats VALUES (?,?,?,?,?,?,?)',db.execute('SELECT * FROM detail_stats'))
        atomic_json(directory/'common-report.json',score.common_report(db,end or self.end))
        if legacy: db.close()
        return directory

    def equivalent(self):
        actual=self.generate();expected=self.generate(legacy=True)
        result=compare(expected,actual)
        self.assertTrue(result['equal'],result)
        self.assertEqual(result['float_differences'],{})
        return actual

    def composite(self,ends):
        """各窗口截止时刻不同：逐窗口取旧算法在各自截止时刻的结果，拼成参照（2026-11-15 近24小时按国家站资料截止）。"""
        base=self.generate(legacy=True,end=ends[15])
        for days,end in ends.items():
            if end==ends[15]: continue
            other=self.generate(legacy=True,end=end)
            for prefix in ('scores','station-scores'):
                shutil.copyfile(other/f'{prefix}-{days}.json',base/f'{prefix}-{days}.json')
            target=sqlite3.connect(base/'detail.sqlite')
            try:
                target.execute('ATTACH DATABASE ? AS other',(str(other/'detail.sqlite'),))
                target.execute('DELETE FROM detail_stats WHERE days=?',(days,))
                target.execute('INSERT INTO detail_stats SELECT * FROM other.detail_stats WHERE days=?',(days,))
                target.commit()
            finally: target.close()
        return base

    def test_one_day_window_can_end_at_national_data_time(self):
        # 用户 2026-11-15「近1天改成有资料的时间」：近24小时单独截止，近3/7/15天仍按本轮评分时刻。
        cases=[{1:self.end-timedelta(hours=13,minutes=5)},           # 一般情况：两端都落在日中
               {1:self.end-timedelta(days=1)},                       # 右端正好落在 1 天前那批配对上（闭区间要含）
               {1:self.end-timedelta(hours=1)},                      # 国家站资料又更新
               {}]                                                   # 国家站追上来：四个窗口同一截止
        for case in cases:
            ends={1:self.end,3:self.end,7:self.end,15:self.end};ends.update(case)
            actual=self.generate(ends=ends)
            result=compare(self.composite(ends),actual)
            self.assertTrue(result['equal'],(case,result))
            self.assertEqual(result['float_differences'],{},case)
        # 同样的截止时刻再跑一轮：整块沿用，不再扫配对
        ends={1:self.end-timedelta(hours=13,minutes=5),3:self.end,7:self.end,15:self.end}
        self.generate(ends=ends)
        with patch.object(fast,'window_rows',side_effect=AssertionError('unchanged cache rescanned')):
            self.generate(ends=ends)
        # 评分时刻前进 3 小时（右侧没有新配对）也要和逐窗口旧算法一致
        self.end+=timedelta(hours=3)
        ends={1:self.end-timedelta(hours=16,minutes=5),3:self.end,7:self.end,15:self.end}
        result=compare(self.composite(ends),self.generate(ends=ends))
        self.assertTrue(result['equal'],result)

    def test_score_window_ends_rule(self):
        end=datetime(2026,11,15,12,55,tzinfo=UTC)
        ends=score.score_window_ends(end,'2026-11-14T12:00:00Z')
        self.assertEqual(ends[1],datetime(2026,11,14,12,55,tzinfo=UTC))
        self.assertEqual([ends[d] for d in (3,7,15)],[end]*3)
        self.assertEqual(score.score_window_ends(end,'2026-11-15T12:00:00Z')[1],end)   # 国家站已追上
        self.assertEqual(score.score_window_ends(end,'2026-11-12T12:00:00Z')[1],end)   # 超过 3 天没更新：退回
        self.assertEqual(score.score_window_ends(end,'2026-11-12T12:55:00Z')[1],datetime(2026,11,12,13,50,tzinfo=UTC))
        self.assertEqual(score.score_window_ends(end,None)[1],end)

    def test_cold_detail_reset_bounds_rollback_journal(self):
        import station_detail_cache as detail
        self.generate()
        self.db.executemany('INSERT INTO detail_stats VALUES (?,?,?,?,?,?,?)',
                            [('old','ec01','t',3,1,None,'x'*4096)]*5000)
        self.db.commit();db=self.db;journal=Path(str(self.root/'working.sqlite')+'-journal');sizes=[]
        class BoundedDB:
            def execute(self,*args): return db.execute(*args)
            def commit(self):
                sizes.append(journal.stat().st_size if journal.exists() else 0)
                db.commit()
        with patch.object(detail,'RESET_ROWS',128): detail.Details(BoundedDB(),False)
        self.assertGreater(len(sizes),30)
        self.assertLess(max(sizes),4*1024**2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM detail_stats').fetchone()[0],0)
        self.assertTrue(detail.Details.valid(db))

    def test_cleanup_uses_rowid_lookups_instead_of_scanning_public_details(self):
        from station_detail_cache import Details
        self.generate()
        before=self.db.execute('SELECT COUNT(*) FROM detail_stats').fetchone()[0]
        self.db.execute('CREATE TEMP TABLE aggregate_series AS '
                        'SELECT DISTINCT station,model,var,lead,days FROM detail_stats')
        self.db.execute('CREATE UNIQUE INDEX aggregate_series_key '
                        'ON aggregate_series(station,model,var,lead,days)')
        details=Details(self.db,True);plans=[];db=self.db
        class PlannedDB:
            def execute(self,query,*args):
                if query.startswith('DELETE FROM detail_stats'):
                    plans.extend(row[3] for row in db.execute('EXPLAIN QUERY PLAN '+query,*args))
                return db.execute(query,*args)
        details.db=PlannedDB();details.cleanup()
        self.assertTrue(any('SEARCH detail_stats USING INTEGER PRIMARY KEY' in row for row in plans),plans)
        self.assertFalse(any('SCAN detail_stats' in row for row in plans),plans)
        self.assertEqual(before,self.db.execute('SELECT COUNT(*) FROM detail_stats').fetchone()[0])
        self.assertEqual(before,self.db.execute('SELECT COUNT(*) FROM aggregate_detail_keys').fetchone()[0])

    def test_readonly_attached_input_keeps_existing_source_indices(self):
        self.generate()
        self.db.execute('CREATE INDEX detail_identity ON detail_stats(days)')
        self.db.commit()
        expected=self.generate(legacy=True)
        directory=self.root/'snapshots'/'readonly';directory.mkdir()
        source=self.root/'working.sqlite'
        identity=(source.stat().st_size,source.stat().st_mtime_ns)
        db=sqlite3.connect(directory/'detail.sqlite')
        try:
            db.execute('ATTACH DATABASE ? AS original',(source.resolve().as_uri()+'?mode=ro',))
            db.execute('CREATE TEMP VIEW pairs AS SELECT * FROM original.pairs')
            score.aggregates(db,directory,self.end)
            atomic_json(directory/'common-report.json',score.common_report(db,self.end))
            db.commit()
        finally:
            db.close()
        self.assertEqual(identity,(source.stat().st_size,source.stat().st_mtime_ns))
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='detail_identity'").fetchone())
        self.assertTrue(compare(expected,directory)['equal'])

    def test_all_windows_edges_counts_low_variance_and_distinct_cc_rules(self):
        directory=self.equivalent()
        rows=json.loads((directory/'scores-1.json').read_text())
        row=next(r for r in rows if r['model']=='gfs')
        self.assertEqual(row['n'],2);self.assertEqual(row['CC'],1.)
        import gzip
        with gzip.open(directory/'grid-rows-t.json.gz','rt') as handle:
            row=next(r for r in json.load(handle) if r['model']==score.GRID_MODELS['gfs'])
        self.assertIsNone(row['CC']);self.assertIsInstance(row['n'],int)

    def test_reuse_then_advance_across_exact_inclusive_hour_boundary(self):
        self.generate()
        with patch.object(fast,'window_rows',side_effect=AssertionError('unchanged cache rescanned')):
            self.generate()
        self.equivalent()
        self.end+=timedelta(seconds=1)
        self.equivalent()

    def test_changed_day_rewrite_moved_valid_replace_and_delete(self):
        self.generate()
        with self.db:
            self.db.execute("UPDATE pairs SET ob=ob+0.25 WHERE valid=?",(iso(self.end-timedelta(days=1)),))
        self.assertEqual(self.db.execute('SELECT day FROM aggregate_dirty').fetchall(),[(iso(self.end-timedelta(days=1))[:10],)])
        self.equivalent()
        with self.db: self.db.execute('UPDATE pairs SET valid=? WHERE model=\'gfs\'',(iso(self.end-timedelta(days=3)),))
        self.assertEqual(len(self.db.execute('SELECT day FROM aggregate_dirty').fetchall()),2)
        self.equivalent()
        with self.db: self.db.execute("DELETE FROM pairs WHERE station='a'")
        self.equivalent()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM detail_stats WHERE station='a'").fetchone()[0],0)

    def test_versions_rules_station_and_region_metadata_invalidate(self):
        self.generate()
        for attribute,value in (('VERSION','test-v2'),('GRID_THRESHOLDS',[.1,1,5])):
            # Signature checks are independent of a particular native layout.
            before=score.aggregate_signature(self.root/'snapshots'/'x')
            with patch.object(score,attribute,value):
                self.assertNotEqual(before,score.aggregate_signature(self.root/'snapshots'/'x'))
        atomic_json(self.root/'stations.json',dict(stations=[dict(id='new')]))
        with patch.object(daily.Daily,'scan',autospec=True,side_effect=daily.Daily.scan) as scan:
            self.generate();self.assertEqual(scan.call_count,1)
        with patch.object(score,'VERSION','test-v3'),patch.object(daily.Daily,'scan',autospec=True,side_effect=daily.Daily.scan) as scan:
            self.generate();self.assertEqual(scan.call_count,1)
        signature=score.aggregate_signature(self.root/'snapshots'/'x')
        with patch.dict(score.XINJIANG,{'another':{}}):
            self.assertNotEqual(signature,score.aggregate_signature(self.root/'snapshots'/'x'))
        # Runtime NMIC/district additions need not be written to stations.json.
        self.assertNotEqual(score.aggregate_signature(self.root/'snapshots'/'x',[dict(id='a')]),
                            score.aggregate_signature(self.root/'snapshots'/'x',[dict(id='b')]))

    def test_rollback_and_repaired_mutation_triggers_are_conservative(self):
        self.generate()
        self.db.execute("DELETE FROM pairs WHERE station='a'");self.db.rollback()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM aggregate_dirty').fetchone()[0],0)
        self.db.execute('DROP TRIGGER aggregate_pairs_update');self.db.commit()
        score.aggregate_tracking(self.db)
        self.assertEqual(self.db.execute('SELECT day FROM aggregate_dirty').fetchall(),[('*',)])
        self.equivalent()

    def test_killed_after_detail_commit_rebuilds_before_reuse(self):
        self.generate()
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+1 WHERE var='t'")
        directory=self.root/'snapshots'/'killed';directory.mkdir()
        script='''import os,signal,sys
from pathlib import Path
import station_score as s
from station_common import stamp
db=s.database(Path(sys.argv[1]))
s.grid_aggregates=lambda *a,**kw:os.kill(os.getpid(),signal.SIGKILL)
s.aggregates(db,Path(sys.argv[2]),stamp(sys.argv[3]))
'''
        child=subprocess.run([sys.executable,'-c',script,str(self.root/'working.sqlite'),str(directory),iso(self.end)],stdout=subprocess.DEVNULL)
        self.assertEqual(child.returncode,-signal.SIGKILL)
        self.assertTrue(self.db.execute('SELECT 1 FROM aggregate_dirty').fetchone())
        self.equivalent()

    def test_chunks_do_not_revert_to_variable_or_sql_scans(self):
        with patch.object(daily,'CHUNK',4),patch.object(fast,'window_rows',side_effect=AssertionError('old path')):
            self.equivalent()
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+.1 WHERE model='gfs'")
        with patch.object(fast,'native',side_effect=fast.Unavailable('no compiler')):
            with self.assertRaises(fast.Unavailable): self.generate()

    def test_compensated_pressure_over_many_days_and_rolling_subtraction(self):
        import random
        rng=random.Random(42)
        rows=[]
        for day in range(32):
            for hour in range(24):
                at=self.end-timedelta(days=day,hours=hour)
                rows.append(('ec01',iso(at-timedelta(hours=6)),6,iso(at),'pressure','msl',
                             1000+rng.random()*.001,1000+rng.random()*.001,'test',0,iso(at)))
        with self.db: self.db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',rows)
        self.equivalent()
        self.db.execute('ANALYZE');self.db.commit()
        self.equivalent()  # Legacy may choose a different SUM scan order.
        self.end+=timedelta(hours=3)
        self.equivalent()

    def test_reverse_and_midnight_roll_and_seven_dirty_days(self):
        self.generate()
        for shift in (21,-3,240,-168):
            self.end+=timedelta(hours=shift)
            self.equivalent()
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+.01 WHERE valid>=?",(iso(self.end-timedelta(days=7)),))
        self.equivalent()

    def test_corrupt_persisted_window_blob_rebuilds_during_output(self):
        self.generate()
        with sqlite3.connect(self.root/'aggregate-days.sqlite') as cache:
            cache.execute("UPDATE states SET value=x'0102' WHERE kind='window' AND part='3'")
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+.1 WHERE model='gfs'")
        self.equivalent()

    def test_killed_checkpoint_is_never_a_complete_generation(self):
        self.generate()
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+.01 WHERE model='gfs'")
        directory=self.root/'snapshots'/'checkpoint-killed';directory.mkdir()
        script="""import os,signal,sys
from pathlib import Path
import station_score as s
import station_aggregate_daily as d
from station_common import stamp
db=s.database(Path(sys.argv[1]))
original=d.Daily.checkpoint
calls=0
def kill(self):
    global calls
    original(self);calls+=1
    if calls==2: os.kill(os.getpid(),signal.SIGKILL)
d.Daily.checkpoint=kill
s.aggregates(db,Path(sys.argv[2]),stamp(sys.argv[3]))
"""
        child=subprocess.run([sys.executable,'-c',script,str(self.root/'working.sqlite'),str(directory),iso(self.end)],stdout=subprocess.DEVNULL)
        self.assertEqual(child.returncode,-signal.SIGKILL)
        with sqlite3.connect(self.root/'aggregate-days.sqlite') as cache:
            self.assertEqual(cache.execute("SELECT value FROM metadata WHERE key='schema'").fetchone(),('building',))
        self.equivalent()

    def test_killed_detail_checkpoint_cannot_reuse_partly_committed_rows(self):
        self.generate()
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+.01 WHERE var='t'")
        directory=self.root/'snapshots'/'detail-checkpoint-killed';directory.mkdir()
        script="""import os,signal,sys
from pathlib import Path
import station_score as s
from station_common import stamp
from station_detail_cache import Details
db=s.database(Path(sys.argv[1]))
def kill(self,count):
    self.db.commit()
    os.kill(os.getpid(),signal.SIGKILL)
Details.checkpoint=kill
s.aggregates(db,Path(sys.argv[2]),stamp(sys.argv[3]))
"""
        child=subprocess.run([sys.executable,'-c',script,str(self.root/'working.sqlite'),str(directory),iso(self.end)],stdout=subprocess.DEVNULL)
        self.assertEqual(child.returncode,-signal.SIGKILL)
        self.assertIn(('*',),self.db.execute('SELECT day FROM aggregate_dirty').fetchall())
        self.equivalent()

    def test_legacy_rollback_invalidates_fast_generation_before_reenable(self):
        self.generate()
        directory=self.root/'snapshots'/'legacy-switch';directory.mkdir()
        with patch.dict(os.environ,STATIONS_AGGFAST='0'):
            score.aggregates(self.db,directory,self.end+timedelta(hours=1))
        self.assertEqual(self.db.execute('SELECT * FROM aggregate_state').fetchall(),[])
        self.assertIn(('*',),self.db.execute('SELECT day FROM aggregate_dirty').fetchall())
        self.equivalent()

    def test_exactly_seven_dirty_utc_days_from_delayed_observations(self):
        rows=[]
        for age in range(7):
            at=self.end-timedelta(days=age)
            rows.append(('gfs',iso(at-timedelta(hours=3)),3,iso(at),'late','t',20+age*.1,20.2,'test',0,iso(at)))
        with self.db: self.db.executemany('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',rows)
        self.generate()
        with self.db: self.db.execute("UPDATE pairs SET ob=ob+.1 WHERE station='late'")
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM aggregate_dirty').fetchone()[0],7)
        self.equivalent()

    def test_shared_right_edge_and_malformed_metadata(self):
        self.generate()
        path=self.root/'aggregate-days.sqlite'
        with sqlite3.connect(path) as cache:
            parts=cache.execute("SELECT DISTINCT part FROM states WHERE kind='edge' AND part LIKE ?",('%'+iso(self.end)[:10],)).fetchall()
            self.assertEqual(parts,[('0:'+iso(self.end)[:10],)])
            cache.execute("UPDATE metadata SET value='[]' WHERE key='edges'")
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+.1 WHERE model='gfs'")
        self.equivalent()

    def test_native_decoder_rejects_incompatible_output_width(self):
        import ctypes
        import numpy as np
        library=fast.native();out=np.empty(fast.COLUMNS,np.float64)
        value=bytearray(512);value[0]=1;value[1]=1;value[2]=100;value[3]=2
        self.assertEqual(library.agg_decode_checked(bytes(value),len(value),fast.COLUMNS,out.ctypes.data),-1)

    def test_warm_scan_only_reads_dirty_day_and_moving_edges(self):
        self.generate()
        statements=[];self.db.set_trace_callback(statements.append)
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+.5 WHERE model='gfs'")
        self.generate(end=self.end+timedelta(hours=3))
        self.db.set_trace_callback(None)
        scans=[q for q in statements if q.startswith(daily.RAW)]
        self.assertTrue(scans)
        self.assertTrue(all('INDEXED BY pair_valid WHERE valid>=' in q for q in scans),scans)
        self.assertFalse(any('var=' in q or 'GROUP BY' in q for q in scans))

    def test_unchanged_detail_series_avoid_json_upserts_after_dirty_day_rebuild(self):
        self.generate()
        total=self.db.execute('SELECT COUNT(*) FROM detail_stats').fetchone()[0]
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+.5 WHERE model='gfs'")
        self.db.execute('CREATE TEMP TABLE detail_audit (id INTEGER)')
        for action in ('UPDATE','INSERT'):
            self.db.execute('CREATE TEMP TRIGGER audit_detail_'+action+' AFTER '+action+
                            ' ON detail_stats BEGIN INSERT INTO detail_audit VALUES (new.rowid); END')
        actual=self.generate()
        writes=self.db.execute('SELECT COUNT(*) FROM detail_audit').fetchone()[0]
        self.assertGreater(writes,0)
        self.assertLess(writes,total//4)
        expected=self.generate(legacy=True)
        self.assertTrue(compare(expected,actual)['equal'])

    def test_missing_detail_table_restores_from_trusted_days_despite_fingerprints(self):
        self.generate()
        for table in ('detail_stats','aggregate_detail_keys'):
            with self.db: self.db.execute('DROP TABLE '+table)
            with patch.object(daily.Daily,'scan',side_effect=AssertionError('trusted day cache should suffice')):
                self.equivalent()
        with self.db:
            self.db.execute('DROP TABLE aggregate_detail_keys')
            self.db.execute('CREATE TABLE aggregate_detail_keys (invalid TEXT)')
        with patch.object(daily.Daily,'scan',side_effect=AssertionError('trusted day cache should suffice')):
            self.equivalent()

    def test_counters_never_use_relative_tolerance(self):
        result=AggregateComparison()
        result.value({'n':1e12,'g1h':1e12,'h0':1e12},{'n':1e12+1,'g1h':1e12+1,'h0':1e12+1},'test')
        self.assertEqual(result.mismatches,3)

    def test_replace_tracks_old_day_without_recursive_delete_triggers(self):
        self.generate()
        row=list(self.db.execute("SELECT * FROM pairs WHERE model='gfs' LIMIT 1").fetchone())
        old=row[3][:10];row[3]=iso(self.end-timedelta(days=5));new=row[3][:10]
        self.db.execute('PRAGMA recursive_triggers=OFF')
        with self.db: self.db.execute('INSERT OR REPLACE INTO pairs VALUES (?,?,?,?,?,?,?,?,?,?,?)',row)
        self.assertEqual({r[0] for r in self.db.execute('SELECT day FROM aggregate_dirty')},{old,new})
        self.equivalent()

    def test_threshold_change_and_corrupt_or_missing_grid_cache_rebuild(self):
        self.generate()
        with patch.dict(score.THRESHOLDS,{'rain6':[.1,10]}): self.equivalent()
        self.generate()
        cache=self.root/'aggregate-days.sqlite'
        cache.write_bytes(b'invalid sqlite cache')
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+1 WHERE model='gfs'")
        self.equivalent()
        cache.unlink()
        with self.db: self.db.execute("DELETE FROM pairs WHERE model='gfs'")
        self.equivalent()

    def test_changed_primitive_rule_source_requires_sql_review(self):
        import guifan_scores
        original=guifan_scores.sql_expressions
        def revised(): return original()
        with patch.object(guifan_scores,'sql_expressions',revised):
            with self.assertRaises(fast.Unavailable): fast.Groups(4)

    def test_streamed_scores_keep_exact_json_bytes(self):
        rows=[dict(station='湖南',n=2,ME=-0.,CC=None),dict(n=3,MAE=1./3,grade=6)]
        old,new=self.root/'old.json',self.root/'new.json'
        atomic_json(old,rows);score.atomic_score_rows(new,rows)
        self.assertEqual(old.read_bytes(),new.read_bytes())

    def test_incremental_identity_index_keeps_existing_api_row_order(self):
        self.generate()
        key=self.db.execute("SELECT station,model,var,lead,days FROM detail_stats WHERE var='rain24' LIMIT 1").fetchone()
        query='SELECT stats FROM detail_stats WHERE station=? AND model=? AND var=? AND lead=? AND days=?'
        def identity():
            rows=[json.loads(r[0]) for r in self.db.execute(query,key)]
            return [(r['threshold'],r.get('grade')) for r in rows]
        before=identity()
        with self.db: self.db.execute("UPDATE pairs SET fc=fc+0.25 WHERE var='rain24'")
        self.generate()
        self.assertEqual(identity(),before)
        self.db.execute('ANALYZE')
        self.assertEqual(identity(),before)
        self.assertTrue(any('detail_lookup' in r[3] for r in self.db.execute('EXPLAIN QUERY PLAN '+query,key)))


if __name__=='__main__': unittest.main()
