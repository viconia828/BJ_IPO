"""Audit and summarize each family's independently selected parameters."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import optimize_funding_families as opt


LABELS={'multiple':'修复后的原倍数模型','direct':'直接冻资模型','account':'修复分户模型',
        'calendar':'原倍数＋日历环境','intercept':'原倍数＋截距校准','t_plus_2':'原倍数＋T+2'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();out=args.output_dir.resolve()
    report=json.loads((out/'report.json').read_text(encoding='utf-8'))
    for name,expected in json.loads((out/'input_hashes.json').read_text(encoding='utf-8')).items():
        assert hashlib.sha256((opt.ROOT/name).read_bytes()).hexdigest()==expected
    audit_count=0;fractional={};t=opt.t
    def audit(pred,target,by_code,cutoff):
        for code in pred['history_context']['history_source_codes']:
            assert t.replay_timeline.result_available_date(by_code[code])<cutoff
            assert t.replay_timeline.known_before(by_code[code],target)
        prior=pred.get('account_pool_prior') or {}
        for sample in prior.get('samples',[]):
            assert sample['account_count_basis']=='repaired_announcement_constrained'
            assert sample['distribution_available_date']<cutoff
        direct=(pred.get('estimate') or {}).get('direct_funding') or {}
        for sample in direct.get('samples',[]):
            assert sample['available_date']<cutoff
            if sample.get('distribution_available_date'):assert sample['distribution_available_date']<cutoff
        level=(pred.get('estimate') or {}).get('recent_market_level') or {}
        for sample in level.get('samples',[]):
            inner=by_code[sample['security_code']]
            for code in sample.get('prediction_source_codes',[]):assert t.replay_timeline.known_before(by_code[code],inner)
            for value in sample.get('prediction_distribution_dates',{}).values():assert value<inner['apply_date']
    for scope in ('full','q3'):
        rows,pools,base=opt.setup(scope);by_code={r['security_code']:r for r in rows};fractional[scope]={}
        for family,result in report['results'][scope].items():
            selection=result['selection'];p=selection['params']
            assert not (set(selection['training_codes']) & {d['security_code'] for d in result['held_details']})
            for code in selection['training_codes']:
                assert t.replay_timeline.result_available_date(by_code[code])<opt.CUTOFF
            settings={**p,opt.PREFIX+'account_pool_rows':pools,t.subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY:{}}
            for d in [*result['training_details'],*result['held_details']]:
                row=by_code[d['security_code']]
                pred=t.subscription_context.build_prediction(t._target_from_row(row),history_rows=rows,params=settings,funding_only=True)
                audit(pred,row,by_code,row['apply_date']);audit_count+=1
                fit=d.get('residual_fit')
                if fit:
                    cutoff=opt.CUTOFF if d in result['held_details'] else row['apply_date']
                    for code in fit['training_codes']:assert t.replay_timeline.result_available_date(by_code[code])<cutoff
            target=t.subscription_context.target_row_from_ipo(json.loads((opt.ROOT/'.tmp/industry_classifier_20261012/analysis_920285.json').read_text(encoding='utf-8'))['ipo_info'])
            audit(result['target_base_prediction'],target,by_code,'2026-10-13');audit_count+=1
            # These environment diagnostics only adjust funding. Do not publish
            # unadjusted fractional thresholds as their own model's accuracy.
            if family not in ('calendar','intercept'):
                replay=t.evaluate_subscription_prediction(rows,params=settings)
                held={d['security_code'] for d in result['held_details']}
                fractional[scope][family]=opt.comparison.previous.metrics([d for d in replay['details'] if d['security_code'] in held])
        print(f'{scope}: temporal and source audit complete',flush=True)
    opt.save(out/'validation.json',{'audited_forecasts':audit_count,'time_violations':0,
             'full_path_alignment_checks':sum(r['full_path_alignment_checks'] for scope in report['results'].values() for r in scope.values()),
             'held_fractional_diagnostics':fractional})
    impact_path=out/'source_fix_impact.json'
    if not impact_path.exists():
        impact={}
        for scope in ('full','q3'):
            rows,pools,base=opt.setup(scope)
            current=t.evaluate_subscription_prediction(rows,params={**base,opt.PREFIX+'account_pool_rows':pools})
            old_path=opt.ROOT/f'tmp/subscription_repaired_funding_20261017/{scope}_baseline_replay.json'
            previous=json.loads(old_path.read_text(encoding='utf-8'))
            before={d['security_code']:d for d in previous['details']}
            ds=[d for d in current['details'] if d['replay_as_of_date']>='2026-08-01'
                and d['security_code']!=str(base.get(opt.PREFIX+'target_frozen_funds_code') or '')]
            impact[scope]={'changes':[{'security_code':d['security_code'],
                'before':before[d['security_code']]['predicted_frozen_funds_yi'],'after':d['predicted_frozen_funds_yi']}
                for d in current['details'] if abs(d['predicted_frozen_funds_yi']-before[d['security_code']]['predicted_frozen_funds_yi'])>1e-6],
                'rolling17':opt.metrics(ds),'held3':opt.metrics([d for d in ds if d['replay_as_of_date']>=opt.CUTOFF])}
        opt.save(impact_path,impact)
    else:
        impact=json.loads(impact_path.read_text(encoding='utf-8'))
    def evaluations(r):
        s=r['selection'];return s['candidate_count']+s.get('initial_search_candidate_count',0)
    total=sum(evaluations(r) for rs in report['results'].values() for r in rs.values())
    lines=['修复后各模型独立寻优与留出对比 — 2026-10-17',
           '顺序：修复所有生产资金路径的数据来源 → 各模型独立寻优 → 固定所选参数 → 同一留出集合比较。',
           '本报告替代上一轮沿用旧原模型参数与旧账户prior来源的选型结论；旧实验保留作历史记录。',
           '', '口径',
           '完整历史与7月1日起分别重建账户表、分别寻优。原始结果冻结截至10月16日。',
           '选参只使用9月23日前已公布结果，评分仅冻资近期加权MAPE，半衰期12只；各模型共用同一范围的顶格漏报护栏。',
           '莫森、南方、文峰作为最近3只留出。参数固定在9月23日前，基础模型预测历史随之后已公布结果正常推进。',
           '远大只使用10月12日快照，不用其自身和文峰结果；参数仍使用9月23日前选出的同一组，不二次根据留出结果选参。',
           '日历/截距模型的ridge与修正强度独立寻优；训练内各票的残差校准也只用当时更早结果。留出及远大的回归系数冻结在9月23日前。',
           f'6个模型×2种历史范围，累计评价{total}次候选配置。分块坐标搜索最多4遍后局部细化；以下为声明范围内已评估的最佳参数，不宣称数学全局最优。',
           '日历/截距模型额外以原模型最优参数＋修正强度0为起点重新寻优，确保可退化版本的训练最优成绩不差于原模型；初轮结果保存在initial_search。',
           '若残差模型最终选择零修正，其找到的更好基础参数也回测计入原模型和T+2的候选，避免把同一个预测公式误报为不同模型的优劣。该交叉检查仍只看训练结果。',
           '旧压缩桶及其金额守恒分支使用已确认错误的户数，只保留旧实验，不再作为修复后的生产候选；修复分户模型已包含低/中/高等档内位置。',
           '固定水位缺省1；每个模型独立搜索近期半衰期、自适应水位、相关顶格/金额参数。倍数模型另调规模/发行/锁定修正、相似资金锚、账户先验。',
           '训练期成绩不可当作样本外准确率。此前8月起17只中的大部分进入本轮训练，故不再用其训练内成绩冒充滚动留出成绩。',
           '', '原模型来源修复的影响（保留原参数，尚未寻优）',
           f'全历史：{len(impact["full"]["changes"])}个历史预测改变，17只MAPE6.00%→{impact["full"]["rolling17"]["frozen_funds_mape"]:.2%}；最近3只4.38%→{impact["full"]["held3"]["frozen_funds_mape"]:.2%}。',
           f'7月起：{len(impact["q3"]["changes"])}个历史预测改变，17只MAPE5.83%→{impact["q3"]["rolling17"]["frozen_funds_mape"]:.2%}；最近3只3.90%→{impact["q3"]["held3"]["frozen_funds_mape"]:.2%}。',
           '原因：原倍数模型账户先验以前仍读旧压缩桶。数据纠错后，旧系数与触发频率不再可直接沿用。这是上一轮比较遗漏的部分。']
    for scope,title in [('full','完整历史'),('q3','7月1日起')]:
        lines.extend(['',title+'：各自寻优后的比较（误差越低越好；金额为亿元）',
                      '模型\t搜索配置数\t训练期加权MAPE\t留出3只MAPE\t留出加权MAPE\t远大估算\t远大对临时参考偏差'])
        for family in opt.FAMILIES:
            r=report['results'][scope][family];m=r['held_metrics'];value=r['target_frozen_funds_yi']
            lines.append(f'{LABELS[family]}\t{evaluations(r)}\t{r["training_metrics"]["frozen_funds_weighted_mape"]:.2%}\t{m["frozen_funds_mape"]:.2%}\t{m["frozen_funds_weighted_mape"]:.2%}\t{value:,.2f}\t{value/13529.8485-1:+.2%}')
        lines.extend(['','留出逐票预测（亿元；带符号百分比为相对实际偏差）','股票\t实际\t'+'\t'.join(LABELS[f] for f in opt.FAMILIES)])
        held=report['results'][scope]['multiple']['held_details']
        for d in held:
            code=d['security_code'];actual=d['actual_frozen_funds_yi'];cells=[]
            for family in opt.FAMILIES:
                x=next(x for x in report['results'][scope][family]['held_details'] if x['security_code']==code)
                value=x['predicted_frozen_funds_yi'];cells.append(f'{value:,.2f} ({value/actual-1:+.2%})')
            lines.append(f'{code}\t{actual:,.2f}\t'+'\t'.join(cells))
        lines.extend(['','各模型所选有效参数'])
        for family in opt.FAMILIES:
            r=report['results'][scope][family];p=r['selection']['params']
            keys=sorted(set(k for grid in opt.grids(family) for k in grid))
            lines.append(LABELS[family]+'：'+', '.join(f'{key.removeprefix(opt.PREFIX)}={p[key]}' for key in keys))
        lines.extend(['','碎股回放诊断（独立于冻资评分，统计可算门槛，不将环境版未重建的门槛混入）'])
        for family,m in fractional[scope].items():
            lines.append(f'{LABELS[family]}：{m["fractional_metric_count"]}条可算碎股门槛，MAPE {m["fractional_mape"]:.2%}' if m['fractional_mape'] is not None else f'{LABELS[family]}：无可算碎股门槛')
    lines.extend(['','验证与边界',
                  '远大参考仍为分户门槛临时反推：(13528.935,13530.762]亿元，中点13529.8485；不是公告已确认值，也不参与选参。',
                  f'共{audit_count}个预测通过来源日期与内层水位时间审计；每家所选参数的训练和留出资金均与完整正式入口逐项相同。',
                  '原倍数先验与分户资金模型现在共用修复源分布；缺少源分布时明确停用先验，绝不回退旧压缩桶。人工个股覆盖继续优先。',
                  '原始history、labels、账户表及策略参数、自动调参记录的输入哈希未变。本轮修复代码已生效，独立寻优参数尚未写回。',
                  '仅3只留出，且这些股票此前已被分析，属于回放对比而非全新封存盲测。训练集最优不保证留出或下一只最佳。',
                  '分户的多层户数仍含形状推断，档内金额仍是假设；手工标签独立首次可用日期缺失时使用公告日代理。'])
    path=out/'各模型独立寻优对比.txt';path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(path)


if __name__=='__main__':main()
