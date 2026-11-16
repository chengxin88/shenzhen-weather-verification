"""Bounded detail updates without maintaining a JSON expression index.

The public detail_stats columns and row order are unchanged. A private identity
table points to their rowids; both live in the same SQLite transaction.
"""
import json
import time


RESET_ROWS = 32768
COLUMNS = ('station','model','var','lead','days','threshold','grade','id')
MATCH = ('k.station=b.station AND k.model=b.model AND k.var=b.var AND '
         'k.lead=b.lead AND k.days=b.days AND k.threshold=COALESCE(b.threshold,-1) AND k.grade=b.grade')
GRADE = "COALESCE(CASE WHEN var='rain24' AND threshold IS NULL THEN json_extract(stats,'$.grade') ELSE 0 END,0)"


class Details:
    @staticmethod
    def valid(db):
        expected=[(name,i+1 if i<7 else 0) for i,name in enumerate(COLUMNS)]
        return [(r[1],r[5]) for r in db.execute('PRAGMA main.table_info(aggregate_detail_keys)')]==expected

    def __init__(self,db,preserve):
        self.db=db;self.preserve=preserve
        self.pending=0;self.seconds=0.;self.written=0
        # All aggregate TEMP tables are disposable; main still uses DELETE
        # journaling. This also avoids DrvFS unlink/truncate TEMP journal errors.
        db.execute('PRAGMA temp.journal_mode=OFF')
        reset_started=time.monotonic();cleared=0
        db.execute('DROP INDEX IF EXISTS main.detail_identity')
        if not preserve:
            # The caller's committed interrupted-build marker protects these
            # bounded resets. secure_delete=ON otherwise journals the entire
            # old public JSON table during one DELETE, even on a cold rebuild.
            db.commit()
            db.execute('DROP TABLE IF EXISTS main.aggregate_detail_keys');db.commit()
            db.execute('DROP INDEX IF EXISTS main.detail_lookup');db.commit()
            while True:
                count=db.execute('DELETE FROM detail_stats WHERE rowid IN '
                    '(SELECT rowid FROM detail_stats LIMIT ?)',(RESET_ROWS,)).rowcount
                cleared+=count;db.commit()
                if not count: break
            print('TIMING aggregates.detail_reset',round(time.monotonic()-reset_started,3),
                  'seconds',cleared,'rows',flush=True)
        db.execute('CREATE TABLE IF NOT EXISTS aggregate_detail_keys (station TEXT,model TEXT,var TEXT,'
                   'lead INTEGER,days INTEGER,threshold REAL,grade INTEGER,id INTEGER NOT NULL,'
                   'PRIMARY KEY(station,model,var,lead,days,threshold,grade)) WITHOUT ROWID')
        db.execute('DROP TABLE IF EXISTS temp.aggregate_detail_batch')
        if preserve:
            db.execute('CREATE TEMP TABLE aggregate_detail_batch (station TEXT,model TEXT,var TEXT,'
                       'lead INTEGER,days INTEGER,threshold REAL,stats TEXT,grade INTEGER)')

    def write(self,batch):
        if not batch: return
        started=time.monotonic();db=self.db
        if not self.preserve:
            db.executemany('INSERT INTO detail_stats VALUES (?,?,?,?,?,?,?)',batch)
            self.written+=len(batch);self.checkpoint(len(batch));self.seconds+=time.monotonic()-started
            return
        db.execute('DELETE FROM aggregate_detail_batch')
        # Only the six rain24 grade rows need JSON parsing; all ordinary rows
        # have grade=0. Existing public rows are never parsed for identity.
        db.executemany('INSERT INTO aggregate_detail_batch VALUES (?,?,?,?,?,?,?,?)',
            ((*r,json.loads(r[6]).get('grade',0) if r[2]=='rain24' and r[5] is None else 0) for r in batch))
        self.written+=db.execute('UPDATE detail_stats AS d SET stats=b.stats FROM aggregate_detail_batch b '
                   'JOIN aggregate_detail_keys k ON '+MATCH+' WHERE d.rowid=k.id AND d.stats IS NOT b.stats').rowcount
        created=list(db.execute('INSERT INTO detail_stats SELECT station,model,var,lead,days,threshold,stats '
            'FROM aggregate_detail_batch b WHERE NOT EXISTS (SELECT 1 FROM aggregate_detail_keys k WHERE '+MATCH+') '
            'RETURNING station,model,var,lead,days,COALESCE(threshold,-1),'+GRADE+',rowid'))
        db.executemany('INSERT INTO aggregate_detail_keys VALUES (?,?,?,?,?,?,?,?)',created)
        self.written+=len(created);self.checkpoint(len(batch));self.seconds+=time.monotonic()-started

    def checkpoint(self,count):
        self.pending+=count
        if self.pending>=32768:
            # The caller persisted an interrupted-build marker before starting
            # aggregation. Rowids and their identities commit together; a killed
            # build cannot pass the product cache check on its next invocation.
            self.db.commit();self.pending=0

    def report(self,days,before,dirty_days):
        print('TIMING',f'aggregates.detail_write.{days}',round(self.seconds-before[0],3),
              'seconds',self.written-before[1],'rows',dirty_days,'dirty_days',flush=True)

    def cleanup(self):
        if self.preserve:
            db=self.db
            db.execute('DROP TABLE IF EXISTS temp.aggregate_detail_stale')
            db.execute('CREATE TEMP TABLE aggregate_detail_stale (station TEXT,model TEXT,var TEXT,'
                       'lead INTEGER,days INTEGER,PRIMARY KEY(station,model,var,lead,days)) WITHOUT ROWID')
            # The small identity table is ordered by series. Check membership
            # once per series, rather than scanning/parsing every public JSON
            # row and repeating the same lookup for each threshold and grade.
            db.execute('INSERT INTO aggregate_detail_stale SELECT k.station,k.model,k.var,k.lead,k.days '
                'FROM aggregate_detail_keys k GROUP BY k.station,k.model,k.var,k.lead,k.days '
                'HAVING NOT EXISTS (SELECT 1 FROM aggregate_series s WHERE s.station=k.station '
                'AND s.model=k.model AND s.var=k.var AND s.lead=k.lead AND s.days=k.days)')
            match='k.station=s.station AND k.model=s.model AND k.var=s.var AND k.lead=s.lead AND k.days=s.days'
            # CROSS JOIN pins the empty/small vanished set outside indexed key
            # lookups. Public rows are visited only when a series disappeared.
            db.execute('DELETE FROM detail_stats WHERE rowid IN (SELECT k.id FROM '
                       'aggregate_detail_stale s CROSS JOIN aggregate_detail_keys k WHERE '+match+')')
            db.execute('DELETE FROM aggregate_detail_keys WHERE (station,model,var,lead,days) IN '
                       '(SELECT station,model,var,lead,days FROM aggregate_detail_stale)')
            db.execute('DROP TABLE aggregate_detail_stale')
            db.execute('DROP TABLE aggregate_detail_batch')

    def finish(self):
        if not self.preserve:
            self.db.execute('INSERT INTO aggregate_detail_keys SELECT station,model,var,lead,days,'
                'COALESCE(threshold,-1),'+GRADE+',rowid FROM detail_stats '
                'ORDER BY station,model,var,lead,days,COALESCE(threshold,-1),'+GRADE)
