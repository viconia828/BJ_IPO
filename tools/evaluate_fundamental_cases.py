"""Fixed sentiment algorithm; isolate earnings, pool, matching and market changes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
sys.path.insert(0, str(ROOT / 'tools'))
import config_loader
import quality_sentiment as qs
import quality_sentiment_data as qdata
from fundamental_anchor import build_reviewed_anchor
from evaluate_quality_sentiment import metrics
from build_fundamental_cases import read, write, formatted, FOLDER

VARIANTS = ('proxy', 'proxy_same_history', 'same_basis', 'expanded_pool', 'matched_pool', 'market_adjusted')
LABELS = {'proxy': '旧基本面代理', 'proxy_same_history': '旧代理＋相同历史覆盖', 'same_basis': '统一盈利口径', 'expanded_pool': '补齐宽池',
          'matched_pool': '匹配分档', 'market_adjusted': '分市场修正'}


def install(item, variant, cases, inputs):
    result = {k: v for k, v in item.items() if k not in {'fundamental_case', 'fundamental_review', 'fundamental_snapshot', 'sentiment_snapshot'}}
    code = str(item.get('SECURITY_CODE'))
    if code not in cases or variant == 'proxy':
        return result
    case = deepcopy(cases[code])
    if variant == 'same_basis':
        codes = {q['code'] for q in result.get('comparable_data', [])}
        case['peers'] = [p for p in case['peers'] if p['code'] in codes]
    else:
        result['comparable_data'] = inputs[code]['comparable_data']
    result.update(fundamental_case=case, _fundamental_reconstruction=True,
                  _fundamental_stage='earnings' if variant in {'same_basis', 'expanded_pool'} else 'matched',
                  _fundamental_market_policy='per_market' if variant == 'market_adjusted' else 'legacy_uniform')
    return result


def main():
    params = config_loader.load_params()
    params['quality_sentiment_mode'] = 'shadow'
    index = read(FOLDER / 'index.json')['items']
    cases = {c: read(ROOT / entry['path']) for c, entry in index.items()}
    inputs = {c: read(FOLDER / 'inputs' / f'{c}.json') for c in cases}
    history, _ = qdata.load_history()
    predictions = {}
    for variant in VARIANTS:
        if variant == 'proxy_same_history':
            continue
        rows = [install(item, variant, cases, inputs) for item in history]
        predictions[variant] = {}
        for item in rows:
            if not item.get('LISTING_DATE') or not item.get('AVERAGE_PRICE'):
                continue
            result = qs.evaluate(item, rows, params, as_of=item['LISTING_DATE'])
            predictions[variant][item['SECURITY_CODE']] = result
    blocked_codes = {c for c, r in predictions['proxy'].items() if r['available']
                     and not predictions['market_adjusted'][c]['available']}
    # Isolate losing a history observation from changing its fundamental estimate.
    controlled = [install(item, 'proxy', cases, inputs) for item in history]
    for item in controlled:
        if item['SECURITY_CODE'] in blocked_codes:
            item['comparable_data'] = []
    predictions['proxy_same_history'] = {item['SECURITY_CODE']: qs.evaluate(item, controlled, params, as_of=item['LISTING_DATE'])
        for item in controlled if item.get('LISTING_DATE') and item.get('AVERAGE_PRICE')}
    # Anchor ablations use a common intersection, with losses explicitly shown.
    eligible = {v: {c for c, r in values.items() if r['available']} for v, values in predictions.items()}
    common = set.intersection(*eligible.values())
    comparison = []
    for item in history:
        code = item['SECURITY_CODE']
        if code not in common:
            continue
        row = {'code': code, 'name': item.get('SECURITY_NAME_ABBR'), 'date': str(qs.day(item['LISTING_DATE'])),
               'issue': item['ISSUE_PRICE'], 'actual': item['AVERAGE_PRICE']}
        row.update({variant: predictions[variant][code]['sentiment']['center'] for variant in VARIANTS})
        comparison.append(row)
    lost = []
    for code in sorted(eligible['proxy'] - eligible['market_adjusted']):
        item = next(r for r in history if r['SECURITY_CODE'] == code)
        predicted = predictions['proxy'][code]['sentiment']['center']
        lost.append({'code': code, 'name': item.get('SECURITY_NAME_ABBR'),
                     'reason': predictions['market_adjusted'][code]['sentiment'].get('reason'),
                     'baseline_gain_error_pp': (predicted-item['AVERAGE_PRICE'])/item['ISSUE_PRICE']*100})
    sensitivities = {}
    for code, case in cases.items():
        target, cutoff = inputs[code], case['valuation_cutoff']
        quotes = target['comparable_data']
        settings = {'reconstruction': True}
        base = build_reviewed_anchor(case, quotes, cutoff, params, **settings)
        discounts = {str(d): build_reviewed_anchor(case, quotes, cutoff, {**params, 'bse_discount_factor': d}, **settings).get('center')
                     for d in [.45, .55, .65]}
        leave_out = {}
        for peer in case['peers']:
            copy = {**case, 'peers': [p for p in case['peers'] if p['code'] != peer['code']]}
            value = build_reviewed_anchor(copy, quotes, cutoff, params, **settings)
            leave_out[peer['code']] = {'available': value['available'], 'center': value.get('center'), 'reason': value.get('reason')}
        no_channel = {**case, 'peers': [p for p in case['peers'] if p['match'] != 'channel']}
        normalized = build_reviewed_anchor(case, quotes, cutoff, params, stage='normalized', **settings)
        parent = base.get('scenarios', {}).get('net_profit_parent', {})
        actual = target.get('AVERAGE_PRICE')
        sensitivities[code] = {'name': case['name'], 'anchor': base, 'discounts': discounts,
                              'leave_one_out': leave_out,
                              'no_channel_center': build_reviewed_anchor(no_channel, quotes, cutoff, params, **settings).get('center'),
                              'normalized_available': normalized['available'], 'normalized_reason': normalized.get('reason'),
                              'parent_only_reference': parent.get('center'),
                              'actual_extra_premium_pct': (actual/base['center']-1)*100 if actual and base.get('available') else None}
        if code in predictions['proxy']:
            sensitivities[code]['sentiment_ablation'] = {v: {'anchor': predictions[v][code]['anchor'].get('center'),
                'sentiment': predictions[v][code]['sentiment'].get('center'), 'available': predictions[v][code]['available']}
                for v in VARIANTS}
        else:
            # Prospective shadow preview uses today's known evidence and unchanged
            # historical proxies; no reconstructed later review enters live history.
            live_target = qdata.with_fundamental_review(target)
            live = qs.evaluate(live_target, history, params, as_of='2026-10-14')
            sensitivities[code]['current_shadow_preview'] = live
    summary = {'schema': 'fundamental_case_ablation_v1', 'mode': 'shadow', 'parameter_search': False,
               'sentiment_algorithm_version': qs.VERSION,
               'sentiment_source_sha256_lf': hashlib.sha256((ROOT/'code/quality_sentiment.py').read_text('utf-8').replace('\r\n','\n').encode()).hexdigest(),
               'same_sample_n': len(common), 'coverage': {k: len(v) for k, v in eligible.items()}, 'lost_cases': lost,
               'metrics': {v: metrics(comparison, v) for v in VARIANTS},
               'recent10': {v: metrics(comparison[-10:], v) for v in VARIANTS},
               'comparison': comparison, 'cases': sensitivities,
               'limitations': ['历史重建，非事前PIT或独立留出验证', '仅四例复核，历史有实际结果的三例；未扩展全样本基本面',
                               '同样本固定±10%价格区间，不使用扩宽的F情景区间评价命中率',
                               '凯达丧失可用F必须与误差一起披露，不能静默只报保留样本',
                               '匹配档位及折价敏感性未按首日结果选优', '周期正常化四例均未批准，未宣称完成周期估值'],
               'promotion': '保持shadow；补齐周期证据、扩大基本面覆盖并积累冻结前向预测后再评估'}
    write(FOLDER / 'ablation.json', summary)
    write(FOLDER / 'ablation_predictions.json', predictions)
    lines = ['# 四例基本面第二阶段验收', '',
             '已接入影子模型，正式定价参数不变。用户要求保留足够参照，因此使用宽业务池和固定匹配档位；不按涨幅反推F。', '',
             '| 公司 | 旧F | 新F中心 | 宽池/有效扣非PE | 主结论 |', '|---|---:|---:|---|---|']
    for code, entry in index.items():
        case = sensitivities[code]
        lines.append(f"| [{case['name']}](cases/{code}.md) | {formatted(entry['old_center'])} | {formatted(entry['new_center'])} | {entry['candidate_count']}/{entry['usable']} | {case['anchor'].get('reason', '同口径TTM，分市场调整')} |")
    lines += ['', 'F是剔除IPO额外情绪的基本面研究参考，不是首日目标价。当前市值/历史财务是本轮抓取版本，不冒充当时预测。', '',
              '## 固定情绪算法的消融对照', '',
              f"共同样本{len(common)}只；各阶段覆盖：" + '，'.join(f'{LABELS[v]}{len(eligible[v])}' for v in VARIANTS) + '。', '',
              '| 阶段 | 涨幅MAE（百分点） | 固定±10%命中率 | 最近10只MAE |', '|---|---:|---:|---:|']
    for v in VARIANTS:
        m = summary['metrics'][v]
        lines.append(f"| {LABELS[v]} | {m['gain_mae_pp']:.2f} | {m['hit_rate']*100:.2f}% | {summary['recent10'][v]['gain_mae_pp']:.2f} |")
    lines += ['', '顺序：先以旧F但相同历史覆盖作控制，再同原报价池统一盈利→补齐遗漏参照→固定匹配分档→纠正BJ同行重复折价。情绪回归、近期半衰期及转向阈值均不调参。', '',
              '凯达不可用不仅减少评价样本，也改变后续新股的情绪历史输入；相同历史覆盖控制用于区分这个影响，不能把它全部归因于盈利修正。', '',
              '失去覆盖的案例：' + '；'.join(f"{x['name']}（{x['reason']}，旧线涨幅误差{x['baseline_gain_error_pp']:.2f}个百分点）" for x in lost), '',
              '## 敏感性与周期门槛', '', '| 公司 | 跨市场折价0.45/0.55/0.65下F | 去掉渠道参照F | 归母口径单独参考 |', '|---|---|---:|---:|']
    for case in sensitivities.values():
        lines.append(f"| {case['name']} | {' / '.join(formatted(v) for v in case['discounts'].values())} | {formatted(case['no_channel_center'])} | {formatted(case['parent_only_reference'])} |")
    lines += ['', '逐一移除同行的结果保留在ablation.json。正归母而扣非亏损的同行不自动改用高归母PE；NQ保留业务参照，缺少可靠报价/流动性口径时不参与价格中枢。', '',
              '四例均未批准正常化盈利。凯达有会计师审核的2026年盈利预测，其他预计按原文证据等级保存；没有同年度同行预测倍数时不混乘TTM PE。', '',
              '## 验收边界', ''] + ['- ' + s for s in summary['limitations']] + ['', summary['promotion'] + '。']
    (FOLDER/'review.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ['same_sample_n', 'coverage', 'lost_cases', 'metrics', 'recent10']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
