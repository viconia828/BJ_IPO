"""Behavior, time boundaries, and price ownership for the feedback experiment."""
from copy import deepcopy
from datetime import date, timedelta
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code'),str(ROOT/'tools'),str(ROOT/'tests')]
import quality_sentiment as qs
import sentiment_pricing as pricing
import report_generator
from evaluate_sentiment_pricing import metrics
from validate_quality_sentiment import issuer, PARAMS
from validate_report_dual_valuation_ranges import _payload


class SentimentPricingTests(unittest.TestCase):
    def setUp(self):
        self.history = [issuer(f'910{i:03}', date(2026,1,1)+timedelta(days=i*3)) for i in range(20)]
        self.target = issuer('920999',date(2026,3,10))
        self.cutoff = '2026-03-10'
        for r in self.history:
            for prefix,days,ratio in [('NEXT',1,.8),('THIRD',2,.6)]:
                r[prefix+'_TRADE_DATE'] = str(qs.day(r['LISTING_DATE'])+timedelta(days=days))
                r[prefix+'_DAY_CLOSE'] = r['AVERAGE_PRICE']*ratio

    def run_model(self,history=None,target=None,params=None):
        return pricing.evaluate_all(target or self.target, self.history if history is None else history,
                                    params or PARAMS, as_of=self.cutoff)

    def test_target_same_day_and_future_cannot_change_any_variant(self):
        before = self.run_model()
        target = {**self.target,'AVERAGE_PRICE':999999,'CLOSE_PRICE':1,
                  'NEXT_TRADE_DATE':'2026-03-11','NEXT_DAY_CLOSE':999999}
        history = self.history+[target,issuer('930001',date(2026,3,10),actual=999999),
                                issuer('930002',date(2026,3,11),actual=1)]
        self.assertEqual(before,self.run_model(history,target))

    def test_constant_environment_does_not_repeatedly_penalize_post_ipo_discount(self):
        result = self.run_model()
        for rule in pricing.RULES:
            self.assertAlmostEqual(result[rule]['sentiment']['center'],30)
        self.assertTrue(result['combined']['pricing_response']['signals']['aftermarket']['available'])

    def test_post_horizons_are_matched_and_future_observation_is_masked(self):
        history = deepcopy(self.history)
        for r in history[-3:]:
            r['THIRD_TRADE_DATE']=self.cutoff
            r['THIRD_DAY_CLOSE']=999999
        result = self.run_model(history)['aftermarket']
        self.assertAlmostEqual(result['pricing_response']['log_correction'],0)
        history[-1]['THIRD_TRADE_DATE']='2026-03-11'
        self.assertEqual(result,self.run_model(history)['aftermarket'])

    def test_worse_and_better_post_returns_move_prices_in_both_directions(self):
        prices=[]
        for factor in [.7,1,1.3]:
            history=deepcopy(self.history)
            for r in history[-3:]:
                for prefix in ['NEXT','THIRD']: r[prefix+'_DAY_CLOSE']*=factor
            prices.append(self.run_model(history)['aftermarket']['sentiment']['center'])
        self.assertLess(prices[0],prices[1]); self.assertGreater(prices[2],prices[1])

    def test_expectation_misses_and_beats_change_price_symmetrically(self):
        values=[]
        for actual in [20,30,45]:
            history=deepcopy(self.history)
            for r in history[-3:]: r['AVERAGE_PRICE']=actual; r['CLOSE_PRICE']=actual
            result=self.run_model(history)['expectation']
            values.append(result['pricing_response']['log_correction'])
        self.assertLess(values[0],0); self.assertAlmostEqual(values[1],0); self.assertGreater(values[2],0)

    def test_missing_recent_f_does_not_pull_old_forecast_errors_into_window(self):
        history=deepcopy(self.history)
        for r in history[-3:]: r['fundamental_review']={}
        result=self.run_model(history)['combined']
        signals=result['pricing_response']['signals']
        self.assertFalse(signals['expectation']['available'])
        self.assertEqual(signals['expectation']['log_correction'],0)
        self.assertTrue(signals['aftermarket']['available'])

    def test_same_day_groups_do_not_fake_multiple_independent_feedback_dates(self):
        observations=[{'listing_date':'2026-01-01','value':.3}]*20
        result=pricing.grouped_signal(observations,['2026-01-01','2026-01-02','2026-01-03'])
        self.assertEqual(result['group_count'],1)
        self.assertFalse(result['available'])
        self.assertEqual(result['log_correction'],0)

    def test_order_and_identical_duplicate_do_not_change_prices(self):
        result=self.run_model()
        self.assertEqual(result,self.run_model(list(reversed(self.history))))
        self.assertEqual(result,self.run_model(self.history+[deepcopy(self.history[-1])]))
        bad={**self.history[-1],'AVERAGE_PRICE':99999}
        with self.assertRaises(ValueError): self.run_model(self.history+[bad])

    def test_missing_baseline_does_not_create_a_price(self):
        target={**self.target,'fundamental_review':{}}
        for r in self.run_model(target=target).values():
            self.assertFalse(r['available']); self.assertIsNone(r['sentiment'].get('center'))

    def test_response_bounded_and_interval_width_and_f_unchanged(self):
        history=deepcopy(self.history)
        for r in history[-3:]:
            r['AVERAGE_PRICE']=30000
            for prefix in ['NEXT','THIRD']: r[prefix+'_DAY_CLOSE']=3000000
        result=self.run_model(history)
        for rule in pricing.RULES:
            r=result[rule]; s=r['sentiment']; b=result['baseline']['sentiment']['center']
            self.assertGreaterEqual(s['center']/b,.8-1e-12); self.assertLessEqual(s['center']/b,1.25+1e-12)
            self.assertAlmostEqual(s['low']/s['center'],.9); self.assertAlmostEqual(s['high']/s['center'],1.1)
            self.assertEqual(r['anchor'],result['baseline']['anchor'])

    def test_no_new_observations_no_artificial_time_decay_in_correction(self):
        a=self.run_model()['combined']
        b=pricing.evaluate(self.target,self.history,PARAMS,as_of='2026-03-17')
        self.assertAlmostEqual(a['sentiment']['center'],b['sentiment']['center'])
        self.assertEqual(a['pricing_response']['log_correction'],b['pricing_response']['log_correction'])

    def test_shadow_attachment_cannot_take_formal_price_even_in_active_baseline_mode(self):
        history=deepcopy(self.history)
        for r in history[-3:]: r['AVERAGE_PRICE']=20
        params={**PARAMS,'quality_sentiment_mode':'active'}
        before=qs.evaluate(self.target,history,params,as_of=self.cutoff)
        frozen=deepcopy(before)
        result=pricing.attach_shadow(before,self.target,history,params,as_of=self.cutoff)
        self.assertEqual(before,frozen)
        self.assertEqual(result['sentiment'],before['sentiment'])
        self.assertFalse(result['response_shadow']['formal_eligible'])
        self.assertNotEqual(result['sentiment']['center'],result['response_shadow']['sentiment']['center'])
        final=qs.attach({'target_price':999},result)
        self.assertEqual(final['target_price'],before['sentiment']['center'])

    def test_report_discloses_shadow_and_unavailable_signal(self):
        baseline=qs.evaluate(self.target,self.history,PARAMS,as_of=self.cutoff)
        result=pricing.attach_shadow(baseline,self.target,self.history,PARAMS,as_of=self.cutoff)
        payload=_payload(); old=deepcopy(payload['final'])
        payload['final']=qs.attach(old,result)
        for content in [report_generator.build_report_markdown(payload),
                        report_generator._build_report_html(report_generator._prepare_report_context(payload))]:
            self.assertIn('情绪反馈试算区间（影子）',content)
            self.assertIn('尚未用于正式定价',content)
        self.assertEqual(payload['final']['target_price'],old['target_price'])

    def test_invalid_shadow_input_preserves_baseline_and_reports_failure(self):
        baseline=qs.evaluate(self.target,self.history,PARAMS,as_of=self.cutoff)
        history=self.history+[{**self.history[-1],'AVERAGE_PRICE':999999}]
        result=pricing.attach_shadow(baseline,self.target,history,PARAMS,as_of=self.cutoff)
        self.assertEqual(result['sentiment'],baseline['sentiment'])
        self.assertFalse(result['response_shadow']['available'])
        self.assertIn('conflicting duplicate',result['response_shadow']['sentiment']['reason'])

    def test_archived_metrics_keep_all_cases_and_same_coverage(self):
        output=json.loads((ROOT/'data/sentiment_pricing/replay.json').read_text('utf-8'))
        self.assertEqual(output['frozen_input_count'],len(output['rows'])+len(output['unscored']))
        self.assertFalse(output['formal_promotion']); self.assertFalse(output['parameter_search'])
        for rule in ['baseline',*pricing.RULES]:
            self.assertEqual(metrics(output['rows'],rule),output['metrics']['all'][rule])
        for row in output['rows']:
            for rule in pricing.RULES:
                self.assertEqual(row['models'][rule]['available'],row['models']['baseline']['available'])


if __name__ == '__main__': unittest.main()
