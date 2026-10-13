"""Separate first-day expectation feedback from aftermarket confirmation.

This module preserves the previous response experiment for exact comparisons.
Aftermarket weakness is not an independent forecast of the next IPO's price.
"""
from copy import deepcopy
import math

import sentiment_pricing as previous
import sentiment_settings as settings

VERSION='sentiment_roles_v1'


def direction(value):
    return 0 if abs(value)<1e-12 else (1 if value>0 else -1)


def response(signals, params=None):
    cfg=settings.resolve(params)
    maximum=math.log(cfg['sentiment_response_max_multiplier'])
    expectation=signals['expectation']
    aftermarket=signals['aftermarket']
    e=expectation['log_correction'] if expectation['available'] else 0.0
    a=aftermarket['log_correction'] if aftermarket['available'] else 0.0
    ed,ad=direction(e),direction(a)
    confirmation=0.0
    if not expectation['available']:
        agreement='missing_expectation'
        note='兑现反馈不足，承接仅作观察，未据其单独调价'
    elif ed and ad and ed==ad:
        agreement='same_direction'
        confirmation=ed*min(abs(e)*cfg['sentiment_role_confirmation_cap_ratio'],abs(a))
        note='兑现与承接同向；承接确认幅度不超过兑现本身'
    elif ed and ad and ed!=ad:
        agreement='conflict'
        note='兑现与承接冲突，判断置信度降低；保留兑现中枢，不把承接反向叠加'
    elif not aftermarket['available']:
        agreement='missing_aftermarket'
        note='承接反馈不足，采用兑现反馈，缺失不作中性解释'
    else:
        agreement='neutral'
        note='没有同向承接确认，采用兑现反馈'
    correction=previous.clipped(e+confirmation,maximum)
    return {'rule':'role_split','expectation_log_correction':e,
        'aftermarket_log_correction':confirmation,'raw_aftermarket_log_correction':a,
        'agreement':agreement,'confidence_note':note,'log_correction':correction,
        'price_change_pct':math.expm1(correction)*100,'maximum_log_correction':maximum,
        'signals':deepcopy(signals)}


def apply_response(baseline, response_result):
    result=deepcopy(baseline)
    result.update(version=VERSION,mode='shadow',formal_eligible=False)
    result['pricing_response']={**response_result,'baseline_version':baseline['version'],
                               'baseline_center':baseline['sentiment'].get('center')}
    if result['available']:
        sentiment=result['sentiment']
        factor=math.exp(response_result['log_correction'])
        for key in ['center','low','high']: sentiment[key]*=factor
        sentiment['log_premium']+=response_result['log_correction']
        sentiment['premium_pct']=math.expm1(sentiment['log_premium'])*100
    result['limitations']+=['信号分工由既有案例启发，仍需真实前向检验；冲突标签不是统计置信区间']
    return result


def evaluate_all(target, history, params, *, as_of):
    results=previous.evaluate_all(target,history,params,as_of=as_of)
    signals=results['combined']['pricing_response']['signals']
    results['role_split']=apply_response(results['baseline'],response(signals,params))
    return results
