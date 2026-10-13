"""Cycle symmetry, pre-listing boundaries and honest replay coverage."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code'), str(ROOT/'tools'), str(ROOT/'tests')]
import fundamental_anchor as fa
import quality_sentiment as qs
import config_loader
from evaluate_recent_fundamental_cases import aftermarket_proxy, metrics, clean
from validate_fundamental_anchor import review, quotes


def cycle_review():
    r=review()
    for c in [r['issuer']] + r['peers']:
        annual=deepcopy(c['records'][0])
        for period,profit in [('2023-12-31',-10),('2024-12-31',30)]:
            record=deepcopy(annual); record['period']=period
            for metric in fa.PROFITS: record['values'][metric]['value']=profit
            c['records'].append(record)
        for record in c['records']:
            record['values']['revenue']={'value':1000 if record['period'].endswith('12-31') else 600,
                                         'blocked':False,'evidence':['fixture']}
    return r


class RecentTests(unittest.TestCase):
    def test_cycle_keeps_losses_and_needs_three_consecutive_years(self):
        c=cycle_review()['issuer']
        p=fa.historical_margin_profit(c,'2026-06-30','adjusted_net_profit_parent','2026-10-14',reconstruction=True)
        self.assertAlmostEqual(p['median_margin'],.03)
        self.assertEqual(p['value'],30)
        self.assertFalse(p['normalization_approved'])
        c['records']=[r for r in c['records'] if r['period']!='2024-12-31']
        self.assertFalse(fa.historical_margin_profit(c,'2026-06-30','net_profit_parent','2026-10-14',reconstruction=True)['available'])

    def test_cycle_is_symmetric_not_forward_eps_times_ttm_pe(self):
        r=cycle_review()
        opts={'reconstruction':True,'stage':'cycle_reference'}
        a=fa.build_reviewed_anchor(r,quotes(),'2026-10-14',{},**opts)
        for c in [r['issuer']]+r['peers']:
            for record in c['records']:
                for metric in fa.PROFITS: record['values'][metric]['value']*=2
        b=fa.build_reviewed_anchor(r,quotes(),'2026-10-14',{},**opts)
        self.assertAlmostEqual(a['center'],b['center'])
        self.assertAlmostEqual(a['eps']*2,b['eps'])
        self.assertAlmostEqual(a['peer_pe']/2,b['peer_pe'])

    def test_cycle_cannot_be_live_or_active(self):
        r=cycle_review()
        self.assertFalse(fa.build_reviewed_anchor(r,quotes(),'2026-10-14',{},stage='cycle_reference')['available'])
        self.assertFalse(fa.build_reviewed_anchor(r,quotes(),'2026-10-14',{'quality_sentiment_mode':'active'},stage='cycle_reference',reconstruction=True)['available'])

    def test_reconstructed_cycle_still_rejects_future_or_conflicted_annuals(self):
        for key in ['future','conflict','currency']:
            c=cycle_review()['issuer']
            record=next(v for v in c['records'] if v['period']=='2024-12-31')
            if key=='future': record['published_at']='2026-10-14'
            if key=='conflict': record['values']['net_profit_parent']['blocked']=True
            if key=='currency': record['currency']='USD'
            self.assertFalse(fa.historical_margin_profit(c,'2026-06-30','net_profit_parent','2026-10-14',reconstruction=True)['available'],key)

    def test_aftermarket_proxy_never_uses_target_or_future_feedback(self):
        target={'SECURITY_CODE':'T','LISTING_DATE':'2026-09-04'}
        def item(code,listing,observed):
            return {'SECURITY_CODE':code,'LISTING_DATE':listing,'AVERAGE_PRICE':10,'CLOSE_PRICE':10,
                    'NEXT_TRADE_DATE':observed,'NEXT_DAY_CLOSE':5}
        past=[item('A','2026-09-01','2026-09-02'),item('B','2026-09-02','2026-09-03')]
        self.assertTrue(aftermarket_proxy(target,past)['weak'])
        past[1]['NEXT_TRADE_DATE']='2026-09-04'
        self.assertFalse(aftermarket_proxy(target,past+[item('T','2026-09-04','2026-09-05')])['weak'])

    def test_frozen_cohort_keeps_unavailable_cases_and_common_sample(self):
        r=json.loads((ROOT/'data/fundamental_cases/recent_replay.json').read_text('utf-8'))
        protocol=json.loads((ROOT/'data/fundamental_cases/recent_protocol.json').read_text('utf-8'))
        self.assertEqual([v['code'] for v in r['rows']],protocol['codes'])
        self.assertEqual(len(r['rows']),10)
        century=next(v for v in r['rows'] if v['code']=='920229')
        self.assertFalse(century['models']['ttm_expanded']['available'])
        common=[v for v in r['rows'] if v['models']['proxy']['available'] and v['models']['ttm_expanded']['available']]
        self.assertEqual(r['common_proxy_ttm_codes'],[v['code'] for v in common])
        for v in ['proxy','ttm_expanded']:
            self.assertEqual(metrics(common,v),r['common_proxy_ttm'][v])

    def test_actual_and_forecast_guidance_cannot_change_fundamental(self):
        case=json.loads((ROOT/'data/fundamental_cases/cases/920025.json').read_text('utf-8'))
        target=json.loads((ROOT/'data/fundamental_cases/inputs/920025.json').read_text('utf-8'))
        params=config_loader.load_params()
        target.update(fundamental_case=case,_fundamental_reconstruction=True)
        a=qs.build_anchor(target,target['LISTING_DATE'],params)
        target['AVERAGE_PRICE']=999999; target['fundamental_case']['forecast']['adjusted_profit']=999999999
        b=qs.build_anchor(target,target['LISTING_DATE'],params)
        self.assertEqual(a,b)


if __name__=='__main__': unittest.main()
