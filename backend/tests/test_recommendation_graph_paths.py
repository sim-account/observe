"""Behavioral coverage for rework classification and bounded branch-cost ranking.

These are model-level regressions, not production-load or convergence guarantees.
"""
import copy
import unittest

import pandas as pd

from routers.simulate import _apply_modifications, _collect_recommendations


def graph_model(graph, terminals=None):
    return {
        'prefix_tree': copy.deepcopy(graph),
        'terminal_probs': terminals or {},
        'routing_probabilities_df': pd.DataFrame(
            [(source, dest, probability) for source, edges in graph.items()
             if '|||' not in source for dest, probability in edges],
            columns=['from_activity', 'to_activity', 'probability']),
        'rework_parameters_df': pd.DataFrame([
            dict(activity='A', rework_rate_pct=40., rework_rate_given_visit_pct=40., max_loops=5)]),
    }


class ReworkGraphTests(unittest.TestCase):
    def reduce(self, graph, terminals=None):
        before = graph_model(graph, terminals)
        after = _apply_modifications(before, [
            dict(type='rework_rate', activity='A', change_type='reduce_pct', value=50)])
        # Public modification entry point must preserve the input model.
        self.assertEqual(before['prefix_tree'], graph)
        pd.testing.assert_frame_equal(before['rework_parameters_df'], graph_model(graph)['rework_parameters_df'])
        self.assertEqual(after['rework_parameters_df'].max_loops.tolist(), [5])
        for edges in after['prefix_tree'].values():
            self.assertAlmostEqual(sum(p for _, p in edges), 1., places=5)
            self.assertTrue(all(0 <= p <= 1 for _, p in edges))
        for row in after['routing_probabilities_df'].itertuples():
            self.assertEqual(row.probability, dict(after['prefix_tree'][row.from_activity])[row.to_activity])
        return after['prefix_tree']

    def test_self_loop_scales_and_redistributes_exits_proportionally(self):
        after = self.reduce({'A': [['A', .4], ['X', .2], ['Y', .4]]})
        self.assertAlmostEqual(dict(after['A'])['A'], .2)
        self.assertAlmostEqual(dict(after['A'])['X'], .8 / 3, places=5)
        self.assertAlmostEqual(dict(after['A'])['Y'], 1.6 / 3, places=5)

    def test_all_returning_branches_and_context_entries_reduce_together(self):
        graph = {'A': [['B', .4], ['Exit', .6]],
                 'Start|||A': [['B', .6], ['Exit', .4]],
                 'B': [['C', .5], ['D', .5]],
                 'C': [['A', 1.]], 'D': [['A', 1.]]}
        after = self.reduce(graph)
        self.assertEqual(dict(after['A'])['B'], .2)
        self.assertEqual(dict(after['Start|||A'])['B'], .3)
        for node in ['B', 'C', 'D']:
            self.assertEqual(after[node], graph[node])
        # Classification must not depend on prefix-tree insertion order.
        self.assertEqual(after, self.reduce(dict(reversed(list(graph.items())))))

    def test_possible_escape_is_not_a_mandatory_return(self):
        graph = {'A': [['B', .4], ['Exit', .6]], 'B': [['A', .9], ['Exit', .1]]}
        self.assertEqual(self.reduce(graph), graph)

    def test_terminal_probability_prevents_mandatory_return_classification(self):
        graph = {'A': [['B', .4], ['Exit', .6]], 'B': [['A', 1.]]}
        self.assertEqual(self.reduce(graph, {'B': .1}), graph)

    def test_zero_probability_escape_does_not_block_loop_reduction(self):
        graph = {'A': [['B', .4], ['Exit', .6]], 'B': [['A', 1.], ['Exit', 0.]]}
        after = self.reduce(graph)
        self.assertEqual(dict(after['A'])['B'], .2)
        self.assertEqual(after['B'], graph['B'])

    def test_unrelated_cycle_and_missing_successor_are_left_unchanged(self):
        graph = {'A': [['B', .2], ['Missing', .2], ['Exit', .6]],
                 'B': [['C', 1.]], 'C': [['B', 1.]]}
        self.assertEqual(self.reduce(graph), graph)

    def test_no_alternative_exit_does_not_break_mandatory_return(self):
        graph = {'A': [['B', 1.]], 'B': [['A', 1.]]}
        self.assertEqual(self.reduce(graph), graph)

    def test_return_beyond_depth_limit_is_conservatively_unmodified(self):
        # Documents the current boundary, not support for arbitrary-length loops.
        for length, expected in [(20, .2), (21, .4)]:
            with self.subTest(length=length):
                graph = {'A': [['N0', .4], ['Exit', .6]]}
                graph.update({f'N{i}': [[f'N{i+1}' if i+1 < length else 'A', 1.]]
                              for i in range(length)})
                self.assertEqual(dict(self.reduce(graph)['A'])['N0'], expected)


