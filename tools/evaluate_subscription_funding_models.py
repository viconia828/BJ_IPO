"""Compare simple funding models with chronological selection; never write parameters.

No network refresh, label synchronisation, production pool rebuild, or final-stock
reference is used in candidate selection. Fractional errors remain diagnostics.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import time

import tune_subscription_prediction as t
import build_account_pool_history as pool

ROOT = Path(__file__).resolve().parents[1]
MODEL_KEY = 'subscription_prediction_funding_model'
HALF_KEY = 'subscription_prediction_sample_decay_half_life_days'
BETA_KEY = 'subscription_prediction_direct_cap_elasticity'
HALVES = (3, 6.25, 10, 15)
BETAS = (0, .25, .5, .75)


def metrics(details):
    details = [dict(d) for d in details]
    result = t._frozen_funds_metrics(details, 12)
    errors = [x['pct_error'] for d in details for x in d.get('manual_ladder_errors', [])
              if x['fractional_lots'] and x['pct_error'] is not None]
    result.update(sample_count=len(details), fractional_metric_count=len(errors),
                  fractional_mape=sum(errors)/len(errors) if errors else None)
    return result


def select_candidate(replays, candidates, by_code, cutoff, *, manual_code='', family=None):
    """Rank only labels available strictly before cutoff, not later replay labels."""
    def available(details):
        return [d for d in details if d['security_code'] != manual_code
                and '' < t.replay_timeline.result_available_date(by_code[d['security_code']]) < cutoff
                and d['replay_as_of_date'] < cutoff]
    baseline = available(replays['baseline']['details'])
    baseline_misses = sum(d['actual_top_apply_below_guaranteed'] and not d['predicted_top_apply_below_guaranteed'] for d in baseline)
    ranked = []
    for key, p in candidates.items():
        if family and p[MODEL_KEY] != family:
            continue
        details = available(replays[key]['details'])
        score = metrics(details)
        misses = sum(d['actual_top_apply_below_guaranteed'] and not d['predicted_top_apply_below_guaranteed'] for d in details)
        if score['frozen_funds_missing_prediction_rows'] or misses > baseline_misses+1 or score['sample_count'] < 8:
            continue
        rank = (score['frozen_funds_weighted_mape'], score['frozen_funds_mape'], key)
        ranked.append((rank,key,score,[d['security_code'] for d in details]))
    if not ranked:
        key = 'bucket_h6.25' if family == 'bucket_funds' else 'direct_h6.25_b0.25'
        details = available(replays[key]['details'])
        return {'candidate':key, 'reason':'fewer_than_8_training_predictions_or_guardrail',
                'metrics':metrics(details), 'training_codes':[d['security_code'] for d in details], 'cutoff_exclusive':cutoff}
    _, key, score, codes = min(ranked)
    return {'candidate':key, 'reason':'lowest_recent_weighted_funding_mape', 'metrics':score,
            'training_codes':codes, 'cutoff_exclusive':cutoff}


def rebuild_scoped_pool(rows):
    state, result = {}, []
    for row in sorted(rows, key=t._row_sort_key):
        points = pool._build_points_for_row(row, state)
        touched = pool._update_threshold_state(state, points, str(row['security_code']))
        snapshot = pool._build_threshold_snapshot_row(row, points, state, touched)
        snapshot['account_pool_snapshot_json'] = json.dumps(snapshot['account_pool_snapshot_json'], ensure_ascii=False)
        result.append(snapshot)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--history-start', default='2026-07-01')
    parser.add_argument('--rolling-start', default='2026-08-01')
    parser.add_argument('--holdout-cutoff', default='2026-09-23')
    parser.add_argument('--target-snapshot', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    for value in (args.as_of,args.history_start,args.rolling_start,args.holdout_cutoff):
        date.fromisoformat(value)
    out = args.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=True)
    started = time.monotonic()
    def log(s):
        line=f'[{time.monotonic()-started:.1f}s] {s}'
        print(line,flush=True)
        with (out/'progress.log').open('a',encoding='utf-8') as f:
            f.write(line+'\n')
    def save(name,value):
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    def hashes():
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (
            t.DEFAULT_HISTORY_PATH,t.DEFAULT_LADDER_LABEL_PATH,t.DEFAULT_PARAMS_PATH,t.DEFAULT_AUTO_RECORD_PATH,
            ROOT/'data/offline_tuning/account_pool_history_thresholds.csv')}
    before=hashes()
    rows=t.subscription_context.load_history_rows()
    rows=sorted([r for r in rows if ''<t.replay_timeline.result_available_date(r)<=args.as_of],key=t._row_sort_key)
    by_code={r['security_code']:r for r in rows}
    q3=[r for r in rows if r['apply_date']>=args.history_start]
    baseline=t._resolve_subscription_base_params(t.config_loader.load_params())
    baseline[MODEL_KEY]='multiple'
    t._preload_account_pool_rows_for_tuning(baseline)
    manual=str(baseline.get('subscription_prediction_target_frozen_funds_code') or '')
    merged=pool._merge_rows(t._load_history_rows(t.DEFAULT_HISTORY_PATH),
                           t.subscription_ladder_labels.load_label_rows(t.DEFAULT_LADDER_LABEL_PATH))
    q3_pool=rebuild_scoped_pool([r for r in merged if r['security_code'] in {r['security_code'] for r in q3}])
    save('q3_pool_snapshots.json',q3_pool)
    fixed={**baseline,'subscription_prediction_unified_recency_enabled':True,
           'subscription_prediction_direct_similarity_bandwidth':.35,
           'subscription_prediction_recent_market_level_factor':1.0,
           'subscription_prediction_recent_market_level_adaptive_enabled':True,
           'subscription_prediction_recent_market_level_adaptive_min_samples':3,
           'subscription_prediction_recent_market_level_adaptive_weight':.5}
    candidates={}
    for half in HALVES:
        for beta in BETAS:
            candidates[f'direct_h{half}_b{beta}']={**fixed,MODEL_KEY:'direct_funds',HALF_KEY:half,BETA_KEY:beta}
        candidates[f'bucket_h{half}']={**fixed,MODEL_KEY:'bucket_funds',HALF_KEY:half,BETA_KEY:.25}
    sources={'full':rows,'q3':q3}
    def scoped(p,scope):
        settings={**p,t.subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY:{}}
        if scope=='q3':
            settings['subscription_prediction_account_pool_rows']=q3_pool
        return settings
    def audit(replay):
        for d in replay['details']:
            target=by_code[d['security_code']]
            for field in ('history_source_codes','funding_source_codes','recent_market_level_source_codes',
                          'account_pool_source_codes','account_pool_effective_source_codes','account_pool_prior_source_codes'):
                for code in d.get(field) or []:
                    assert code in by_code and t.replay_timeline.known_before(by_code[code],target),(field,code,target['security_code'])
    all_replays={}
    for scope,source in sources.items():
        replays={}
        for name,p in {'baseline':baseline,**candidates}.items():
            replay=t.evaluate_subscription_prediction(source,params=scoped(p,scope))
            audit(replay)
            replays[name]=replay
            save(f'{scope}_{name}_replay.json',replay)
            log(f'{scope} {name}: {replay["evaluated_rows"]} predictions; weighted MAPE={replay["frozen_funds_weighted_mape"]:.4%}')
        all_replays[scope]=replays
    common=set.intersection(*[{d['security_code'] for d in r['details']} for rs in all_replays.values() for r in rs.values()])
    rolling_codes={r['security_code'] for r in rows if r['apply_date']>=args.rolling_start} & common
    held_codes={r['security_code'] for r in rows if r['apply_date']>=args.holdout_cutoff} & common
    recent_codes={r['security_code'] for r in rows[-6:]} & common
    groups={'common':common,'rolling':rolling_codes,'rolling_model_only':rolling_codes-{manual},
            'held':held_codes,'recent_six':recent_codes,
            'recent_model_only':recent_codes-{manual}}
    selections={}
    rolling={}
    summary={}
    def details_for(replay,codes):
        return [d for d in replay['details'] if d['security_code'] in codes]
    for scope,rs in all_replays.items():
        summary[scope]={'baseline':{g:metrics(details_for(rs['baseline'],codes)) for g,codes in groups.items()}}
        selections[scope]={}
        rolling[scope]={}
        for family in ('direct_funds','bucket_funds','combined'):
            family_arg=None if family=='combined' else family
            held=select_candidate(rs,candidates,by_code,args.holdout_cutoff,manual_code=manual,family=family_arg)
            selections[scope][family]=held
            summary[scope][family+'_fixed']={g:metrics(details_for(rs[held['candidate']],codes)) for g,codes in groups.items()}
            sequence=[]
            for row in rows:
                code=row['security_code']
                if code not in rolling_codes:
                    continue
                selected=select_candidate(rs,candidates,by_code,row['apply_date'],manual_code=manual,family=family_arg)
                assert code not in selected['training_codes']
                prediction=next(d for d in rs[selected['candidate']]['details'] if d['security_code']==code)
                sequence.append({'security_code':code,'selection':selected,'prediction':prediction})
            rolling[scope][family]=sequence
            summary[scope][family+'_rolling']={g:metrics([x['prediction'] for x in sequence if x['security_code'] in codes])
                                              for g,codes in groups.items() if g in ('rolling','rolling_model_only','held','recent_model_only')}
            log(f'{scope}/{family}: fixed selection {held["candidate"]}; rolling MAPE={summary[scope][family+"_rolling"]["rolling"]["frozen_funds_mape"]:.4%}')
    save('selections.json',selections)
    save('rolling_predictions.json',rolling)
    target_predictions=[]
    if args.target_snapshot:
        snap=json.loads(args.target_snapshot.read_text(encoding='utf-8'))
        prediction_cutoff=(date.fromisoformat(snap['analysis_date'])+timedelta(days=1)).isoformat()
        for scope,rs in all_replays.items():
            for family in ('baseline','direct_funds','bucket_funds','combined'):
                for selection_time in ('held','as_of') if family!='baseline' else ('baseline',):
                    selected=None if family=='baseline' else (selections[scope][family] if selection_time=='held' else
                        select_candidate(rs,candidates,by_code,prediction_cutoff,manual_code=manual,
                                         family=None if family=='combined' else family))
                    p=baseline if selected is None else candidates[selected['candidate']]
                    pred=t.subscription_context.build_prediction(snap['ipo_info'],history_rows=sources[scope],
                            params=scoped(p,scope),as_of_date=snap['analysis_date'])
                    target_predictions.append({'scope':scope,'family':family,'selection_time':selection_time,
                        'selection':selected,'prediction':pred})
        save('target_predictions.json',target_predictions)
    # Keep stability out of the funding loss. Audit the selected structures on
    # the same recent targets, with fixed parameters across both history scopes.
    stability={}
    variants={'baseline':baseline}
    for scope in sources:
        for family in ('direct_funds','bucket_funds'):
            variants[scope+'_'+family]=candidates[selections[scope][family]['candidate']]
    for name,p in variants.items():
        values=[]
        for r in rows:
            if r['security_code'] not in rolling_codes:
                continue
            pred={}
            for scope in sources:
                for ratio in (.8,1,1.2):
                    modified={**p,HALF_KEY:float(p[HALF_KEY])*ratio}
                    result=t.subscription_context.build_prediction(t._target_from_row(r),history_rows=sources[scope],params=scoped(modified,scope))
                    pred[f'{scope}_{ratio}']=result['frozen_funds_yi']
            values.append({'security_code':r['security_code'],'predictions':pred,
                           'window_relative_change':abs(pred['q3_1']/pred['full_1']-1),
                           'half_life_relative_change':max(abs(pred[f'{s}_{ratio}']/pred[f'{s}_1']-1) for s in sources for ratio in (.8,1.2))})
        stability[name]={'details':values, **{f'{field}_{stat}': (max(v[field] for v in values) if stat=='max' else sum(v[field] for v in values)/len(values))
            for field in ('window_relative_change','half_life_relative_change') for stat in ('mean','max')}}
        log(f'stability {name}: window mean={stability[name]["window_relative_change_mean"]:.4%}; half-life max={stability[name]["half_life_relative_change_max"]:.4%}')
    save('stability.json',stability)
    # Replays use the shared production entry. Verify serialized forecasts and
    # reported thresholds for every chosen family, rather than just funding.
    alignment_count=0
    for scope in sources:
        for name in {v['candidate'] for v in selections[scope].values()}:
            settings=scoped(candidates[name],scope)
            for d in all_replays[scope][name]['details']:
                pred=t.subscription_context.build_prediction(t._target_from_row(by_code[d['security_code']]),history_rows=sources[scope],params=settings)
                assert pred['frozen_funds_yi']==d['predicted_frozen_funds_yi']
                assert pred['guaranteed_threshold_amount_wan']==d['predicted_guaranteed_amount_wan']
                ladders={(x['regular_lots'],x['fractional_lots']):x for x in pred['lot_thresholds']}
                for x in d['manual_ladder_errors']:
                    item=ladders.get((x['regular_lots'],x['fractional_lots']))
                    if item:
                        assert item['threshold_amount_wan']==x['predicted_threshold_amount_wan']
                alignment_count+=1
    assert before==hashes(), 'Production inputs changed during experiment'
    safe_keys=(MODEL_KEY,HALF_KEY,BETA_KEY,'subscription_prediction_direct_similarity_bandwidth',
               'subscription_prediction_recent_market_level_factor','subscription_prediction_recent_market_level_adaptive_enabled',
               'subscription_prediction_recent_market_level_adaptive_min_samples','subscription_prediction_recent_market_level_adaptive_weight')
    save('candidate_parameters.json',{k:{key:p[key] for key in safe_keys} for k,p in candidates.items()})
    report={'settings':vars(args)|{'output_dir':str(out),'target_snapshot':str(args.target_snapshot)},
            'candidates':len(candidates),'manual_override_code':manual,'common_codes':sorted(common),
            'rolling_codes':sorted(rolling_codes),'held_codes':sorted(held_codes),'selections':selections,'metrics':summary,
            'stability':{k:{field:v for field,v in x.items() if field!='details'} for k,x in stability.items()},
            'validation':{'production_input_hashes_unchanged':True,'input_hashes':before,'source_time_violations':0,
                          'production_replay_alignment_count':alignment_count},
            'elapsed_seconds':time.monotonic()-started}
    save('report.json',report)
    log('Complete; no parameter, label, or production dataset writeback.')


if __name__=='__main__':
    main()
