"""Offline full-scorer replay: real NMIC files, synthetic forecasts/legacy coverage.

All caches are temporary directories, deleted on exit. No network.
This proves isolation on controlled inputs, not production cache equivalence.
"""
from pathlib import Path
import argparse
from datetime import timedelta
from unittest.mock import patch
import copy, json, os, sys, tempfile
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from station_common import ROOT, MODELS, atomic_json, iso, stamp
from station_obs import select_observations, qc_rows
import nmic_obs
import station_score
from tools.check_nmic_equivalence import compare

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data-dir',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--scratch',type=Path,help='Existing scratch directory (WSL: use the E: drive)')
args=parser.parse_args()
os.environ['STATIONS_NMIC_DIR']=str(args.data_dir.resolve())
records,_=nmic_obs.read_records(args.data_dir)
if not records:parser.error('No NMIC reports')
end=max(at for sid,at in records)
run=(end-timedelta(days=6)).replace(hour=0,minute=0,second=0,microsecond=0)
table=json.loads((ROOT/'stations.json').read_text());old=copy.deepcopy(table['stations']);expanded=copy.deepcopy(table)
nmic_obs.add_stations(expanded,end=end)
all_rows=qc_rows(nmic_obs.observation_rows(expanded['stations'],end,run))
oldids={s['id'] for s in old}
# Simulate full legacy coverage at the actual old-site observation keys. These
# are controlled inputs, not production SYNOP evidence. Source/value priority
# is tested with a different wind at every pre-existing key.
legacy=[{**r,'source':'ogimet','value':r['value']+(.2 if r['var']=='wind' else 0.)} for r in all_rows if r['station'] in oldids]
before=select_observations(copy.deepcopy(legacy),old)
if not before:parser.error('No overlap with the baseline station table')
after=select_observations(copy.deepcopy(legacy+all_rows),expanded['stations'])
assert all(after[k]==v for k,v in before.items())
with tempfile.TemporaryDirectory(prefix='nmic-replay-',dir=args.scratch) as temporary:
 roots=[Path(temporary)/n for n in ('baseline','candidate')]
 for idx,root in enumerate(roots):
  root.mkdir();atomic_json(root/'stations.json',dict(stations=old if idx==0 else expanded['stations']))
  chosen=before if idx==0 else after
  def forecasts(root,stations,*a,**kw):
   # Deterministic synthetic gridded forecasts at the real station coordinates.
   for model in MODELS:
    samples={}
    for h in range(0,min(240,int((end-run).total_seconds()/3600))+1,3):
     point=np.array([s['lon']*.01+s['lat']*.01 for s in stations])
     samples[h]=dict(t=20+point,td=10+point,msl=1010+point,wind=2+point)
     if h:samples[h].update(rain=np.full(len(stations),.2),rain_start=h-3)
    yield model,run,samples,'frozen-synthetic-'+model,[]
  with patch('nmic_obs.add_stations',side_effect=lambda table,*a:table),patch('sz_district_obs.add_stations',side_effect=lambda table:table),patch.object(station_score,'load_observations',return_value=(chosen,{},{})),patch.object(station_score,'forecast_runs',side_effect=forecasts),patch.object(station_score,'shenzhen_sanity',return_value=dict(n=0)):
   station_score.refresh(root,end)
 result=compare(*roots)
 result.update(evidence='actual NMIC observations/metadata; synthetic forecasts for all 9 configured models; simulated complete legacy coverage at old-site keys, not production-cache validation',
               observation_keys=dict(baseline=len(before),candidate=len(after)),new_stations=len(expanded['stations'])-len(old))
 args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(result,ensure_ascii=False,indent=2))
 assert result['equal']
