import unittest,tempfile,json,time,datetime as dt
from pathlib import Path
from unittest.mock import patch
import numpy as np
from qc_monitor import Monitor,check_frame
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'data';self.root.mkdir();self.state=Path(self.tmp.name)/'state';self.m=Monitor(self.root,self.state,stable=0)
 def tearDown(self):self.m.db.close();self.tmp.cleanup()
 def frame(self,slug='aigfs',bad=False):
  run=dt.datetime.now(dt.timezone.utc).replace(minute=0,second=0,microsecond=0);p=self.root/slug/(slug+'_'+run.strftime('%Y%m%d%H')+'_f006_global1.json');p.parent.mkdir(exist_ok=True)
  d=dict(schema=2,model='NCEP-AIGFS',run=run.isoformat(),valid=(run+dt.timedelta(hours=6)).isoformat(),lead=6,lon=[100,100.25],lat=[20,20.25],levels=['surface'],capabilities={'surface_rh':False},rain_start=run.isoformat(),interval_hours=6)
  p.write_text(json.dumps(d));fd=p.with_suffix('.fields');fd.mkdir(exist_ok=True)
  for key,val in [('surface_t',20),('surface_u',2),('surface_v',3),('surface_msl',1000),('rain6h',1)]:
   a=np.asarray([[val,val+1],[val+2,val+3]],dtype=np.float32);np.save(fd/(key+'.npy'),a[:1] if bad and key=='surface_t' else a)
  np.save(fd/'surface_rh.npy',np.full((2,2),np.nan,dtype=np.float32));return p
 def test_baseline_does_not_read_history(self):
  d=self.root/'ecmwf';d.mkdir();(d/'ecmwf_bad.json').write_text('broken');self.m.init();self.m.scan();self.assertEqual(self.m.status()['events'],0)
 def test_new_and_dedup(self):
  self.m.init();self.frame();self.m.scan();self.m.scan();self.m.scan();self.assertEqual(self.m.status()['events'],1)
 def test_capability_and_6h(self):
  p=self.frame();r=check_frame(p,time.time());self.assertEqual(r['status'],'pass');self.assertEqual(r['arrays'],5)
 def test_bad_shape(self):
  p=self.frame(bad=True);self.assertEqual(check_frame(p,time.time())['status'],'error')
 def test_no_data_daily(self):
  self.m.init();self.m.scan();self.m.started-=86400;self.m.daily(dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(hour=20));s=self.m.db.execute('SELECT content FROM outbox').fetchone()[0];self.assertIn('不能判定无异常',s)
 def test_retry(self):
  self.m.init();self.m.enqueue('test','test')
  with patch.dict('os.environ',{'WECOM_QC_WEBHOOK':'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=TEST'}),patch('urllib.request.urlopen',side_effect=OSError('network')):self.m.flush()
  r=self.m.db.execute('SELECT tries,sent FROM outbox').fetchone();self.assertEqual(r,(1,None))
if __name__=='__main__':unittest.main()
