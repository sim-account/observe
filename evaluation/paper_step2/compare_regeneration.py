"""Compare regenerated scientific evidence against the supplied reference evidence."""
import argparse,json,sys
from pathlib import Path
import pandas as pd
from simulation_helpers import ROOT
from evaluation.paths import data_root,output_path
from verify import equivalent

def run(reference,regenerated):
    json_names={'simulation_model.json','simulation_settings.json','kpis.json','modification.json','matched_observations.json','baseline_matched_observations.json','selected_recommendations.json','case_attribute_observations.json','behavior_measurements.json','what_if_results.json'}
    counts={'json_evidence':0,'event_logs':0,'case_attribute_logs':0}
    for p in reference.rglob('*'):
        if not p.is_file() or p.name not in json_names|{'events.csv','case_attributes.csv'}:continue
        q=regenerated/p.relative_to(reference);assert q.is_file(),str(p.relative_to(reference))
        if p.suffix=='.json':
            try:
                actual=json.loads(q.read_text());expected=json.loads(p.read_text())
                if p.name == 'kpis.json':
                    # The engine ranks rounded bottleneck shares; tied entries can differ in presentation order.
                    for payload in [actual, expected]:
                        if 'bottlenecks' in payload: payload['bottlenecks'] = sorted(payload['bottlenecks'], key=lambda row: (-row['total_wait_share_pct'], row['activity']))
                equivalent(actual, expected)
            except AssertionError as exc: raise AssertionError(f'{p.relative_to(reference)}: {exc}') from exc
            counts['json_evidence']+=1
        else:
            a=pd.read_csv(p,dtype={'case_id':str});b=pd.read_csv(q,dtype={'case_id':str})
            for field in ['start_time','end_time']:
                if field in a:a[field]=pd.to_datetime(a[field],format='mixed',utc=True);b[field]=pd.to_datetime(b[field],format='mixed',utc=True)
            pd.testing.assert_frame_equal(a,b,check_dtype=False,rtol=1e-12,atol=1e-8)
            counts['event_logs' if p.name=='events.csv' else 'case_attribute_logs']+=1
    a=pd.read_csv(reference/'trials.csv');b=pd.read_csv(regenerated/'trials.csv');pd.testing.assert_frame_equal(a,b,check_dtype=False,rtol=1e-12,atol=1e-10)
    counts['planned_trial_records']=120
    return counts
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--data-root',type=Path);ap.add_argument('--regenerated',type=Path,default=ROOT/'generated-results/opportunities');ap.add_argument('--output',type=Path,default=ROOT/'generated-results/regeneration-equivalence');a=ap.parse_args();inputs=data_root(a.data_root);out=output_path(a.output,inputs);out.mkdir(parents=True,exist_ok=True)
    report=run(inputs/'opportunities',a.regenerated);(out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
