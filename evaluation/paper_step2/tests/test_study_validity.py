"""Regression checks for the discovery, scenario validity, and the four-opportunity study."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / 'backend'))
from generate_log import generate, make_injection, make_control, measure_behavior, InjectionSpec
from study_measurements import verify_injection
from regenerate import OBSERVATION_CONFIGS, _match_case_attr_observation, _effective_signature, _run_what_if
from src.process_observations import _conditional_routing_rules, _conditional_rework_rules, extract_process_observations
from src.case_attribute_extractor import extract_case_attribute_observations
from routers.simulate import _apply_modifications, _collect_recommendations, _collect_case_attribute_recommendations
from src.simulation_engine import run_simulation, SimulationConfig
from regenerate import _discover_model, _extract_case_observations, _tmp_csv


def traces(paths):
    rows = []
    for i, path in enumerate(paths):
        for j, activity in enumerate(path):
            start = pd.Timestamp('2024-01-01 09:00', tz='UTC') + pd.Timedelta(minutes=i*10+j)
            rows.append(dict(case_id=str(i), activity=activity, resource='R1' if i%2 else 'R2',
                             start_time=start, end_time=start+pd.Timedelta(minutes=1), duration_minutes=1))
    return pd.DataFrame(rows)


class GeneratorTests(unittest.TestCase):
    def test_penalty_applies_only_to_target_activity_rework(self):
        events, attrs = generate(700, 2026, make_injection('O1', 'B', 3))
        measured = measure_behavior(events, attrs, 'O1', 'B')
        self.assertAlmostEqual(measured['condition_value']/measured['other_value'], 3, delta=.2)
        self.assertAlmostEqual(measured['other_value'], 45, delta=2)

    def test_segment_rework_is_injected(self):
        events, attrs = generate(1000, 2026, make_injection('O4', 'B', .65))
        measured = measure_behavior(events, attrs, 'O4', 'B')
        self.assertAlmostEqual(measured['condition_value'], .65, delta=.10)
        self.assertAlmostEqual(measured['other_value'], .08, delta=.05)
        control, control_attrs = generate(1000, 2026, make_control('O4', 'B'))
        self.assertTrue(verify_injection(measure_behavior(control,control_attrs,'O4','B'),measured)[0])

    def test_slow_resource_preserves_queuing(self):
        events, _ = generate(100, 2026, make_injection('O2','A',3))
        for _, group in events.groupby('resource'):
            group = group.sort_values('start_time')
            self.assertFalse((group.start_time < group.end_time.cummax().shift()).any())

    def test_controls_use_the_same_baseline_parameters(self):
        for name, context in [('O1','B'), ('O2','A'), ('O3','B'), ('O4','B')]:
            self.assertEqual(make_control(name,context), InjectionSpec())


class DiscoveryTests(unittest.TestCase):
    def test_majority_routing_condition_and_rare_branch_remain_detectable(self):
        # 90% condition; rare branch is <5% overall, but 25% in controls.
        paths = [['P1','Gateway','Approve'] for _ in range(180)]
        paths += [['P2','Gateway','Reject' if i<5 else 'Approve'] for i in range(20)]
        rows = _conditional_routing_rules(traces(paths))
        target = rows[rows.condition_type.eq('predecessor') & rows.condition_value.eq('P1') & rows.branch_activity.eq('Approve')]
        self.assertEqual(len(target),1)
        self.assertAlmostEqual(target.iloc[0]['ratio'], 1/.75, delta=.002)

    def test_first_execution_prevents_tautological_rework_observation(self):
        ev = traces([['Start','Check','Check','End'] if i<30 else ['Start','Check','End'] for i in range(100)])
        rows = _conditional_rework_rules(ev)
        self.assertFalse(((rows.activity=='Check') & (rows.condition_value=='Check')).any())

    def test_majority_rework_condition_compares_to_complement(self):
        paths=[]
        for i in range(1000):
            condition = i<900
            repeats = i%10<2 if condition else i%20==0
            paths.append(['P1' if condition else 'P2','Check'] + (['Check'] if repeats else []) + ['End'])
        rules=_conditional_rework_rules(traces(paths))
        target=rules[rules.activity.eq('Check') & rules.condition_type.eq('preceded_by') & rules.condition_value.eq('P1')]
        self.assertEqual(len(target),1)
        self.assertAlmostEqual(target.iloc[0]['ratio'],4,delta=.01)

    def test_working_time_resource_effect_survives_overnight_noise(self):
        ev,_=generate(700,45,make_injection('O2','A',1.5))
        ev['duration_minutes']=(ev.end_time-ev.start_time).dt.total_seconds()/60
        rules=extract_process_observations(ev)['conditional_time_rules']
        target=rules[rules.activity.eq('Check Completeness') & rules.condition_type.eq('resource_speed_gap') & rules.condition_value.str.startswith('Clerk-1 vs')]
        self.assertEqual(len(target),1)
        self.assertAlmostEqual(target.iloc[0]['ratio'],1.5,delta=.1)

    def test_absence_uses_other_cases_for_eligibility(self):
        paths=[];attrs=[]
        for i in range(1000):
            segment=i<300
            present=(i%50==0) if segment else (i%4==0)
            paths.append(['Start']+(['Info'] if present else [])+['End'])
            attrs.append({'case_id':str(i),'segment':'target' if segment else 'other'})
        observations=extract_case_attribute_observations(traces(paths),pd.DataFrame(attrs),'case_id')
        target=[a for a in observations if a['sub_type']=='activity_absence' and a['attribute_value']=='target' and a.get('activity')=='Info']
        self.assertEqual(len(target),1)
        self.assertEqual(target[0]['baseline_value'],25)

    def test_case_rework_keeps_responsible_activity(self):
        paths=[];attrs=[]
        for i in range(200):
            segment=i<100
            paths.append(['A','A','B']+(['B'] if i%(2 if segment else 10)==0 else [])+['End'])
            attrs.append({'case_id':str(i),'segment':'target' if segment else 'other'})
        observations=extract_case_attribute_observations(traces(paths),pd.DataFrame(attrs),'case_id')
        target=[a for a in observations if a['sub_type']=='rework_effect' and a['attribute_value']=='target']
        self.assertEqual([a['activity'] for a in target],['B'])


class InterventionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ev,cls.attrs=generate(300,2026,InjectionSpec())
        cls.model,_=_discover_model(cls.ev)

    def test_rework_change_reduces_loop_entry_not_mandatory_return(self):
        before=self.model
        after=_apply_modifications(before,[{'type':'rework_rate','activity':'Check Completeness','change_type':'reduce_pct','value':50}])
        old=dict(before['prefix_tree']['Check Completeness'])
        new=dict(after['prefix_tree']['Check Completeness'])
        self.assertAlmostEqual(new['Request Additional Info'],old['Request Additional Info']*.5,places=5)
        self.assertEqual(before['prefix_tree']['Request Additional Info'],after['prefix_tree']['Request Additional Info'])
        self.assertEqual(before['rework_parameters_df'].max_loops.tolist(),after['rework_parameters_df'].max_loops.tolist())
        config=SimulationConfig(num_cases=1500,random_seed=2026)
        b=run_simulation(before,config);a=run_simulation(after,config)
        self.assertLess(a['rework_rate_pct'],b['rework_rate_pct']-5)

    def test_invalid_sequence_changes_are_rejected(self):
        for source,target in [('Approve','Approve Loan Offer'),('Escalate','Assess Risk')]:
            with self.assertRaises(ValueError):
                _apply_modifications(self.model,[{'type':'sequence','from_activity':source,'to_activity':target,'value':10}])

    def test_eventual_relation_does_not_offer_missing_direct_edge(self):
        recs=_collect_recommendations(self.model)
        targets=[r for r in recs if r['observation_type']=='sequence' and "'Approve' → 'Approve Loan Offer'" in r['rule_text']]
        self.assertTrue(targets)
        self.assertTrue(all(r['suggestion'] is None for r in targets))

    def test_case_rework_recommendation_targets_observed_activity(self):
        observation=dict(observation_type='case_attribute',sub_type='rework_effect',attribute='channel',attribute_value='branch',
                  activity='Check Credit History',ratio=2,n_cases=100,n_total=300,impact=.2,severity='orange',
                  rule_text='target',conditional_value=30,baseline_value=15)
        recs=_collect_case_attribute_recommendations([observation],self.model)
        self.assertEqual(recs[0]['suggestion']['activity'],'Check Credit History')

    def test_missing_step_has_no_fictitious_duration_fix(self):
        observation=dict(sub_type='activity_absence',attribute='channel',attribute_value='branch',activity='Request Additional Info',
                  ratio=.2,n_cases=100,n_total=300,impact=.2,severity='orange',rule_text='target',conditional_value=5,baseline_value=25)
        self.assertIsNone(_collect_case_attribute_recommendations([observation],self.model)[0]['suggestion'])

    def test_matcher_requires_activity_attribute_and_direction(self):
        cfg=OBSERVATION_CONFIGS['O4']
        correct=dict(sub_type='rework_effect',attribute='channel',attribute_value='branch',activity='Check Credit History',ratio=2)
        observations=[correct,{**correct,'activity':'Other'},{**correct,'ratio':.5},{**correct,'attribute':'Other'}]
        self.assertEqual(_match_case_attr_observation(observations,cfg,'O4','B'),[correct])

    def test_metadata_only_mutation_is_not_an_effective_change(self):
        changed=copy.deepcopy(self.model)
        changed['rework_parameters_df']['rework_rate_pct']*=.5
        self.assertEqual(_effective_signature(self.model),_effective_signature(changed))


class PipelineTests(unittest.TestCase):
    def test_strict_discovery_does_not_hide_extractor_failure(self):
        with patch('src.process_observations._conditional_time_rules', side_effect=RuntimeError('broken')):
            with self.assertRaisesRegex(RuntimeError, 'broken'):
                extract_process_observations(traces([['Start','End']]*40), strict=True)

    def test_identical_injection_fails_manipulation_check(self):
        measurement=dict(condition_n=100,other_n=100,expected_direction=1,condition_value=.2,other_value=.1)
        self.assertFalse(verify_injection(measurement,measurement)[0])

    def test_recommendation_is_selected_before_its_outcome(self):
        ev,_=generate(100,2026,InjectionSpec())
        model,_=_discover_model(ev)
        recs=[dict(observation_type='conditional_time',rule_text='target',impact=impact,stable_key=str(impact),
                   suggestion=dict(type='time',activity='Assess Risk',change_type='reduce_pct',value=value))
              for impact,value in [(1,50),(2,10)]]
        calls=[]
        def simulate(changed,args,directory):
            directory.mkdir()
            calls.append(changed)
            return None, dict(avg_cycle_time_hours=11,rework_rate_pct=0)
        with tempfile.TemporaryDirectory() as directory:
            with patch('regenerate.final_recommendations',return_value=recs), \
                 patch('regenerate.simulate_and_save',side_effect=simulate), \
                 patch('regenerate.check_replay'):
                selected,outcomes=_run_what_if(model,[{'rule_text':'target'}],'conditional_time',None,
                                               Path(directory),{'avg_cycle_time_hours':10})
        self.assertEqual(len(calls),1)
        self.assertEqual(selected[0]['impact'],2)
        self.assertEqual(outcomes[0]['cycle_time_reduction_after_recommendation_hours'],-1)
        self.assertEqual(outcomes[1]['status'],'not_selected_by_prespecified_policy')

    def test_missing_suggestion_is_not_simulated(self):
        rec=dict(observation_type='sequence',rule_text='target',suggestion=None,
                 unsupported_reason='missing direct edge')
        with tempfile.TemporaryDirectory() as directory:
            with patch('regenerate.final_recommendations',return_value=[rec]), \
                 patch('regenerate.simulate_and_save') as simulate:
                _,outcomes=_run_what_if({},[{'rule_text':'target'}],'sequence',None,
                                       Path(directory),{'avg_cycle_time_hours':10})
                simulate.assert_not_called()
        self.assertEqual(outcomes[0]['status'],'no_modification')


if __name__=='__main__':
    unittest.main()
