"""Live case-page regression: use the replay service, never fabricated fields.

Start server.py on loopback port 8543, then run:
  CASE_TEST_URL=http://127.0.0.1:8543/Verification/test/api/case/ python -m unittest -v test_case_page
Artifacts are saved under evidence/case-page/.
"""
import hashlib
import json
import os
from pathlib import Path
import time
import unittest
import urllib.error
import urllib.request

BASE=os.getenv('CASE_TEST_URL')
EVIDENCE=Path(__file__).parent/'evidence'/'case-page'


def request(path,p=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(p).encode() if p is not None else None,
                               headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=240) as response:return json.load(response)


def score(p):
    job=request('score',p)
    deadline=time.monotonic()+900
    while job['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(3)
        job=request('jobs/'+job['id'])
    if job['status']!='done':raise AssertionError(job.get('message'))
    return job['result']


@unittest.skipUnless(BASE,'Set CASE_TEST_URL to enable real replay integration tests.')
class CasePageLiveTests(unittest.TestCase):
    def test_01_scores_unchanged(self):
        before=json.loads((EVIDENCE/'score-before.json').read_text())['result']
        after=score({})
        report={'status':'pass','request':{},'comparisons':{}}
        for key in ('summary','scores'):
            self.assertEqual(before[key],after[key])
            digest=hashlib.sha256(json.dumps(after[key],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            report['comparisons'][key]={'rows':len(after[key]),'equal':True,'sha256':digest}
        (EVIDENCE/'score-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))

    def test_02_fields_match_contingency_counts(self):
        contexts=[('默认6小时',{}),('12小时累计',{'hours':12}),('24小时累计',{'hours':24}),
                  ('独立样本细网格',{'common':False,'spacing':.01,'models':['cma1','pangu']})]
        checks=[]
        for label,p in contexts:
            result=score(p)
            first_run=result['run_coverage'][0]
            times=first_run['valid_times']
            self.assertTrue(times,label)
            for valid in sorted({r['有效UTC'] for r in result['scores'] if r['有效时间'] in times}):
                d=request('fields',{**p,'valid':valid})
                self.assertEqual(d['run'],result['selection']['runs'][0])
                self.assertEqual(len(d['points']),result['points'])
                self.assertFalse(d['issues'],label)
                for m in d['models']:
                    self.assertEqual(len(m['values']),len(d['points']))
                    for t in result['selection']['thresholds']:
                        counts=dict.fromkeys(['N','H','M','F','C'],0)
                        for f,o in zip(m['values'],d['observations']):
                            if f is None or o is None:continue
                            counts['N']+=1
                            counts['H' if f>=t and o>=t else 'M' if o>=t else 'F' if f>=t else 'C']+=1
                        row=next((r for r in result['scores'] if r['模式']==m['label'] and r['有效UTC']==valid and r['阈值(mm)']==t),None)
                        self.assertIsNotNone(row,(label,m['id'],valid,t))
                        expected={k:row[k] for k in counts}
                        self.assertEqual(counts,expected,(label,m['id'],valid,t))
                        self.assertEqual(counts['N'],m['N'])
                        checks.append(dict(context=label,run=d['run'],valid=valid,model=m['id'],threshold=t,actual=counts,expected=expected,equal=True))
        (EVIDENCE/'fields-score-check.json').write_text(json.dumps({'status':'pass','checks':len(checks),'rows':checks},ensure_ascii=False,indent=2))
        print(f'落区与评分核对：{len(checks)} 组 N/H/M/F/C 完全相同',flush=True)

    def test_03_validation_and_first_run(self):
        meta=request('catalog')
        self.assertEqual(meta['case'],meta['cases'][0]['id'])
        for p in [{'case':'unknown'}, {'case':[]}, {'runs':['bad']}]:
            with self.assertRaises(urllib.error.HTTPError) as caught:request('score',p)
            self.assertEqual(caught.exception.code,400)
        for p in [{'valid':'2026-07-25T06:00:00Z','kind':'2米气温'}, {'valid':'bad'},
                  {'valid':'2026-07-28T00:00:00Z'}, {'valid':'2026-07-25T06:00:00Z','runs':['2026072512']}]:
            with self.assertRaises(urllib.error.HTTPError) as caught:request('fields',p)
            self.assertEqual(caught.exception.code,400)
        d=request('fields',{'valid':'2026-07-25T06:00:00Z','runs':[r['id'] for r in meta['runs']]})
        self.assertEqual(d['run'],meta['runs'][0]['id'])
        self.assertEqual([m['id'] for m in d['models']],[m['id'] for m in meta['models']])


if __name__=='__main__':unittest.main()
