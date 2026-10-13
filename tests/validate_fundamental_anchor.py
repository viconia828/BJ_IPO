"""Financial denominator, time, broad matching and dilution invariants."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
import fundamental_anchor as fa
import quality_sentiment as qs
import quality_sentiment_data as qd


def company(code, parent=(100, 30, 40), adjusted=(80, 20, 30)):
    records = []
    for i, period in enumerate(['2025-12-31', '2025-06-30', '2026-06-30']):
        records.append({'period': period, 'currency': 'CNY', 'scope': 'consolidated',
                        'period_basis': 'FY' if i == 0 else 'YTD', 'published_at': '2026-08-20',
                        'available_not_before': '2026-10-13',
                        'values': {key: {'value': values[i], 'evidence': ['fixture'], 'blocked': False}
                                   for key, values in [('net_profit_parent', parent), ('adjusted_net_profit_parent', adjusted)]}})
    return {'code': code, 'records': records}


def review():
    peers = [{**company(code), 'match': 'direct', 'reason': 'same manufacturing business', 'match_evidence': ['fixture']}
             for code in ['600001.SH', '600002.SH', '920001.BJ']]
    return {'schema': fa.VERSION, 'available_not_before': '2026-10-13', 'excludes_ipo_sentiment': True,
            'earnings_end': '2026-06-30', 'issuer': company('920999.BJ'), 'peers': peers,
            'shares': {'post_issue_base': 10, 'post_issue_if_greenshoe': 11, 'basis': 'post_issue_diluted', 'evidence': ['fixture']}}


def quotes():
    return [{'code': code, 'trade_date': '20261012', 'mkt_cap': cap / 1e8, 'pe_ttm': 9999}
            for code, cap in [('600001.SH', 1100), ('600002.SH', 2200), ('920001.BJ', 3300)]]


class FundamentalTests(unittest.TestCase):
    def build(self, obj=None, data=None, **kwargs):
        return fa.build_reviewed_anchor(obj or review(), data or quotes(), '2026-10-14', {'bse_discount_factor': .55}, **kwargs)

    def test_ttm_is_ytd_bridge_not_half_year_times_two(self):
        r = fa.ttm(company('A'), '2026-06-30', 'net_profit_parent', '2026-10-14')
        self.assertEqual(r['value'], 110)
        self.assertEqual(len(r['components']), 3)

    def test_same_basis_recalculates_pe_and_post_issue_eps(self):
        r = self.build()
        self.assertEqual(r['eps'], 9)
        self.assertAlmostEqual(r['center'], 121)
        self.assertAlmostEqual(r['peer_details'][0]['metrics']['net_profit_parent']['pe'], 10)
        self.assertAlmostEqual(r['peer_details'][0]['metrics']['adjusted_net_profit_parent']['pe'], 1100/90)

    def test_bse_reference_does_not_receive_second_discount(self):
        r = self.build()
        self.assertEqual(r['peer_details'][2]['market_factor'], 1)
        self.assertEqual(r['peer_details'][0]['market_factor'], .55)

    def test_greenshoe_only_dilutes_scenario_not_base(self):
        r = self.build()
        self.assertAlmostEqual(r['scenarios']['adjusted_net_profit_parent']['low'], 55)
        other = review(); other['shares']['post_issue_if_greenshoe'] = 10
        self.assertEqual(r['center'], self.build(other)['center'])
        self.assertLess(r['low'], self.build(other)['low'])

    def test_current_review_cannot_be_backdated(self):
        self.assertFalse(fa.build_reviewed_anchor(review(), quotes(), '2026-10-13', {})['available'])
        self.assertTrue(fa.build_reviewed_anchor(review(), quotes(), '2026-10-13', {}, reconstruction=True)['available'])

    def test_reconstruction_still_rejects_future_financial_publication(self):
        r = review(); r['issuer']['records'][-1]['published_at'] = '2026-10-14'
        self.assertFalse(self.build(r, reconstruction=True)['available'])

    def test_missing_conflicted_or_quarter_profit_is_not_zero(self):
        for change in ['missing', 'conflict', 'quarter', 'currency', 'scope']:
            r = review(); record = r['issuer']['records'][-1]
            if change == 'missing': del record['values']['adjusted_net_profit_parent']
            if change == 'conflict': record['values']['adjusted_net_profit_parent']['blocked'] = True
            if change == 'quarter': record['period_basis'] = 'quarter'
            if change == 'currency': record['currency'] = 'USD'
            if change == 'scope': record['scope'] = 'parent_only'
            self.assertFalse(self.build(r)['available'], change)

    def test_loss_peer_retained_but_never_gets_a_positive_pe(self):
        r = review(); r['peers'][0]['records'][0]['values']['adjusted_net_profit_parent']['value'] = -999
        result = self.build(r)
        self.assertTrue(result['available'])
        self.assertEqual(len(result['peer_details']), 3)
        self.assertEqual(result['coverage']['usable'], 2)
        self.assertFalse(result['peer_details'][0]['metrics']['adjusted_net_profit_parent']['available'])

    def test_one_adjusted_peer_does_not_silently_use_parent_pe(self):
        r = review()
        for p in r['peers'][:2]: p['records'][0]['values']['adjusted_net_profit_parent']['value'] = -999
        result = self.build(r)
        self.assertFalse(result['available'])
        self.assertTrue(result['scenarios']['net_profit_parent']['available'])

    def test_related_and_channel_peers_remain_in_wide_pool(self):
        r = review(); r['peers'][1]['match'] = 'related'; r['peers'][2]['match'] = 'channel'
        result = self.build(r)
        self.assertEqual(result['coverage']['usable'], 3)
        self.assertEqual([p['weight'] for p in result['peer_details']], [1, .5, .25])

    def test_duplicate_peers_and_stale_or_future_quotes_fail(self):
        r = review(); r['peers'].append(deepcopy(r['peers'][0]))
        self.assertFalse(self.build(r)['available'])
        for d in ['20261014', '20260801']:
            q = quotes()
            for p in q: p['trade_date'] = d
            self.assertFalse(self.build(data=q)['available'])

    def test_duplicate_quotes_do_not_change_anchor(self):
        self.assertEqual(self.build()['center'], self.build(data=quotes()+quotes())['center'])

    def test_no_arbitrary_quality_factor_or_actual_price(self):
        r = review(); r['quality_factor'] = 100; r['AVERAGE_PRICE'] = 10000
        self.assertEqual(self.build()['center'], self.build(r)['center'])

    def test_normalization_requires_both_sides_and_recovery_review(self):
        r = review()
        plan = {'reviewed': True, 'available_not_before': '2026-10-13', 'business_scope_reason': 'stable scope',
                'financial_end': '2026-06-30',
                'currency': 'CNY', 'scope': 'consolidated',
                'sustainable_margin_reason': 'audited cycle', 'recovery_reason': 'already sustainable',
                'evidence': ['fixture'], 'recovery_years': 0, 'sustainable_revenue': 1000, 'parent_net_margin': .1}
        r['issuer']['normalization'] = plan
        self.assertFalse(self.build(r, stage='normalized')['available'])
        for peer in r['peers']: peer['normalization'] = deepcopy(plan)
        result = self.build(r, stage='normalized')
        self.assertTrue(result['available'])
        self.assertAlmostEqual(result['peer_details'][0]['metrics']['net_profit_parent']['pe'], 11)
        r['issuer']['normalization']['recovery_years'] = 2
        self.assertFalse(self.build(r, stage='normalized')['available'])

    def test_manual_review_does_not_mean_perfect_confidence(self):
        self.assertLess(self.build()['confidence'], 1)

    def test_legacy_uniform_discount_only_in_research(self):
        with self.assertRaises(ValueError): self.build(market_policy='legacy_uniform')

    def test_reconstruction_cannot_be_promoted_to_active_pricing(self):
        result = fa.build_reviewed_anchor(review(), quotes(), '2026-10-14',
                    {'quality_sentiment_mode': 'active'}, reconstruction=True)
        self.assertFalse(result['available'])

    def test_loaded_four_cases_preserve_past_and_enable_current_shadow(self):
        history, _ = qd.load_history()
        for item in history:
            if item['SECURITY_CODE'] not in {'920202', '920157', '920025'}: continue
            before = {k: v for k, v in item.items() if k != 'fundamental_case'}
            self.assertEqual(qs.build_anchor(item, item['LISTING_DATE'], {}), qs.build_anchor(before, item['LISTING_DATE'], {}))
        item = json.loads((ROOT / 'data/fundamental_cases/inputs/920285.json').read_text('utf-8'))
        target = qd.with_fundamental_review(item)
        result = qs.build_anchor(target, '2026-10-14', {'bse_discount_factor': .55})
        self.assertEqual(result['basis'], fa.VERSION)
        self.assertTrue(result['available'])


if __name__ == '__main__':
    unittest.main()
