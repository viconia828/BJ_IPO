"""Opening-bar turnover vs waiting, with an explicit auction data-quality gate.

The cache has no separately identified 09:25 auction records. A flat 09:30 OHLC
bar with amount/volume matching its price is a proxy, not proof of pure auction
volume. Non-flat opening bars cannot be used as opening-time auction signals.
"""
from __future__ import annotations

import hashlib
import json
import copy
from pathlib import Path

import analyze_turnover_halt_paths as a

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/研究产物/最新/opening_auction_wait_20260923'
BANDS = [(0, 3, '<3%'), (3, 4, '3%-4%'), (4, 5, '4%-5%'), (5, 6, '5%-6%'), (6, float('inf'), '>=6%')]
NODES = ['09:35', '09:45', '10:00']


def summarize(rows):
    result = {'n': len(rows), 'names': [r['name'] for r in rows]}
    for node in NODES:
        values = [r['outcomes'][node]['exit_return'] for r in rows]
        observed = [r['outcomes'][node]['clock_return'] for r in rows if r['outcomes'][node]['clock_return'] is not None]
        result[node] = {
            'reference_return': a.stats(values), 'positive_n': sum(x > 0 for x in values),
            'mean': sum(values) / len(values) if values else None,
            'clock_active_n': len(observed), 'clock_return': a.stats(observed),
            'halted_n': len(values)-len(observed),
            'clock_positive_n': sum(x > 0 for x in observed),
            'peak_ge10': sum(r['outcomes'][node]['peak_until_clock'] >= 10 for r in rows),
            'dip_le_minus5': sum(r['outcomes'][node]['dip_until_exit'] <= -5 for r in rows),
            'dip': a.stats([r['outcomes'][node]['dip_until_exit'] for r in rows]),
        }
    return result


