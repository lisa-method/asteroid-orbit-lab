from pathlib import Path
import json
from unittest.mock import patch
import run_selector_holdout24 as r
from run_development_benchmark import load_development_context
from prepare_fresh_holdout import immutable_json,sha
root=Path('.').resolve();read=lambda p:json.loads((root/p).read_text())
cfg=read('configs/force_models_v2.json');fcfg=read('configs/development30.json');obj=read('data/processed/development30/sample.json')['objects'][0]
ctx=load_development_context(root,cfg);model=cfg['models'][-1]
with patch.object(r,'RAW_DIRECTORY','data/raw/development30'):
 cases,prod,fine=r._run_pair(root,obj,model,ctx,cfg,fcfg)
old={(x['object_id'],x['model_id'],x['horizon_days']):x for x in read('outputs/force_models_v2/results.json')['records']}
for case in cases:
 record=case['record'];expected=old[(obj['id'],model['model_id'],case['horizon_days'])]
 for key in ('max_position_error_km','max_velocity_error_m_s','numerical_difference_km'):
  if record[key]!=expected[key]:raise ValueError((key,record[key],expected[key]))
 trace=r.trace_json(prod);points=r._trace_states(trace)
 if points[0]['state']!=prod['accepted_states'][0]['state']:raise ValueError('roundtrip')
print('preflight',obj['id'],len(cases),cases[-1]['record']['max_position_error_km'],cases[-1]['record']['closest_geometry']['errors'])
immutable_json(root/'outputs/selector_v3/runner_preflight.json',{'object_id':obj['id'],'scope':'inspected train object only; full physical pair and five prefixes','records_exactly_match_frozen_v2':5,'geometry':cases[-1]['record']['closest_geometry'],'runner_sha256':sha(root/'src/run_selector_holdout24.py')})
