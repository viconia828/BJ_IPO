"""Opt-in funding diagnostics, not production defaults or observed distributions.

2026 calendar was announced on 2025-12-22, before this audit's observations.
T+2 is a capital-return proxy, not an observed account credit time. Bucket
counts are synthetic; the conserving scenarios do not identify actual means.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import math

import subscription_ladder_labels as labels

CALENDAR_SOURCE = 'https://www.bse.cn/important_news/200027428.html'
CALENDAR_PUBLISHED = '2025-12-22'
HOLIDAY_RANGES = (('2026-01-01','2026-01-03'), ('2026-02-15','2026-02-23'),
                  ('2026-04-04','2026-04-06'), ('2026-05-01','2026-05-05'),
                  ('2026-06-19','2026-06-21'), ('2026-09-25','2026-09-27'),
                  ('2026-10-01','2026-10-07'))


def _range(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [a+timedelta(days=i) for i in range((b-a).days+1)]


HOLIDAYS = {d for a,b in HOLIDAY_RANGES for d in _range(a,b)}
TRADING_DAYS = [d for d in _range('2026-01-01','2026-12-31')
                if d.weekday() < 5 and d not in HOLIDAYS]
FEATURE_NAMES = ('month_end_3', 'quarter_end_3', 'pre_long_holiday_3',
                 'post_long_holiday_5', 'occupation_extra_days_div5')


def calendar_features(apply_date: str) -> dict:
    d = date.fromisoformat(apply_date[:10])
    if d not in TRADING_DAYS:
        raise ValueError('Calendar proxy requires a 2026 BSE trading day')
    i = TRADING_DAYS.index(d)
    if i+2 >= len(TRADING_DAYS):
        raise ValueError('T+2 exceeds the verified 2026 calendar')
    refund = TRADING_DAYS[i+2]
    month = [x for x in TRADING_DAYS if x.month == d.month]
    month_end = d in month[-3:]
    pre = post = False
    # Detect full closures, including weekends adjoining the listed holiday.
    for a,b in zip(TRADING_DAYS,TRADING_DAYS[1:]):
        if (b-a).days-1 < 5:
            continue
        ai,bi = TRADING_DAYS.index(a),TRADING_DAYS.index(b)
        pre |= d in TRADING_DAYS[max(0,ai-2):ai+1]
        post |= d in TRADING_DAYS[bi:bi+5]
    return {'apply_date':d.isoformat(), 'capital_return_proxy_date':refund.isoformat(),
            'capital_occupation_days':(refund-d).days,
            'month_end_3':int(month_end), 'quarter_end_3':int(month_end and d.month in (3,6,9,12)),
            'pre_long_holiday_3':int(pre), 'post_long_holiday_5':int(post),
            'occupation_extra_days_div5':max(0,(refund-d).days-2)/5}


def with_capital_return_proxy(record: dict) -> dict:
    feature = calendar_features(str(record['APPLY_DATE']))
    return {**record,'CAPITAL_OCCUPATION_DAYS':feature['capital_occupation_days'],
            'CAPITAL_OCCUPATION_SOURCE':'2026_BSE_calendar_T+2_proxy'}


def _num(value, default=0.0):
    try:
        result = float(value)
    except (ValueError,TypeError):
        return default
    return result if math.isfinite(result) else default


def conserving_buckets(row: dict, position: float) -> dict:
    """Bounded common within-band position plus a conserving unallocated mean.

    Bands are assumed from observed thresholds and synthetic awarded counts.
    `position` picks low/mid/high within the *feasible* common-position interval.
    Each band's transfer distribution is two atoms at its bounds, not a claim
    about the true distribution. No uniform scaling can push amounts above cap.
    """
    if not 0 <= position <= 1:
        raise ValueError('Bucket position must be in [0,1]')
    fit = row.get('allocation_fit_json') or {}
    if isinstance(fit,str):
        fit = json.loads(fit)
    cap = _num(row.get('top_apply_amount_wan'))
    funds = _num(row.get('frozen_funds_yi'))*1e4
    if cap <= 0 or funds <= 0 or not fit.get('buckets'):
        return {'available':False,'reason':'missing_buckets_cap_or_total'}
    manual = {int(x['total_lots']):_num(x['threshold_amount_wan'])
              for x in labels.parse_manual_ladder(row.get('manual_ladder'),cap)}
    raw_bands = []
    for item in fit['buckets']:
        n = _num(item.get('accounts'))
        low = manual.get(int(item.get('allocated_lots') or 0),_num(item.get('threshold_amount_wan')))
        if n <= 0:
            continue
        if low < 0 or low > cap+1e-6:
            return {'available':False,'reason':'threshold_outside_cap'}
        raw_bands.append((n,min(low,cap)))
    if not raw_bands:
        return {'available':False,'reason':'empty_buckets'}
    boundaries = sorted({cap,*(min(x,cap) for x in manual.values() if x > 0),*(x[1] for x in raw_bands)})
    bands = [{'accounts':n,'lower':lo,'upper':next((b for b in boundaries if b>lo+1e-8),cap)}
             for n,lo in raw_bands]
    un = _num(fit.get('unallocated_accounts'))
    un_cap = min(cap, min(x['lower'] for x in bands),
                 _num(fit.get('unallocated_cap_shares'))*_num(row.get('issue_price'))/1e4)
    allocated_low = sum(x['accounts']*x['lower'] for x in bands)
    width = sum(x['accounts']*(x['upper']-x['lower']) for x in bands)
    if width > 1e-8:
        lo = max(0.0,(funds-allocated_low-un*un_cap)/width)
        hi = min(1.0,(funds-allocated_low)/width)
    else:
        lo,hi = 0.0,1.0
        if not -1e-5 <= funds-allocated_low <= un*un_cap+1e-5:
            return {'available':False,'reason':'no_conserving_amounts'}
    if lo > hi+1e-9:
        return {'available':False,'reason':'no_conserving_amounts','position_lower':lo,'position_upper':hi}
    q = lo + position*(hi-lo)
    allocated = allocated_low + q*width
    mean = (funds-allocated)/un if un > 0 else 0.0
    if not -1e-5 <= mean <= un_cap+1e-5 or (not un and abs(funds-allocated)>1e-4):
        return {'available':False,'reason':'invalid_unallocated_mean'}
    mean = min(un_cap,max(0.0,mean))
    return {'available':True,'security_code':row['security_code'],'bands':bands,
            'source_cap_wan':cap,'source_total_wan':funds,'position_lower':lo,'position_upper':hi,
            'position':q,'unallocated_accounts':un,'unallocated_mean_wan':mean,
            'unallocated_cap_wan':un_cap,
            'confidence':_num(row.get('allocation_fit_confidence')) or _num(fit.get('fit_confidence'))}


def conserving_demand(row: dict, target_cap_wan: float, position: float) -> dict | None:
    model = conserving_buckets(row,position)
    if not model['available'] or target_cap_wan <= 0:
        return None
    q = model['position']
    total = sum(x['accounts']*((1-q)*min(x['lower'],target_cap_wan)+q*min(x['upper'],target_cap_wan))
                for x in model['bands'])
    total += model['unallocated_accounts']*min(model['unallocated_mean_wan'],target_cap_wan)
    return {'estimated_demand_wan':total,'confidence':model['confidence']}
