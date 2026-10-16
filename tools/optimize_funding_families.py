"""Independently tune each repaired model before comparing fixed held stocks.

Block coordinate search over declared discrete ranges, followed by local fine
search. Reports the best evaluated setting, not a proof of a global optimum.
"""
from __future__ import annotations

import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import time

import evaluate_repaired_account_funding as comparison
import evaluate_subscription_funding_hypotheses as env
import subscription_funding_hypotheses as h

t=comparison.t
ROOT=comparison.ROOT
PREFIX='subscription_prediction_'
HALF=comparison.HALF
MODEL=comparison.MODEL
POSITION=comparison.POSITION
CUTOFF='2026-09-23'
FAMILIES=('multiple','direct','account','calendar','intercept','t_plus_2')


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def metrics(details):
    return env.funding_metrics(details)


def grids(family):
    common=[{HALF:[2,3,5,6.25,10,15,20]},
            {PREFIX+'recent_market_level_adaptive_weight':[0,.25,.5,.75,1],
             PREFIX+'recent_market_level_adaptive_min_samples':[1,3,5]}]
    if family in ('direct','account'):
        key=PREFIX+'direct_cap_elasticity' if family=='direct' else POSITION
        return [common[0],{PREFIX+'direct_similarity_bandwidth':[.1,.2,.35,.5,.8],
                          key:[0,.15,.25,.5,.75,1] if family=='direct' else [0,.25,.5,.75,1]},common[1]]
    blocks=[{PREFIX+'account_pool_prior_weight':[0,.8,1,1.1,1.2],POSITION:[0,.5,1]},common[0],
            {PREFIX+'cap_factor_direction':['target_over_median','median_over_target'],
             PREFIX+'cap_factor_exponent':[0,.15,.3,.45,.6]},
            {PREFIX+'issue_factor_direction':['target_over_median','median_over_target'],
             PREFIX+'issue_factor_exponent':[0,.15,.3,.45,.6]},
            {PREFIX+'lock_factor_exponent':[0,.15,.3,.45],PREFIX+'multiple_scale':[.85,.95,1,1.05,1.15]},common[1],
            {PREFIX+'similar_top_apply_frozen_weight':[0,.25,.45,.65,.85,1],
             PREFIX+'similar_top_apply_frozen_bandwidth':[.1,.2,.35,.5]},
            {PREFIX+'similar_top_apply_frozen_max_relative_distance':[.2,.35,.5,1],
             PREFIX+'similar_top_apply_frozen_min_samples':[1,3]},
            {PREFIX+'account_pool_prior_min_source_samples':[1,3,5]}]
    if family in ('calendar','intercept'):
        blocks.append({'residual_ridge':[1,4,16],'residual_strength':[0,.5,1]})
    return blocks


def setup(scope):
    rows=sorted([r for r in t.subscription_context.load_history_rows()
                 if ''<t.replay_timeline.result_available_date(r)<='2026-10-16'
                 and (scope=='full' or r['apply_date']>='2026-07-01')],key=t._row_sort_key)
    merged=comparison.previous.pool._merge_rows(t._load_history_rows(t.DEFAULT_HISTORY_PATH),t.subscription_ladder_labels.load_label_rows())
    codes={r['security_code'] for r in rows}
    pools=comparison.previous.rebuild_scoped_pool([r for r in merged if r['security_code'] in codes])
    base={k:v for k,v in t._resolve_subscription_base_params(t.config_loader.load_params()).items() if k.startswith(PREFIX)}
    base.update({MODEL:'multiple',PREFIX+'unified_recency_enabled':True,
                 PREFIX+'recent_market_level_factor':1.,PREFIX+'recent_market_level_adaptive_enabled':True,
                 PREFIX+'similar_top_apply_frozen_enabled':True,POSITION:.5,
                 PREFIX+'direct_similarity_bandwidth':.35,PREFIX+'direct_cap_elasticity':.25,
                 'residual_ridge':4.,'residual_strength':1.})
    return rows,pools,base


def fast_replay(rows,pools,params,codes):
    settings={**params,PREFIX+'account_pool_rows':pools,t.subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY:{}}
    details=[]
    for row in rows:
        if row['security_code'] not in codes or len(t.subscription_context.known_history(rows,row))<3:
            continue
        pred=t.subscription_context.build_prediction(t._target_from_row(row),history_rows=rows,params=settings,funding_only=True)
        actual=float(row['frozen_funds_yi']);amount=pred.get('frozen_funds_yi')
        prior=pred.get('account_pool_prior') or {}
        details.append({'security_code':row['security_code'],'replay_as_of_date':row['apply_date'],
                        'actual_frozen_funds_yi':actual,'predicted_frozen_funds_yi':amount,
                        'frozen_funds_abs_error_yi':abs(amount-actual) if amount else None,
                        'frozen_funds_pct_error':abs(amount/actual-1) if amount else None,
                        'actual_top_apply_below_guaranteed':t._parse_bool(row.get('top_apply_below_guaranteed')),
                        'predicted_top_apply_below_guaranteed':pred.get('top_apply_below_guaranteed'),
                        'predicted_guaranteed_amount_wan':pred.get('guaranteed_threshold_amount_wan'),
                        'prior_applied':prior.get('applied',False),'prior_count_basis':prior.get('account_count_basis'),
                        'prior_source_dates':{s['security_code']:s['distribution_available_date'] for s in prior.get('samples',[])},
                        'manual_ladder_errors':[]})
    return details


