"""Refresh the guide from 60 cached minute samples; no trading config changes.

Keeps the original price-only exit path as a descriptive comparison. Frozen
old-40 turnover cutoffs evaluate later samples; current-60 cutoffs are descriptive.
Minute closes/peaks are observations, not executable or net trading returns.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import analyze_turnover_halt_paths as a

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/研究产物/最新/guide_v5_audit_20260923'
NODES = ['09:35', '09:45', '10:00', '10:30', '11:00', '11:30']
POLICY = ['09:35', '09:45', '10:00', '11:00', '11:30']
EXTERNAL = {'920176', '920059'}  # frozen original labels, never label ex post
CUTOFF = '2026-07-14'


def summarize(rows):
    early = [r for r in rows if r['early_exit']]
    comparable = [r for r in early if r['forced_active']]
    large = [r for r in rows if r['large_late_rally']]
    return {
        'n': len(rows), 'early_n': len(early),
        'regret_comparable_n': len(comparable),
        'regret': a.stats([r['regret'] for r in comparable]),
        'regret_ge10': sum(r['regret'] >= 10 for r in comparable),
        'regret_inactive_excluded': [r['code'] for r in early if not r['forced_active']],
        'exit_counts': dict(Counter(r['exit_node'] for r in rows)),
        'inactive_exit': [r['code'] for r in rows if not r['exit_active']],
        'afternoon_high_1pct': sum(r['afternoon_high_1pct'] for r in rows),
        'afternoon_high_codes': [r['code'] for r in rows if r['afternoon_high_1pct']],
        'high_time_buckets': dict(Counter(r['high_time_bucket'] for r in rows)),
        'next_close': a.stats([r['next_day_close_return'] for r in rows]),
        'next_close_positive': sum(r['next_day_close_return'] is not None and r['next_day_close_return'] > 0 for r in rows),
        'close_below_issue50': sum(r['issue_close_gain'] is not None and r['issue_close_gain'] < 50 for r in rows),
        'issue_price_n': sum(r['issue_close_gain'] is not None for r in rows),
        'large_late_n': len(large),
        'large_late_peak_amount': a.stats([r['rally_peak_amount_yi'] for r in large]),
        'large_late_4to6': sum(4 <= r['rally_peak_amount_yi'] <= 6 for r in large),
        'large_late_3min_dd10': sum(r['rally_dd10_delay'] is not None and r['rally_dd10_delay'] <= 3 for r in large),
    }


def run():
    base = a.read_json(ROOT / 'outputs/研究产物/最新/turnover_halt_study_20260923/analysis.json')
    items = {r['SECURITY_CODE']: r for r in a.read_json(ROOT / 'data/offline_tuning/replay_dataset.json')['items']}
    rows = []
    hashes = []
    for s in base['stocks']:
        path = ROOT / s['bar_source']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == s['sha256'], path
        hashes.append({'code': s['code'], 'sha256': s['sha256']})
        bars, _ = a.read_bars(path)
        live = [b for b in bars if b['volume'] > 0]
        snaps = s['nodes']
        opening = s['open']
        flags = {n: snaps[n]['price'] < opening and snaps[n]['price'] < snaps[n]['vwap'] for n in POLICY[:3]}
        early_high = max(b['high'] for b in live if b['t'] <= '10:00')
        late_high = max(b['high'] for b in live if '10:00' < b['t'] <= '11:00')
        flags['11:00'] = late_high <= early_high or snaps['11:00']['price'] < opening or snaps['11:00']['price'] < snaps['11:00']['vwap']
        flags['11:30'] = True
        exit_node = next(n for n in POLICY if flags[n])
        forced_node = next(n for n in POLICY[2:] if flags[n])
        early = exit_node in POLICY[:2]
        period = 'old' if s['date'] <= CUTOFF else 'recent_external' if s['code'] in EXTERNAL else 'recent_normal'
        morning_high = max(b['high'] for b in live if b['t'] <= '11:30')
        pm_high = max(b['high'] for b in live if b['t'] >= '13:00')
        issue_price = items.get(s['code'], {}).get('ISSUE_PRICE')
        issue_price = float(issue_price) if issue_price else None
        # Same retrospective maximum-low-to-later-high leg as the old audit.
        best, low_i = None, 0
        for peak_i in range(2, len(live)):
            candidate_i = peak_i - 2
            if live[candidate_i]['low'] < live[low_i]['low']:
                low_i = candidate_i
            gain = a.pct(live[peak_i]['high'], live[low_i]['low'])
            if best is None or gain > best[0]:
                best = (gain, low_i, peak_i)
        gain, start_i, peak_i = best
        peak = live[peak_i]
        dd = next((b for b in live[peak_i + 1:] if b['close'] <= peak['high'] * .9), None)
        row = {k: s[k] for k in ['code', 'name', 'date', 'high_time', 'next_day_close_return', 'daily_turnover', 'csv_turnover']}
        row.update({
            'period': period, 'exit_node': exit_node, 'exit_price': snaps[exit_node]['price'],
            'exit_active': snaps[exit_node]['active'], 'early_exit': early,
            'forced_node': forced_node, 'forced_active': snaps[forced_node]['active'],
            'regret': a.pct(snaps[forced_node]['price'], snaps[exit_node]['price']) if early else None,
            'afternoon_high_1pct': pm_high >= morning_high * 1.01,
            'high_time_bucket': '09:30-09:59' if s['high_time'] < '10:00' else '10:00-11:00' if s['high_time'] <= '11:00' else '11:01-11:30' if s['high_time'] <= '11:30' else '13:00-15:00',
            'issue_close_gain': a.pct(s['csv_close'], issue_price),
            'large_late_rally': gain >= 20 and peak['t'] >= '09:40',
            'rally_peak_time': peak['t'], 'rally_gain': gain,
            'rally_peak_amount_yi': sum(b['amount'] for b in live[:peak_i + 1]) / 1e8,
            'rally_dd10_delay': a.minute(dd['t']) - a.minute(peak['t']) if dd else None,
            'nodes': {},
        })
        prev = 0
        for node in NODES:
            snap = snaps[node]
            later = [b for b in live if b['t'] > node and (node == '11:30' or b['t'] <= '11:30')]
            high = max((b['high'] for b in later), default=snap['price'])
            row['nodes'][node] = {
                'cum': snap['turnover'], 'delta': snap['turnover'] - prev,
                'active': snap['active'], 'price': snap['price'], 'vwap': snap['vwap'],
                'state': '双高' if snap['price'] > opening and snap['price'] > snap['vwap'] else '双低' if snap['price'] < opening and snap['price'] < snap['vwap'] else '中性',
                'held_after': exit_node > node,
                'later_peak': max(a.pct(high, snap['price']), 0),
                'close_return': a.pct(s['csv_close'], snap['price']),
            }
            prev = snap['turnover']
        rows.append(row)
    groups = {p: [r for r in rows if r['period'] == p] for p in ['old', 'recent_normal', 'recent_external']}
    thresholds = {}
    recent_checks = {}
    for node in NODES:
        thresholds[node] = {p: {metric: a.q([r['nodes'][node][metric] for r in subset], 2/3) for metric in ['cum', 'delta']} for p, subset in [('old40', groups['old']), ('all60', rows)]}
        threshold = thresholds[node]['old40']['cum']
        pool = [r for r in groups['recent_normal'] if r['nodes'][node]['active'] and (node == '11:30' or r['nodes'][node]['held_after'])]
        recent_checks[node] = {}
        for tier in ['high', 'not_high']:
            selected = [r for r in pool if (r['nodes'][node]['cum'] >= threshold) == (tier == 'high')]
            outcomes = [r['nodes'][node]['later_peak'] for r in selected]
            recent_checks[node][tier] = {'n': len(selected), 'peak_ge10': sum(x >= 10 for x in outcomes), 'peak': a.stats(outcomes), 'codes': [r['code'] for r in selected]}
    high90 = [r for r in rows if r['daily_turnover'] is not None and r['daily_turnover'] >= 90]
    result = {
        'as_of': '2026-09-23', 'n': len(rows), 'date_range': [min(r['date'] for r in rows), max(r['date'] for r in rows)],
        'definitions': {
            'cutoff': CUTOFF, 'external_codes_frozen': sorted(EXTERNAL),
            'quantile': 'linear interpolation at 2/3; all60 descriptive, old40 frozen comparison',
            'regret': 'forced later price-rule exit / first 09:35 or 09:45 double-low exit - 1; exclude inactive forced nodes from summary; minute closes, no fees or slippage',
            'node_forward': 'maximum later high through 11:30; 11:30 through close; clamp at zero; excludes inactive node; early nodes condition on held-after',
            'late_rally': 'hindsight maximum low-to-high gain >=20%, >=2 bars apart, peak >=09:40; not a signal',
        },
        'thresholds': thresholds, 'summaries': {p: summarize(rs) for p, rs in groups.items()},
        'all_summary': summarize(rows), 'high90_summary': summarize(high90),
        'recent_frozen_cutoff_checks': recent_checks, 'rows': rows,
        'validation': {'minute_file_hashes_verified': len(hashes), 'hashes': hashes,
                       'early_forced_inactive': [r['code'] for r in rows if r['early_exit'] and not r['forced_active']]},
    }
    assert len(rows) == 60 and [len(groups[k]) for k in groups] == [40, 18, 2]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    normal = result['summaries']['recent_normal']
    lines = [
        '# 首日指南 v5 全量复核', '',
        '截至2026-09-23，60只本地分钟样本；旧40只截至7月14日，其后普通18只、原外力标签2只（维琪、双英）。',
        '原PDF和v4保留，本次生成v5四页；换手/临停研究仍在单独一页。', '',
        '| 旧版项目 | v5更新 |', '|---|---|',
        '| 基础53只、近期普通11只 | 全部重算为60只、近期普通18只 |',
        '| 节点高档使用旧40只 | 指南展示60只2/3分位；后续样本比较仍固定旧40只门槛，二者不混用 |',
        '| 近期普通早卖3/11、中位-4.9%、零大后悔 | 7/18；延后价差中位-3.9%，其中1/7超过10%（世纪） |',
        '| 维琪延后卖出价差+77.6% | 延后节点处于临停，停前价不能当成该时点可成交价；从汇总剔除 |',
        '| 11:30旧高换手组下午0/4再涨10% | 假设仍持有全部近期普通样本，更新为1/8，中位最大涨幅0.9% |',
        '| 下午只限外力例外 | 近期普通18只中世纪午后超上午高点1%以上；标签不是唯一条件 |',
        '| 11:30普通一律清仓 | 明确为保守默认；预设小尾仓且仍持仓、双高、修复时可观察，未回测其收益 |',
        '| 4-6亿元及+50%软锚 | 仅3/8次近期普通大幅晚拉峰值累计成交在4-6亿，4/60收盘低于发行价+50%；删除统一见顶/保底解释 |', '',
        '## 更新后的换手时钟', '',
        '| 节点 | 旧40只累计分位（复算） | 最新60只累计分位 | 最新60只区间新增分位 |', '|---|---:|---:|---:|',
    ]
    for node, values in thresholds.items():
        lines.append(f"| {node} | {values['old40']['cum']:.2f}% | {values['all60']['cum']:.2f}% | {values['all60']['delta']:.2f}个百分点 |")
    lines += [
        '', '9:35累计与新增均包含集合竞价，其余新增为相邻节点累计差；分位数不能按不同区间长度直接比较速度。', '',
        '## 必须保留的限制', '',
        '- 原退出路径在世纪9:35双低时已经卖出，不能把午后大涨计为该路径捕获的收益。',
        '- “后续上涨”使用事后最高价，只表示机会；早卖比较用分钟价，无费用、滑点、盘口可成交验证。',
        '- 60只分位数是描述性参考，不是样本外验证；临停及午后观察阈值为探索条件。',
        '- 30%尾仓及11:30/14:30兑现时间是风险管理约定，未因本次复盘证明最优；没有改动正式策略参数。',
        '- 分母42只来自日线校准、18只来自回放换手校准；凯达分钟缺量约0.80%未摊入早盘。',
        '- 早/晚触停时间与换手高低混杂；二停前60%-80%没有可评估样本。', '',
        '复现：先运行 `tools/audit_first_day_guide_v5.py`，再用ReportLab环境运行 `tools/render_first_day_sell_guide_compact_pdf.py`。',
        '机器可读结果及定义见同目录 `audit.json`；原始分钟文件的60个SHA-256已逐个核验。', '',
    ]
    (OUT / '首日指南v5全量复核.md').write_text('\n'.join(lines), 'utf-8')
    print(json.dumps({k: result[k] for k in ['thresholds', 'summaries', 'all_summary', 'high90_summary', 'recent_frozen_cutoff_checks']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()
