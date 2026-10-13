"""Bounded, dated feedback on a frozen fundamental/structural baseline.

This is a shadow experiment, never an automatic replacement for live prices.
Forecast errors always refer to the unchanged v1 baseline, avoiding a recursive
correction of our own adjusted forecasts. D2/D3 are compared at matching horizons.
"""
from copy import deepcopy
import math
import statistics

import quality_sentiment as qs

VERSION = 'sentiment_response_v1'
RULES = ('expectation', 'aftermarket', 'combined')
WINDOW = 3
REFERENCE_WINDOW = 20
MIN_GROUPS = 2
MIN_REFERENCE_GROUPS = 5
PRIOR_GROUPS = 3
CLIP = math.log(2)
MAX_CORRECTION = math.log(1.25)


def clipped(value, limit=CLIP):
    return max(-limit, min(limit, value))


def grouped_signal(observations, recent_dates, *, minimum=MIN_GROUPS, minimum_coverage=2 / 3):
    by_date = {}
    for row in observations:
        by_date.setdefault(row['listing_date'], []).append(row['value'])
    groups = [{'listing_date': d, 'value': statistics.mean(by_date[d])} for d in sorted(by_date)]
    available = len(groups) >= minimum and len(groups) / max(len(recent_dates), 1) >= minimum_coverage
    raw = statistics.mean(r['value'] for r in groups) if groups else None
    shrink = len(groups) / (len(groups) + PRIOR_GROUPS) if available else 0.0
    return {'available': available, 'observations': observations, 'date_groups': groups,
            'group_count': len(groups), 'raw_log_signal': raw, 'shrinkage': shrink,
            'log_correction': raw * shrink if available else 0.0,
            'reason': None if available else '最近3个实际上市日组中可用反馈不足；不向前补样本'}


def _prior(target, history, cutoff):
    # Reject conflicting duplicates rather than letting input order select a price.
    by_code = {}
    for row in history:
        listing = qs.day(row.get('LISTING_DATE'))
        code = str(row.get('SECURITY_CODE') or '')
        if not code or code == str(target.get('SECURITY_CODE')) or not listing or listing >= cutoff:
            continue
        if not (qs._positive(row.get('AVERAGE_PRICE')) or qs._positive(row.get('CLOSE_PRICE'))):
            continue
        if code in by_code and by_code[code] != row:
            raise ValueError('conflicting duplicate history: ' + code)
        by_code[code] = row
    return sorted(by_code.values(), key=lambda r: (qs.day(r['LISTING_DATE']), r['SECURITY_CODE']))


def feedback_signals(target, history, params, *, as_of):
    cutoff = qs.day(as_of)
    if cutoff is None:
        raise ValueError('explicit as_of required')
    prior = _prior(target, history, cutoff)
    dates = sorted({qs.day(r['LISTING_DATE']).isoformat() for r in prior})
    recent_dates, reference_dates = dates[-WINDOW:], dates[-(WINDOW + REFERENCE_WINDOW):-WINDOW]
    recent = [r for r in prior if qs.day(r['LISTING_DATE']).isoformat() in recent_dates]
    reference = [r for r in prior if qs.day(r['LISTING_DATE']).isoformat() in reference_dates]
    expectations = []
    for row in recent:
        actual = qs._positive(row.get('AVERAGE_PRICE'))
        if actual is None:
            continue
        baseline = qs.evaluate(row, prior, params, as_of=row['LISTING_DATE'])
        if baseline['available']:
            predicted = baseline['sentiment']['center']
            raw = math.log(actual / predicted)
            expectations.append({'code': row['SECURITY_CODE'],
                'listing_date': qs.day(row['LISTING_DATE']).isoformat(), 'value': clipped(raw),
                'actual_vwap': actual, 'baseline_prediction': predicted, 'raw_log_error': raw,
                'prediction_origin': 'baseline_walk_forward_reconstruction'})

    def observed(row, prefix):
        listing, day = qs.day(row['LISTING_DATE']), qs.day(row.get(prefix + '_TRADE_DATE'))
        close, actual = qs._positive(row.get(prefix + '_DAY_CLOSE')), qs._positive(row.get('AVERAGE_PRICE'))
        if day and listing < day < cutoff and close and actual:
            return {'date': day.isoformat(), 'log_return': clipped(math.log(close / actual))}
        return None

    reference_stats = {}
    for prefix in ('NEXT', 'THIRD'):
        observations = []
        for row in reference:
            obs = observed(row, prefix)
            if obs:
                observations.append({'code': row['SECURITY_CODE'],
                    'listing_date': qs.day(row['LISTING_DATE']).isoformat(),
                    'value': obs['log_return'], **obs})
        stats = grouped_signal(observations, reference_dates, minimum=MIN_REFERENCE_GROUPS, minimum_coverage=0)
        if not stats['available']:
            stats['reason'] = '同期限承接参照不足5个上市日组'
        reference_stats[prefix] = stats
    post = []
    for row in recent:
        comparisons = []
        for prefix in ('NEXT', 'THIRD'):
            obs, ref = observed(row, prefix), reference_stats[prefix]
            if obs and ref['available']:
                comparisons.append({'horizon': prefix, **obs,
                    'reference_log_return': ref['raw_log_signal'],
                    'innovation': clipped(obs['log_return'] - ref['raw_log_signal'])})
        if comparisons:
            post.append({'code': row['SECURITY_CODE'],
                'listing_date': qs.day(row['LISTING_DATE']).isoformat(),
                'value': statistics.mean(c['innovation'] for c in comparisons), 'comparisons': comparisons})
    return {'as_of_exclusive': cutoff.isoformat(), 'recent_listing_dates': recent_dates,
            'expectation': grouped_signal(expectations, recent_dates),
            'aftermarket': grouped_signal(post, recent_dates), 'post_reference': reference_stats,
            'uses_target_outcome': False,
            'note': '上涨和下跌对称响应；承接信号衡量相对历史常态的变化，不把所有D2/D3折价都扣一次'}


