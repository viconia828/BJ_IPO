"""Paired sequential experiments from the prior immutable feedback traces."""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'tools')]
import sentiment_roles
from evaluate_sentiment_pricing import metrics
from prepare_sentiment_roles_protocol import FOLDER,verify_protocol


def read(path): return json.loads(path.read_text('utf-8'))


def write(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def score_response(row,feedback):
    base=row['models']['baseline']
    model=deepcopy(base)
    if model['available']:
        factor=math.exp(feedback['log_correction'])
        for key in ['center','low','high']: model[key]*=factor
        model['price_error_pct']=(model['center']/row['actual_vwap']-1)*100
        model['gain_error_pp']=(model['center']-row['actual_vwap'])/row['issue']*100
        model['hit']=model['low']<=row['actual_vwap']<=model['high']
        model['price_change_pct']=feedback['price_change_pct']
    return model


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['roles'],default='roles')
    args=parser.parse_args()
    protocol_path=FOLDER/f'{args.stage}_protocol.json'
    protocol=read(protocol_path);verify_protocol(protocol)
    source=read(ROOT/'data/sentiment_pricing/replay.json')
    old_protocol=read(ROOT/'data/sentiment_pricing/protocol.json')
    rows=deepcopy(source['rows'])
    rules=['baseline','combined','expectation','role_split']
    for row in rows:
        feedback=sentiment_roles.response(row['signals'])
        row['models']['role_split']=score_response(row,feedback)
        row['role_diagnostics']={k:v for k,v in feedback.items() if k!='signals'}
    recent=[r for r in rows if r['code'] in old_protocol['recent_codes']]
    cohorts={'all':rows,'earlier':[r for r in rows if r['date']<'2026-09-01'],
        'recent10':recent,'cooling_all':[r for r in rows if r['cooling']],
        'cooling_recent':[r for r in recent if r['cooling']],
        'conflict':[r for r in rows if sentiment_roles.direction(r['signals']['expectation']['log_correction'])*
                   sentiment_roles.direction(r['signals']['aftermarket']['log_correction'])<0]}
    scores={name:{rule:metrics(group,rule) for rule in rules} for name,group in cohorts.items()}
    candidate,control=protocol['primary_rule'],protocol['control']
    guards={
        'overall_mape':scores['all'][candidate]['price_mape_pct']<=scores['all'][control]['price_mape_pct'],
        'recent_mape':scores['recent10'][candidate]['price_mape_pct']<=scores['recent10'][control]['price_mape_pct'],
        'cooling_high_error':scores['cooling_all'][candidate]['positive_price_error_pct']<=scores['cooling_all'][control]['positive_price_error_pct'],
        'earlier_under_error':scores['earlier'][candidate]['negative_price_error_pct']<=scores['earlier'][control]['negative_price_error_pct']+protocol['guardrails']['earlier_underestimate_max_increase_pp']}
    out={'schema':'sentiment_roles_replay_v1','stage':args.stage,'primary_rule':candidate,'control':control,
        'protocol_sha256':hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        'source_sha256_lf':{p:hashlib.sha256((ROOT/p).read_text('utf-8').replace('\r\n','\n').encode()).hexdigest()
                           for p in ['code/sentiment_roles.py','tools/evaluate_sentiment_roles.py']},
        'guardrails':guards,'default_shadow_eligible':all(guards.values()),'formal_promotion':False,
        'historical_pit_eligible':False,'metrics':scores,'rows':rows,'unscored':source['unscored'],
        'frozen_input_count':source['frozen_input_count'],'rules':rules}
    write(FOLDER/f'{args.stage}_replay.json',out)
    labels={'baseline':'原情绪基准','combined':'上轮直接组合','expectation':'仅兑现','role_split':'第一步：分工'}
    fmt=lambda x:'—' if x is None else f'{x:.2f}'
    lines=['# 情绪信号分工实验' if args.stage=='roles' else '# 情绪转向响应实验','',
        '基本面、宽池、样本及±10%区间不变。先以兑现决定方向，承接只作同向确认；冲突不反向压价。', '',
        f"本阶段主候选：{candidate}；直接对照：{control}；默认影子替换条件：{'全部通过' if all(guards.values()) else '未全部通过，保留当前默认'}。正式价格不变。", '',
        '| 区段 | 版本 | 可预测数 | 高估均值% | 低估均值% | 价格MAPE% | 涨幅MAE/百分点 | 高估>10% | 低估>10% | ±10%命中 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name,title in [('earlier','9月前'),('recent10','最近10只'),('all','全部'),('cooling_all','基准降温'),('conflict','旧信号冲突')]:
        for rule in rules:
            m=scores[name][rule]
            lines.append(f"| {title} | {labels[rule]} | {m['count']} | {fmt(m.get('positive_price_error_pct'))} | {fmt(m.get('negative_price_error_pct'))} | {fmt(m.get('price_mape_pct'))} | {fmt(m.get('gain_mae_pp'))} | {m.get('over_10_count','—')} | {m.get('under_10_count','—')} | {m.get('fixed_10pct_hits','—')} |")
    lines+=['','高估部分对低估记0、低估部分对高估记0；二者之和为价格MAPE。65条冻结输入中63条有结果、48条能预测；最近10条中9条能预测，缺失均保留。', '',
        '## 最近10只','', '| 股票 | 实际均价 | 原组合 | 仅兑现 | 分工 | 分工误差% | 信号关系 |',
        '|---|---:|---:|---:|---:|---:|---|']
    for r in recent:
        m=r['models']
        lines.append(f"| {r['name']} {r['code']} | {fmt(r['actual_vwap'])} | {fmt(m['combined']['center'])} | {fmt(m['expectation']['center'])} | {fmt(m['role_split']['center'])} | {fmt(m['role_split']['price_error_pct'])} | {r['role_diagnostics']['agreement']} |")
    lines+=['', '## 保护条件','']
    lines += [f"- {key}：{'通过' if value else '未通过'}" for key,value in guards.items()]
    lines+=['', '## 限制','',protocol['limitation'], '',
        '保护条件在本阶段首次计算前固定：全体及近期价格MAPE不恶化、降温高估不恶化、更早低估均值增加不超过1个百分点。它们只决定默认影子展示，不足以批准正式定价。', '',
        '冲突集合用旧两信号异号划定，不根据候选表现或实际涨跌筛选；未加入大盘/行业行情。']
    (FOLDER/f'{args.stage}_review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'stage':args.stage,'guardrails':guards,'metrics':scores},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
