"""Same-denominator fundamental research anchors, independent of IPO sentiment.

Wide peer pools use three fixed, explained match bands, never fitted to returns.
Current-vintage reconstructions require an explicit research context; live callers
cannot turn a later review into pre-listing information.
"""
from __future__ import annotations

from datetime import date
import math
import statistics

VERSION = 'fundamental_anchor_v2'
MATCH_WEIGHTS = {'direct': 1.0, 'related': 0.5, 'channel': 0.25, 'observation': 0.0}
PROFITS = ('net_profit_parent', 'adjusted_net_profit_parent')


def numeric(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def day(value):
    text = str(value or '').strip()[:10]
    try:
        return date.fromisoformat(f'{text[:4]}-{text[4:6]}-{text[6:]}' if len(text) == 8 else text)
    except ValueError:
        return None


def weighted_quantile(pairs, quantile):
    pairs = sorted((v, w) for v, w in pairs if w > 0)
    total = sum(w for _, w in pairs)
    threshold, accumulated = total * quantile, 0.0
    for i, (value, weight) in enumerate(pairs):
        accumulated += weight
        if accumulated >= threshold:
            if math.isclose(accumulated, threshold) and i + 1 < len(pairs):
                return (value + pairs[i + 1][0]) / 2
            return value
    return pairs[-1][0] if pairs else None


def ttm(company, end, metric, as_of, *, reconstruction=False):
    cutoff, end_date = day(as_of), day(end)
    if not cutoff or not end_date or end_date >= cutoff or metric not in PROFITS + ('revenue',):
        return {'available': False, 'reason': '无效期间或指标'}
    year = end_date.year
    periods = [end] if end.endswith('12-31') else [f'{year-1}-12-31', end, f'{year-1}{end[4:]}']
    values, sources = [], []
    for period in periods:
        records = [r for r in company.get('records', []) if r.get('period') == period]
        if len(records) != 1:
            return {'available': False, 'reason': f'{period}缺失或重复版本'}
        record = records[0]
        if (record.get('currency') != 'CNY' or record.get('scope') != 'consolidated'
                or record.get('period_basis') != ('FY' if period.endswith('12-31') else 'YTD')):
            return {'available': False, 'reason': f'{period}币种/合并范围/累计期间不一致'}
        published = day(record.get('published_at'))
        available = day(record.get('available_not_before'))
        if not published or published >= cutoff or (not reconstruction and (not available or available >= cutoff)):
            return {'available': False, 'reason': f'{period}时点不可用'}
        cell = record.get('values', {}).get(metric, {})
        value = numeric(cell.get('value'))
        if value is None or cell.get('blocked') or not cell.get('evidence'):
            return {'available': False, 'reason': f'{period}/{metric}缺失或口径冲突'}
        values.append(value)
        sources.extend(cell['evidence'])
    value = values[0] if len(values) == 1 else values[0] + values[1] - values[2]
    return {'available': True, 'value': value, 'metric': metric, 'end': end, 'basis': 'TTM',
            'components': dict(zip(periods, values)), 'evidence': sorted(set(sources)),
            'historical_pit_eligible': False}


def normalized_profit(company, as_of, *, reconstruction=False):
    """Explicit reviewed sustainable-profit assumptions, not an automatic 5Y mean."""
    plan = company.get('normalization') or {}
    mandatory = ('business_scope_reason', 'sustainable_margin_reason', 'recovery_reason', 'evidence')
    if not plan.get('reviewed') or any(not plan.get(k) for k in mandatory):
        return {'available': False, 'reason': '正常化业务范围、利润率、恢复路径与证据尚未复核'}
    if plan.get('currency') != 'CNY' or plan.get('scope') != 'consolidated':
        return {'available': False, 'reason': '正常化币种或合并范围不一致'}
    cutoff, available = day(as_of), day(plan.get('available_not_before'))
    financial_end = day(plan.get('financial_end'))
    if (not cutoff or not available or not financial_end or financial_end >= cutoff
            or (not reconstruction and available >= cutoff)):
        return {'available': False, 'reason': '正常化假设时点不可用'}
    # Phase 2 only accepts a currently sustainable run-rate; recovery forecasts
    # require a cash-flow/time model before they can become pricing denominators.
    if plan.get('recovery_years') != 0:
        return {'available': False, 'reason': '恢复需要时间，不能把远期正常利润当作即期盈利'}
    revenue, margin = numeric(plan.get('sustainable_revenue')), numeric(plan.get('parent_net_margin'))
    if revenue is None or revenue <= 0 or margin is None or not 0 < margin < 1:
        return {'available': False, 'reason': '正常化金额无效'}
    return {'available': True, 'value': revenue * margin, 'basis': 'normalized',
            'metric': 'net_profit_parent', 'evidence': plan['evidence']}


def historical_margin_profit(company, end, metric, as_of, *, reconstruction=False):
    """Three completed FY margins at current revenue, a research stress scenario.

    Loss years stay in the median. This does not assert a full economic cycle,
    unchanged segments, sustainable earnings or immediate recovery from losses.
    """
    if not reconstruction:
        return {'available': False, 'reason': '历史利润率情景仅供重建压力对照，非正常化定价'}
    end_date = day(end)
    if not end_date:
        return {'available': False, 'reason': '无效期间'}
    last_year = end_date.year if end.endswith('12-31') else end_date.year - 1
    annual = []
    sources = []
    for year in range(last_year - 2, last_year + 1):
        period = f'{year}-12-31'
        profit = ttm(company, period, metric, as_of, reconstruction=True)
        revenue = ttm(company, period, 'revenue', as_of, reconstruction=True)
        if not profit.get('available') or not revenue.get('available') or revenue['value'] <= 0:
            return {'available': False, 'reason': f'{period}三年连续盈利/收入口径不完整', 'annual': annual}
        annual.append({'period': period, 'profit': profit['value'], 'revenue': revenue['value'],
                       'margin': profit['value'] / revenue['value']})
        sources.extend(profit['evidence'] + revenue['evidence'])
    revenue = ttm(company, end, 'revenue', as_of, reconstruction=True)
    if not revenue.get('available') or revenue['value'] <= 0:
        return {'available': False, 'reason': '缺少当前TTM收入', 'annual': annual}
    median = statistics.median(row['margin'] for row in annual)
    return {'available': True, 'value': revenue['value'] * median,
            'basis': 'historical_margin_reference', 'metric': metric, 'annual': annual,
            'revenue_ttm': revenue['value'], 'median_margin': median,
            'margin_min': min(row['margin'] for row in annual),
            'margin_max': max(row['margin'] for row in annual),
            'evidence': sorted(set(sources + revenue['evidence'])),
            'normalization_approved': False, 'historical_pit_eligible': False,
            'limitation': '三年历史利润率不是完整周期或可持续盈利承诺，亏损恢复和业务结构变化未获定价批准'}


def build_reviewed_anchor(review, quotes, as_of, params, *, reconstruction=False, stage='matched', market_policy='per_market'):
    cutoff = day(as_of)
    unavailable = {'available': False, 'basis': VERSION, 'as_of': str(cutoff),
                   'historical_pit_eligible': False, 'research_reconstruction': reconstruction}
    if review.get('schema') != VERSION or not cutoff or review.get('excludes_ipo_sentiment') is not True:
        return {**unavailable, 'reason': '基本面复核格式无效'}
    if reconstruction and params.get('quality_sentiment_mode') == 'active':
        return {**unavailable, 'reason': '事后重建资料禁止用于正式定价'}
    available = day(review.get('available_not_before'))
    if not available or (not reconstruction and available >= cutoff):
        return {**unavailable, 'reason': '复核及资料采集晚于估值时点'}
    if stage not in {'earnings', 'matched', 'normalized', 'cycle_reference'}:
        raise ValueError('unknown fundamental stage')
    if stage == 'cycle_reference' and not reconstruction:
        return {**unavailable, 'reason': '历史周期情景禁止进入实时定价'}
    if market_policy not in {'per_market', 'legacy_uniform'} or (market_policy == 'legacy_uniform' and not reconstruction):
        raise ValueError('legacy market policy is only an explicit research ablation')
    shares = review.get('shares', {})
    base_shares = numeric(shares.get('post_issue_base'))
    max_shares = numeric(shares.get('post_issue_if_greenshoe'))
    if (not base_shares or base_shares <= 0 or not max_shares or max_shares < base_shares
            or shares.get('basis') != 'post_issue_diluted' or not shares.get('evidence')):
        return {**unavailable, 'reason': '缺少发行后稀释股本及来源'}
    end = review.get('earnings_end', '')
    issuer = review.get('issuer', {})
    profit_function = historical_margin_profit if stage == 'cycle_reference' else ttm
    earnings = {metric: profit_function(issuer, end, metric, as_of, reconstruction=reconstruction) for metric in PROFITS}
    metrics = PROFITS
    if stage == 'normalized':
        earnings = {'net_profit_parent': normalized_profit(issuer, as_of, reconstruction=reconstruction)}
        metrics = ('net_profit_parent',)
    by_code = {}
    for q in quotes:
        observed = day(q.get('trade_date'))
        if not observed or not observed < cutoff or (cutoff - observed).days > 45:
            continue
        code = q.get('code')
        if code and (code not in by_code or observed > day(by_code[code]['trade_date'])):
            by_code[code] = q
    discount = numeric(params.get('bse_discount_factor', .55))
    if discount is None or not 0 < discount <= 1:
        return {**unavailable, 'reason': '市场折价参数无效'}
    candidates, seen = [], set()
    for peer in review.get('peers', []):
        code = peer['code']
        if code in seen:
            return {**unavailable, 'reason': '可比公司重复'}
        seen.add(code)
        weight = (1.0 if stage == 'earnings' else MATCH_WEIGHTS.get(peer.get('match'), 0.0))
        quote = by_code.get(code, {})
        cap = numeric(quote.get('mkt_cap'))
        market = code.split('.')[-1]
        # Preserve BSE reference valuations without charging a second BSE discount.
        factor = (discount if market_policy == 'legacy_uniform' and market in {'SH', 'SZ', 'BJ'} else
                  1.0 if market == 'BJ' else discount if market in {'SH', 'SZ'} else None)
        evidence_ok = bool(peer.get('reason') and peer.get('match_evidence'))
        values = {}
        for metric in metrics:
            profit = (normalized_profit(peer, as_of, reconstruction=reconstruction) if stage == 'normalized'
                      else profit_function(peer, end, metric, as_of, reconstruction=reconstruction))
            error = None
            if not evidence_ok:
                error = '缺少业务匹配证据'
            elif weight <= 0:
                error = '仅观察'
            elif factor is None:
                error = '跨市场缺少可验证流动性/折价口径，保留业务参照'
            elif not cap or cap <= 0:
                error = '缺少有效时点市值'
            elif not profit.get('available'):
                error = profit.get('reason')
            elif profit['value'] <= 0:
                error = '盈利非正，保留业务身份但PE不可用'
            if error:
                values[metric] = {'available': False, 'reason': error, 'profit': profit}
            else:
                pe = cap * 1e8 / profit['value']
                values[metric] = {'available': True, 'pe': pe, 'market_adjusted_pe': pe * factor, 'profit': profit}
        candidates.append({'code': code, 'name': peer.get('name', code), 'match': peer.get('match'),
                           'weight': weight, 'market': market, 'market_factor': factor,
                           'reason': peer.get('reason'), 'trade_date': quote.get('trade_date'),
                           'mkt_cap_yi': cap, 'metrics': values})
    scenarios = {}
    for metric in metrics:
        profit = earnings[metric]
        valid = [p for p in candidates if p['metrics'][metric]['available']]
        if not profit.get('available') or profit.get('value', 0) <= 0 or len(valid) < 2:
            scenarios[metric] = {'available': False, 'reason': profit.get('reason') or '正盈利有效可比不足两家',
                                 'peer_count': len(valid)}
            continue
        pairs = [(p['metrics'][metric]['market_adjusted_pe'], p['weight']) for p in valid]
        eps = profit['value'] / base_shares
        median = weighted_quantile(pairs, .5)
        scenarios[metric] = {'available': True, 'center': eps * median,
                            'low': profit['value'] / max_shares * weighted_quantile(pairs, .25),
                            'high': eps * weighted_quantile(pairs, .75), 'eps': eps,
                            'peer_multiple': median, 'peer_count': len(valid),
                            'peer_codes': [p['code'] for p in valid], 'profit': profit}
    # Both denominators are shown. A missing adjusted denominator is not replaced
    # by a convenient parent-profit estimate for the principal research anchor.
    selected = scenarios.get('net_profit_parent' if stage == 'normalized' else 'adjusted_net_profit_parent', {})
    result = {**unavailable, 'earnings': earnings, 'scenarios': scenarios, 'peer_details': candidates,
              'coverage': {'candidates': len(candidates), 'usable': selected.get('peer_count', 0)},
              'review_source': review.get('source'), 'cycle': review.get('cycle'), 'stage': stage,
              'limitations': list(review.get('limitations', [])) + [
                  '当前资料历史重建不是原始事前PIT', '匹配档位为固定研究规则，未按首日涨幅调参',
                  '区间是盈利口径、可比离散与绿鞋摊薄情景，不是统计置信区间'],
              'market_policy': {'SH_SZ': discount, 'BJ': discount if market_policy == 'legacy_uniform' else 1.0, 'NQ': None}}
    if not selected.get('available'):
        return {**result, 'reason': selected.get('reason', '基本面不可用')}
    valid_scenarios = [s for s in scenarios.values() if s.get('available')]
    low = min(s['low'] for s in valid_scenarios)
    high = max(s['high'] for s in valid_scenarios)
    center = selected['center']
    dispersion = max(high / center, center / low)
    confidence = min(selected['peer_count'] / 4, 1) * .7 / max(1, dispersion)
    return {**result, 'available': True, 'center': center, 'low': min(low, center), 'high': max(high, center),
            'confidence': confidence, 'peer_count': selected['peer_count'], 'peer_codes': selected['peer_codes'],
            'eps': selected['eps'], 'peer_pe': selected['peer_multiple'], 'bse_discount': discount,
            'earnings_basis': ('正常化归母利润/发行后股本 × 同口径同行倍数' if stage == 'normalized' else
                '双方近三年扣非利润率中位数 × 各自TTM收入；历史周期压力参照' if stage == 'cycle_reference' else
                '扣非TTM/发行后股本 × 同行市值/扣非TTM（分市场调整）'),
            'reference_dates': sorted({p['trade_date'] for p in candidates if p.get('trade_date')}),
            'as_of': cutoff.isoformat()}
