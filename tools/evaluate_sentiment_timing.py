"""Step two only: compare adaptive timing to the frozen step-one decisions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'tools')]
import sentiment_timing as timing
import sentiment_roles as roles
from evaluate_sentiment_pricing import metrics
from evaluate_sentiment_roles import read,write,score_response
from prepare_sentiment_roles_protocol import FOLDER,verify_protocol


def main():
    protocol_path=FOLDER/'timing_protocol.json'
    protocol=read(protocol_path);verify_protocol(protocol)
    assert protocol['timing_material_log']==timing.MATERIAL_LOG
    stage1=read(FOLDER/'roles_replay.json')
    rows=deepcopy(stage1['rows'])
    for row in rows:
        feedback=timing.response(row['signals'])
        row['models']['role_adaptive']=score_response(row,feedback)
        row['timing_diagnostics']={k:v for k,v in feedback.items() if k!='signals'}
    old_protocol=read(ROOT/'data/sentiment_pricing/protocol.json')
    recent=[r for r in rows if r['code'] in old_protocol['recent_codes']]
    fast=[r for r in rows if r['timing_diagnostics']['timing']['fast_mode']]
    reversal=[]
    for row in rows:
        last_dates=row['signals']['recent_listing_dates'][-2:]
        values={r['listing_date']:r['value'] for r in row['signals']['expectation']['date_groups']}
        if len(last_dates)==2 and all(d in values for d in last_dates) and values[last_dates[0]]<0<values[last_dates[1]]:
            reversal.append(row)
    cohorts={'all':rows,'earlier':[r for r in rows if r['date']<'2026-09-01'],
        'recent10':recent,'cooling_all':[r for r in rows if r['cooling']],
        'cooling_recent':[r for r in recent if r['cooling']],
        'fast_triggered':fast,'fast_down':[r for r in fast if r['timing_diagnostics']['expectation_log_correction']<0],
        'fast_up':[r for r in fast if r['timing_diagnostics']['expectation_log_correction']>0],
        'first_positive_reversal':reversal}
    rules=['combined','role_split','role_adaptive']
    scores={name:{rule:metrics(group,rule) for rule in rules} for name,group in cohorts.items()}
    candidate,control=protocol['primary_rule'],protocol['control']
    def compare(other):
        return {
            'overall_mape':scores['all'][candidate]['price_mape_pct']<=scores['all'][other]['price_mape_pct'],
            'recent_mape':scores['recent10'][candidate]['price_mape_pct']<=scores['recent10'][other]['price_mape_pct'],
            'cooling_high_error':scores['cooling_all'][candidate]['positive_price_error_pct']<=scores['cooling_all'][other]['positive_price_error_pct'],
            'earlier_under_error':scores['earlier'][candidate]['negative_price_error_pct']<=scores['earlier'][other]['negative_price_error_pct']+protocol['guardrails']['earlier_underestimate_max_increase_pp']}
    guards=compare(control);current_default_guards=compare('combined')
    out={'schema':'sentiment_timing_replay_v1','primary_rule':candidate,'control':control,
        'protocol_sha256':hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        'source_sha256_lf':{p:hashlib.sha256((ROOT/p).read_text('utf-8').replace('\r\n','\n').encode()).hexdigest()
            for p in ['code/sentiment_roles.py','code/sentiment_timing.py','tools/evaluate_sentiment_timing.py']},
        'guardrails':guards,'versus_current_default_guardrails':current_default_guards,
        'default_shadow_eligible':stage1['default_shadow_eligible'] and all(guards.values()) and all(current_default_guards.values()),
        'default_shadow_rule':timing.DEFAULT_SHADOW_RULE,'formal_promotion':False,'historical_pit_eligible':False,
        'metrics':scores,'rows':rows,'unscored':stage1['unscored'],'frozen_input_count':stage1['frozen_input_count']}
    write(FOLDER/'timing_replay.json',out)
    labels={'combined':'原直接组合','role_split':'第一步：分工','role_adaptive':'第二步：两组响应'}
    fmt=lambda x:'—' if x is None else f'{x:.2f}'
    lines=['# 情绪信号分工与转向速度：第二步','',
        '在第一步分工规则上，只改变兑现反馈的取样速度。最新两组同向且均达到阈值时用两组，否则回到三组。承接参照、基本面、宽池和区间宽度均不变。', '',
        '第一步及第二步未通过全部保护条件时，默认影子仍保留原直接组合；两个新实验独立展示。正式定价不变。', '',
        '| 区段 | 版本 | 可预测数 | 高估均值% | 低估均值% | 价格MAPE% | 涨幅MAE/百分点 | 高估>10% | 低估>10% | ±10%命中 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name,title in [('earlier','9月前'),('recent10','最近10只'),('all','全部'),('cooling_all','基准降温'),('fast_triggered','两组模式'),('fast_down','两组下行'),('fast_up','两组上行'),('first_positive_reversal','首次正向反转')]:
        for rule in rules:
            m=scores[name][rule]
            lines.append(f"| {title} | {labels[rule]} | {m['count']} | {fmt(m.get('positive_price_error_pct'))} | {fmt(m.get('negative_price_error_pct'))} | {fmt(m.get('price_mape_pct'))} | {fmt(m.get('gain_mae_pp'))} | {m.get('over_10_count','—')} | {m.get('under_10_count','—')} | {m.get('fixed_10pct_hits','—')} |")
    lines+=['', '高估/低估部分均值的另一方向记0；两者之和为价格MAPE。同一行组保持同样本，不与上一轮不同覆盖率直接相减。', '',
        '## 最近10只','', '| 股票 | 实际均价 | 原组合 | 分工 | 两组响应 | 两组响应误差% | 两组模式 |',
        '|---|---:|---:|---:|---:|---:|---|']
    for r in recent:
        m=r['models'];fast_mode=r['timing_diagnostics']['timing']['fast_mode']
        lines.append(f"| {r['name']} {r['code']} | {fmt(r['actual_vwap'])} | {fmt(m['combined']['center'])} | {fmt(m['role_split']['center'])} | {fmt(m['role_adaptive']['center'])} | {fmt(m['role_adaptive']['price_error_pct'])} | {'启用' if fast_mode else '未启用'} |")
    lines+=['','## 分步检验','',f"第一步相对原组合默认替换条件：{'通过' if stage1['default_shadow_eligible'] else '未通过'}。",'',
        f"第二步相对第一步：{'全部通过' if all(guards.values()) else '未全部通过'}；相对当前默认组合：{'全部通过' if all(current_default_guards.values()) else '未全部通过'}。",'',
        '| 条件 | 第二步对第一步 | 第二步对原组合 |','|---|---|---|']
    lines += [f"| {key} | {'通过' if value else '未通过'} | {'通过' if current_default_guards[key] else '未通过'} |" for key,value in guards.items()]
    lines+=['','## 使用与限制','',
        '两个新实验均保留为候选，不以近期个别股票改善掩盖全体或降温组退步。承接仅确认后的凯达、华汇，以及两组加速后的反例均保留。', '',
        '两组模式并不保证调幅更大：较少样本仍使用2/(2+3)收缩。它改变近期信息占比，而不是任意提高杠杆；最新一组反向即退出加速，回到原三组。', '',
        '首次正向反转集合按估值时已经观察到的前负后正兑现反馈定义，不按目标股票涨跌挑选。', '',
        protocol['limitation'], '',
        '暂不引入大盘/行业实时数据。复现：`python tools/evaluate_sentiment_timing.py`；第一步归档见roles_review.md。']
    (FOLDER/'timing_review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'guardrails':guards,'versus_current_default':current_default_guards,
                      'metrics':scores},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
