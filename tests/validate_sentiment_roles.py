"""Role separation, turn confirmation and unchanged accepted price ownership."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'tools'),str(ROOT/'tests')]
import quality_sentiment as qs
import sentiment_pricing as previous
import sentiment_roles as roles
import sentiment_timing as timing
import report_generator
from evaluate_sentiment_pricing import metrics
from validate_quality_sentiment import issuer,PARAMS
from validate_report_dual_valuation_ranges import _payload


def signals(values,aftermarket=.2):
    dates=['2026-01-01','2026-01-02','2026-01-03']
    observations=[{'listing_date':d,'value':v,'code':str(i)} for i,(d,v) in enumerate(zip(dates,values)) if v is not None]
    return {'recent_listing_dates':dates,'expectation':previous.grouped_signal(observations,dates),
            'aftermarket':previous.grouped_signal([{'listing_date':d,'value':aftermarket} for d in dates],dates)}


class SentimentRolesTests(unittest.TestCase):
    def setUp(self):
        self.history=[issuer(f'910{i:03}',date(2026,1,1)+timedelta(days=i*3)) for i in range(20)]
        self.target=issuer('920999',date(2026,3,10))
        for row in self.history:
            for prefix,days,ratio in [('NEXT',1,.8),('THIRD',2,.6)]:
                row[prefix+'_TRADE_DATE']=str(qs.day(row['LISTING_DATE'])+timedelta(days=days))
                row[prefix+'_DAY_CLOSE']=row['AVERAGE_PRICE']*ratio

    def test_conflicting_aftermarket_does_not_reverse_either_direction(self):
        for sign in [-1,1]:
            result=roles.response(signals([sign*.2]*3,aftermarket=-sign*.6))
            self.assertEqual(result['agreement'],'conflict')
            self.assertEqual(result['aftermarket_log_correction'],0)
            self.assertAlmostEqual(result['log_correction'],sign*.1)

    def test_confirmation_never_exceeds_primary_expectation(self):
        for sign in [-1,1]:
            result=roles.response(signals([sign*.1]*3,aftermarket=sign*10))
            self.assertAlmostEqual(result['aftermarket_log_correction'],sign*.05)
            self.assertAlmostEqual(result['log_correction'],sign*.1)

    def test_missing_expectation_cannot_be_replaced_by_aftermarket(self):
        result=roles.response(signals([None,None,.2]))
        self.assertEqual(result['agreement'],'missing_expectation')
        self.assertEqual(result['log_correction'],0)

    def test_fast_mode_requires_two_actual_latest_groups_not_backfilled(self):
        self.assertTrue(timing.response(signals([None,-.3,-.2]))['timing']['fast_mode'])
        missing=timing.response(signals([-.3,-.2,None]))
        self.assertFalse(missing['timing']['fast_mode'])
        self.assertEqual(missing['log_correction'],roles.response(signals([-.3,-.2,None]))['log_correction'])

    def test_fast_mode_is_symmetric_and_retains_two_group_shrinkage(self):
        for sign in [-1,1]:
            result=timing.response(signals([-sign*.3,sign*.2,sign*.4],aftermarket=0))
            self.assertTrue(result['timing']['fast_mode'])
            self.assertAlmostEqual(result['expectation_log_correction'],sign*.3*.4)

    def test_latest_reversal_releases_fast_mode_immediately(self):
        before=signals([-.4,-.3,-.2],aftermarket=0)
        self.assertTrue(timing.response(before)['timing']['fast_mode'])
        after=signals([-.3,-.2,.4],aftermarket=0)
        self.assertFalse(timing.response(after)['timing']['fast_mode'])
        self.assertEqual(timing.response(after)['log_correction'],roles.response(after)['log_correction'])

    def test_small_same_direction_changes_do_not_activate_fast_mode(self):
        result=timing.response(signals([.4,.05,.06]))
        self.assertFalse(result['timing']['fast_mode'])
        self.assertAlmostEqual(result['log_correction'],roles.response(signals([.4,.05,.06]))['log_correction'])

    def test_future_target_and_same_day_outcomes_do_not_change_candidates(self):
        args=dict(as_of='2026-03-10')
        before=timing.evaluate_all(self.target,self.history,PARAMS,**args)
        target={**self.target,'AVERAGE_PRICE':999999}
        extra=[target,issuer('930001',date(2026,3,10),actual=999999),issuer('930002',date(2026,3,11),actual=1)]
        after=timing.evaluate_all(target,self.history+extra,PARAMS,**args)
        self.assertEqual(before,after)

    def test_live_candidates_match_archived_response_traces(self):
        from evaluate_recent_fundamental_cases import install
        folder=ROOT/'data/fundamental_cases'
        read=lambda p:json.loads(p.read_text('utf-8'))
        index=read(folder/'index.json')['items']
        cases={c:read(ROOT/v['path']) for c,v in index.items()}
        inputs={c:read(folder/'inputs'/f'{c}.json') for c in index}
        history=[install(r,cases,inputs) for r in read(folder/'inputs/recent_history.json')['items']]
        import config_loader
        params=config_loader.load_params()
        archived=read(ROOT/'data/sentiment_pricing/roles/timing_replay.json')
        targets={r['SECURITY_CODE']:r for r in history}
        for row in archived['rows']:
            if row['code'] not in {'920268','920025','920157'}: continue
            target=targets[row['code']]
            result=timing.evaluate_all(target,history,params,as_of=target['LISTING_DATE'])
            for rule in ['combined','role_split','role_adaptive']:
                self.assertAlmostEqual(result[rule]['sentiment']['center'],row['models'][rule]['center'])
                self.assertEqual(result[rule]['anchor'],result['baseline']['anchor'])

    def test_unavailable_fundamental_stays_unavailable(self):
        result=timing.evaluate_all({**self.target,'fundamental_review':{}},self.history,PARAMS,as_of='2026-03-10')
        for rule in ['role_split','role_adaptive']:
            self.assertFalse(result[rule]['available']);self.assertIsNone(result[rule]['sentiment'].get('center'))

    def test_current_default_and_formal_ownership_are_preserved(self):
        params={**PARAMS,'quality_sentiment_mode':'active'}
        base=qs.evaluate(self.target,self.history,params,as_of='2026-03-10')
        expected=previous.attach_shadow(base,self.target,self.history,params,as_of='2026-03-10')
        actual=timing.attach_shadow(base,self.target,self.history,params,as_of='2026-03-10')
        self.assertEqual(actual['sentiment'],base['sentiment'])
        self.assertEqual(actual['response_shadow'],expected['response_shadow'])
        self.assertEqual(qs.attach({'target_price':999},actual)['target_price'],base['sentiment']['center'])
        self.assertEqual(timing.DEFAULT_SHADOW_RULE,'combined')

    def test_report_labels_both_candidates_and_failure_preserves_baseline(self):
        base=qs.evaluate(self.target,self.history,PARAMS,as_of='2026-03-10')
        attached=timing.attach_shadow(base,self.target,self.history,PARAMS,as_of='2026-03-10')
        payload=_payload();payload['final']=qs.attach(payload['final'],attached)
        for content in [report_generator.build_report_markdown(payload),
                        report_generator._build_report_html(report_generator._prepare_report_context(payload))]:
            self.assertIn('信号分工候选区间（研究）',content)
            self.assertIn('转向响应候选区间（研究）',content)
            self.assertIn('尚未用于正式定价',content)
        bad=timing.attach_shadow(base,self.target,self.history+[{**self.history[-1],'AVERAGE_PRICE':1}],PARAMS,as_of='2026-03-10')
        self.assertEqual(bad['sentiment'],base['sentiment'])
        self.assertIn('不可用',bad['response_experiment_error'])

    def test_metrics_and_frozen_sequential_protocols(self):
        root=ROOT/'data/sentiment_pricing/roles'
        for stage in ['roles','timing']:
            protocol=json.loads((root/f'{stage}_protocol.json').read_text('utf-8'))
            replay=json.loads((root/f'{stage}_replay.json').read_text('utf-8'))
            self.assertEqual(replay['protocol_sha256'],hashlib.sha256((root/f'{stage}_protocol.json').read_bytes()).hexdigest())
            self.assertFalse(replay['formal_promotion'])
            for rule in replay['metrics']['all']:
                self.assertEqual(metrics(replay['rows'],rule),replay['metrics']['all'][rule])
            self.assertFalse(protocol['market_or_sector_data_added'])
        # Stage two must have frozen the stage-one result it compares against.
        self.assertEqual(protocol['frozen_sources']['data/sentiment_pricing/roles/roles_replay.json'],
                         hashlib.sha256((root/'roles_replay.json').read_bytes()).hexdigest())


if __name__=='__main__':unittest.main()