class RoutingCostTests(unittest.TestCase):
    def model(self, graph, costs, terminals=None, resources=None):
        model = graph_model(graph, terminals)
        model['activity_durations_df'] = pd.DataFrame([
            dict(activity=activity, mean_minutes=cost) for activity, cost in costs.items()])
        model['conditional_routing_rules'] = pd.DataFrame([
            dict(gateway_activity='G', branch_activity=branch, condition_type='predecessor',
                 condition_value='P', ratio=1.6, baseline_pct=50., conditional_pct=80.,
                 count=100, rule_text=f'bias toward {branch}', severity='orange')
            for branch in ['B', 'C']])
        if resources is not None:
            model['resource_assignments_df'] = pd.DataFrame(resources)
        return model

    def recommendations(self, model):
        before = copy.deepcopy(model)
        recs = [r for r in _collect_recommendations(model) if r['observation_type'] == 'conditional_routing']
        for key in model:
            if isinstance(model[key], pd.DataFrame):
                pd.testing.assert_frame_equal(model[key], before[key])
            else:
                self.assertEqual(model[key], before[key])
        return recs

    def branches(self, model):
        return [r['suggestion']['to_activity'] for r in self.recommendations(model)]

    def test_downstream_work_reverses_immediate_duration_ranking(self):
        model = self.model({'G': [['B', .8], ['C', .2]], 'B': [['Tail', 1.]]},
                           {'B': 1., 'C': 10., 'Tail': 100.})
        recs = self.recommendations(model)
        self.assertEqual([r['suggestion']['to_activity'] for r in recs], ['B'])
        suggestion = recs[0]['suggestion']
        self.assertEqual(suggestion['from_activity'], 'G')
        self.assertEqual(suggestion['value'], 50.)
        changed = _apply_modifications(model, [suggestion])
        self.assertEqual(dict(changed['prefix_tree']['G']), {'B': .5, 'C': .5})

    def test_terminal_probability_discounts_continuation_cost(self):
        graph = {'G': [['B', .5], ['C', .5]], 'B': [['Tail', 1.]]}
        costs = {'B': 1., 'C': 20., 'Tail': 100.}
        self.assertEqual(self.branches(self.model(graph, costs)), ['B'])
        self.assertEqual(self.branches(self.model(graph, costs, {'B': .9})), ['C'])

    def test_resource_weighted_service_cost_overrides_activity_average(self):
        resources = [dict(activity='B', resource='r1', probability=.25, mean_duration_min=80.),
                     dict(activity='B', resource='r2', probability=.75, mean_duration_min=0.)]
        model = self.model({'G': [['B', .5], ['C', .5]]}, {'B': 1., 'C': 15.}, resources=resources)
        # Weighted B cost is 20, above C=15. An unweighted/activity mean gives a different ranking.
        self.assertEqual(self.branches(model), ['B'])
        model['activity_durations_df'].loc[model['activity_durations_df'].activity.eq('C'), 'mean_minutes'] = 30.
        self.assertEqual(self.branches(model), ['C'])

    def test_repeated_work_can_outweigh_a_slower_single_visit(self):
        model = self.model({'G': [['B', .5], ['C', .5]], 'B': [['B', .9], ['Exit', .1]]},
                           {'B': 2., 'C': 10., 'Exit': 0.})
        # B's immediate cost is 2; repeated work makes it more costly than C=10.
        self.assertEqual(self.branches(model), ['B'])

    def test_no_rules_equal_cost_and_reduced_branch_bias_offer_no_routing_fix(self):
        model = self.model({'G': [['B', .5], ['C', .5]]}, {'B': 10., 'C': 10.})
        self.assertEqual(self.branches(model), [])
        model['activity_durations_df'].loc[model['activity_durations_df'].activity.eq('B'), 'mean_minutes'] = 100.
        model['conditional_routing_rules']['ratio'] = .8
        self.assertEqual(self.branches(model), [])
        model['conditional_routing_rules'] = pd.DataFrame()
        self.assertEqual(self.recommendations(model), [])

    def test_truncation_can_miss_a_cost_beyond_the_horizon(self):
        # A documented limitation: a long zero-cost path hides its expensive tail.
        graph = {'G': [['B', .5], ['C', .5]], 'B': [['N0', 1.]]}
        graph.update({f'N{i}': [[f'N{i+1}' if i < 20 else 'Tail', 1.]] for i in range(21)})
        model = self.model(graph, {'B': 0., 'C': 10., 'Tail': 100.})
        self.assertEqual(self.branches(model), ['C'])
        # Shortening the path makes the same downstream cost visible.
        graph['B'] = [['Tail', 1.]]
        self.assertEqual(self.branches(self.model(graph, {'B': 0., 'C': 10., 'Tail': 100.})), ['B'])


if __name__ == '__main__':
    unittest.main()