def environmental(base_details,source_details,rows,params,family,freeze=None):
    if family not in ('calendar','intercept'): return base_details
    by_code={r['security_code']:r for r in rows}
    result=[]
    for d in base_details:
        row=by_code[d['security_code']]
        fit=env.residual_correction(source_details,by_code,row,freeze or row['apply_date'],
            h.FEATURE_NAMES if family=='calendar' else (),
            manual_code=str(params.get(PREFIX+'target_frozen_funds_code') or ''),half=float(params[HALF]),ridge=params['residual_ridge'])
        factor=fit['factor']**params['residual_strength']
        item=env.adjusted_detail(d,factor)
        # Recompute the classification guardrail from the adjusted amount.
        shares=item['predicted_frozen_funds_yi']*1e8/float(row['issue_price'])
        threshold=t.subscription_predictor._ceil_to_lot(100*shares/float(row['online_issue_shares']))
        capshares=t.subscription_predictor._money_to_shares(float(row['top_apply_amount_wan']),float(row['issue_price']))
        item['predicted_top_apply_below_guaranteed']=threshold>capshares
        item['residual_fit']=fit
        result.append(item)
    return result


def optimize_job(scope,family,directory,seeds=None,seeds_only=False):
    out=Path(directory); logpath=out/f'{scope}_{family}_progress.log';start=time.monotonic()
    def log(msg):
        with logpath.open('a',encoding='utf-8') as f:f.write(f'[{time.monotonic()-start:.1f}s] {msg}\n')
    rows,pools,base=setup(scope)
    base[MODEL]={'direct':'direct_funds','account':'account_pool_funds'}.get(family,'multiple')
    if family=='t_plus_2':base[PREFIX+'lock_days_basis']='t_plus_2_calendar_2026'
    manual=str(base.get(PREFIX+'target_frozen_funds_code') or '')
    training={r['security_code'] for r in rows if r['security_code']!=manual and r['apply_date']<CUTOFF
              and ''<t.replay_timeline.result_available_date(r)<CUTOFF}
    held={r['security_code'] for r in rows if r['apply_date']>=CUTOFF and r['security_code']!=manual}
    # A common repaired original-model guardrail for every family in a scope.
    guard_params={**base,MODEL:'multiple',PREFIX+'lock_days_basis':'result_date'}
    guard=fast_replay(rows,pools,guard_params,training)
    max_misses=sum(d['actual_top_apply_below_guaranteed'] and not d['predicted_top_apply_below_guaranteed'] for d in guard)+1
    seen={};raw_cache={};trials=[]
    def evaluate(p):
        signature=json.dumps(p,sort_keys=True)
        if signature in seen:return seen[signature]
        raw_key=json.dumps({k:v for k,v in p.items() if not k.startswith('residual_')},sort_keys=True)
        if raw_key not in raw_cache:raw_cache[raw_key]=fast_replay(rows,pools,p,training)
        raw=raw_cache[raw_key];ds=environmental(raw,raw,rows,p,family)
        m=metrics(ds)
        assert len(ds)>=8
        misses=sum(d['actual_top_apply_below_guaranteed'] and not d['predicted_top_apply_below_guaranteed'] for d in ds)
        passed=not m['frozen_funds_missing_prediction_rows'] and misses<=max_misses
        rank=(not passed,m['frozen_funds_weighted_mape'],m['frozen_funds_mape'])
        value={'params':p.copy(),'metrics':m,'rank':rank,'misses':misses,'guardrail_passed':passed}
        seen[signature]=value;trials.append(value)
        if len(trials)%20==0:log(f'{len(trials)} candidates; best training weighted MAPE {min(x["rank"] for x in trials)[1]:.4%}')
        return value
    best=evaluate(base);rounds=[]
    for seed in seeds or []:
        candidate=evaluate({**base,**seed})
        if candidate['rank']<best['rank']:best=candidate
    for iteration in range(1,1 if seeds_only else 5):
        before=best['rank']
        for block in grids(family):
            anchor=best['params'].copy();keys=list(block)
            for values in itertools.product(*(block[k] for k in keys)):
                candidate=evaluate({**anchor,**dict(zip(keys,values))})
                if candidate['rank']<best['rank']:best=candidate
        rounds.append({'round':iteration,'evaluated':len(trials),'rank':best['rank']})
        log(f'round {iteration}: {len(trials)} evaluated; weighted MAPE {best["rank"][1]:.4%}')
        if best['rank']==before:break
    # One local refinement sweep, using only training labels.
    fine_keys={HALF:(1,30,.2),PREFIX+'recent_market_level_adaptive_weight':(0,1,.1)}
    if family in ('direct','account'):
        fine_keys[PREFIX+'direct_similarity_bandwidth']=(.05,1,.05)
        fine_keys[PREFIX+'direct_cap_elasticity' if family=='direct' else POSITION]=(0,1,.1)
    else:
        fine_keys.update({PREFIX+'multiple_scale':(.7,1.3,.025),PREFIX+'cap_factor_exponent':(0,.8,.05),
                         PREFIX+'issue_factor_exponent':(0,.8,.05),PREFIX+'lock_factor_exponent':(0,.6,.05),
                         PREFIX+'account_pool_prior_weight':(0,1.5,.05),
                         PREFIX+'similar_top_apply_frozen_weight':(0,1,.1),
                         PREFIX+'similar_top_apply_frozen_bandwidth':(.05,.8,.05),POSITION:(0,1,.1)})
    for key,(low,high,step) in ({} if seeds_only else fine_keys).items():
        anchor=best['params'].copy();value=float(anchor[key]);delta=value*step if key==HALF else step
        for v in (max(low,value-delta),min(high,value+delta)):
            candidate=evaluate({**anchor,key:round(v,8)})
            if candidate['rank']<best['rank']:best=candidate
    # Persist chosen parameters before any held outcomes are evaluated.
    selected={**best,'scope':scope,'family':family,'cutoff_exclusive':CUTOFF,
              'training_codes':sorted(training),'rounds':rounds,'candidate_count':len(trials),
              'max_false_negative_count':max_misses,'search_ranges':grids(family),
              'search_method':'cross_check_discovered_collapsed_configurations' if seeds_only else 'block_coordinate_up_to_4_passes_plus_local_fine_sweep',
              'extra_start_count':len(seeds or []),
              'global_optimum_proven':False}
    save(out/f'{scope}_{family}_selection.json',selected)
    save(out/f'{scope}_{family}_trials.json',trials)
    p=best['params'];settings={**p,PREFIX+'account_pool_rows':pools,t.subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY:{}}
    train_raw=fast_replay(rows,pools,p,training)
    raw_held=fast_replay(rows,pools,p,held)
    held_details=environmental(raw_held,train_raw,rows,p,family,freeze=CUTOFF)
    # Full production-path agreement for every selected training/held forecast.
    aligned=0
    for d in [*train_raw,*raw_held]:
        row=next(r for r in rows if r['security_code']==d['security_code'])
        pred=t.subscription_context.build_prediction(t._target_from_row(row),history_rows=rows,params=settings)
        assert pred['frozen_funds_yi']==d['predicted_frozen_funds_yi']
        assert pred['guaranteed_threshold_amount_wan']==d['predicted_guaranteed_amount_wan']
        for available in d['prior_source_dates'].values():assert available<row['apply_date']
        aligned+=1
    snap=json.loads((ROOT/'.tmp/industry_classifier_20261012/analysis_920285.json').read_text(encoding='utf-8'))
    pred=t.subscription_context.build_prediction(snap['ipo_info'],history_rows=rows,params=settings,as_of_date=snap['analysis_date'])
    assert '920285' not in pred['history_context']['history_source_codes'] and '920296' not in pred['history_context']['history_source_codes']
    target_value=pred['frozen_funds_yi'];target_fit=None
    if family in ('calendar','intercept'):
        target_row=t.subscription_context.target_row_from_ipo(snap['ipo_info'])
        target_fit=env.residual_correction(train_raw,{r['security_code']:r for r in rows},target_row,CUTOFF,
            h.FEATURE_NAMES if family=='calendar' else (),manual_code=manual,half=p[HALF],ridge=p['residual_ridge'])
        target_value*=target_fit['factor']**p['residual_strength']
    result={'selection':selected,'training_metrics':best['metrics'],'held_metrics':metrics(held_details),
            'held_details':held_details,'training_details':environmental(train_raw,train_raw,rows,p,family),
            'target_frozen_funds_yi':target_value,'target_base_prediction':pred,'target_residual_fit':target_fit,
            'full_path_alignment_checks':aligned,'elapsed_seconds':time.monotonic()-start}
    save(out/f'{scope}_{family}_result.json',result)
    log(f'complete: {len(trials)} candidates, held {result["held_metrics"]["frozen_funds_mape"]:.4%}')
    return {'scope':scope,'family':family,'count':len(trials),'held_mape':result['held_metrics']['frozen_funds_mape']}