def run():
    source = ROOT / 'outputs/研究产物/最新/turnover_halt_study_20260923/analysis.json'
    base = a.read_json(source)
    stocks = sorted(base['stocks'], key=lambda s: (s['date'], s['code']))
    rows = []
    checks = 0
    for s in stocks:
        path = ROOT / s['bar_source']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == s['sha256']
        bars, _ = a.read_bars(path)
        live = [b for b in bars if b['volume'] > 0]
        first = live[0]
        assert first['t'] == '09:30'
        first_t = first['volume']/s['shares']*100
        assert abs(first_t-s['auction_turnover']) < 1e-8
        flat = first['open'] == first['high'] == first['low'] == first['close']
        vwap_deviation = first['amount']/first['volume']/first['open'] - 1
        single = flat and abs(vwap_deviation) < .001
        checks += 3
        r = {
            'code': s['code'], 'name': s['name'], 'date': s['date'], 'open': s['open'],
            'single_price_proxy': single, 'first_bar': first, 'turnover': first_t,
            'first_vwap_deviation_pct': vwap_deviation*100,
            'band': next(label for lo, hi, label in BANDS if lo <= first_t < hi),
            'period': 'old40' if s['date'] <= '2026-07-14' else 'recent20',
            'external_label': s['code'] in ['920176','920059'],
            'close_vs_open': a.pct(s['csv_close'], s['open']),
            'denominator_source': s['denominator_source'], 'outcomes': {}, 'sentiment': {},
        }
        for node in NODES:
            snap = s['nodes'][node]
            active = snap['active']
            if active:
                exit_price, exit_time = snap['price'], node
                through = [b for b in live if b['t'] <= node]
            else:
                reopen = next(b for b in live if b['t'] > node)
                exit_price, exit_time = reopen['open'], reopen['t']
                # We exit at the first reopening trade proxy, not that minute's low.
                through = [b for b in live if b['t'] < exit_time]
            clock_bars = [b for b in live if b['t'] <= node]
            r['outcomes'][node] = {
                'clock_return': a.pct(snap['price'], s['open']) if active else None,
                'exit_time': exit_time, 'exit_price': exit_price,
                'exit_return': a.pct(exit_price, s['open']), 'delayed_for_halt': not active,
                'dip_until_exit': min(a.pct(min([b['low'] for b in through] + [exit_price]), s['open']), 0),
                'peak_until_clock': max(a.pct(max(b['high'] for b in clock_bars), s['open']), 0),
            }
            if active:
                assert abs(r['outcomes'][node]['exit_return'] - snap['gain_open']) < 1e-8
            checks += 1
        # No same-date listings or future close can enter a morning sentiment signal.
        prior = [x for x in rows if x['date'] < s['date']]
        for length in [3, 5, 10]:
            selected = prior[-length:]
            complete = len(selected) == length
            med = a.q([x['close_vs_open'] for x in selected], .5) if complete else None
            mood = 'unknown' if med is None else 'positive' if med > 0 else 'nonpositive'
            r['sentiment'][str(length)] = {
                'n': len(selected), 'codes': [x['code'] for x in selected],
                'latest_date': selected[-1]['date'] if selected else None,
                'median_open_to_close': med, 'state': mood,
            }
            assert all(x['date'] < s['date'] for x in selected)
            checks += 1
        rows.append(r)
    manifest_path=ROOT/'outputs/研究产物/最新/ifind_auction_snapshots_20260923/manifest.json'
    manifest=a.read_json(manifest_path)
    assert manifest['verified_n']==manifest['requested_n']==60
    auctions={r['code']:r for r in manifest['rows']}
    verified=[]
    for r in rows:
        record=auctions[r['code']]
        assert record['status']=='verified' and record['date']==r['date']
        assert record['snapshot_time'][11:]<'09:30:00'
        # One exported CSV rounded 1,464,708 shares to 1,464,700; accept <=0.001%.
        assert record['auction_volume']<=r['first_bar']['volume']*1.00001+.01
        v=copy.deepcopy(r)
        v['opening_bar_turnover']=r['turnover']
        v['turnover']=record['auction_turnover']
        v['auction_snapshot']=record
        v['band']=next(label for lo,hi,label in BANDS if lo<=v['turnover']<hi)
        verified.append(v)
        checks+=3
    groups = {}
    for scope, subset in [('verified_auction',verified),('single_price_proxy', [r for r in rows if r['single_price_proxy']]), ('mixed_opening_bar', [r for r in rows if not r['single_price_proxy']]), ('all60_not_auction', rows)]:
        groups[scope] = {
            'summary': summarize(subset),
            'bands': {label:summarize([r for r in subset if r['band']==label]) for _,_,label in BANDS},
            'threshold5': {side:summarize([r for r in subset if (r['turnover'] >= 5)==(side=='at_least5')]) for side in ['below5','at_least5']},
            'middle3to5': summarize([r for r in subset if 3 <= r['turnover'] < 5]),
            'periods': {p: {side:summarize([r for r in subset if r['period']==p and (r['turnover'] >= 5)==(side=='at_least5')]) for side in ['below5','at_least5']} for p in ['old40','recent20']},
            'sentiment': {length: {state: {side:summarize([r for r in subset if r['sentiment'][length]['state']==state and (r['turnover'] >= 5)==(side=='at_least5')]) for side in ['below5','at_least5']} for state in ['unknown','positive','nonpositive']} for length in ['3','5','10']},
        }
    high = [r for r in verified if r['turnover'] >= 5]
    robustness = {
        'high5_without_external': summarize([r for r in high if not r['external_label']]),
        'high5_without_latest_kaida': summarize([r for r in high if r['code'] != '920025']),
        'high5_without_external_or_latest': summarize([r for r in high if not r['external_label'] and r['code'] != '920025']),
    }
    result = {
        'as_of': '2026-09-23', 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'auction_manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        'definitions': {
            'verified_auction':'iFinD snap_shot records at/after09:25 and before09:30, matched listing-date/open and amount=volume*open; 60/60 verified',
            'single_price_proxy':'09:30 OHLC all equal, abs(amount/volume/open-1)<0.001; still not certified 09:25 auction data',
            'main_return':'09:35 minute close / opening price - 1; if halted, first later traded minute open / opening price - 1',
            'timing':'Final auction volume is known only after auction matching; opening price is a comparison baseline, not an assured later exit price.',
            'sentiment':'median of prior 5 locally cached IPO first-day close/open-1; positive vs nonpositive; 3/10 sensitivity; exclude same-date and require full window',
            'turnover':'fixed retrospectively calibrated float denominator, not proved available before listing',
            'thresholds':'3/4/5/6 percentage points supplied by user; no optimized split',
        },
        'groups':groups, 'robustness':robustness, 'rows':verified, 'opening_bar_rows':rows, 'validation_checks':checks,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'analysis.json').write_text(json.dumps(result, ensure_ascii=False,indent=2),'utf-8')
    lines = ['# 集合竞价换手与一手仓位的开盘选择', '',
             '## 先确认可用的数据', '',
             '已通过iFinD官方HTTP snap_shot接口取得60/60只上市日独立竞价成交记录，时间为09:25:00/01，逐只核对成交价等于开盘价、成交额约等于成交量乘成交价。竞价量按股计；换手分母仍沿用原研究的事后校准流通股本。',
             '原缓存33只09:30首条混有多价成交，不能当纯竞价量。世纪真实竞价849,300股、换手7.48%，不是原首条的11.43%；凯达4,332,318股、换手8.49%。9:35累计34.36%的结论不受这一标签更正影响。',
             '[iFinD官方接口手册](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html)：高频序列最细1分钟；日内快照支持历史行情和盘口，本研究用实际秒级时间戳记录提取竞价，不把快照称为逐笔成交。', '',
             '## 独立竞价换手：按用户提出的档位比较', '',
             '| 竞价换手 | 样本 | 等到9:35更高 | 9:35参考价差中位 | 9:45参考价差中位 | 等到9:35曾下探≥5% |', '|---|---:|---:|---:|---:|---:|']
    for _,_,label in BANDS:
        g=groups['verified_auction']['bands'][label];n=g['n'];x=g['09:35'];y=g['09:45']
        lines.append(f"| {label} | {n} | {x['positive_n']}/{n} | {x['reference_return']['median']:+.2f}% | {y['reference_return']['median']:+.2f}% | {x['dip_le_minus5']}/{n} |")
    lines += ['', '均相对开盘价，未扣费用和滑点。节点临停时顺延至复牌首个有成交分钟的开价作为价格参考：彩客9:35、双英9:45分别受影响；开价不是保证成交价。真正固定节点可交易样本数及结果同时保存在JSON，避免把停前价当可成交价。', '',
              '## 近期情绪：只用此前已上市样本', '',
              '主代理为此前5只本地新股首日“收盘/开盘-1”的中位数，大于0记为正，其余为非正。该代理衡量开盘后赚钱效应，不等于完整市场情绪；不足5只单列未知。', '',
              '| 前5只赚钱效应 | 竞价换手 | 样本 | 9:35更高 | 参考价差中位 |', '|---|---|---:|---:|---:|']
    for state,cn in [('positive','正'),('nonpositive','非正'),('unknown','不足5只')]:
        for side,lab in [('below5','<5%'),('at_least5','≥5%')]:
            g=groups['verified_auction']['sentiment']['5'][state][side];x=g['09:35']
            med='无样本' if x['reference_return']['median'] is None else f"{x['reference_return']['median']:+.2f}%"
            lines.append(f"| {cn} | {lab} | {g['n']} | {x['positive_n']}/{g['n']} | {med} |")
    lines += ['', '## 结论边界', '',
              '- ≥5%共14只，11只等待后参考价更高，中位+6.78%，其中3只等待中曾跌破开盘价5%；可以作为等到9:35的加分项，不能保证一路上涨。',
              '- 3%-4%组仅4/13等待后更高，中位-5.57%，支持提高开盘后尽快兑现的优先级；4%-5%组为5/10、中位+0.68%，接近中性，不能与3%-4%合并判死。',
              '- <3%并未延续“越低越弱”，不能把所有低于5%的票统一判为没戏；华大海天是明显反例。',
              '- 竞价≥5%且此前5只赚钱效应为正的7只，5只等待更高；赚钱效应非正的7只反而6只更高。当前情绪代理未提高区分度，不能据此追加稳定的情绪开关。',
              '- 最终竞价量在撮合后才确定；此时决定卖出，实际是等待连续竞价成交，不能保证仍按开盘价退出。',
              '- 成交量已由独立竞价数据确认；仍需要盘前可得流通股本、开盘后首笔/盘口与费用模型，才能做严格的执行收益验证。',
              '- 后续价格与回撤仍采用原分钟缓存；iFinD凯达完整分钟另存备查，补齐尾盘393,345股，少数高低价与缓存不同，未悄悄混入原回放。', '',
              '完整分层、3/10只窗口、旧40/新20分期及剔除外力/凯达敏感性见analysis.json；不改动交易参数。', '']
    (OUT/'竞价换手与开盘选择.md').write_text('\n'.join(lines),'utf-8')
    slim = {'auction_bands':groups['verified_auction']['bands'], 'sentiment5':groups['verified_auction']['sentiment']['5'], 'robustness':robustness, 'validation_checks':checks}
    print(json.dumps(slim,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    run()
