"""Verify the complete paper cohort; optionally rediscover inputs or replay all simulations."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
from simulation_helpers import ROOT, serialize_model, _apply_modifications
from evaluation.paths import data_root, output_path
from regenerate import (PAPER_CONFIGS,_discover_model_from_path,_extract_case_observations,
                        _match_observation,_match_case_attr_observation,_effective_signature)
from recommendation_helpers import final_recommendations
from replay_helpers import check_replay,read,saved_model
from generate_log import measure_behavior
from study_measurements import verify_injection
from plot_results import prepare,results_overview

def equivalent(actual,expected):
    if isinstance(expected,dict):
        assert isinstance(actual,dict) and actual.keys()==expected.keys(), 'Dictionary schema differs'
        for k in expected:equivalent(actual[k],expected[k])
    elif isinstance(expected,list):
        assert len(actual)==len(expected),'List length differs'
        for a,b in zip(actual,expected):equivalent(a,b)
    elif isinstance(expected,(int,float)) and not isinstance(expected,bool):
        assert np.isclose(actual,expected,rtol=1e-12,atol=1e-10,equal_nan=True),(actual,expected)
    else:assert actual==expected,(actual,expected)

def verify(root,rediscover=False,replay=False):
    trials=pd.read_csv(root/'trials.csv');settings=read(root/'study_settings.json')
    assert len(trials)==120 and not trials.duplicated(['observation_type','context','strength','seed']).any()
    assert trials.error.isna().all() and trials.injection_verified.all()
    assert trials.target_detected.sum()==117 and trials.recommendation_executed.sum()==117
    assert not trials.target_detected_in_baseline.any()
    controls={};discovery={};replayed=0
    for _,r in trials.iterrows():
        cfg=PAPER_CONFIGS[r.observation_type]
        assert r.context in cfg['contexts'] and r.strength in cfg['strengths'] and r.seed in range(2036,2046)
        directory=root/r.directory;control=root/r.baseline_directory
        if control not in controls:
            events=pd.read_csv(control/'events.csv',dtype={'case_id': str});events['start_time']=pd.to_datetime(events.start_time,format='mixed',utc=True);events['end_time']=pd.to_datetime(events.end_time,format='mixed',utc=True);attrs=pd.read_csv(control/'case_attributes.csv',dtype={'case_id': str})
            controls[control]=(events,attrs)
            if rediscover:
                model,obs=_discover_model_from_path(control/'events.csv')
                ca=_extract_case_observations(control/'events.csv',attrs)
                equivalent(serialize_model(model),read(control/'simulation_model.json'))
                equivalent(ca,read(control/'case_attribute_observations.json'))
                discovery[control]=(obs,ca)
        events=pd.read_csv(directory/'events.csv',dtype={'case_id': str});events['start_time']=pd.to_datetime(events.start_time,format='mixed',utc=True);events['end_time']=pd.to_datetime(events.end_time,format='mixed',utc=True);attrs=pd.read_csv(directory/'case_attributes.csv',dtype={'case_id': str})
        base_events,base_attrs=controls[control]
        measured=measure_behavior(events,attrs,cfg['injection'],r.context)
        baseline=measure_behavior(base_events,base_attrs,cfg['injection'],r.context)
        assert verify_injection(baseline,measured)[0]
        equivalent(measured,read(directory/'behavior_measurements.json')['injected'])
        equivalent(baseline,read(directory/'behavior_measurements.json')['baseline'])
        matched=read(directory/'matched_observations.json');base_matched=read(directory/'baseline_matched_observations.json')
        assert not base_matched and bool(matched)==bool(r.target_detected)
        model=saved_model(directory/'simulation_model.json')
        ca=read(directory/'case_attribute_observations.json') if cfg.get('needs_case_attrs') else []
        if rediscover:
            new,obs=_discover_model_from_path(directory/'events.csv');new_ca=_extract_case_observations(directory/'events.csv',attrs) if cfg.get('needs_case_attrs') else []
            equivalent(serialize_model(new),serialize_model(model));equivalent(new_ca,ca)
            base_obs,base_ca=discovery[control]
            if cfg.get('needs_case_attrs'):
                found=_match_case_attr_observation(new_ca,cfg,cfg['injection'],r.context)
                found_base=_match_case_attr_observation(base_ca,cfg,cfg['injection'],r.context)
            else:
                found=_match_observation(obs,cfg,cfg['injection'],r.context)
                found_base=_match_observation(base_obs,cfg,cfg['injection'],r.context)
            equivalent(found,matched);equivalent(found_base,base_matched)
        inj=read(directory/'simulation/kpis.json');base=read(control/'simulation/kpis.json')
        assert np.isclose(inj['avg_cycle_time_hours'],r.injected_cycle_hours,rtol=1e-12)
        assert np.isclose(base['avg_cycle_time_hours'],r.baseline_cycle_hours,rtol=1e-12)
        status=read(directory/'what_if_results.json')
        if r.recommendation_executed:
            recommendations=final_recommendations(model,ca_observations=ca if cfg.get('needs_case_attrs') else [])
            texts={o['rule_text'] for o in matched}
            candidates=[rec for rec in recommendations if rec['observation_type']==cfg['recommendation_observation_type'] and rec.get('rule_text') in texts]
            candidates=sorted(candidates,key=lambda x:(-float(x.get('impact',0)),x.get('stable_key',x.get('rule_text',''))))
            equivalent(candidates,read(directory/'selected_recommendations.json'))
            executed=[o for o in status if o['status']=='executed'];assert len(executed)==1
            target=directory/f"what_if_{executed[0]['index']}"
            change=read(target/'modification.json')['modification'];assert change==candidates[0]['suggestion']
            revised=_apply_modifications(model,[change]);equivalent(serialize_model(revised),read(target/'simulation_model.json'))
            assert _effective_signature(revised)!=_effective_signature(model)
            new=read(target/'kpis.json');assert np.isclose(inj['avg_cycle_time_hours']-new['avg_cycle_time_hours'],r.cycle_time_reduction_after_recommendation_hours,rtol=1e-12)
        else:assert not status
    assert len(controls)==10
    simulations=sorted(root.rglob('simulation_settings.json'));assert len(simulations)==247
    for p in simulations:
        cfg=read(p);kpi=read(p.parent/'kpis.json')
        assert cfg['num_cases']==1300 and cfg['warmup_cases']==300 and cfg['random_seed'] in range(2036,2046)
        assert kpi['cases_completed']==1000
        if replay:check_replay(p.parent);replayed+=1;print(f'Replayed {replayed}/247',flush=True)
    overview=results_overview(prepare(root));ref=pd.read_csv(Path(__file__).parent/'reference/results.csv')
    for column in overview.columns:
        if column == 'source': continue
        tolerance = 1e-6 if column.startswith('recovery_ci') else 1e-8 if 'ci95' in column else 1e-10
        pd.testing.assert_series_equal(overview[column], ref[column], check_dtype=False, rtol=1e-12, atol=tolerance)
    return {'planned':120,'verified_manipulations':120,'identified':117,'executed':117,'controls':10,
        'model_modification_checks':117,'positive_benefit_intervals':int(overview.estimated_improvement.sum()),
        'median_recovery_pct':float(overview.recovery_pct.median()),'simulations':247,
        'rediscovered':130 if rediscover else 0,'replayed':replayed}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--data-root',type=Path);ap.add_argument('--results',type=Path)
    ap.add_argument('--output',type=Path,default=ROOT/'generated-results/opportunities-checks');ap.add_argument('--rediscover',action='store_true');ap.add_argument('--replay',action='store_true')
    a=ap.parse_args();inputs=data_root(a.data_root);out=output_path(a.output,inputs);out.mkdir(parents=True,exist_ok=True)
    report=verify(a.results or inputs/'opportunities',a.rediscover,a.replay)
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
