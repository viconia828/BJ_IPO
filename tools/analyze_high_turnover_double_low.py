"""Describe high-turnover 09:35 double-low cases and next-node repair.

Uses old-40 quantiles as a frozen reference, and reports historical-58 separately
from the latest two names. The rounded 30% observation line is exploratory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import analyze_turnover_halt_paths as a

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/研究产物/最新/high_turnover_double_low_20260923'


def summary(rows):
    return {
        'n': len(rows), 'codes': [r['code'] for r in rows],
        'repair_0945': sum(r['double_high_0945'] for r in rows),
        'return_positive_0945': sum(r['return_0945'] > 0 for r in rows),
        'return_0945': a.stats([r['return_0945'] for r in rows]),
        'worst_dip_from_0935': min((r['dip_to_0945'] for r in rows), default=None),
    }


def run():
    src = ROOT / 'outputs/研究产物/最新/turnover_halt_study_20260923/analysis.json'
    base = a.read_json(src)
    stocks = base['stocks']
    auction_manifest = a.read_json(ROOT / 'outputs/研究产物/最新/ifind_auction_snapshots_20260923/manifest.json')
    auctions = {r['code']: r for r in auction_manifest.get('rows', []) if r['status']=='verified'}
    old = [s for s in stocks if s['date'] <= '2026-07-14']
    history = [s for s in stocks if s['code'] not in a.TARGETS]
    quantiles = {name: {str(q): a.q([s['nodes']['09:35']['turnover'] for s in rs], q) for q in [2/3, .8, .9, .95]} for name, rs in [('old40', old), ('history58', history), ('all60', stocks)]}
    rows = []
    validations = 0
    for s in stocks:
        path = ROOT / s['bar_source']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == s['sha256']
        validations += 1
        n, m = s['nodes']['09:35'], s['nodes']['09:45']
        if not (n['price'] < s['open'] and n['price'] < n['vwap']):
            continue
        bars, _ = a.read_bars(path)
        live = [b for b in bars if b['volume'] > 0]
        between = [b for b in live if '09:35' < b['t'] <= '09:45']
        first_repair = None
        for b in between:
            prefix = [z for z in live if z['t'] <= b['t']]
            vwap = sum(z['amount'] for z in prefix) / sum(z['volume'] for z in prefix)
            if b['close'] > s['open'] and b['close'] > vwap:
                first_repair = b['t']
                break
        raw_turnover = sum(b['volume'] for b in live if b['t'] <= '09:35') / s['shares'] * 100
        assert abs(raw_turnover - n['turnover']) < 1e-8
        assert n['active'] and m['active']
        validations += 2
        rows.append({
            'code': s['code'], 'name': s['name'], 'date': s['date'],
            'history': s['code'] not in a.TARGETS,
            'turnover_0935': n['turnover'], 'opening_bar_turnover': s['auction_turnover'],
            'after_opening_bar_to_0935': n['turnover'] - s['auction_turnover'],
            'verified_auction_turnover': auctions.get(s['code'],{}).get('auction_turnover'),
            'gain_open_0935': n['gain_open'], 'bias_vwap_0935': n['bias_vwap'],
            'price_0935': n['price'], 'price_0945': m['price'],
            'turnover_increment_0945': m['turnover'] - n['turnover'],
            'return_0945': a.pct(m['price'], n['price']),
            'dip_to_0945': min(a.pct(min(b['low'] for b in between), n['price']), 0),
            'first_double_high_after_0935_by_0945': first_repair,
            'double_high_0945': m['price'] > s['open'] and m['price'] > m['vwap'],
        })
    bands = {}
    for key, low in [('old_p67', quantiles['old40'][str(2/3)]), ('rounded30', 30), ('old_p90', quantiles['old40']['0.9'])]:
        bands[key] = {'threshold': low, 'all60': summary([r for r in rows if r['turnover_0935'] >= low]), 'history58': summary([r for r in rows if r['history'] and r['turnover_0935'] >= low])}
    century = next(r for r in rows if r['code'] == '920229')
    century['rank_all60'] = 1 + sum(s['nodes']['09:35']['turnover'] > century['turnover_0935'] for s in stocks)
    century['rank_among_prior58_plus_self'] = 1 + sum(s['nodes']['09:35']['turnover'] > century['turnover_0935'] for s in history)
    result = {
        'as_of': '2026-09-23', 'source_sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
        'quantiles': quantiles, 'double_low_n': len(rows), 'bands': bands, 'century': century,
        'rows': rows, 'validation_checks': validations,
        'limits': ['Only 2 historical cases at 30%; zero historical double-low cases above the old P90.',
                   '30% is an exploratory rounded line, not an independently validated cutoff.',
                   'The 09:45 state and returns are outcomes, unavailable at 09:35.',
                   'No fees, slippage, optimal stopping, or full-path portfolio backtest.',
                   'Original sell-path records are preserved; a proposed small observation position is separate.'],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    lines = ['# 异常高换手与9:35双低后的修复', '',
             '世纪值得单列为“成交异常活跃、价格暂弱，观察能否迅速修复”。不能只记为双低规则漏掉，也不能凭极少案例改成全仓持有。', '',
             f"世纪9:35换手{century['turnover_0935']:.2f}%，此前58只P95为{quantiles['history58']['0.95']:.2f}%；当时连同世纪排第{century['rank_among_prior58_plus_self']}/59，加入凯达后第{century['rank_all60']}/60。",
             f"09:30首条混合换手{century['opening_bar_turnover']:.2f}%，不能当纯集合竞价。iFinD独立竞价快照确认其竞价换手{century['verified_auction_turnover']:.2f}%；9:35至9:45再增{century['turnover_increment_0945']:.2f}个百分点。9:39首次修复双高，9:45节点仍双高。", '',
             '| 9:35双低且换手≥30% | 9:35换手 | 9:45较9:35 | 9:45双高 | 等待期间较9:35最大下探 |', '|---|---:|---:|---|---:|']
    for r in rows:
        if r['turnover_0935'] >= 30:
            lines.append(f"| {r['name']} | {r['turnover_0935']:.2f}% | {r['return_0945']:+.2f}% | {'是' if r['double_high_0945'] else '否'} | {r['dip_to_0945']:.2f}% |")
    lines += ['',
              '≥30%为旧40只P80=29.56%附近的圆整观察线。历史只有科马、维琪2例，1例在9:45修复双高；世纪增加第3例。旧40只P90=33.08%以上的双低股仅世纪，没有历史同类验证。',
              '若放宽至旧P67=25.76%，双低共有5例，仅2例在9:45修复双高；爱得、金戈到9:45继续下跌，说明一般高换手不够。', '',
              '建议作为待验证旁路：9:35双低且换手约30%以上时，可按盘前计划先兑现主仓，只留至多30%观察仓到9:45；9:45仍未双高就兑现，修复双高才进入下一节点。仓位比例是风险约定，不能当成统计最优值。',
              '维琪虽在9:45上涨但仍未双高，这条旁路也会退出其观察仓；后续修复不能倒改节点条件。历史延后回报不含费用和滑点，不代表整条策略收益。', '']
    (OUT / '异常高换手早盘修复.md').write_text('\n'.join(lines), 'utf-8')
    print(json.dumps({k:result[k] for k in ['quantiles','double_low_n','bands','century','validation_checks']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()
