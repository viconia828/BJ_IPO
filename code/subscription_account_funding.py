"""Funding transfer from announcement-constrained account counts.

Counts come from the repaired builder, never allocation_fit_json. For three or
more lot levels the counts still depend on an inferred historical shape; neither
these counts nor within-band subscription amounts are observed microdata.
"""
from __future__ import annotations

import json
import math
from typing import Any


def _num(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    return n if math.isfinite(n) else 0.0


def distribution(row: dict, position: float = .5) -> dict:
    """Reconcile allocated bands plus non-winning accounts to source funding.

    A shared position inside each allocated band and the non-winning mean are
    not identifiable separately. Position selects a point in the feasible set;
    0/.5/1 are tested as explicit assumptions, not fitted observations.
    """
    if not math.isfinite(position) or not 0 <= position <= 1:
        raise ValueError('Account amount position must be in [0, 1]')
    raw = row.get('source_lot_distribution_json') or {}
    try:
        source = json.loads(raw) if isinstance(raw, str) else raw
        thresholds = {int(k): float(v) for k,v in source.get('thresholds_by_lot', {}).items()}
        counts = {int(k): float(v) for k,v in source.get('cumulative_accounts', {}).items()}
    except (ValueError, TypeError, AttributeError):
        return {'available': False, 'reason': 'invalid_source_distribution'}
    if not counts or set(counts) != set(range(1, len(counts)+1)) or set(counts) != set(thresholds):
        return {'available': False, 'reason': 'missing_complete_source_distribution'}
    cap = _num(row.get('top_apply_amount_wan'))
    funds = _num(row.get('frozen_funds_yi')) * 1e4
    allocated = _num(row.get('online_allocated_accounts'))
    valid = _num(row.get('online_valid_accounts'))
    lots = _num(row.get('online_issue_shares')) / 100
    tolerance = 1e-4
    if (cap <= 0 or funds <= 0 or valid < allocated or allocated <= 0
            or abs(counts[1]-allocated) > tolerance
            or abs(sum(counts.values())-max(lots, allocated)) > tolerance):
        return {'available': False, 'reason': 'announcement_constraints_failed'}
    bands = []
    for k in sorted(counts):
        n = counts[k]-counts.get(k+1, 0)
        lo, hi = thresholds[k], thresholds.get(k+1, cap)
        if not all(math.isfinite(v) for v in (n,lo,hi)) or n < -tolerance or lo < 0 or hi < lo or hi > cap+tolerance:
            return {'available': False, 'reason': 'invalid_account_band'}
        bands.append({'lot_level': k, 'accounts': max(0.,n), 'lower': lo, 'upper': min(hi,cap)})
    un = valid-allocated
    un_cap = min(cap,thresholds[1])
    floor = sum(b['accounts']*b['lower'] for b in bands)
    width = sum(b['accounts']*(b['upper']-b['lower']) for b in bands)
    low, high = (max(0., (funds-floor-un*un_cap)/width), min(1.,(funds-floor)/width)) if width > 1e-8 else (0.,1.)
    if low > high+1e-9 or not floor-tolerance <= funds <= floor+width+un*un_cap+tolerance:
        return {'available': False, 'reason': 'no_conserving_amounts',
                'minimum_total_yi': floor/1e4, 'maximum_total_yi': (floor+width+un*un_cap)/1e4,
                'actual_frozen_funds_yi': funds/1e4}
    q = max(0., min(1.,low+position*(high-low)))
    leftover = funds-floor-q*width
    mean = leftover/un if un else 0.
    if not -tolerance <= mean <= un_cap+tolerance or (not un and abs(leftover)>tolerance):
        return {'available': False, 'reason': 'invalid_nonwinning_mean'}
    return {'available': True, 'bands': bands, 'source_total_wan': funds, 'source_cap_wan': cap,
            'unallocated_accounts': un, 'unallocated_mean_wan': max(0.,min(un_cap,mean)),
            'unallocated_cap_wan': un_cap, 'position_lower': low, 'position_upper': high,
            'position': q, 'requested_position': position, 'count_basis': source.get('basis'),
            'confidence': .95 if source.get('basis') == 'announcement_exact' else .75,
            'is_observed_distribution': False}


def demand(model: dict, target_cap_wan: float) -> dict | None:
    if not model.get('available') or target_cap_wan <= 0:
        return None
    q = model['position']
    total = sum(b['accounts']*((1-q)*min(b['lower'],target_cap_wan)+q*min(b['upper'],target_cap_wan))
                for b in model['bands'])
    total += model['unallocated_accounts']*min(model['unallocated_mean_wan'],target_cap_wan)
    return {'estimated_demand_wan': total, 'confidence': model['confidence'],
            'account_count_basis': 'repaired_announcement_constrained',
            'shape_basis': model['count_basis'], 'is_observed_distribution': False}
