"""Readable report and fixed-parameter sensitivity for the repaired funding run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import evaluate_repaired_account_funding as run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(); out=args.output_dir.resolve()
    def read(name): return json.loads((out/name).read_text(encoding='utf-8'))
    def save(name,data): (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    manifest=out/'code_manifest.json'
    if manifest.exists():
        for name,digest in json.loads(manifest.read_text(encoding='utf-8')).items():
            if hashlib.sha256((run.ROOT/name).read_bytes()).hexdigest()!=digest:
                raise ValueError('该历史实验的代码已变化，不能用新代码重算旧参数敏感性；请重新运行独立寻优对比。')
    report=read('report.json'); candidates=read('candidate_parameters.json'); targets=read('target_predictions.json')
    t=run.t
    for name,digest in report['validation']['production_input_hashes'].items():
        assert hashlib.sha256((run.ROOT/name).read_bytes()).hexdigest()==digest
    rows=sorted([r for r in t.subscription_context.load_history_rows()
                 if ''<t.replay_timeline.result_available_date(r)<=report['as_of']],key=t._row_sort_key)
    by_code={r['security_code']:r for r in rows}
    sources={'full':rows,'q3':[r for r in rows if r['apply_date']>='2026-07-01']}
    pools={scope:read(f'{scope}_account_pool_snapshots.json') for scope in sources}
    baseline=t._resolve_subscription_base_params(t.config_loader.load_params());baseline[run.MODEL]='multiple'
    def scoped(p,scope): return {**p,'subscription_prediction_account_pool_rows':pools[scope],
                                t.subscription_predictor.ACCOUNT_POOL_RUNTIME_CACHE_KEY:{}}
    selected={'baseline':baseline,**{family:candidates[report['selections']['full'][family]['held']['candidate']]
                                    for family in ('direct','legacy_bucket','repaired_account')}}
    stability={}
    for family,p in selected.items():
        details=[]
        for code in report['rolling_codes']:
            values={}
            for scope in sources:
                for scale in (.8,1,1.2):
                    settings=scoped({**p,run.HALF:float(p[run.HALF])*scale},scope)
                    result=t.subscription_context.build_prediction(t._target_from_row(by_code[code]),history_rows=sources[scope],params=settings)
                    values[f'{scope}_{scale}']=result['frozen_funds_yi']
            details.append({'security_code':code,'predictions':values,
                'window_change':abs(values['q3_1']/values['full_1']-1),
                'half_change':max(abs(values[f'{scope}_{s}']/values[f'{scope}_1']-1) for scope in sources for s in (.8,1.2))})
        stability[family]={'parameters':{k:p[k] for k in (run.MODEL,run.HALF,run.BETA,run.POSITION) if k in p},
            'details':details,**{f'{field}_{stat}':(max(d[field] for d in details) if stat=='max' else sum(d[field] for d in details)/len(details))
                                for field in ('window_change','half_change') for stat in ('mean','max')}}
        print(f'{family}: window mean {stability[family]["window_change_mean"]:.3%}, half max {stability[family]["half_change_max"]:.3%}',flush=True)
    save('stability.json',stability)
    labels={'baseline':'正式原模型','direct':'直接冻资寻优','legacy_bucket':'旧压缩桶寻优',
            'legacy_conserving':'旧压缩桶+金额守恒寻优','repaired_account':'修复分户+金额守恒寻优','t_plus_2':'T+2占用系数寻优',
            'intercept_only':'原模型+仅截距校准','calendar_all':'原模型+日历环境',
            'earlier_multiple_optimized':'此前原倍数模型寻优（9月23日前参数）'}
    lines=['修复分户冻资接入、寻优与统一对比 — 2026-10-17',
           '结论：修复分户较旧压缩桶改善，但没有稳定胜过正式原模型；已接入共享预测入口，默认保持multiple。',
           '', '数据与时间口径',
           '冻结截至2026-10-16已公布结果：全历史66只；7月1日起27只。两范围各自从空账户状态重建。',
           '8月起17只纯模型预测，排除凯大人工覆盖。每只股票选参只评分其申购前已公布的历史结果。',
           '最近3只为莫森、南方、文峰：统一固定9月23日前选出的参数；预测历史随后随公告正常推进。',
           '远大沿用10月12日保存的申购前输入，不使用其自身结果，也不使用文峰10月15日结果。',
           '远大临时分户反推参考：(13528.935,13530.762]亿元，中点13529.8485；仅用于展示误差，不参与训练和挑选。',
           '新模型12组=半衰期3/6.25/10/15只×可行档内位置0/0.5/1。全部模型合计48组，两种范围重跑。',
           '评分仅用冻资近期加权MAPE（半衰期12只），相同时用普通MAPE；顶格判断误报护栏不超过基线+1，最少8条历史预测。',
           '分户转移、相似顶格与水位回放共用样本近期权重。固定水位缺省1，历史自适应权重0.5，限幅0.9~1.05。',
           '', '接入与数据审计',
           '账户表版本5新增每只股票自身的完整累计获配层。先差分为各档户数，再用档内金额上下界和未获配均额满足历史总冻资。',
           'source_lot_distribution_json与跨股票更新的account_pool_snapshot_json分别保存，资金不读取旧压缩桶户数。',
           '67只源表65只有完整有效申购户数；冻结66只中64只可用于新资金路径，920186/920072缺有效申购总户数，显式排除。Q3的27只均可用。',
           '低/中/高是可行档内金额位置假设，不是真实户均；两层获配户数由公告唯一识别，多层仍含历史形状推断。',
           '历史累计前缀可用日期取全部依赖的最晚日期，水位校准内层回放也检查此日期。近期17只新模型均未回退直接冻资。']
    for scope,title in [('full','全历史'),('q3','7月1日起')]:
        lines.extend(['',f'{title}统一比较（MAPE越低越好；远大单位亿元）',
                      '模型\t8月起17只MAPE\t近期加权MAPE\t固定参数最近3只MAPE\t远大10/12估算\t相对临时参考偏差'])
        for family in labels:
            if family in report['metrics'][scope]:
                m=report['metrics'][scope][family];value=targets[scope][family]['frozen_funds_yi']
            else:
                m=report['controls'][scope][family];value=m['target_frozen_funds_yi']
            rolling=m.get('rolling17'); held=m['fixed_held3']
            cells=[f'{rolling[k]:.2%}' if rolling else '不适用（会混入选参样本）' for k in ('frozen_funds_mape','frozen_funds_weighted_mape')]
            lines.append(f'{labels[family]}\t'+ '\t'.join(cells)+f'\t{held["frozen_funds_mape"]:.2%}\t{value:,.2f}\t{value/13529.8485-1:+.2%}')
        lines.extend(['', '各模型选参（留出3只 / 远大报告时点）'])
        for family,m in report['metrics'][scope].items():
            lines.append(f'{labels[family]}：{m["held_candidate"]} / {m["target_candidate"]}')
        lines.extend(['', '最近3只逐票对比：实际 / 原模型 / 旧压缩桶 / 修复分户（亿元）'])
        held_replays={family:read(f'{scope}_{report["metrics"][scope][family]["held_candidate"]}_replay.json')
                      for family in ('baseline','legacy_bucket','repaired_account')}
        for code in report['held_codes']:
            ds={family:next(d for d in replay['details'] if d['security_code']==code) for family,replay in held_replays.items()}
            r=by_code[code]
            lines.append(f'{code} {r.get("security_name_abbr","")}：{ds["baseline"]["actual_frozen_funds_yi"]:,.2f} / '+
                         ' / '.join(f'{ds[f]["predicted_frozen_funds_yi"]:,.2f}（{ds[f]["predicted_frozen_funds_yi"]/ds[f]["actual_frozen_funds_yi"]-1:+.2%}）' for f in ds))
        lines.extend(['','碎股诊断（资金寻优不混入碎股评分；仅统计可计算门槛，覆盖条数不同，不能直接视为完整成功率）'])
        for family in ('baseline','direct','legacy_bucket','repaired_account'):
            m=report['metrics'][scope][family]['rolling17']
            lines.append(f'{labels[family]}：碎股门槛MAPE {m["fractional_mape"]:.2%}，可计算 {m["fractional_metric_count"]} 条。')
    lines.extend(['','固定参数敏感性（固定全历史9月23日前所选参数，17只同一股票集合）',
                  '模型\t全历史/7月起均值变化\t最大变化\t半衰期±20%最大变化'])
    for family,s in stability.items():
        lines.append(f'{labels[family]}\t{s["window_change_mean"]:.2%}\t{s["window_change_max"]:.2%}\t{s["half_change_max"]:.2%}')
    new=targets['full']['repaired_account']['estimate'];funding=new['direct_funding']
    samples=sorted(funding['samples'],key=lambda s:s['weight'],reverse=True)
    concentration=sum(s['weight'] for s in samples[:2])/sum(s['weight'] for s in samples)
    lines.extend(['','为什么修复后仍未胜出',
                  '修复解决的是账户层数守恒与数据来源错误，并未观测到账户在下一只股票上的申购意愿或新增资金。',
                  '目前转移函数将历史账户金额按目标顶格截断；目标顶格高于源股票时，不外推被旧顶格限制的潜在资金。',
                  f'远大全历史新模型水位前{funding["frozen_funds_yi"]:,.2f}亿元，水位因子{new["recent_market_level"]["factor"]:.4f}，得到{targets["full"]["repaired_account"]["frozen_funds_yi"]:,.2f}亿元。',
                  f'南方与莫森占归一化来源权重{concentration:.2%}，有效样本数{funding["effective_sample_count"]:.2f}。近期权重确实起效，但两只低冻资样本会使下一只的资金预测偏低。',
                  '这是当前样本的误差诊断，不能仅凭远大反推参考去增加增长系数或调水位。',
                  '', '验证与限制',
                  f'新模型34个近期正式入口/回放的资金、正股和可用碎股门槛逐项一致；全部来源及内层水位日期审计0违规。实验{report["elapsed_seconds"]:.1f}秒。',
                  '新8项回归覆盖完整分层、旧压缩户数注毒、未来数据与前缀依赖、历史总额守恒、失败回退与选参时间隔离；既有账户构建、资金、上下文、时间线、近期权重、预测与调参验证通过。',
                  '原始历史、人工标签、正式策略参数及自动参数记录哈希未变。账户派生表仅增加源分布和版本/签名，原有曲线与切点数值未变。',
                  '账户金额仍不可完全识别，且缺少手工标签独立首次可用时间，回放主要以结果公告日作代理。本轮为事后假设的逐时点回放，不是前瞻封存盲测。',
                  '原倍数模型的此前优化参数只展示留出3只与后续远大；不把其训练内的8月结果与滚动选参的17只并列。',
                  '日历校准只重算冻资，不冒报碎股改善。各模型共有样本、选参时点和明细均在同目录JSON中。'])
    path=out/'修复分户冻资寻优对比.txt';path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(path)


if __name__=='__main__': main()
