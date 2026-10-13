"""Frozen ten-IPO walk-forward reconstruction, with explicit coverage controls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'code'), str(ROOT / 'tools')]
import config_loader
import quality_sentiment as qs
from fundamental_anchor import build_reviewed_anchor
from build_fundamental_cases import read, write, formatted, FOLDER

STAGE2 = 'e09ee41e16c808c9b6a8a275f1b48de3c1b25eb8'
WARNINGS = {'cooling_watch', 'cooling_confirmed', 'weak_aftermarket'}
VARIANTS = ['proxy', 'stage2', 'ttm_expanded', 'cycle_sensitivity']


def git_json(path):
    return json.loads(subprocess.check_output(['git', 'show', f'{STAGE2}:{path}'], cwd=ROOT).decode('utf-8'))


def clean(item):
    return {k: deepcopy(v) for k, v in item.items() if k not in
            {'fundamental_case', 'fundamental_review', 'fundamental_snapshot', 'sentiment_snapshot'}}


def install(item, cases, inputs, *, cycle=False):
    item = clean(item)
    code = item['SECURITY_CODE']
    if code in cases:
        item.update(fundamental_case=cases[code], comparable_data=inputs[code]['comparable_data'],
                    _fundamental_reconstruction=True,
                    _fundamental_stage='cycle_reference' if cycle and code == '920025' else 'matched')
    return item


def aftermarket_proxy(target, history):
    cutoff = qs.day(target['LISTING_DATE'])
    prior = [r for r in history if qs.day(r.get('LISTING_DATE')) and qs.day(r['LISTING_DATE']) < cutoff]
    dates = sorted({str(qs.day(r['LISTING_DATE'])) for r in prior})[-3:]
    observed = []
    for item in prior:
        if str(qs.day(item['LISTING_DATE'])) not in dates:
            continue
        values = [r['from_vwap_pct'] for r in qs.visible_post_returns(item, cutoff) if r['from_vwap_pct'] is not None]
        if values:
            observed.append({'code': item['SECURITY_CODE'], 'mean_return_pct': statistics.mean(values),
                             'dates': [r['date'] for r in qs.visible_post_returns(item, cutoff)]})
    return {'weak': len(observed) >= 2 and all(r['mean_return_pct'] < 0 for r in observed),
            'listing_dates': dates, 'observations': observed}


def metrics(rows, variant):
    usable = [r for r in rows if r['models'][variant]['available']]
    if not usable:
        return {'count': 0, 'cohort_count': len(rows)}
    errors = [r['models'][variant]['price_error_pct'] for r in usable]
    over = [max(v, 0) for v in errors]
    return {'count': len(usable), 'cohort_count': len(rows),
            'mean_positive_price_error_pct': statistics.mean(over),
            'overestimate_gt_10pct_count': sum(e > 10 for e in errors),
            'underestimate_gt_10pct_count': sum(e < -10 for e in errors),
            'price_mape_pct': statistics.mean(abs(e) for e in errors),
            'gain_mae_pp': statistics.mean(abs(r['models'][variant]['gain_error_pp']) for r in usable),
            'interval_hit_count': sum(r['models'][variant]['interval_hit'] for r in usable),
            'warning_count': sum(r['models'][variant]['state'] in WARNINGS for r in usable),
            'warning_followed_by_underestimate_count': sum(r['models'][variant]['state'] in WARNINGS
                and r['models'][variant]['price_error_pct'] < -10 for r in usable)}


def summarize(result, target):
    sentiment, anchor = result['sentiment'], result['anchor']
    actual, issue = target['AVERAGE_PRICE'], target['ISSUE_PRICE']
    available = result['available']
    return {'available': available, 'fundamental': anchor.get('center'), 'center': sentiment.get('center'),
            'low': sentiment.get('low'), 'high': sentiment.get('high'),
            'price_error_pct': (sentiment['center'] / actual - 1) * 100 if available else None,
            'gain_error_pp': (sentiment['center'] - actual) / issue * 100 if available else None,
            'interval_hit': sentiment['low'] <= actual <= sentiment['high'] if available else None,
            'state': result['state']['code'], 'state_label': result['state']['label'],
            'recent_coverage': result['state'].get('recent_coverage'),
            'reason': None if available else sentiment.get('reason'),
            'peer_count': anchor.get('peer_count'), 'history_count': result['history_count']}


def main():
    protocol = read(FOLDER / 'recent_protocol.json')
    params = config_loader.load_params()
    assert params['quality_sentiment_mode'] == 'shadow'
    assert float(params['price_range_width']) == .10, 'fixed-width protocol changed'
    frozen_path = FOLDER/'inputs/recent_history.json'
    history = read(frozen_path)['items']
    history = [clean(item) for item in history]
    known = sorted((r for r in history if r.get('LISTING_DATE') and r.get('AVERAGE_PRICE')
                    and qs.day(r['LISTING_DATE']) <= qs.day('2026-10-13')),
                   key=lambda r: (qs.day(r['LISTING_DATE']), r['SECURITY_CODE']))
    assert [r['SECURITY_CODE'] for r in known[-10:]] == protocol['codes'], 'frozen cohort changed'
    cohort = known[-10:]
    current_index = read(FOLDER / 'index.json')['items']
    cases = {c: read(ROOT / v['path']) for c, v in current_index.items()}
    inputs = {c: read(FOLDER / 'inputs' / f'{c}.json') for c in cases}
    old_index = git_json('data/fundamental_cases/index.json')['items']
    old_cases = {c: git_json(v['path']) for c, v in old_index.items()}
    old_inputs = {c: git_json(f'data/fundamental_cases/inputs/{c}.json') for c in old_index}
    variants = {'proxy': history, 'stage2': [install(r, old_cases, old_inputs) for r in history],
                'ttm_expanded': [install(r, cases, inputs) for r in history],
                'cycle_sensitivity': [install(r, cases, inputs, cycle=True) for r in history]}
    predictions = {}
    for variant, rows in variants.items():
        predictions[variant] = {r['SECURITY_CODE']: qs.evaluate(r, rows, params, as_of=r['LISTING_DATE'])
                                for r in rows if r['SECURITY_CODE'] in protocol['codes']}
    # Change only history coverage: originals keep old F, newly covered history
    # receives its audited new F. New targets remain unavailable on the old line.
    original_anchors = {r['SECURITY_CODE']: qs.build_anchor(r, r['LISTING_DATE'], params).get('available')
                        for r in known}
    controlled = []
    for r in history:
        code = r['SECURITY_CODE']
        reviewed = install(r, cases, inputs)
        valid = bool(r.get('LISTING_DATE') and qs.build_anchor(reviewed, r['LISTING_DATE'], params).get('available'))
        if code in cases and not original_anchors.get(code) and valid:
            controlled.append(reviewed)
        elif original_anchors.get(code) and not valid:
            row = clean(r); row['comparable_data'] = []; controlled.append(row)
        else:
            controlled.append(clean(r))
    predictions['proxy_same_history'] = {r['SECURITY_CODE']: qs.evaluate(clean(r), controlled, params, as_of=r['LISTING_DATE'])
                                        for r in cohort}
    all_variants = VARIANTS + ['proxy_same_history']
    rows = []
    for target in cohort:
        code = target['SECURITY_CODE']
        baseline = predictions['proxy'][code]
        feedback = aftermarket_proxy(target, history)
        cooling = baseline['state']['code'] in WARNINGS or (not baseline['anchor']['available'] and feedback['weak'])
        rows.append({'code': code, 'name': target['SECURITY_NAME_ABBR'], 'date': str(qs.day(target['LISTING_DATE'])),
                     'issue': target['ISSUE_PRICE'], 'actual_vwap': target['AVERAGE_PRICE'],
                     'cooling_subset': cooling, 'aftermarket_proxy': feedback,
                     'models': {v: summarize(predictions[v][code], target) for v in all_variants}})
    common = [r for r in rows if r['models']['proxy']['available'] and r['models']['ttm_expanded']['available']]
    stage2_common = [r for r in rows if all(r['models'][v]['available'] for v in ['proxy','stage2','ttm_expanded'])]
    output = {'schema': 'recent_ipo_replay_v1', 'protocol_sha256': hashlib.sha256((FOLDER/'recent_protocol.json').read_bytes()).hexdigest(),
              'stage2_commit': STAGE2, 'mode': 'shadow', 'historical_pit_eligible': False,
              'parameter_sha256': hashlib.sha256((ROOT/'策略参数.txt').read_bytes()).hexdigest(),
              'frozen_history_sha256': hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
              'rows': rows, 'all_available': {v: metrics(rows, v) for v in all_variants},
              'common_proxy_ttm_codes': [r['code'] for r in common],
              'common_proxy_ttm': {v: metrics(common, v) for v in all_variants},
              'common_stage2_codes': [r['code'] for r in stage2_common],
              'common_stage2': {v: metrics(stage2_common, v) for v in all_variants},
              'cooling_codes': [r['code'] for r in rows if r['cooling_subset']],
              'cooling_common': {v: metrics([r for r in common if r['cooling_subset']], v) for v in all_variants},
              'cooling_all': {v: metrics([r for r in rows if r['cooling_subset']], v) for v in all_variants},
              'coverage_added': [r['code'] for r in rows if not r['models']['proxy']['available'] and r['models']['ttm_expanded']['available']],
              'coverage_lost': [r['code'] for r in rows if r['models']['proxy']['available'] and not r['models']['ttm_expanded']['available']],
              'limitations': ['当前供应商历史版本的事后重建；财报披露、报价、已知后续行情严格按日期截断，但不等于原始PIT',
                              '只有10例且降温子集更小，不能据此调参或确认稳定提升',
                              '预警后低估是过度保守案例检查，不代表情绪真值标签',
                              '周期参照改变盈利可用性及可比构成，不能单独归因于利润率修正']}
    write(FOLDER / 'recent_replay.json', output)
    # Only ten targets, full audit traces in a separate artifact.
    write(FOLDER / 'recent_predictions.json', predictions)
    old_metrics = output['common_proxy_ttm']['proxy']
    new_metrics = output['common_proxy_ttm']['ttm_expanded']
    lines = ['# 最近10只：盈利口径与降温高估回放', '',
             '按2026-10-13固定最近10只；当前证据重建，保持情绪参数不变，保留shadow，正式估值参数不变。', '',
             f"本轮没有改善高估。有效覆盖从{output['all_available']['proxy']['count']}/10增至{output['all_available']['ttm_expanded']['count']}/10；共同{len(common)}只的高估部分均值由{old_metrics['mean_positive_price_error_pct']:.2f}%升至{new_metrics['mean_positive_price_error_pct']:.2f}%，涨幅MAE由{old_metrics['gain_mae_pp']:.2f}升至{new_metrics['gain_mae_pp']:.2f}个百分点。不能拿9只与6只各自样本的MAE直接比较。", '',
             '凯达盈利与周期口径见[kada_earnings_review.md](kada_earnings_review.md)；选择与评分规则在[recent_protocol.json](recent_protocol.json)中事先固定。', '',
             '| 股票 | 上市日 | 旧F | 新F | 实际均价 | 旧情绪中枢 | 新情绪中枢 | 新预测误差% | 上市前状态（旧→新） |',
             '|---|---|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        old, new = r['models']['proxy'], r['models']['ttm_expanded']
        lines.append(f"| {r['name']} {r['code']} | {r['date']} | {formatted(old['fundamental'])} | {formatted(new['fundamental'])} | {r['actual_vwap']:.2f} | {formatted(old['center'])} | {formatted(new['center'])} | {formatted(new['price_error_pct'])} | {old['state_label']}→{new['state_label']} |")
    lines += ['', '正误差表示高估；负误差表示低估。实际采用首日成交均价，非最高价/收盘涨幅。', '',
              '## 共同样本比较', '', '| 范围/版本 | 有效数 | 高估部分均值% | 高估>10% | 涨幅MAE/百分点 | ±10%命中 | 低估>10% |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for label, group, selected in [('旧新共同', output['common_proxy_ttm'], [v for v in all_variants if v != 'stage2']),
                                  ('上阶段三线共同', output['common_stage2'], ['proxy','stage2','ttm_expanded']),
                                  ('固定降温共同', output['cooling_common'], all_variants),
                                  ('全表各自覆盖', output['all_available'], all_variants)]:
        for v in selected:
            m = group[v]
            lines.append(f"| {label}/{v} | {m['count']} | {formatted(m.get('mean_positive_price_error_pct'))} | {m.get('overestimate_gt_10pct_count', '—')} | {formatted(m.get('gain_mae_pp'))} | {m.get('interval_hit_count', '—')} | {m.get('underestimate_gt_10pct_count','—')} |")
    lines += ['', '固定降温子集：'+'、'.join(output['cooling_codes'])+'。'+protocol['cooling_subset']+'。', '',
              '新增覆盖：'+'、'.join(output['coverage_added'])+'；退出覆盖：'+('、'.join(output['coverage_lost']) or '无')+'。', '',
              'proxy_same_history在旧线补入新增历史样本的同口径F，旧目标继续用旧F；用于区分历史覆盖变化和基本面修订。', '',
              'stage2为提交'+STAGE2[:7]+'的四例精修。cycle_sensitivity只将凯达替换成双方同三年利润率压力参照，不推广为可持续盈利。', '',
              '## 预警后的保守误差', '', '| 版本 | 预警数（有预测） | 预警后仍低估>10% |', '|---|---:|---:|']
    for v in all_variants:
        m=output['all_available'][v]
        lines.append(f"| {v} | {m.get('warning_count',0)} | {m.get('warning_followed_by_underestimate_count',0)} |")
    lines += ['', '## 对情绪引导的诊断', '',
              '补齐基本面后，百迈科、腾信和凯达上市前能识别降温观察；百迈科和腾信仍高估约30.23%、11.39%，凯达则低估约68.99%。因此不能看到降温就机械压低所有股票。', '',
              '当前实现先计算情绪中枢，再生成状态提示；降温标签不会直接触发一次独立的价格下调，中枢仅通过既有8日半衰期、历史兑现和个股结构回归变化。下一阶段应隔离检验情绪溢价收缩，而不是再次把基本面F压低；保留降温后反弹/低估的反例。', '',
              '宇特上市前最近三只相对首日均价的后续收益均值约-41.86%，但个股调整后的首日额外定价窗口仍在修复，因此输出“溢价与承接分化”。这是两种反馈冲突，不能把承接弱自动等同于首日净情绪继续下降。', '',
              '固定旧规则降温子集仅金钛、华汇两只，其中旧新共同可评分仅华汇一只。该子集不足以证明对“情绪转弱时高估”的统计改善，本轮只能确认覆盖和提示能力提升。']
    lines += ['', '## 限制', ''] + ['- '+v for v in output['limitations']]
    (FOLDER/'recent_review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k: output[k] for k in ['common_proxy_ttm','cooling_codes','cooling_common','coverage_added','coverage_lost']},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
