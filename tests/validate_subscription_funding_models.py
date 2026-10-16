from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code'), str(ROOT/'tools')]
import subscription_funding_model as f
import subscription_prediction_context as c
import tune_subscription_prediction as t
import evaluate_subscription_funding_models as experiment
from validate_subscription_replay_timeline import row, pool_row


class FundingModelTests(unittest.TestCase):
    def setUp(self):
        self.rows = [row(str(i), f'2026-07-{i:02d}', f'2026-07-{i:02d}') for i in range(1,10)]
        for i,r in enumerate(self.rows):
            r['frozen_funds_yi'] *= 1+i/10
            r['online_valid_shares'] *= 1+i/10
        self.target = row('target','2026-07-12','2026-07-15')
        self.params = {'subscription_prediction_funding_model':'direct_funds',
            'subscription_prediction_unified_recency_enabled':True,
            'subscription_prediction_sample_decay_half_life_days':6.25,
            'subscription_prediction_account_pool_rows':[pool_row(r) for r in self.rows],
            'subscription_prediction_recent_market_level_adaptive_enabled':True,
            'subscription_prediction_account_pool_prior_weight':1.2}

    def predict(self, rows=None, params=None):
        return c.build_prediction(c._target_from_row(self.target), history_rows=self.rows if rows is None else rows,
                                  params=self.params if params is None else params)

    def test_future_and_target_outcomes_cannot_change_prediction(self):
        expected = self.predict()
        poisoned = row('future','2026-07-10','2026-07-13')
        poisoned.update(frozen_funds_yi=1e12,online_valid_shares=1e20)
        same = {**poisoned,'security_code':'same','issue_result_date':'2026-07-12'}
        actual = self.predict(self.rows+[poisoned,same,self.target])
        self.assertEqual(expected['frozen_funds_yi'],actual['frozen_funds_yi'])
        self.assertEqual(expected['lot_thresholds'],actual['lot_thresholds'])

    def test_water_level_replays_each_historical_prediction_without_future_sources(self):
        result = self.predict()['estimate']
        by_code = {r['security_code']:r for r in self.rows}
        for s in result['recent_market_level']['samples']:
            for code in s['prediction_source_codes']:
                self.assertTrue(f.timeline.known_before(by_code[code],by_code[s['security_code']]))

    def test_recency_is_shared_with_fractional_pool_and_missing_caps_do_not_reset_age(self):
        rows=copy.deepcopy(self.rows)
        rows[-1]['top_apply_amount_wan']=''
        rows[-2]['issue_result_date']=rows[-3]['issue_result_date']='2026-07-09'
        p=self.predict(rows)
        self.assertEqual(p['estimate']['recency_weights'],p['history_context']['recency_weights'])
        self.assertEqual(p['estimate']['recency_weights']['7'],p['estimate']['recency_weights']['8'])

    def test_direct_funding_does_not_depend_on_issuance_amount_or_old_corrections(self):
        base=self.predict()
        changed={**self.params,'subscription_prediction_multiple_scale':10,
                 'subscription_prediction_issue_factor_exponent':10,
                 'subscription_prediction_cap_factor_exponent':10,
                 'subscription_prediction_account_pool_prior_weight':100}
        self.assertEqual(base['frozen_funds_yi'],self.predict(params=changed)['frozen_funds_yi'])
        target=c._target_from_row(self.target)
        target['ONLINE_ISSUE_NUM'] *= 2
        p=c.build_prediction(target,history_rows=self.rows,params=self.params)
        self.assertEqual(base['frozen_funds_yi'],p['frozen_funds_yi'])
        self.assertAlmostEqual(base['allocation_rate_pct']*2,p['allocation_rate_pct'])

    def test_old_prior_is_not_applied_twice(self):
        with patch.object(c,'_apply_account_pool_prior',side_effect=AssertionError('old prior')):
            self.assertTrue(self.predict()['available'])

    def test_small_half_life_change_produces_small_change(self):
        a=self.predict()['frozen_funds_yi']
        b=self.predict(params={**self.params,'subscription_prediction_sample_decay_half_life_days':6.250001})['frozen_funds_yi']
        self.assertLess(abs(b/a-1),1e-6)

    def test_bucket_reconciliation_preserves_source_total_at_same_cap(self):
        def demand(r,cap):
            return {'estimated_demand_wan':7*min(cap,2.5), 'confidence':.7}
        params={**self.params,'subscription_prediction_funding_model':'bucket_funds',
                'subscription_prediction_recent_market_level_adaptive_enabled':False}
        result=f.estimate_funding(self.target,self.rows,params,demand=demand)
        for s in result['direct_funding']['samples']:
            self.assertAlmostEqual(s['projected_frozen_funds_yi'],s['source_frozen_funds_yi'])

    def test_missing_buckets_explicitly_fall_back_to_direct_model(self):
        p=self.predict(params={**self.params,'subscription_prediction_funding_model':'bucket_funds'})
        self.assertEqual(p['estimate']['direct_funding']['fallback_reason'],'insufficient_bucket_sources')
        self.assertEqual(p['frozen_funds_yi'],self.predict()['frozen_funds_yi'])

    def test_actual_and_manual_target_semantics_are_preserved(self):
        target={**c._target_from_row(self.target),'ONLINE_VA_SHARES':100000}
        p=c.build_prediction(target,history_rows=self.rows,params=self.params)
        self.assertEqual(p['mode'],'actual')
        self.assertEqual(p['frozen_funds_yi'],.01)
        p=self.predict(params={**self.params,'subscription_prediction_target_frozen_funds_code':'target',
                               'subscription_prediction_target_frozen_funds_yi':.02})
        self.assertEqual(p['frozen_funds_yi'],.02)
        self.assertIsNotNone(p['target_frozen_funds_override'])

    def test_tuner_and_production_rebuild_same_thresholds(self):
        p=self.predict()
        d=t.evaluate_subscription_prediction(self.rows+[self.target],params=self.params)['details'][-1]
        self.assertEqual(p['frozen_funds_yi'],d['predicted_frozen_funds_yi'])
        self.assertEqual(p['guaranteed_threshold_amount_wan'],d['predicted_guaranteed_amount_wan'])
        ladders={(s['regular_lots'],s['fractional_lots']):s for s in p['lot_thresholds']}
        for s in d['manual_ladder_errors']:
            candidate=ladders.get((s['regular_lots'],s['fractional_lots']))
            if candidate:
                self.assertEqual(s['predicted_threshold_amount_wan'],candidate['threshold_amount_wan'])

    def test_invalid_parameters_are_rejected(self):
        for value in (0,float('nan'),float('inf')):
            with self.assertRaises(ValueError):
                self.predict(params={**self.params,'subscription_prediction_sample_decay_half_life_days':value})

    def test_old_search_cannot_silently_tune_inactive_parameters(self):
        with self.assertRaisesRegex(ValueError,'evaluate_subscription_funding_models'):
            t.evaluate_candidate_grid(self.rows,base_params=self.params)

    def test_candidate_selection_ignores_not_yet_published_outcomes(self):
        by_code={str(i):row(str(i),f'2026-06-{i+1:02d}',f'2026-06-{i+2:02d}') for i in range(9)}
        by_code['8']['issue_result_date']='2026-07-02'
        candidates={'a':{'subscription_prediction_funding_model':'direct_funds'},
                    'b':{'subscription_prediction_funding_model':'direct_funds'}}
        def details(normal,future):
            return [{'security_code':str(i),'replay_as_of_date':r['apply_date'],
                'actual_top_apply_below_guaranteed':False,'predicted_top_apply_below_guaranteed':False,
                'actual_frozen_funds_yi':100,'frozen_funds_pct_error':normal if i<8 else future,
                'frozen_funds_abs_error_yi':100*(normal if i<8 else future),
                'manual_ladder_errors':[]} for i,r in enumerate(by_code.values())]
        replays={k:{'details':details(n,e)} for k,n,e in [('baseline',.1,.1),('a',.02,100),('b',.04,0)]}
        result=experiment.select_candidate(replays,candidates,by_code,'2026-07-01')
        self.assertEqual(result['candidate'],'a')
        self.assertNotIn('8',result['training_codes'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
