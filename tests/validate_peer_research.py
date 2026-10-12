"""Deterministic financial and valuation-boundary tests; no network required."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import sys
import unittest
import tempfile
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'code'))
from peer_research import company_diagnostics, growth, pair_diagnostics, validate_valuation_basis
from peer_fundamentals import parse_mcp, visible_record
from peer_fundamentals import attach_cached_fundamentals
sys.path.insert(0, str(ROOT/'tools'))
from collect_peer_fundamentals import compare_independent

PERIODS = [f'{y}1231' for y in range(2019,2026)] + ['20250630','20260630']


def fixture():
    result = []
    for period in PERIODS:
        iso = f'{period[:4]}-{period[4:6]}-{period[6:]}'
        result.append({'code':'600001.SH','report_period':iso,
            'published_at':f'{int(period[:4])+1}-03-01' if period.endswith('1231') else f'{period[:4]}-08-20',
            'retrieved_at':'2026-10-12T12:00:00+08:00','statement_scope':'consolidated','period_basis':'YTD',
            'historical_pit_eligible':False,'conflicts':[],
            'values':{k:{'value':v,'conflict':False,'rounding_tolerance':.005,'evidence':'fixture'} for k,v in {
                'revenue':1000.,'net_profit_parent':100.,'adjusted_net_profit_parent':90.,
                'operating_cash_flow':95.,'gross_margin_pct':30.,'reported_revenue_yoy_pct':0.,
                'reported_profit_yoy_pct':0.,'accounts_receivable':100.,'inventory':80.}.items()}})
    return result


def run(records):
    return company_diagnostics(records, [], '2026-10-13', PERIODS)


def status(result, rule):
    return next(c['status'] for c in result['checks'] if c['rule']==rule)


class Checks(unittest.TestCase):
    def test_future_crosscheck_cannot_inherit_older_collection_time(self):
        financial=[fixture()[-1]]
        checks=[{'code':'600001.SH','period':'20260630','row':{'revenue':1000.,'ann_date':'20260820'},
                 'evidence':'ts','retrieved_at':'2026-10-13T01:00:00+08:00'}]
        compare_independent(financial,checks)
        self.assertFalse(visible_record(financial[0],'2026-10-13'))
        self.assertTrue(visible_record(financial[0],'2026-10-14'))

    def test_only_verified_matching_source_precision_resolves_rounding(self):
        def financial():
            return [{'code':'600001.SH','report_period':'2026-06-30','published_at':'2026-08-20','conflicts':[],
                     'values':{'revenue':{'value':1000032.29,'rounding_tolerance':.005,'evidence':'api','conflict':False}}}]
        checks=[{'code':'600001.SH','period':'20260630','row':{'revenue':1000000.,'ann_date':'20260820'},'evidence':'ts'}]
        self.assertFalse(compare_independent(financial(),checks)[0]['consistent'])
        review={'code':'600001.SH','report_period':'2026-06-30','metric':'revenue','verified':True,
                'unit':'万元','decimals':2,'display_value_cny':1000000.}
        self.assertTrue(compare_independent(financial(),checks,[review])[0]['consistent'])
        self.assertFalse(compare_independent(financial(),checks,[{**review,'report_period':'2025-06-30'}])[0]['consistent'])
        self.assertFalse(compare_independent(financial(),checks,[{**review,'verified':False}])[0]['consistent'])
        self.assertFalse(compare_independent(financial(),checks,[{**review,'display_value_cny':999900.}])[0]['consistent'])
        larger=financial(); larger[0]['values']['revenue']['value']=1000600
        self.assertFalse(compare_independent(larger,checks,[review])[0]['consistent'])

    def test_broad_cache_is_read_only_and_hides_future_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); folder=root/'data/peer_research'; papers=folder/'workpapers/companies'
            papers.mkdir(parents=True)
            (folder/'catalog.json').write_text(json.dumps({'companies':{'600001.SH':{}}}),'utf-8')
            company={'financials':fixture(),'profiles':[], 'diagnostics':{'as_of':'2026-10-14','checks':[{'reason':'future'}]}}
            (papers/'600001.SH.json').write_text(json.dumps(company),'utf-8')
            rows,summary=attach_cached_fundamentals([{'code':'600001.SH','pe_ttm':25,'price':10}],root,as_of='2026-10-13')
            self.assertEqual(rows[0]['pe_ttm'],25);self.assertEqual(rows[0]['price'],10)
            self.assertEqual(rows[0]['fundamental_evidence']['review_checks'],[])
            self.assertEqual(summary['attached'],1)
            self.assertIn('peer_research',summary['catalog'])

    def test_constant_series_and_three_year_cash(self):
        result=run(fixture())
        self.assertEqual(result['ttm']['values']['net_profit_parent'],100)
        self.assertEqual(result['cagr_three_year_pct']['revenue'],0)
        self.assertEqual(result['cash_conversion_three_year'],.95)
        self.assertEqual(result['cycle']['illustrative_profit_at_median_margin'],100)
        self.assertEqual(status(result,'C02'),'pass')
        self.assertFalse(result['pricing_eligible'])
        self.assertEqual(status(result,'T01'),'blocked')

    def test_negative_and_zero_bases_never_produce_growth_rates(self):
        self.assertEqual(growth(10,-5),{'value_pct':None,'status':'loss_to_profit'})
        self.assertIsNone(growth(10,0)['value_pct'])
        self.assertEqual(growth(-10,5)['status'],'profit_to_loss')

    def test_low_base_profit_growth_is_flagged(self):
        records=fixture()
        prior=next(r for r in records if r['report_period']=='2025-06-30')
        prior['values']['adjusted_net_profit_parent']['value']=1
        self.assertEqual(status(run(records),'G01'),'review')

    def test_restatements_block_ttm(self):
        records=fixture()
        records[-1]['values']['reported_revenue_yoy_pct']['value']=10
        result=run(records)
        self.assertEqual(status(result,'D03'),'blocked')
        self.assertFalse(result['ttm']['available'])
        self.assertIsNone(result['cycle']['illustrative_profit_at_median_margin'])

    def test_absent_reported_growth_is_not_a_pass(self):
        records=fixture()
        records[-1]['values'].pop('reported_revenue_yoy_pct')
        self.assertEqual(status(run(records),'D03'),'unknown')

    def test_conflicted_profit_does_not_enter_ttm(self):
        records=fixture()
        records[-1]['values']['net_profit_parent']['conflict']=True
        records[-1]['conflicts']=['net_profit_parent']
        result=run(records)
        self.assertNotIn('net_profit_parent',result['ttm']['values'])
        self.assertEqual(status(result,'D02'),'blocked')

    def test_future_or_uncollected_report_is_not_visible(self):
        for field in ['published_at','retrieved_at']:
            records=fixture()
            records[-1][field]='2026-10-13'
            result=run(records)
            self.assertEqual(status(result,'D01'),'blocked')
            self.assertEqual(result['growth_yoy']['revenue']['status'],'missing')

    def test_annual_hole_cannot_be_filled_by_older_year(self):
        records=[r for r in fixture() if r['report_period']!='2023-12-31']
        result=run(records)
        self.assertEqual(status(result,'C01'),'unknown')
        self.assertIsNone(result['cash_conversion_three_year'])
        self.assertIsNone(result['cycle']['illustrative_profit_at_median_margin'])

    def test_nonrecurring_and_working_capital_flags(self):
        records=fixture()
        records[-1]['values']['adjusted_net_profit_parent']['value']=50
        records[-1]['values']['inventory']['value']=160
        result=run(records)
        self.assertEqual(status(result,'G03'),'review')
        self.assertEqual(status(result,'G05'),'review')

    def test_loss_peer_retained_and_shared_words_never_core(self):
        records=fixture()
        records[-1]['values']['net_profit_parent']['value']=-10
        issuer={'code':'920001.BJ','name':'issuer','profiles':[{'business':'汽车铝合金压铸','evidence':'issuer'}],
                'financials':fixture(),'diagnostics':run(fixture())}
        peer={'code':'600001.SH','name':'peer','profiles':[{'business':'汽车铝合金压铸','evidence':'peer'}],
              'financials':records,'diagnostics':run(records)}
        result=pair_diagnostics(issuer,peer)
        self.assertTrue(result['lossmaking_peer_retained'])
        self.assertFalse(result['same_market'])
        self.assertFalse(result['core_eligible'])
        self.assertIn('压铸',result['shared_product_terms'])

    def test_matching_valuation_denominators(self):
        descriptor={'profit_metric':'parent','period':'20260630','basis':'ttm','scope':'consolidated','currency':'CNY',
                    'share_basis':'post_issue_diluted','profit_value':100,'published_at':'2026-08-20',
                    'retrieved_at':'2026-10-12','valuation_date':'2026-10-12'}
        self.assertTrue(validate_valuation_basis(descriptor,descriptor,'2026-10-13')['eligible'])
        for field,value in [('basis','forward'),('profit_metric','adjusted'),('period','20251231'),('currency','USD'),('scope','parent_only')]:
            self.assertFalse(validate_valuation_basis(descriptor,{**descriptor,field:value},'2026-10-13')['eligible'])
        for change in [{'share_basis':'weighted_average'},{'profit_value':-1},{'retrieved_at':'2026-10-13'},
                       {'basis':'normalized'},{'basis':'forward'}]:
            altered={**descriptor,**change}
            self.assertFalse(validate_valuation_basis(altered,altered,'2026-10-13')['eligible'])

    def test_balance_cells_are_instant_not_cumulative(self):
        import json
        data={'code':1,'data':{'answer':'|证券代码|日期|存货（单位：元）|\n|---|---|---|\n|600001.SH|20260630|2亿|',
             'indicators_params':{'存货':{'报告期':'20260630','报表类型':'合并报表'}}}}
        result=parse_mcp({'retrieved_at':'2026-10-12','result':{'content':[{'type':'text','text':json.dumps(data)}]}},'fixture')
        value=result['financials'][0]['values']['inventory']
        self.assertEqual(value['value'],200000000)
        self.assertEqual(value['period_basis'],'instant')


if __name__=='__main__':
    unittest.main()