def adopt_collapsed_candidates(out,results,workers=4):
    """Credit the parent model for better settings found in a zero-effect child.

    Selection still uses training scores only. The zero-strength residual model
    must not appear to outperform its identical parent solely through search.
    """
    archive=out/'nested_search';archive.mkdir(exist_ok=True)
    for path in out.glob('*_selection.json'):shutil.copy2(path,archive/path.name)
    for path in out.glob('*_trials.json'):shutil.copy2(path,archive/path.name)
    for path in out.glob('*_result.json'):shutil.copy2(path,archive/path.name)
    save(archive/'report.json',{'results':results})
    jobs=[]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for scope in ('full','q3'):
            collapsed=[r['selection']['params'] for family,r in results[scope].items()
                       if family in ('calendar','intercept') and r['selection']['params']['residual_strength']==0]
            if not collapsed:continue
            for family in ('multiple','t_plus_2'):
                own=results[scope][family]['selection']['params']
                basis='t_plus_2_calendar_2026' if family=='t_plus_2' else 'result_date'
                seeds=[own,*[{**p,MODEL:'multiple',PREFIX+'lock_days_basis':basis} for p in collapsed]]
                jobs.append((scope,family,executor.submit(optimize_job,scope,family,str(out),seeds,True)))
        for scope,family,future in jobs:
            print(json.dumps(future.result(),ensure_ascii=False),flush=True)
            previous=results[scope][family]['selection']
            result=json.loads((out/f'{scope}_{family}_result.json').read_text(encoding='utf-8'))
            result['selection']['initial_search_candidate_count']=previous['candidate_count']+previous.get('initial_search_candidate_count',0)
            result['selection']['previous_search_method']=previous['search_method']
            results[scope][family]=result
            save(out/f'{scope}_{family}_result.json',result)
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    def hashes():
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                (t.DEFAULT_HISTORY_PATH,t.DEFAULT_LADDER_LABEL_PATH,t.DEFAULT_PARAMS_PATH,t.DEFAULT_AUTO_RECORD_PATH,
                 ROOT/'data/offline_tuning/account_pool_history_thresholds.csv')}
    initial=hashes();save(out/'input_hashes.json',initial)
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures=[executor.submit(optimize_job,scope,family,str(out)) for scope in ('full','q3') for family in FAMILIES]
        for future in as_completed(futures):print(json.dumps(future.result(),ensure_ascii=False),flush=True)
    assert hashes()==initial,'Production inputs changed'
    results={scope:{family:json.loads((out/f'{scope}_{family}_result.json').read_text(encoding='utf-8')) for family in FAMILIES}
             for scope in ('full','q3')}
    # Residual models contain the original model at strength=0. A poorer
    # training score here indicates a local-search failure, not model quality.
    archive=out/'initial_search';archive.mkdir(exist_ok=True)
    for path in out.glob('*_selection.json'):shutil.copy2(path,archive/path.name)
    for path in out.glob('*_trials.json'):shutil.copy2(path,archive/path.name)
    for path in out.glob('*_result.json'):shutil.copy2(path,archive/path.name)
    save(archive/'report.json',{'results':results})
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures=[]
        for scope in ('full','q3'):
            for family in ('calendar','intercept'):
                own=results[scope][family]['selection']['params']
                parent={**results[scope]['multiple']['selection']['params'],'residual_strength':0.}
                futures.append(executor.submit(optimize_job,scope,family,str(out),[own,parent]))
        for future in as_completed(futures):print(json.dumps(future.result(),ensure_ascii=False),flush=True)
    for scope in ('full','q3'):
        for family in ('calendar','intercept'):
            refined=json.loads((out/f'{scope}_{family}_result.json').read_text(encoding='utf-8'))
            refined['selection']['initial_search_candidate_count']=results[scope][family]['selection']['candidate_count']
            assert refined['training_metrics']['frozen_funds_weighted_mape']<=results[scope]['multiple']['training_metrics']['frozen_funds_weighted_mape']+1e-12
            results[scope][family]=refined
            save(out/f'{scope}_{family}_result.json',refined)
    results=adopt_collapsed_candidates(out,results,args.workers)
    assert hashes()==initial,'Production inputs changed'
    save(out/'report.json',{'training_cutoff_exclusive':CUTOFF,'target_as_of':'2026-10-12','results':results,
                          'input_hashes_unchanged':True,'production_parameters_written':False,
                          'nested_family_multistart_verified':True})
    print('All independent searches and held comparisons complete.',flush=True)


if __name__=='__main__':main()
