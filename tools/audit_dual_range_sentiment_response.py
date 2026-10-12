"""Read-only mechanism audit; this does not tune parameters or forecast prices."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
import config_loader
import valuation_engine as engine


def parse_day(value):
    raw = str(value or '').strip().split(' ')[0]
    return datetime.strptime(raw, '%Y%m%d').date() if raw.isdigit() else date.fromisoformat(raw)


def load_samples(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key in ['vwap_gain_pct', 'close_gain_pct', 'issue_price', 'close']:
            row[key] = float(row[key])
    return sorted(rows, key=lambda row: (row['date'], row['code']))


def visible_record(row, reference):
    """Mask each D2/D3 observation at the valuation cutoff, before aggregation."""
    record = {'SECURITY_CODE':row['code'], 'SECURITY_NAME_ABBR':row['name'],
              'LISTING_DATE':row['date'], 'LD_AVERAGE_CHANGE':row['vwap_gain_pct'],
              'LD_CLOSE_CHANGE':row['close_gain_pct']}
    cache = ROOT / 'data/tushare_db/variable_fields' / (row['code'] + '.BJ.json')
    fields = json.loads(cache.read_text(encoding='utf-8')).get('fields', {}) if cache.exists() else {}
    post = fields.get('tushare_ipo_post_listing_performance') or {}
    observations = sorted([r for r in post.get('rows', [])
                           if parse_day(r['trade_date']) > parse_day(row['date'])],
                          key=lambda r: r['trade_date'])[:2]
    visible = [r for r in observations if parse_day(r['trade_date']) < reference and r.get('close') is not None]
    for prefix, observation in zip(('NEXT', 'THIRD'), observations):
        if observation not in visible:
            continue
        record[f'{prefix}_TRADE_DATE'] = str(observation['trade_date'])
        record[f'{prefix}_DAY_FROM_LISTING_CLOSE_PCT'] = (float(observation['close']) / row['close'] - 1) * 100
    if visible:
        record['POST_LISTING_PROFIT_EFFECT_PCT'] = statistics.fmean(
            (float(r['close']) / row['close'] - 1) * 100 for r in visible)
    record['audit_post_observation_dates'] = [str(r['trade_date']) for r in visible]
    for observed in visible:
        assert parse_day(row['date']) < parse_day(observed['trade_date']) < reference
    return record


def audit_at(rows, reference, params, target=None):
    previous = [row for row in rows if parse_day(row['date']) < reference]
    records = [visible_record(row, reference) for row in previous]
    gains = [row['vwap_gain_pct'] for row in previous]
    median5 = statistics.median(gains[-5:]) if gains else None
    score, reasons = engine._local_center_proxy_score({'recent5_median_change':median5}, {})
    m3 = engine.method3_recent_sentiment(1.0, records, params,
                                       target_code=(target or {}).get('code'),
                                       target_listing_date=reference.isoformat())
    recent = previous[-3:]
    latest_ratio = (1 + gains[-1] / 100) / (1 + gains[-2] / 100) - 1 if len(gains) >= 2 else None
    down_streak = 0
    for index in range(len(gains)-1, 0, -1):
        if gains[index] >= gains[index-1]:
            break
        down_streak += 1
    visible_post = [r['POST_LISTING_PROFIT_EFFECT_PCT'] for r in records[-3:] if 'POST_LISTING_PROFIT_EFFECT_PCT' in r]
    return {'reference_date':reference.isoformat(), 'target_code':(target or {}).get('code'),
            'target_name':(target or {}).get('name'),
            'recent_first_day_series':[{'code':r['code'],'name':r['name'],'date':r['date'],
                                       'vwap_gain_pct':r['vwap_gain_pct']} for r in recent],
            'recent5_median_gain_pct':median5,'recent5_score_contribution':score,'recent5_score_reasons':reasons,
            'last_multiplier_change_pct':latest_ratio * 100 if latest_ratio is not None else None,
            'consecutive_gain_declines':down_streak,
            'recent3_visible_post_profit_pct':statistics.fmean(visible_post) if visible_post else None,
            'recent3_visible_post_count':len(visible_post),
            'recent3_visible_post_records':[{'code':r['SECURITY_CODE'], 'observation_dates':r['audit_post_observation_dates'],
                                             'profit_pct':r.get('POST_LISTING_PROFIT_EFFECT_PCT')} for r in records[-3:]],
            'method3_first_day_factor_pct':m3.get('first_day_factor_pct'),
            'method3_post_listing_factor_pct':m3.get('post_listing_factor_pct'),
            'method3_raw_premium_pct':m3.get('raw_sentiment_premium_pct'),
            'method3_clipped_premium_pct':m3.get('sentiment_premium_pct'),
            'method3_codes':m3.get('sample_codes', []),
            'days_since_last_ipo':(reference-parse_day(previous[-1]['date'])).days if previous else None,
            'current_target_outcome_used':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, default=ROOT / 'outputs/研究产物/最新/新股基本面与情绪检验_20261012/canonical_samples.csv')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs/研究产物/最新/双区间情绪响应审计_20261012')
    args = parser.parse_args()
    params_path = ROOT / '策略参数.txt'
    params_before = hashlib.sha256(params_path.read_bytes()).hexdigest()
    params = config_loader.load_params(params_path)
    samples = load_samples(args.samples)
    sequence = [audit_at(samples, parse_day(row['date']), params, row) for row in samples]
    # A future valuation cutoff here means all observations through 10/12 close are usable.
    next_snapshot = audit_at(samples, date(2026, 10, 13), params,
                             {'code':'next_ipo_snapshot', 'name':'截至10/12收盘，下一只新股观察'})
    latest = sequence[-6:] + [next_snapshot]
    fixed_pool = [visible_record(row, date(2026, 9, 30)) for row in samples
                  if row['code'] in ['920025','920201','920202']]
    aging = [engine.method3_recent_sentiment(1.0, fixed_pool, params,
                                            target_listing_date=day) for day in ['2026-09-30','2026-10-08']]
    assert aging[0]['sample_codes'] == aging[1]['sample_codes']
    assert math.isclose(aging[0]['first_day_factor_pct'], aging[1]['first_day_factor_pct'], abs_tol=1e-10)
    assert math.isclose(aging[0]['raw_sentiment_premium_pct'], aging[1]['raw_sentiment_premium_pct'], abs_tol=1e-10)
    # Isolate the legacy grade response without changing any other issuer feature.
    grade_probe = []
    for value in [637.1461535272496,390.4578374975344,270.6511213025563]:
        score, reasons = engine._local_center_proxy_score({'recent5_median_change':value}, {})
        grade_probe.append({'hypothetical_recent5_median_pct':value,'score_contribution':score,'reasons':reasons})
    assert len({r['score_contribution'] for r in grade_probe}) == 1
    out = {'schema':'dual_range_sentiment_mechanism_audit_v1',
           'asof_date':'2026-10-12', 'research_only':True,
           'scope_note':'Current engine on corrected research samples with per-observation date masks; not a reproduction of archived pre-IPO reports and not a calibrated new signal. Raw IPO gains are not quality-adjusted sentiment; a higher gain across different issuers cannot establish sentiment recovery.',
           'canonical_input':str(args.samples),'canonical_input_sha256':hashlib.sha256(args.samples.read_bytes()).hexdigest(),
           'parameters_sha256':params_before,
           'parameters_used':{key:params[key] for key in ['recent_days','sentiment_decay_half_life_days','sentiment_first_day_baseline_pct',
                'sentiment_first_day_scale','sentiment_post_listing_scale','sentiment_premium_cap_pct','sentiment_premium_floor_pct',
                'local_center_alpha','local_center_history_window','local_center_actual_cap_pct']},
           'latest_contexts':latest,'all_contexts':sequence,
           'frozen_pool_aging_probe':{'source_pool_asof':'2026-09-30 before open','sample_codes':aging[0]['sample_codes'],
                'first_day_factors':[r['first_day_factor_pct'] for r in aging],
                'raw_premiums':[r['raw_sentiment_premium_pct'] for r in aging],
                'explanation':'With an unchanged sample window and no new observations, normalized exponential weights cancel a common passage of time.'},
           'isolated_grade_probe':grade_probe,
           'method3_direct_sensitivity_in_formal_center':1-float(params['local_center_alpha']),
           'quality_adjustment_requirement':{
               'fundamental_anchor':'F_i: issuer-specific pre-listing earnings times comparable valuation with business-quality adjustments; exclude the IPO mood being estimated',
               'excess_pricing':'log(first_day_VWAP_i / F_i)',
               'expectation_error':'log(first_day_VWAP_i / frozen_sentiment_center_i)',
               'distinction':'Excess pricing and expectation error are different; a miss against a forecast that already embeds mood does not establish negative premium against fundamentals.',
               'data_gate':'The existing displayed PE interval includes method3 mood; do not use it unadjusted as F_i. Poor or missing baselines produce an uncertain state, not a raw-gain fallback.',
               'latest_Yute_gain_rebound':'471% vs Anda 271% is an increase in raw issue-price return only; it is not evidence of sentiment recovery.'},
           'checks':{'prior_only_observations':True,'no_target_outcomes':True,
                     'unchanged_pool_aging_invariant':True,'same_high_grade_for_declining_levels':True}}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'mechanism_audit.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    columns = ['reference_date','target_code','target_name','recent5_median_gain_pct','recent5_score_contribution',
               'consecutive_gain_declines','last_multiplier_change_pct','recent3_visible_post_profit_pct',
               'method3_first_day_factor_pct','method3_raw_premium_pct','method3_clipped_premium_pct','days_since_last_ipo']
    with (args.output_dir / 'latest_contexts.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(latest)
    assert hashlib.sha256(params_path.read_bytes()).hexdigest() == params_before
    print(json.dumps({'latest_contexts':[{k:r[k] for k in columns} for r in latest],
                      'aging_probe':out['frozen_pool_aging_probe'], 'checks':out['checks']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
