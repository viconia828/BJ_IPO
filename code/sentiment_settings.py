"""Typed shadow settings; defaults keep older parameter files compatible."""
from __future__ import annotations

import math

DEFAULTS = {
    'quality_sentiment_mode': 'shadow',
    'quality_sentiment_half_life_days': 8.0,
    'quality_sentiment_min_history': 8,
    'quality_sentiment_history_window': 40,
    'quality_sentiment_same_industry_weight': 1.5,
    'quality_sentiment_ridge_penalty': 2.0,
    'quality_sentiment_state_window_groups': 3,
    'quality_sentiment_state_min_groups': 2,
    'quality_sentiment_state_min_coverage': 2 / 3,
    'quality_sentiment_state_change_pct': 10.0,
    'quality_sentiment_state_miss_share': 2 / 3,
    'quality_sentiment_state_min_post_samples': 2,
    'quality_sentiment_state_stale_days': 14,
    'sentiment_response_window_groups': 3,
    'sentiment_response_reference_groups': 20,
    'sentiment_response_min_groups': 2,
    'sentiment_response_min_coverage': 2 / 3,
    'sentiment_response_min_reference_groups': 5,
    'sentiment_response_prior_groups': 3.0,
    'sentiment_response_clip_multiplier': 2.0,
    'sentiment_response_max_multiplier': 1.25,
    'sentiment_role_confirmation_cap_ratio': 1.0,
    'sentiment_timing_material_pct': 10.0,
    'sentiment_tuning_half_life_candidates': (4.0, 8.0, 12.0, 16.0),
    'sentiment_tuning_min_validation_samples': 5,
    'sentiment_tuning_earlier_under_tolerance_pp': 1.0,
}


def get(params, key):
    return (params or {}).get(key, DEFAULTS[key])


def resolve(params=None):
    settings = {key: get(params, key) for key in DEFAULTS}
    mode = settings['quality_sentiment_mode']
    if mode not in {'off', 'shadow', 'active'}:
        raise ValueError('quality_sentiment_mode 仅支持 off / shadow / active')
    for key, default in DEFAULTS.items():
        if isinstance(default, (str, tuple)):
            continue
        raw = settings[key]
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f'{key} 必须为有限数值') from None
        if isinstance(raw, bool) or not math.isfinite(value):
            raise ValueError(f'{key} 必须为有限数值')
        if isinstance(default, int):
            if not value.is_integer() or value < 1:
                raise ValueError(f'{key} 必须为正整数')
            value = int(value)
        elif key in {'sentiment_response_prior_groups', 'sentiment_role_confirmation_cap_ratio',
                     'sentiment_tuning_earlier_under_tolerance_pp'}:
            if value < 0:
                raise ValueError(f'{key} 不得小于0')
        elif value <= 0:
            raise ValueError(f'{key} 必须大于0')
        settings[key] = value
    for key in ('quality_sentiment_state_min_coverage', 'quality_sentiment_state_miss_share',
                'sentiment_response_min_coverage', 'sentiment_role_confirmation_cap_ratio'):
        if settings[key] > 1:
            raise ValueError(f'{key} 不得大于1')
    if settings['quality_sentiment_half_life_days'] < 1:
        raise ValueError('quality_sentiment_half_life_days 必须至少为1天')
    if settings['quality_sentiment_state_change_pct'] >= 100:
        raise ValueError('quality_sentiment_state_change_pct 必须小于100')
    for key in ('sentiment_response_clip_multiplier', 'sentiment_response_max_multiplier'):
        if settings[key] < 1:
            raise ValueError(f'{key} 必须至少为1；1表示关闭该项价格变化')
    for lower, upper in (
        ('quality_sentiment_min_history', 'quality_sentiment_history_window'),
        ('quality_sentiment_state_min_groups', 'quality_sentiment_state_window_groups'),
        ('sentiment_response_min_groups', 'sentiment_response_window_groups'),
        ('sentiment_response_min_reference_groups', 'sentiment_response_reference_groups'),
    ):
        if settings[lower] > settings[upper]:
            raise ValueError(f'{lower} 不得超过 {upper}')
    if settings['sentiment_response_window_groups'] < 2:
        raise ValueError('sentiment_response_window_groups 至少为2，供两组转向研究对照使用')
    raw = settings['sentiment_tuning_half_life_candidates']
    values = raw.split(',') if isinstance(raw, str) else raw
    if not isinstance(values, (tuple, list)) or not values or len(values) > 50:
        raise ValueError('sentiment_tuning_half_life_candidates 须为1至50个逗号分隔的天数')
    try:
        candidates = [float(v) for v in values]
    except (TypeError, ValueError):
        raise ValueError('sentiment_tuning_half_life_candidates 含无效天数') from None
    if any(isinstance(v, bool) for v in values) or any(not math.isfinite(v) or v < 1 for v in candidates):
        raise ValueError('sentiment_tuning_half_life_candidates 每项须为至少1天的有限数值')
    settings['sentiment_tuning_half_life_candidates'] = tuple(sorted(set(candidates)))
    return settings
