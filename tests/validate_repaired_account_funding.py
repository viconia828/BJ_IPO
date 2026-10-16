from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'tools')]
import subscription_account_funding as a
import subscription_prediction_context as c
import subscription_funding_model as f
import evaluate_subscription_funding_models as e
import evaluate_repaired_account_funding as experiment
import build_account_pool_history as builder


def row(code,day):
    return {'security_code':code,'apply_date':f'2026-07-{day:02d}','issue_result_date':f'2026-07-{day+1:02d}',
            'issue_price':10,'online_issue_shares':10000,'top_apply_amount_wan':30,
            'online_allocated_accounts':60,'online_valid_accounts':100,'online_valid_shares':1500000,
            'manual_ladder_label_ready':True,
            'frozen_funds_yi':.15,'manual_ladder':'1+0=10;1+1=20','allocation_fit_json':'{}'}


class RepairedAccountTests(unittest.TestCase):
    def setUp(self):
        self.rows=[row(str(i),i) for i in range(1,10)]
        self.pools=e.rebuild_scoped_pool(self.rows)
        self.target=row('target',12)
        self.params={'subscription_prediction_funding_model':'account_pool_funds',
                     'subscription_prediction_account_pool_rows':self.pools,
                     'subscription_prediction_min_samples':3,
                     'subscription_prediction_recent_market_level_adaptive_enabled':True}

    def source(self,index=0):
        return {**self.rows[index],'source_lot_distribution_json':self.pools[index]['source_lot_distribution_json']}

    def predict(self,rows=None,pools=None,asof=None):
        return c.build_prediction(c._target_from_row(self.target),history_rows=rows or self.rows,
                                  params={**self.params,'subscription_prediction_account_pool_rows':pools or self.pools},as_of_date=asof)

    def test_two_layer_counts_and_source_total(self):
        for p in (0,.5,1):
            m=a.distribution(self.source(),p)
            self.assertTrue(m['available'])
            self.assertEqual([b['accounts'] for b in m['bands']],[20,40])
            self.assertAlmostEqual(a.demand(m,30)['estimated_demand_wan'],1500)
            self.assertLessEqual(a.demand(m,15)['estimated_demand_wan'],1500)
            self.assertAlmostEqual(a.demand(m,300)['estimated_demand_wan'],1500)
            self.assertTrue(0<=m['unallocated_mean_wan']<=10)

    def test_legacy_counts_and_pooled_curve_do_not_enter_funding(self):
        before=self.predict()['frozen_funds_yi']
        rows=copy.deepcopy(self.rows); pools=copy.deepcopy(self.pools)
        for r in rows:
            r['allocation_fit_json']=json.dumps({'buckets':[{'accounts':1e12,'threshold_amount_wan':1e8}]})
        for p in pools:
            p['account_pool_snapshot_json']='{}'
        self.assertEqual(before,self.predict(rows,pools)['frozen_funds_yi'])

    def test_invalid_counts_or_impossible_funding_are_rejected(self):
        r=self.source(); r['frozen_funds_yi']=.01
        self.assertEqual(a.distribution(r)['reason'],'no_conserving_amounts')
        r=self.source(); r['source_lot_distribution_json']=copy.deepcopy(r['source_lot_distribution_json'])
        r['source_lot_distribution_json']['cumulative_accounts'][2]=10
        self.assertEqual(a.distribution(r)['reason'],'announcement_constraints_failed')
        for p in (-1,2,float('nan')):
            with self.assertRaises(ValueError): a.distribution(self.source(),p)

    def test_snapshot_keeps_hidden_levels_before_pool_updates(self):
        r=row('a',1);r.update(online_issue_shares=14000,manual_ladder='1+0=10;2+1=25')
        points=builder._build_points_for_row(r)
        self.assertEqual(len(points),2)
        source=points[0]['_source_lot_distribution']
        self.assertEqual(set(source['cumulative_accounts']),{1,2,3})
        self.assertAlmostEqual(sum(source['cumulative_accounts'].values()),140)
        state={};touched=builder._update_threshold_state(state,points,'a')
        snapshot=builder._build_threshold_snapshot_row(r,points,state,touched)
        later=row('b',2);later['manual_ladder']='1+0=12;1+1=24'
        builder._update_threshold_state(state,builder._build_points_for_row(later,state),'b')
        self.assertEqual(snapshot['source_lot_distribution_json']['thresholds_by_lot'][1],10)

    def test_prefix_availability_is_enforced_in_inner_water_replays(self):
        rows=copy.deepcopy(self.rows)
        rows[0]['issue_result_date']='2026-07-08'
        result=self.predict(rows)
        self.assertEqual(result['estimate']['direct_funding']['basis'],'account_pool_funds')
        by_code={r['security_code']:r for r in rows}
        samples=result['estimate']['recent_market_level']['samples']
        self.assertTrue(samples)
        self.assertTrue(any(s['prediction_fallback_reason'] for s in samples))
        for s in samples:
            for code,available in s['prediction_distribution_dates'].items():
                self.assertLess(available,by_code[s['security_code']]['apply_date'])
        result=self.predict(rows,asof='2026-07-07')
        self.assertEqual(result['estimate']['direct_funding']['fallback_reason'],'insufficient_repaired_account_sources')

    def test_future_poison_does_not_change_prediction_or_ladders(self):
        expected=self.predict()
        future=row('future',10);future.update(issue_result_date='2026-07-13',frozen_funds_yi=1e9)
        pools=e.rebuild_scoped_pool(self.rows+[future])
        actual=self.predict(self.rows+[future],pools)
        self.assertEqual(expected['frozen_funds_yi'],actual['frozen_funds_yi'])
        self.assertEqual(expected['lot_thresholds'],actual['lot_thresholds'])

    def test_missing_rebuilt_distribution_is_explicit_fallback(self):
        pools=copy.deepcopy(self.pools)
        for p in pools: p.pop('source_lot_distribution_json')
        direct=self.predict(pools=pools)['estimate']['direct_funding']
        self.assertEqual(direct['basis'],'direct_funds')
        self.assertEqual(direct['fallback_reason'],'insufficient_repaired_account_sources')
        self.assertTrue(direct['excluded_account_sources'])

    def test_selection_cannot_use_unpublished_scores(self):
        rows=[row(str(i),i+1) for i in range(9)]
        by_code={r['security_code']:r for r in rows};by_code['8']['issue_result_date']='2026-08-01'
        def ds(error,last):
            return [{'security_code':r['security_code'],'replay_as_of_date':r['apply_date'],
                'actual_top_apply_below_guaranteed':False,'predicted_top_apply_below_guaranteed':False,
                'actual_frozen_funds_yi':100,'frozen_funds_pct_error':error if i<8 else last,
                'frozen_funds_abs_error_yi':100*(error if i<8 else last),'manual_ladder_errors':[]}
                for i,r in enumerate(rows)]
        candidates={'a':{'_family':'repaired_account'},'b':{'_family':'repaired_account'}}
        replays={k:{'details':ds(error,last)} for k,error,last in [('baseline',.1,.1),('a',.01,99),('b',.02,0)]}
        result=experiment.select(replays,candidates,'repaired_account',by_code,'2026-07-20','')
        self.assertEqual(result['candidate'],'a');self.assertNotIn('8',result['training_codes'])

    def original_params(self):
        return {**self.params,'subscription_prediction_funding_model':'multiple',
                'subscription_prediction_account_pool_prior_weight':1,
                'subscription_prediction_cap_factor_exponent':0,
                'subscription_prediction_issue_factor_exponent':0,
                'subscription_prediction_lock_factor_exponent':0,
                'subscription_prediction_multiple_scale':.5,
                'subscription_prediction_recent_market_level_adaptive_enabled':False,
                'subscription_prediction_similar_top_apply_frozen_enabled':False,
                'subscription_prediction_frozen_funds_floor_enabled':False,
                'subscription_prediction_frozen_funds_cap_enabled':False}

    def test_original_model_uses_repaired_prior_and_rebuilds_ladders(self):
        from unittest.mock import patch
        p=self.original_params()
        with patch.object(c,'_account_pool_demand_wan_from_row',side_effect=AssertionError('legacy counts')):
            result=c.build_prediction(c._target_from_row(self.target),history_rows=self.rows,params=p)
        self.assertTrue(result['account_pool_prior']['applied'])
        self.assertEqual(result['account_pool_prior']['account_count_basis'],'repaired_announcement_constrained')
        self.assertAlmostEqual(result['frozen_funds_yi'],.15)
        expected=c.build_prediction({**c._target_from_row(self.target),'ONLINE_VA_SHARES':result['valid_subscription_shares']},history_rows=self.rows,params=p)
        self.assertEqual(result['lot_thresholds'],expected['lot_thresholds'])

    def test_original_model_does_not_fall_back_to_compressed_prior(self):
        pools=copy.deepcopy(self.pools)
        for p in pools:p.pop('source_lot_distribution_json')
        p={**self.original_params(),'subscription_prediction_account_pool_rows':pools}
        result=c.build_prediction(c._target_from_row(self.target),history_rows=self.rows,params=p)
        self.assertFalse(result['account_pool_prior']['applied'])
        self.assertEqual(result['account_pool_prior']['rejected_reason'],'no_available_repaired_account_sources')
        expected=c.build_prediction(c._target_from_row(self.target),history_rows=self.rows,params={**p,'subscription_prediction_account_pool_prior_weight':0})
        self.assertEqual(result['frozen_funds_yi'],expected['frozen_funds_yi'])

    def test_original_prior_waits_for_entire_prefix_and_analysis_date(self):
        rows=copy.deepcopy(self.rows);rows[0]['issue_result_date']='2026-07-08'
        result=c.build_prediction(c._target_from_row(self.target),history_rows=rows,params=self.original_params(),as_of_date='2026-07-07')
        self.assertFalse(result['account_pool_prior']['applied'])
        self.assertEqual(result['account_pool_prior']['source_codes'],[])

    def test_original_prior_preserves_explicit_manual_target(self):
        p={**self.original_params(),'subscription_prediction_target_frozen_funds_code':'target',
           'subscription_prediction_target_frozen_funds_yi':.05}
        result=c.build_prediction(c._target_from_row(self.target),history_rows=self.rows,params=p)
        self.assertEqual(result['frozen_funds_yi'],.05)
        self.assertNotIn('account_pool_prior',result)

    def test_money_only_path_matches_full_forecasts_with_and_without_prior(self):
        for p in (self.params,self.original_params(),{**self.original_params(),'subscription_prediction_account_pool_prior_weight':0}):
            full=c.build_prediction(c._target_from_row(self.target),history_rows=self.rows,params=p)
            fast=c.build_prediction(c._target_from_row(self.target),history_rows=self.rows,params=p,funding_only=True)
            for key in ('frozen_funds_yi','valid_subscription_shares','guaranteed_threshold_amount_wan',
                        'top_apply_below_guaranteed','account_pool_prior'):
                self.assertEqual(full.get(key),fast.get(key),key)

    def test_optimizer_training_forecasts_ignore_poisoned_held_out_outcomes(self):
        import optimize_funding_families as optimize
        codes={r['security_code'] for r in self.rows[:6]}
        expected=optimize.fast_replay(self.rows,self.pools,self.original_params(),codes)
        rows=copy.deepcopy(self.rows)
        for r in rows[6:]:
            r['frozen_funds_yi']=1e9;r['online_valid_shares']=1e18;r['manual_ladder']='0+1=25'
        actual=optimize.fast_replay(rows,e.rebuild_scoped_pool(rows),self.original_params(),codes)
        self.assertEqual(expected,actual)


if __name__=='__main__': unittest.main(verbosity=2)
