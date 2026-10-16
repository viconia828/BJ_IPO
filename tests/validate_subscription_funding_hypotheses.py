from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'tools')]
import subscription_funding_hypotheses as h
import subscription_prediction_context as c
import subscription_predictor as p
import evaluate_subscription_funding_hypotheses as e
from validate_subscription_replay_timeline import row


class HypothesisTests(unittest.TestCase):
    def test_calendar_weekends_holidays_and_flags(self):
        f=h.calendar_features('2026-09-28')
        self.assertEqual(f['capital_return_proxy_date'],'2026-09-30')
        self.assertEqual(f['capital_occupation_days'],2)
        self.assertEqual((f['month_end_3'],f['quarter_end_3'],f['pre_long_holiday_3']),(1,1,1))
        self.assertEqual(h.calendar_features('2026-09-23')['capital_return_proxy_date'],'2026-09-28')
        self.assertEqual(h.calendar_features('2026-10-12')['post_long_holiday_5'],1)
        self.assertEqual(h.calendar_features('2026-10-14')['capital_return_proxy_date'],'2026-10-16')
        self.assertEqual(h.calendar_features('2026-10-09')['capital_return_proxy_date'],'2026-10-13')
        for invalid in ('2025-09-28','2026-10-10','2026-12-31'):
            with self.assertRaises(ValueError):
                h.calendar_features(invalid)

    def test_lock_proxy_preserves_result_information_date(self):
        r={'APPLY_DATE':'2026-09-28','ISSUE_RESULT_DATE':'2026-10-08'}
        changed=h.with_capital_return_proxy(r)
        self.assertEqual(r['ISSUE_RESULT_DATE'],changed['ISSUE_RESULT_DATE'])
        self.assertEqual(p._resolve_lock_days(r)[0],10)
        self.assertEqual(p._resolve_lock_days(changed)[0],2)
        with self.assertRaises(ValueError):
            p._resolve_lock_days({**r,'CAPITAL_OCCUPATION_DAYS':0})

    def bucket_row(self):
        return {'security_code':'example','top_apply_amount_wan':100,'issue_price':10,
                'frozen_funds_yi':.08,'allocation_fit_json':{'buckets':[
                    {'accounts':10,'threshold_amount_wan':40,'allocated_lots':1}],
                    'unallocated_accounts':10,'unallocated_cap_shares':40000,'fit_confidence':.7}}

    def test_bounded_means_conserve_and_transfer_below_cap(self):
        r=self.bucket_row()
        means=[]
        for position in (0,.5,1):
            model=h.conserving_buckets(r,position)
            self.assertTrue(model['available'])
            self.assertLessEqual(model['unallocated_mean_wan'],model['unallocated_cap_wan'])
            means.append(model['unallocated_mean_wan'])
            self.assertAlmostEqual(h.conserving_demand(r,100,position)['estimated_demand_wan'],800)
            self.assertLessEqual(h.conserving_demand(r,30,position)['estimated_demand_wan'],20*30)
        self.assertGreater(means[0],means[1])
        self.assertGreater(means[1],means[2])

    def test_infeasible_buckets_are_rejected(self):
        r=self.bucket_row()
        for total in (.001,1.0):
            self.assertFalse(h.conserving_buckets({**r,'frozen_funds_yi':total},.5)['available'])
        with self.assertRaises(ValueError):
            h.conserving_buckets(r,1.1)

    def test_future_residuals_and_target_labels_do_not_change_environment(self):
        rows={str(i):row(str(i),f'2026-06-{i+1:02d}',f'2026-06-{i+2:02d}') for i in range(1,10)}
        # All dates must be exchange days for features; use verified July dates.
        days=[d.isoformat() for d in h.TRADING_DAYS if d.month==7][:9]
        for i,r in enumerate(rows.values()):
            r['apply_date']=r['issue_result_date']=days[i]
        target=row('target','2026-07-20','2026-07-23')
        details=[{'security_code':k,'actual_frozen_funds_yi':110,'predicted_frozen_funds_yi':100} for k in rows]
        original=e.residual_correction(details,rows,target,'2026-07-20',h.FEATURE_NAMES)
        future=row('future','2026-07-17','2026-07-20')
        changed={**copy.deepcopy(rows),'future':future,'target':target}
        poison=details+[{'security_code':k,'actual_frozen_funds_yi':1e15,'predicted_frozen_funds_yi':1} for k in ('future','target')]
        actual=e.residual_correction(poison,changed,target,'2026-07-20',h.FEATURE_NAMES)
        self.assertEqual(original,actual)
        self.assertLessEqual(actual['factor'],1.1)

    def test_all_available_history_scenarios_conserve(self):
        rows=c.load_history_rows()
        available=0
        for r in rows:
            for pos in (0,.5,1):
                model=h.conserving_buckets(r,pos)
                if not model['available']:
                    continue
                available+=1
                demand=h.conserving_demand(r,float(r['top_apply_amount_wan']),pos)
                self.assertAlmostEqual(demand['estimated_demand_wan']/1e4,float(r['frozen_funds_yi']),places=6)
        self.assertGreater(available,100)

    def test_shared_entry_options_keep_information_cutoff(self):
        rows=[]
        for i in range(1,4):
            r={**row(str(i),f'2026-07-0{i}',f'2026-07-0{i+3}'),**self.bucket_row(),
               'security_code':str(i),'manual_ladder':'','online_valid_shares':800000}
            rows.append(r)
        target=c._target_from_row({**rows[0],'security_code':'target','apply_date':'2026-07-10'})
        # Refund proxy is July 9, but outcome remains unavailable until July 13.
        future={**rows[0],'security_code':'future','apply_date':'2026-07-07',
                'issue_result_date':'2026-07-13','frozen_funds_yi':1e12,'online_valid_shares':1e20}
        for model in ('multiple','bucket_funds'):
            params={'subscription_prediction_funding_model':model,
                    'subscription_prediction_lock_days_basis':'t_plus_2_calendar_2026',
                    'subscription_prediction_bucket_amount_basis':'conserving_bounds',
                    'subscription_prediction_account_pool_rows':[],
                    'subscription_prediction_account_pool_prior_weight':0}
            original=c.build_prediction(target,history_rows=rows,params=params)
            changed=c.build_prediction(target,history_rows=rows+[future],params=params)
            self.assertTrue(original['available'])
            self.assertEqual(original['frozen_funds_yi'],changed['frozen_funds_yi'])
            self.assertEqual(original['lot_thresholds'],changed['lot_thresholds'])
            self.assertNotIn('future',changed['history_context']['history_source_codes'])
            if model=='bucket_funds':
                self.assertEqual(original['estimate']['direct_funding']['basis'],'bucket_funds')


if __name__=='__main__':
    unittest.main()
