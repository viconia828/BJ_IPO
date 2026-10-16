"""Offline calendar and bucket-amount audit; no tuning or production writeback.

Reuses the frozen 2026-10-16 experiment only if its input hashes still match.
Calendar residual regressions are exploratory: no model is picked using holdout.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import evaluate_subscription_funding_models as previous
import tune_subscription_prediction as t
import subscription_funding_hypotheses as h

ROOT = previous.ROOT
HALF = previous.HALF_KEY
MODEL = previous.MODEL_KEY


def residual_correction(details, by_code, target, cutoff, features, *, manual_code='', half=6.25, ridge=4.0):
    """Fit only past out-of-sample log residuals (default ridge=4, +/-10% cap).

    Intercept-only is a control for generic residual recalibration. Each feature
    needs 3 past nonzero observations; collinearity is handled by the fixed ridge.
    Recency ages include all known rows, as in the funding model.
    """
    known = [r for r in by_code.values() if r['apply_date'] < cutoff
             and '' < t.replay_timeline.result_available_date(r) < cutoff
             and r['security_code'] != target['security_code']]
    dates = [t.replay_timeline.result_available_date(r) for r in known]
    train = [d for d in details if d['security_code'] != manual_code
             and d['security_code'] in {r['security_code'] for r in known}
             and (d.get('predicted_frozen_funds_yi') or 0) > 0]
    support = {k:sum(bool(h.calendar_features(by_code[d['security_code']]['apply_date'])[k]) for d in train)
               for k in features}
    active = [k for k in features if support[k] >= 3]
    result = {'factor':1.0,'training_codes':[d['security_code'] for d in train],
              'cutoff_exclusive':cutoff,'feature_support':support,'active_features':active,
              'ridge':ridge,'half_life_samples':half,'coefficients':{}}
    if len(train)<8:
        return {**result,'reason':'fewer_than_8_past_residuals'}
    matrix, y, weights = [], [], []
    for d in train:
        row = by_code[d['security_code']]
        feature = h.calendar_features(row['apply_date'])
        matrix.append([1.0,*[feature[k] for k in active]])
        y.append(np.clip(math.log(d['actual_frozen_funds_yi']/d['predicted_frozen_funds_yi']),-.3,.3))
        weights.append(.5**(sum(x>t.replay_timeline.result_available_date(row) for x in dates)/half))
    x,y,w = np.array(matrix),np.array(y),np.array(weights)
    if not math.isfinite(ridge) or ridge <= 0:
        raise ValueError('Residual ridge must be positive')
    coef = np.linalg.solve(x.T@(w[:,None]*x)+ridge*np.eye(x.shape[1]), x.T@(w*y))
    feature = h.calendar_features(target['apply_date'])
    predicted = np.array([1.,*[feature[k] for k in active]])@coef
    return {**result,'factor':math.exp(float(np.clip(predicted,math.log(.9),math.log(1.1)))),
            'coefficients':dict(zip(['intercept',*active],coef.tolist())),
            'effective_samples':float(w.sum()**2/(w*w).sum()),'reason':'past_residual_ridge'}


def adjusted_detail(detail, factor):
    result = dict(detail)
    amount = detail['predicted_frozen_funds_yi']*factor
    result.update(predicted_frozen_funds_yi=amount,
                  frozen_funds_abs_error_yi=abs(amount-detail['actual_frozen_funds_yi']),
                  frozen_funds_pct_error=abs(amount/detail['actual_frozen_funds_yi']-1))
    # This is a funding-only residual audit; do not report stale ladder metrics.
    result['manual_ladder_errors'] = []
    return result


def funding_metrics(details):
    result = previous.metrics(details)
    return {k:v for k,v in result.items() if k.startswith('frozen_funds_') or k=='sample_count'}


def write_readable_report(out: Path):
    def read(name):
        return json.loads((out/name).read_text(encoding='utf-8'))
    report=read('report.json');models=report['model_metrics'];env=read('environment_predictions.json')
    targets=read('target_predictions.json');audit=read('source_audit.json')
    reference=13529.8485  # Display-only provisional Yuanda label; never used in fits.
    lines=['资金环境与分户金额假设验证 — 2026-10-17',
           '结论：本轮没有发现足以替换正式模型的稳定提升。保留实验开关，正式默认及参数未改。',
           '', '样本与方法',
           '冻结数据截至2026-10-16，共66只已公布结果；不包括远大10月19日才公布的结果。',
           '8月起17只纯模型预测，排除凯大人工冻资覆盖；9月23日起留出莫森、南方、文峰3只。',
           '全历史与7月1日起两种范围；基础近期半衰期6.25只，评分半衰期12只。',
           '环境修正：过去逐时点预测的log残差，固定ridge=4，限幅±10%，各特征至少3个已知非零样本。',
           '17只按每票当时已知结果滚动学习；留出3只的环境系数固定在9月23日前。',
           '另列仅截距校准，区分日历特征与普通水位校准的作用；不根据远大结果挑模型。',
           '', '全历史主要对比（MAPE为平均绝对百分比误差，越低越好）',
           '方案\t8月起17只\t近期加权误差\t最近3只\t远大估算/亿元']
    def row(label,m,held,amount):
        lines.append(f'{label}\t{m["frozen_funds_mape"]:.2%}\t{m["frozen_funds_weighted_mape"]:.2%}\t{held["frozen_funds_mape"]:.2%}\t{amount:,.2f}')
    for name,label in (('baseline','原模型'),('calendar_lock','T+2替换占用天数'),
                       ('bucket_h6.25','原分户模型'),('bucket_h6.25_conserve_0','守恒分户：低档内位置'),
                       ('bucket_h6.25_conserve_0.5','守恒分户：中档内位置'),('bucket_h6.25_conserve_1','守恒分户：高档内位置')):
        row(label,models['full'][name]['rolling17'],models['full'][name]['held3'],targets['full'][name]['frozen_funds_yi'])
    for key,label in (('intercept_only','原模型+仅截距校准'),('calendar_all','原模型+全部日历特征')):
        m=report['environment_metrics']['full']['baseline'][key]
        row(label,m['rolling17'],m['fixed_held3'],env['full']['baseline'][key]['target']['rolling']['frozen_funds_yi'])
    lines.extend(['远大一列使用10月12日报告时点，只作额外验证；环境系数使用该时点已知样本。',
                  f'远大分户门槛反推参考区间：(13,528.935, 13,530.762]亿元；中点{reference:,.4f}，不是正式公告结果。',
                  '环境版远大虽接近参考，但近期3只退步，不能仅凭这只票选用。',
                  '', '7月起范围与时间口径复核'])
    for scope,label in (('full','全历史'),('q3','7月起')):
        b=models[scope]['baseline'];cal=report['environment_metrics'][scope]['baseline']['calendar_all']
        lines.append(f'{label}：原模型17只{b["rolling17"]["frozen_funds_mape"]:.2%}，日历版{cal["rolling17"]["frozen_funds_mape"]:.2%}；最近3只{b["held3"]["frozen_funds_mape"]:.2%}→{cal["fixed_held3"]["frozen_funds_mape"]:.2%}。')
        s=report['lock_selection_summary'][scope]['t_plus_2_calendar_2026']
        lines.append(f'{label}T+2系数0/0.15/0.30/0.45逐时点选择：17只{s["rolling17"]["frozen_funds_mape"]:.2%}，9/23固定选择后的3只{s["fixed_held3"]["frozen_funds_mape"]:.2%}；仍未改善。')
    lines.extend(['南方旧字段为申购9/28至结果10/08，共10天；T+2代理退回日9/30，预计资金占用2天。',
                  '远大旧字段为10/14至结果10/19，共5天；T+2代理为10/16，快照REFUND_DATE也是10/16。',
                  '旧字段实际衡量的是公告间隔，不能解释为真实冻结时间；改成代理并重选有限系数后仍未通过准确率验证。',
                  '', '日历特征样本支撑（全历史可评分样本）'])
    for k,v in report['feature_diagnostics'].items():
        lines.append(f'{k}: {v["sample_count"]}只，原模型平均有符号误差{v["signed_bias"]:.2%}；代码{",".join(v["codes"])}。')
    lines.extend(['季末仅3只、长假前仅2只，9月23日前季末只有2只，无法可靠学习季末独立系数。',
                  '这些是日历环境代理，未验证回购利率、账户可用余额等真实流动性变量；不能概括为所有资金环境特征都无效。',
                  '', '分户金额审计'])
    lines.append(f'已公布的66行中，真实subscription_distribution_json非空为{sum(x["real_distribution_present"] for x in audit)}；64只有粗拟合分档，2只缺分档。')
    lines.extend(['分档由获配总户数、总手数压缩拟合；未获配均额由实际总冻资倒算。合计吻合是约束，不能作为模型准确性的独立证据。',
                  '旧方法把手工门槛替换进分档后，没有同步重算未获配户均，导致原始分档合计不再等于该票实际冻资。',
                  '代码\t实际冻资/亿元\t门槛替换后原始合计/亿元\t守恒低/中/高位置的未获配户均/万元'])
    for x in audit:
        if x['security_code'] in ('920093','920162','920196','920296'):
            means='/'.join(f'{x["bucket_scenarios"][str(p)]["unallocated_mean_wan"]:.2f}' for p in (0,.5,1))
            lines.append(f'{x["security_code"]}\t{x["actual_frozen_funds_yi"]:,.2f}\t{x["raw_bucket_total_yi"]:,.2f}\t{means}')
    lines.extend(['旧简化分户模型已通过整体归一补齐总额，但补齐总额不等于恢复真实分布。',
                  '本次各档上下界取手工门槛/下一门槛/顶格；在可行范围选低、中、高共同位置，未获配均额重新守恒求解。',
                  '跨股票迁移采用档内两端点分布截断，属于敏感性情景，不是真实金额分布。',
                  '低/中/高情景都能复原源股票总冻资，近期预测却仍弱于原模型，说明只修正户均金额不足以解决问题。',
                  '该分户迁移在目标顶格高于源股票顶格时不会自动扩张资金，无法从被顶格截断的历史申购额识别潜在新增需求。',
                  '', '可复核性与限制',
                  f'生产输入哈希未变；{report["validation"]["shared_entry_alignment_count"]}项近期预测与共享正式入口一致；来源时点审计0违规。',
                  '这是事后提出假设的逐时点验证，不是事前封存的盲测。手工标签缺独立可用日期时仍沿用原结果公告日代理。',
                  '环境修正只核验冻资；没有把未重算的碎股门槛当成新模型结果。',
                  '正式参数、生产数据、标签均未写回。实验代码及测试保留，可重跑。',
                  '2026北交所休市安排（2025-12-22公布）：'+h.CALENDAR_SOURCE,
                  'T+2规则参考：https://www.bse.cn/fxrz_list/200018119.html',
                  '2026公告例证：https://www.bse.cn/disclosure/2026/2026-06-04/1780568905_075141.pdf',
                  '', '机器明细：report.json / source_audit.json / environment_predictions.json / lock_selection.json / target_predictions.json'])
    (out/'资金环境与分户金额验证.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,default=ROOT/'tmp/subscription_objective_audit_20261016/simple_funding_run')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'tmp/subscription_environment_audit_20261017')
    args = parser.parse_args()
    out=args.output_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
    start=time.monotonic()
    def read(name):
        return json.loads((args.source_dir/name).read_text(encoding='utf-8'))
    def save(name,obj):
        (out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    def log(message):
        message=f'[{time.monotonic()-start:.1f}s] {message}'
        print(message,flush=True)
        with (out/'progress.log').open('a',encoding='utf-8') as handle:
            handle.write(message+'\n')
    prior=read('report.json')
    expected=prior['validation']['input_hashes']
    def hashes():
        return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in expected}
    assert hashes()==expected,'Input files differ from frozen comparison; rerun the base experiment first'
    all_rows=t.subscription_context.load_history_rows()
    rows=sorted([r for r in all_rows if ''<t.replay_timeline.result_available_date(r)<='2026-10-16'],key=t._row_sort_key)
    by_code={r['security_code']:r for r in rows}
    manual=prior['manual_override_code']
    rolling=set(prior['rolling_codes'])-{manual}
    held=set(prior['held_codes'])-{manual}
    baseline=t._resolve_subscription_base_params(t.config_loader.load_params())
    baseline[MODEL]='multiple'
    t._preload_account_pool_rows_for_tuning(baseline)
    candidates=read('candidate_parameters.json')
    q3pool=read('q3_pool_snapshots.json')
    snap=json.loads((ROOT/'.tmp/industry_classifier_20261012/analysis_920285.json').read_text(encoding='utf-8'))
    target=t.subscription_context.target_row_from_ipo(snap['ipo_info'])
    target_cutoff=min(target['apply_date'],(date.fromisoformat(snap['analysis_date'])+timedelta(days=1)).isoformat())
    audits=[]
    for r in rows:
        fit=json.loads(r.get('allocation_fit_json') or '{}')
        cap=float(r['top_apply_amount_wan'])
        own=t.subscription_context._account_pool_demand_wan_from_row(r,cap)
        total=float(r['frozen_funds_yi'])*1e4
        factor=total/own['estimated_demand_wan'] if own else None
        thresholds=t.subscription_context._manual_ladder_thresholds_by_lot(r)
        scaled_over_cap=[]
        for b in fit.get('buckets',[]):
            amount=thresholds.get(int(b['allocated_lots']),float(b['threshold_amount_wan']))
            if factor and amount*factor>cap+1e-6:
                scaled_over_cap.append({'accounts':b['accounts'],'implied_amount_wan':amount*factor})
        record={'security_code':r['security_code'],'security_name':r.get('security_name'),
                'apply_date':r['apply_date'],'actual_frozen_funds_yi':total/1e4,
                'raw_bucket_total_yi':own['estimated_demand_wan']/1e4 if own else None,
                'uniform_reconciliation_factor':factor,'uniform_scale_cap_violations':scaled_over_cap,
                'real_distribution_present':bool(json.loads(r.get('subscription_distribution_json') or '[]')),
                'fit_quality':fit.get('fit_quality'),
                'old_result_interval_days':(date.fromisoformat(r['issue_result_date'])-date.fromisoformat(r['apply_date'])).days,
                'calendar':h.calendar_features(r['apply_date']),
                'bucket_scenarios':{str(p):h.conserving_buckets(r,p) for p in (0,.5,1)}}
        for p,s in record['bucket_scenarios'].items():
            if s['available']:
                demand=h.conserving_demand(r,cap,float(p))
                assert abs(demand['estimated_demand_wan']/total-1)<1e-9
        audits.append(record)
    save('source_audit.json',audits)
    log(f'Input audit: {len(rows)} rows; real distributions={sum(x["real_distribution_present"] for x in audits)}')
    all_replays={}; all_targets={}; summary={}; parameters={}
    alignment=0
    for scope in ('full','q3'):
        source=rows if scope=='full' else [r for r in rows if r['apply_date']>='2026-07-01']
        replays={name:read(f'{scope}_{name}_replay.json') for name in ('baseline','direct_h6.25_b0.25','bucket_h6.25')}
        settings={'baseline':baseline,**{name:{**baseline,**candidates[name]} for name in replays if name!='baseline'}}
        settings['calendar_lock']={**baseline,'subscription_prediction_lock_days_basis':'t_plus_2_calendar_2026'}
        # Follow-up to the direct replacement: isolate coefficient adaptation
        # on a small declared grid, with labels available before each selection.
        for basis in ('result_date','t_plus_2_calendar_2026'):
            for exponent in (0,.15,.3,.45):
                if exponent == .3:
                    continue  # Existing baseline/calendar_lock use 0.3.
                settings[f'lock_{basis}_{exponent}']={**baseline,
                    'subscription_prediction_lock_days_basis':basis,
                    'subscription_prediction_lock_factor_exponent':exponent}
        # Fixed 6.25 keeps amount hypotheses separate from reselecting recency.
        # Also repeat the previously selected bucket half-life as sensitivity.
        best=prior['selections'][scope]['bucket_funds']['candidate']
        for half_key in dict.fromkeys(('bucket_h6.25',best)):
            if half_key not in replays:
                replays[half_key]=read(f'{scope}_{half_key}_replay.json')
                settings[half_key]={**baseline,**candidates[half_key]}
            for position in (0,.5,1):
                settings[f'{half_key}_conserve_{position}']={**baseline,**candidates[half_key],
                    'subscription_prediction_bucket_amount_basis':'conserving_bounds',
                    'subscription_prediction_bucket_amount_position':position}
        targets={}
        for name,p in settings.items():
            params={**p,t.subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY:{}}
            if scope=='q3':
                params['subscription_prediction_account_pool_rows']=q3pool
            if name not in replays:
                replays[name]=t.evaluate_subscription_prediction(source,params=params)
                save(f'{scope}_{name}_replay.json',replays[name])
            prediction=t.subscription_context.build_prediction(snap['ipo_info'],history_rows=source,params=params,as_of_date=snap['analysis_date'])
            targets[name]=prediction
            # Rebuild recent forecasts through the same shared entry and audit sources.
            for d in replays[name]['details']:
                for field in ('history_source_codes','funding_source_codes','recent_market_level_source_codes',
                              'account_pool_source_codes','account_pool_effective_source_codes'):
                    for code in d.get(field) or []:
                        assert t.replay_timeline.known_before(by_code[code],by_code[d['security_code']])
                if d['security_code'] in held:
                    check=t.subscription_context.build_prediction(t._target_from_row(by_code[d['security_code']]),history_rows=source,params=params)
                    assert check['frozen_funds_yi']==d['predicted_frozen_funds_yi']
                    alignment+=1
            summary.setdefault(scope,{})[name]={group:funding_metrics([d for d in replays[name]['details'] if d['security_code'] in codes])
                                               for group,codes in (('rolling17',rolling),('held3',held))}
            log(f'{scope}/{name}: rolling={summary[scope][name]["rolling17"]["frozen_funds_mape"]:.3%}; held={summary[scope][name]["held3"]["frozen_funds_mape"]:.3%}')
        parameters[scope]={name:{key:value for key,value in p.items() if key in (MODEL,HALF,'subscription_prediction_lock_days_basis',
                    'subscription_prediction_lock_factor_exponent','subscription_prediction_bucket_amount_basis',
                    'subscription_prediction_bucket_amount_position')} for name,p in settings.items()}
        all_replays[scope]=replays;all_targets[scope]=targets
    save('model_metrics.json',summary);save('target_predictions.json',all_targets);save('parameters.json',parameters)
    env={}; env_summary={}; feature_groups={'intercept_only':(), 'calendar_all':h.FEATURE_NAMES,
                                            **{k:(k,) for k in h.FEATURE_NAMES}}
    for scope,replays in all_replays.items():
        scope_rows={k:r for k,r in by_code.items() if scope=='full' or r['apply_date']>='2026-07-01'}
        env[scope]={};env_summary[scope]={}
        for base in ('baseline','calendar_lock','direct_h6.25_b0.25'):
            details=replays[base]['details']; output={}; scores={}
            for name,features in feature_groups.items():
                sequence=[]
                for d in details:
                    if d['security_code'] not in rolling:
                        continue
                    row=by_code[d['security_code']]
                    for mode,cutoff in (('rolling',row['apply_date']),('fixed_0923','2026-09-23')):
                        if mode=='fixed_0923' and d['security_code'] not in held:
                            continue
                        corr=residual_correction(details,scope_rows,row,cutoff,features,manual_code=manual)
                        assert all(t.replay_timeline.known_before(by_code[c],row) for c in corr['training_codes'])
                        sequence.append({'security_code':d['security_code'],'mode':mode,'correction':corr,
                                         'prediction':adjusted_detail(d,corr['factor'])})
                target_variants={}
                for mode,cutoff in (('rolling',target_cutoff),('fixed_0923','2026-09-23')):
                    corr=residual_correction(details,scope_rows,target,cutoff,features,manual_code=manual)
                    assert all(t.replay_timeline.result_available_date(by_code[c])<target_cutoff for c in corr['training_codes'])
                    target_variants[mode]={'correction':corr,'frozen_funds_yi':all_targets[scope][base]['frozen_funds_yi']*corr['factor']}
                output[name]={'sequence':sequence,'target':target_variants}
                scores[name]={group:funding_metrics([x['prediction'] for x in sequence if x['mode']==mode and x['security_code'] in codes])
                              for group,mode,codes in (('rolling17','rolling',rolling),('rolling_held3','rolling',held),('fixed_held3','fixed_0923',held))}
            env[scope][base]=output;env_summary[scope][base]=scores
    save('environment_predictions.json',env);save('environment_metrics.json',env_summary)
    lock_selection={}
    for scope,replays in all_replays.items():
        lock_selection[scope]={}
        for basis,fallback in (('result_date','baseline'),('t_plus_2_calendar_2026','calendar_lock')):
            names=[fallback,*[f'lock_{basis}_{e}' for e in (0,.15,.45)]]
            def choose(cutoff):
                def available(name):
                    return [d for d in replays[name]['details'] if d['security_code']!=manual
                            and ''<t.replay_timeline.result_available_date(by_code[d['security_code']])<cutoff
                            and by_code[d['security_code']]['apply_date']<cutoff]
                baseline_misses=sum(d['actual_top_apply_below_guaranteed'] and not d['predicted_top_apply_below_guaranteed'] for d in available('baseline'))
                ranks=[]
                for name in names:
                    details=available(name)
                    misses=sum(d['actual_top_apply_below_guaranteed'] and not d['predicted_top_apply_below_guaranteed'] for d in details)
                    m=funding_metrics(details)
                    if len(details)>=8 and not m['frozen_funds_missing_prediction_rows'] and misses<=baseline_misses+1:
                        ranks.append((m['frozen_funds_weighted_mape'],m['frozen_funds_mape'],name))
                name=min(ranks)[2] if ranks else fallback
                return {'model':name,'cutoff_exclusive':cutoff,'training_codes':[d['security_code'] for d in available(name)],
                        'reason':'past_weighted_mape' if ranks else 'insufficient_samples_or_guardrail'}
            sequence=[]
            fixed=choose('2026-09-23')
            for code in sorted(rolling,key=lambda c:by_code[c]['apply_date']):
                chosen=choose(by_code[code]['apply_date'])
                d=next(x for x in replays[chosen['model']]['details'] if x['security_code']==code)
                sequence.append({'selection':chosen,'prediction':d})
            target_choice=choose(target_cutoff)
            lock_selection[scope][basis]={'fixed_selection':fixed,'rolling':sequence,
                'rolling17':funding_metrics([x['prediction'] for x in sequence]),
                'fixed_held3':funding_metrics([d for d in replays[fixed['model']]['details'] if d['security_code'] in held]),
                'target_as_of_selection':target_choice,
                'target_frozen_funds_yi':all_targets[scope][target_choice['model']]['frozen_funds_yi']}
    save('lock_selection.json',lock_selection)
    # Descriptive support/bias, explicitly not used to choose a feature subset.
    feature_diagnostics={}
    for name in h.FEATURE_NAMES:
        active={x['security_code'] for x in audits if x['calendar'][name]}
        matching=[d for d in all_replays['full']['baseline']['details'] if d['security_code'] in active and d['security_code']!=manual]
        feature_diagnostics[name]={'codes':[d['security_code'] for d in matching],
            'sample_count':len(matching),'signed_bias':sum(d['predicted_frozen_funds_yi']/d['actual_frozen_funds_yi']-1 for d in matching)/len(matching) if matching else None}
    assert hashes()==expected,'Production files changed during audit'
    save('report.json',{'as_of':'2026-10-16','history_rows':len(rows),'manual_override_excluded':manual,
        'rolling_codes':sorted(rolling),'held_codes':sorted(held),'calendar_source':h.CALENDAR_SOURCE,
        'calendar_published':h.CALENDAR_PUBLISHED,'calendar_features':h.FEATURE_NAMES,
        'feature_diagnostics':feature_diagnostics,'model_metrics':summary,'environment_metrics':env_summary,
        'lock_coefficient_grid':[0,.15,.3,.45],
        'lock_selection_summary':{scope:{basis:{key:value for key,value in s.items() if key!='rolling'} for basis,s in groups.items()}
                                  for scope,groups in lock_selection.items()},
        'validation':{'input_hashes':expected,'production_input_hashes_unchanged':True,'source_time_violations':0,
                      'shared_entry_alignment_count':alignment,'yuanda_not_used_for_selection':True},
        'limits':['Synthetic bucket counts; bands and unallocated means are not identified from real distributions.',
                  'T+2 is a scheduled proxy; result availability dates remain unchanged.',
                  'Exploratory architecture choices made in October; not a sealed prospective test.',
                  'Manual labels without independent availability dates still use the result announcement date proxy.',
                  'Calendar residual changes only score funding; no stale ladder accuracy is reported.'],
        'elapsed_seconds':time.monotonic()-start})
    write_readable_report(out)
    log('Complete; parameters, labels and production inputs unchanged.')


if __name__=='__main__':
    main()
