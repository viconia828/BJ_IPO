from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'tools')]
import subscription_ladder_labels as labels
import build_account_pool_history as pool


class DistributionPipelineTests(unittest.TestCase):
    def test_numeric_time_priority_qualifier(self):
        items=labels.parse_manual_ladder('1+0=900;1+1=1250.18抢时间',1500)
        self.assertEqual(items[1]['threshold_amount_wan'],1250.18)
        self.assertTrue(items[1]['time_priority_required'])
        self.assertEqual(items[1]['threshold_text'],'1250.18抢时间')
        unknown=labels.parse_manual_ladder('1+1=1250.18可能',1500)
        self.assertIsNone(unknown[0]['threshold_amount_wan'])

    def test_fractional_only_label_is_not_dropped(self):
        r={'manual_ladder':'0+1=480','top_apply_amount_wan':850.1625,'issue_price':13.75}
        d=labels.derive_fields_from_ladder(r)
        self.assertEqual(d['manual_ladder_fractional_amount_wan'],480)
        self.assertAlmostEqual(d['manual_ladder_fractional_shares'],480*10000/13.75)
        self.assertNotIn('manual_ladder_frozen_funds_yi',d)
        merged=labels.apply_labels_to_history_rows([], [{'security_code':'a',**r}])[0]
        self.assertEqual(merged['fractional_threshold_amount_wan'],'480')

    def test_manual_last_threshold_survives_cap_and_fit(self):
        for text,expected in [('1+0=610;1+1=809.98',{1:610,2:809.98}),('0+1=480',{1:480})]:
            manual=pool._manual_points({'manual_ladder':text,'top_apply_amount_wan':1145})
            actual=pool._complete_lot_thresholds(manual_by_lot=manual,
                fit_by_lot={max(manual):{'threshold_amount_wan':1145}},
                max_lots=max(manual),top_apply_amount_wan=1145)
            self.assertEqual(actual,expected)

    def test_two_layers_are_uniquely_identified_independent_of_prior(self):
        for prior in (1000,47608.448168,110000):
            values,basis=pool._announcement_constrained_cumulative(allocated_accounts=113202,total_lots=200000,
                thresholds_by_lot={1:610,2:809.98},prior_state={'p':{'threshold_amount_wan':809.98,'estimate':prior,'deviation':1000}})
            self.assertEqual(values,{1:113202,2:86798})
            self.assertEqual(basis,'announcement_exact')

    def test_multilayer_expansion_obeys_both_aggregate_equations(self):
        for total in (100001,200000,399999):
            values,basis=pool._announcement_constrained_cumulative(allocated_accounts=100000,total_lots=total,
                thresholds_by_lot={1:100,2:200,3:300,4:400},
                prior_state={'p':{'threshold_amount_wan':200,'estimate':50000,'deviation':1000}})
            self.assertEqual(values[1],100000)
            self.assertAlmostEqual(sum(values.values()),total,places=5)
            self.assertTrue(all(values[k]>=values[k+1] for k in (1,2,3)))
            exact=[values[k]-values.get(k+1,0) for k in (1,2,3,4)]
            self.assertAlmostEqual(sum(exact),100000)
            self.assertAlmostEqual(sum(k*x for k,x in enumerate(exact,1)),total,places=5)

    def test_impossible_capacity_is_not_silently_clipped(self):
        values,basis=pool._announcement_constrained_cumulative(allocated_accounts=100,total_lots=301,
            thresholds_by_lot={1:10,2:20,3:30},prior_state={})
        self.assertFalse(values)
        self.assertEqual(basis,'unavailable')

    def test_old_cache_version_is_invalidated(self):
        rows=[{'security_code':'a','apply_date':'2026-07-01'}]
        old=[{**rows[0],'account_pool_build_version':3,'account_pool_input_signature':pool._account_pool_input_signature(rows[0])}]
        self.assertEqual(pool._incremental_rebuild_index(rows,old,force_rebuild=False),0)

    def test_all_local_labels_resolve_and_join_shared_history(self):
        import subscription_prediction_context as context
        rows={r['security_code']:r for r in context.load_history_rows()}
        for r in labels.load_label_rows():
            self.assertEqual(rows[r['security_code']]['manual_ladder'],r['manual_ladder'])
            for item in labels.parse_manual_ladder(r['manual_ladder'],r['top_apply_amount_wan']):
                self.assertGreater(item['threshold_amount_wan'],0)


if __name__=='__main__':
    unittest.main()