def evaluate_all(target, history, params, *, as_of):
    cutoff = qs.day(as_of)
    if cutoff is None:
        raise ValueError('explicit as_of required')
    history = _prior(target, history, cutoff)
    baseline = qs.evaluate(target, history, params, as_of=as_of)
    signals = feedback_signals(target, history, params, as_of=as_of)
    results = {'baseline': baseline}
    for rule in RULES:
        result = deepcopy(baseline)
        e = signals['expectation']['log_correction'] if rule != 'aftermarket' else 0.0
        a = signals['aftermarket']['log_correction'] if rule != 'expectation' else 0.0
        correction = clipped(e + a, MAX_CORRECTION)
        result.update(version=VERSION, mode='shadow', formal_eligible=False)
        result['pricing_response'] = {'rule': rule, 'baseline_version': baseline['version'],
            'baseline_center': baseline['sentiment'].get('center'),
            'expectation_log_correction': e, 'aftermarket_log_correction': a,
            'log_correction': correction, 'price_change_pct': math.expm1(correction) * 100,
            'maximum_log_correction': MAX_CORRECTION, 'signals': signals}
        if result['available']:
            sentiment = result['sentiment']
            multiplier = math.exp(correction)
            for key in ('center', 'low', 'high'):
                sentiment[key] *= multiplier
            sentiment['log_premium'] += correction
            sentiment['premium_pct'] = math.expm1(sentiment['log_premium']) * 100
        result['limitations'] += ['情绪响应固定规则未做参数寻优；尚未获得真实前向验证',
                                  '历史预测误差可能混有个股遗漏，不能全部解释为情绪因果效应']
        results[rule] = result
    return results


def evaluate(target, history, params, *, as_of, rule='combined'):
    if rule not in RULES:
        raise ValueError('unknown sentiment response rule')
    return evaluate_all(target, history, params, as_of=as_of)[rule]


def attach_shadow(baseline, target, history, params, *, as_of):
    """Keep baseline and formal ownership; persist the candidate alongside them."""
    output = deepcopy(baseline)
    try:
        candidate = evaluate(target, history, params, as_of=as_of)
    except (ValueError, ArithmeticError) as exc:
        output['response_shadow'] = {'version':VERSION,'mode':'shadow','formal_eligible':False,
            'available':False,'as_of_exclusive':str(as_of),
            'sentiment':{'available':False,'reason':f'情绪反馈试算不可用：{type(exc).__name__}: {exc}'}}
        return output
    output['response_shadow'] = {'version':VERSION,'mode':'shadow','formal_eligible':False,
        'available':candidate['available'],'as_of_exclusive':candidate['as_of_exclusive'],
        'sentiment':candidate['sentiment'],'pricing_response':candidate['pricing_response']}
    return output
