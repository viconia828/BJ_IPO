"""Chronological, common-cohort comparison of repaired and earlier funding models."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import time

import evaluate_subscription_funding_models as previous
import evaluate_subscription_funding_hypotheses as environment
import subscription_account_funding as account
import subscription_funding_hypotheses as hypotheses
import tune_subscription_prediction as t

ROOT = previous.ROOT
MODEL, HALF, BETA = previous.MODEL_KEY, previous.HALF_KEY, previous.BETA_KEY
POSITION = 'subscription_prediction_account_amount_position'


def select(replays, candidates, family, by_code, cutoff, manual):
    keys = [k for k,p in candidates.items() if p['_family'] == family]
    def available(key):
        return [d for d in replays[key]['details'] if d['security_code'] != manual
                and d['replay_as_of_date'] < cutoff
                and '' < t.replay_timeline.result_available_date(by_code[d['security_code']]) < cutoff]
    base_misses = sum(d['actual_top_apply_below_guaranteed'] and not d['predicted_top_apply_below_guaranteed']
                      for d in available('baseline'))
    ranked = []
    for key in keys:
        ds = available(key)
        m = previous.metrics(ds)
        misses = sum(d['actual_top_apply_below_guaranteed'] and not d['predicted_top_apply_below_guaranteed'] for d in ds)
        if len(ds)>=8 and not m['frozen_funds_missing_prediction_rows'] and misses<=base_misses+1:
            ranked.append((m['frozen_funds_weighted_mape'], m['frozen_funds_mape'], key))
    fallback = {'direct':'direct_h6.25_b0.25', 'legacy_bucket':'legacy_h6.25',
                'legacy_conserving':'legacy_conserving_h6.25_p0.5',
                'repaired_account':'repaired_h6.25_p0.5', 't_plus_2':'t_plus_2_e0.3'}[family]
    key = min(ranked)[2] if ranked else fallback
    return {'candidate':key, 'cutoff_exclusive':cutoff, 'training_codes':[d['security_code'] for d in available(key)],
            'reason':'minimum_past_weighted_mape' if ranked else 'prespecified_fallback_insufficient_training_or_guardrail',
            'metrics':previous.metrics(available(key))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
    started = time.monotonic()
    def save(name,value):
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    def log(message):
        line = f'[{time.monotonic()-started:.1f}s] {message}'
        print(line,flush=True)
        with (out/'progress.log').open('a',encoding='utf-8') as f: f.write(line+'\n')
    def hashes():
        paths = [t.DEFAULT_HISTORY_PATH,t.DEFAULT_LADDER_LABEL_PATH,t.DEFAULT_PARAMS_PATH,t.DEFAULT_AUTO_RECORD_PATH,
                 ROOT/'data/offline_tuning/account_pool_history_thresholds.csv']
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before = hashes()
    rows = sorted([r for r in t.subscription_context.load_history_rows()
                   if '' < t.replay_timeline.result_available_date(r) <= '2026-10-16'],key=t._row_sort_key)
    by_code = {r['security_code']:r for r in rows}
    sources = {'full':rows,'q3':[r for r in rows if r['apply_date'] >= '2026-07-01']}
    merged = previous.pool._merge_rows(t._load_history_rows(t.DEFAULT_HISTORY_PATH),t.subscription_ladder_labels.load_label_rows())
    pools = {scope:previous.rebuild_scoped_pool([r for r in merged if r['security_code'] in {s['security_code'] for s in source}])
             for scope,source in sources.items()}
    baseline = t._resolve_subscription_base_params(t.config_loader.load_params())
    baseline[MODEL] = 'multiple'
    manual = str(baseline.get('subscription_prediction_target_frozen_funds_code') or '')
    fixed = {**baseline,'subscription_prediction_unified_recency_enabled':True,
             'subscription_prediction_direct_similarity_bandwidth':.35,
             'subscription_prediction_recent_market_level_factor':1.,
             'subscription_prediction_recent_market_level_adaptive_enabled':True,
             'subscription_prediction_recent_market_level_adaptive_min_samples':3,
             'subscription_prediction_recent_market_level_adaptive_weight':.5}
    candidates = {}
    for half in previous.HALVES:
        for beta in previous.BETAS:
            candidates[f'direct_h{half}_b{beta}'] = {**fixed,MODEL:'direct_funds',HALF:half,BETA:beta,'_family':'direct'}
        candidates[f'legacy_h{half}'] = {**fixed,MODEL:'bucket_funds',HALF:half,BETA:.25,'_family':'legacy_bucket'}
        for pos in (0,.5,1):
            candidates[f'legacy_conserving_h{half}_p{pos}'] = {**fixed,MODEL:'bucket_funds',HALF:half,BETA:.25,
                'subscription_prediction_bucket_amount_basis':'conserving_bounds',
                'subscription_prediction_bucket_amount_position':pos,'_family':'legacy_conserving'}
            candidates[f'repaired_h{half}_p{pos}'] = {**fixed,MODEL:'account_pool_funds',HALF:half,BETA:.25,
                                                     POSITION:pos,'_family':'repaired_account'}
    for exponent in (0,.15,.3,.45):
        candidates[f't_plus_2_e{exponent}'] = {**baseline,'subscription_prediction_lock_days_basis':'t_plus_2_calendar_2026',
            'subscription_prediction_lock_factor_exponent':exponent,'_family':'t_plus_2'}
    families = sorted({p['_family'] for p in candidates.values()})
    def scoped(p,scope):
        return {**p,'subscription_prediction_account_pool_rows':pools[scope],t.subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY:{}}
    def audit(replay,scope):
        allowed = {r['security_code'] for r in sources[scope]}
        for d in replay['details']:
            target = by_code[d['security_code']]
            for field in ('history_source_codes','funding_source_codes','recent_market_level_source_codes',
                          'account_pool_source_codes','account_pool_effective_source_codes','account_pool_prior_source_codes'):
                for code in d.get(field) or []:
                    assert code in allowed and t.replay_timeline.known_before(by_code[code],target),(field,code,target['security_code'])
            for available in d['funding_distribution_dates'].values():
                assert available < target['apply_date']
            for available in d.get('account_pool_prior_distribution_dates',{}).values():
                assert available < target['apply_date']
            for inner in d['funding_calibration_distribution_dates']:
                for code,available in inner['source_dates'].items():
                    assert code in allowed and available < by_code[inner['security_code']]['apply_date']
    source_audit = {}
    replays = {}
    for scope,source in sources.items():
        save(f'{scope}_account_pool_snapshots.json',pools[scope])
        source_audit[scope] = []
        for snapshot in pools[scope]:
            row = {**by_code[snapshot['security_code']], 'source_lot_distribution_json':snapshot['source_lot_distribution_json']}
            scenarios = {str(p):account.distribution(row,p) for p in (0,.5,1)}
            for m in scenarios.values():
                if m['available']:
                    assert abs(account.demand(m,float(row['top_apply_amount_wan']))['estimated_demand_wan']/m['source_total_wan']-1)<1e-9
            source_audit[scope].append({'security_code':row['security_code'],'scenarios':scenarios})
        replays[scope] = {}
        for key,p in {'baseline':baseline,**candidates}.items():
            replay = t.evaluate_subscription_prediction(source,params=scoped(p,scope))
            audit(replay,scope)
            replays[scope][key] = replay
            save(f'{scope}_{key}_replay.json',replay)
            log(f'{scope}/{key}: {replay["evaluated_rows"]} predictions')
    save('source_audit.json',source_audit)
    common = set.intersection(*[{d['security_code'] for d in r['details']} for rs in replays.values() for r in rs.values()])
    rolling = {r['security_code'] for r in rows if r['apply_date'] >= '2026-08-01'} & common - {manual}
    held = {r['security_code'] for r in rows if r['apply_date'] >= '2026-09-23'} & common - {manual}
    assert len(rolling)==17 and len(held)==3
    snap = json.loads((ROOT/'.tmp/industry_classifier_20261012/analysis_920285.json').read_text(encoding='utf-8'))
    target = t.subscription_context.target_row_from_ipo(snap['ipo_info'])
    cutoff = min(target['apply_date'],(date.fromisoformat(snap['analysis_date'])+timedelta(days=1)).isoformat())
    summaries, selections, predictions, targets, controls, fixed_metrics = {}, {}, {}, {}, {}, {}
    def subset(ds,codes): return [d for d in ds if d['security_code'] in codes]
    for scope,rs in replays.items():
        summaries[scope]={}; selections[scope]={}; predictions[scope]={}; targets[scope]={}; controls[scope]={}
        fixed_metrics[scope]={k:{'rolling17':previous.metrics(subset(r['details'],rolling)),
                               'held3':previous.metrics(subset(r['details'],held))} for k,r in rs.items()}
        for family in ['baseline',*families]:
            sequence=[]
            held_selection=None if family=='baseline' else select(rs,candidates,family,by_code,'2026-09-23',manual)
            target_selection=None if family=='baseline' else select(rs,candidates,family,by_code,cutoff,manual)
            held_key='baseline' if held_selection is None else held_selection['candidate']
            target_key='baseline' if target_selection is None else target_selection['candidate']
            for row in rows:
                if row['security_code'] not in rolling: continue
                choice=None if family=='baseline' else select(rs,candidates,family,by_code,row['apply_date'],manual)
                key='baseline' if choice is None else choice['candidate']
                if choice: assert row['security_code'] not in choice['training_codes']
                d=next(d for d in rs[key]['details'] if d['security_code']==row['security_code'])
                sequence.append({'security_code':row['security_code'],'selection':choice,'prediction':d})
            predictions[scope][family]=sequence
            selections[scope][family]={'held':held_selection,'target_as_of':target_selection}
            ds=[x['prediction'] for x in sequence]
            summaries[scope][family]={'rolling17':previous.metrics(ds),'fixed_held3':previous.metrics(subset(rs[held_key]['details'],held)),
                'rolling_fallback_count':sum(bool(d['funding_fallback_reason']) for d in ds),
                'held_candidate':held_key,'target_candidate':target_key}
            p=baseline if family=='baseline' else candidates[target_key]
            targets[scope][family]=t.subscription_context.build_prediction(snap['ipo_info'],history_rows=sources[scope],
                                                   params=scoped(p,scope),as_of_date=snap['analysis_date'])
            assert '920285' not in targets[scope][family]['history_context']['history_source_codes']
            assert '920296' not in targets[scope][family]['history_context']['history_source_codes']
        scope_by_code={r['security_code']:r for r in sources[scope]}
        for label,features in [('intercept_only',()),('calendar_all',hypotheses.FEATURE_NAMES)]:
            ds=[]; held_ds=[]; fit_records=[]
            for d in rs['baseline']['details']:
                if d['security_code'] not in rolling: continue
                row=by_code[d['security_code']]
                fit=environment.residual_correction(rs['baseline']['details'],scope_by_code,row,row['apply_date'],features,manual_code=manual)
                ds.append(environment.adjusted_detail(d,fit['factor']))
                fit_records.append({'security_code':row['security_code'],'fit':fit})
                if row['security_code'] in held:
                    frozen=environment.residual_correction(rs['baseline']['details'],scope_by_code,row,'2026-09-23',features,manual_code=manual)
                    held_ds.append(environment.adjusted_detail(d,frozen['factor']))
                    fit_records.append({'security_code':row['security_code'],'fixed_held_fit':frozen})
            fit=environment.residual_correction(rs['baseline']['details'],scope_by_code,target,cutoff,features,manual_code=manual)
            controls[scope][label]={'rolling17':environment.funding_metrics(ds),'fixed_held3':environment.funding_metrics(held_ds),
                'target_frozen_funds_yi':targets[scope]['baseline']['frozen_funds_yi']*fit['factor'],
                'target_fit':fit,'fits':fit_records,'predictions':ds,'fixed_held_predictions':held_ds}
        # Previously selected original-model parameters are valid only for the
        # three held stocks and the later pre-application target, not August.
        old_path=ROOT/f'tmp/subscription_objective_audit_20261016/unified_recency_run/{scope}_holdout_parameters.json'
        old_params={**baseline,**json.loads(old_path.read_text(encoding='utf-8'))}
        old_replay=t.evaluate_subscription_prediction(sources[scope],params=scoped(old_params,scope))
        audit(old_replay,scope)
        old_pred=t.subscription_context.build_prediction(snap['ipo_info'],history_rows=sources[scope],params=scoped(old_params,scope),as_of_date=snap['analysis_date'])
        controls[scope]['earlier_multiple_optimized']={'parameters':json.loads(old_path.read_text(encoding='utf-8')),
            'parameter_file':str(old_path),'fixed_held3':previous.metrics(subset(old_replay['details'],held)),
            'held_predictions':subset(old_replay['details'],held),'target_frozen_funds_yi':old_pred['frozen_funds_yi']}
        log(f'{scope}: rolling/held/target selections complete')
    # Production and replay share the forecast entry; check chosen account
    # forecasts including ladders, and audit nested calibration source dates.
    alignment=0
    for scope in sources:
        for entry in predictions[scope]['repaired_account']:
            d=entry['prediction']; p=candidates[entry['selection']['candidate']]
            result=t.subscription_context.build_prediction(t._target_from_row(by_code[d['security_code']]),history_rows=sources[scope],params=scoped(p,scope))
            assert result['frozen_funds_yi']==d['predicted_frozen_funds_yi']
            assert result['guaranteed_threshold_amount_wan']==d['predicted_guaranteed_amount_wan']
            ladders={(x['regular_lots'],x['fractional_lots']):x for x in result['lot_thresholds']}
            for x in d['manual_ladder_errors']:
                item=ladders.get((x['regular_lots'],x['fractional_lots']))
                if item: assert item['threshold_amount_wan']==x['predicted_threshold_amount_wan']
            alignment+=1
    assert before==hashes(),'Production inputs changed during experiment'
    for filename,data in [('selections.json',selections),('rolling_predictions.json',predictions),('target_predictions.json',targets),
                          ('controls.json',controls),('fixed_candidate_metrics.json',fixed_metrics),
                          ('candidate_parameters.json',{k:{f:v for f,v in p.items() if f.startswith('subscription_prediction_') or f=='_family'} for k,p in candidates.items()})]: save(filename,data)
    report={'as_of':'2026-10-16','holdout_cutoff':'2026-09-23','target_as_of':snap['analysis_date'],
            'history_counts':{k:len(v) for k,v in sources.items()},'candidate_count':len(candidates),
            'rolling_codes':sorted(rolling),'held_codes':sorted(held),'manual_excluded':manual,
            'metrics':summaries,'selections':selections,'controls':controls,
            'source_coverage':{scope:{'total':len(records),'usable':sum(r['scenarios']['0.5']['available'] for r in records),
                'rejections':[{'security_code':r['security_code'],**r['scenarios']['0.5']} for r in records if not r['scenarios']['0.5']['available']]}
                for scope,records in source_audit.items()},
            'validation':{'production_input_hashes':before,'input_hashes_unchanged':True,'time_violations':0,'forecast_alignment_checks':alignment},
            'elapsed_seconds':time.monotonic()-started}
    save('report.json',report)
    log('Complete; production parameters unchanged.')


if __name__=='__main__':
    main()
