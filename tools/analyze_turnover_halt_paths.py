"""Local-only IPO turnover / halt-path research; does not change trading parameters.

Run with Python 3.12+. All features at a clock node use bars <= that node.
Daily data calibrates a fixed tradable-share denominator retrospectively; it is
not a point-in-time provenance guarantee for that share count.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "研究产物" / "最新" / "turnover_halt_study_20260923"
TARGETS = {'920229', '920025'}
NODES = ['09:35', '09:45', '10:00', '10:30', '11:00', '11:30', '13:05', '13:10', '13:20', '13:30', '14:00', '14:30']
LABELS = {'double_fly': '二停后大涨且收盘仍高于二停价20%', 'halt_fade': '触发临停后明显回落',
          'near_fail': '接近一停但失败回落', 'other_halt': '其他临停路径', 'other': '其他未临停'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def minute(t):
    h, m = map(int, t.split(':'))
    return h * 60 + m


def clock_slot(t):
    m = minute(t)
    return m - 570 if m <= 690 else m - 660


def pct(a, b):
    return (a / b - 1) * 100 if b else None


def q(xs, prob):
    xs = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not xs:
        return None
    p = (len(xs) - 1) * prob
    lo, hi = math.floor(p), math.ceil(p)
    return xs[lo] * (1 - (p - lo)) + xs[hi] * (p - lo)


def stats(xs):
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return {'n': len(xs), 'median': q(xs, .5), 'p25': q(xs, .25), 'p75': q(xs, .75)}


def read_bars(path):
    raw = path.read_bytes()
    for enc in ['utf-8-sig', 'gb18030']:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    bars, skipped = [], 0
    for row in csv.DictReader(io.StringIO(text)):
        try:
            value = row['DateTime'].replace('/', '-')
            try:
                dt = datetime.fromisoformat(value)
            except ValueError:
                dt = datetime.strptime(value, '%Y-%m-%d %H:%M')
            b = {k: float(row[k]) for k in ['open', 'high', 'low', 'close', 'volume', 'amount']}
        except (ValueError, KeyError, TypeError):
            skipped += 1
            continue
        if not all(math.isfinite(v) for v in b.values()):
            assert b['volume'] == 0, (path, row)
            skipped += 1
            continue
        assert b['volume'] >= 0 and b['amount'] >= 0
        assert b['high'] >= b['low'] > 0
        b.update(t=dt.strftime('%H:%M'), date=dt.strftime('%Y-%m-%d'))
        # Attach any separate 13:00 auction bar to the first afternoon minute,
        # without changing elapsed clock minutes at the session boundary.
        b['slot'] = 121 if b['t'] == '13:00' else clock_slot(b['t'])
        bars.append(b)
    bars.sort(key=lambda b: b['t'])
    assert len({b['date'] for b in bars}) == 1, (path, len(bars), {b['date'] for b in bars})
    assert len({b['t'] for b in bars}) == len(bars)
    return bars, skipped


def infer_events(bars, op):
    events = []
    for name, ratio, up in [('up30', 1.3, True), ('up60', 1.6, True), ('down30', .7, False), ('down60', .4, False)]:
        hit = next((i for i, b in enumerate(bars) if b['volume'] > 0 and
                    (b['high'] >= op * ratio - 1e-8 if up else b['low'] <= op * ratio + 1e-8)), None)
        if hit is None:
            continue
        resume = next((j for j in range(hit + 1, len(bars)) if bars[j]['volume'] > 0), None)
        # Match a threshold touch to the following zero/missing-volume gap.
        gap = bars[resume]['slot'] - bars[hit]['slot'] if resume is not None else 0
        corroborated = gap >= 8
        events.append({'type': name, 'time': bars[hit]['t'], 'hit_index': hit,
                       'threshold': round(op * ratio, 6), 'resume_time': bars[resume]['t'] if resume else None,
                       'resume_index': resume, 'gap_minutes': gap, 'corroborated': corroborated})
    return events


def active_slots(bars, events, end):
    # 09:30 (auction / first bar) is deliberately excluded from minute rates.
    slots = set(range(1, clock_slot(end) + 1))
    for e in events:
        if e['corroborated'] and e['time'] <= end:
            hit = bars[e['hit_index']]['slot']
            resume = bars[e['resume_index']]['slot']
            slots.difference_update(range(hit + 1, min(resume, clock_slot(end) + 1)))
    return slots


def window(bars, events, start, end, denominator):
    selected = [b for b in bars if start < b['t'] <= end]
    slots = active_slots(bars, events, end) - active_slots(bars, events, start)
    turnover = sum(b['volume'] for b in selected) / denominator * 100
    return {'turnover': turnover, 'minutes': len(slots), 'rate': turnover / len(slots) if slots else None}


def snapshot(bars, events, node, denominator, close, op):
    past = [b for b in bars if b['t'] <= node]
    traded = [b for b in past if b['volume'] > 0]
    slots = sorted(active_slots(bars, events, node))
    if not traded or not slots:
        return None
    p = traded[-1]['close']
    v = sum(b['volume'] for b in past)
    a = sum(b['amount'] for b in past)
    byslot = {}
    for b in past:
        byslot[b['slot']] = byslot.get(b['slot'], 0) + b['volume']
    def span(chosen):
        return sum(byslot[s] for s in chosen if s in byslot) / denominator * 100
    last30, prev30 = slots[-30:], slots[-60:-30]
    rate30 = span(last30) / len(last30)
    prev_rate = span(prev30) / len(prev30) if prev30 else None
    future = [b for b in bars if b['t'] > node and b['volume'] > 0]
    future30 = [b for b in future if b['slot'] in sorted(active_slots(bars,events,'15:00')-set(slots))[:30]]
    peak = max(b['high'] for b in traded)
    running = p
    fdd = 0
    for b in future:
        running = max(running, b['high'])
        fdd = min(fdd, pct(b['low'], running))
    return {'node': node, 'price': p, 'turnover': v / denominator * 100,
            'rate30': rate30, 'delta30': span(last30), 'prev_rate30': prev_rate,
            'retention30': rate30 / prev_rate if prev_rate else None,
            'vwap': a / v, 'bias_vwap': pct(p, a / v), 'gain_open': pct(p, op),
            'drawdown_sofar': pct(p, peak),
            'active': bool(clock_slot(node)-traded[-1]['slot']<=1 and not any(
                e['corroborated'] and e['time'] < node < e['resume_time'] for e in events)),
            'last_trade': traded[-1]['t'],
            'future_peak': pct(max((b['high'] for b in future), default=p), p),
            'future_low': pct(min((b['low'] for b in future), default=p), p),
            'future_path_drawdown': fdd, 'close_return': pct(close, p),
            'future30_close_return': pct(future30[-1]['close'],p) if future30 else None,
            'future30_peak': pct(max(b['high'] for b in future30),p) if future30 else None,
            'hit30_sofar': peak >= op * 1.3 - 1e-8, 'hit60_sofar': peak >= op * 1.6 - 1e-8}


def classify(op, hi, close, has30, has60):
    if has60 and hi >= op * 1.6 * 1.2 and close >= op * 1.6 * 1.2:
        return 'double_fly'
    if has30 and (close <= hi * .8 or close < op * (1.6 if has60 else 1.3)):
        return 'halt_fade'
    if not has30 and hi >= op * 1.2 and close <= hi * .9:
        return 'near_fail'
    return 'other_halt' if has30 else 'other'


def build_stock(path, item):
    bars, skipped = read_bars(path)
    code = path.stem
    variable_path = ROOT / f'data/tushare_db/variable_fields/{code}.BJ.json'
    fields = read_json(variable_path).get('fields', {})
    daily = fields.get('tushare_ipo_listing_day', {})
    date = bars[0]['date']
    daily_matched = daily.get('listing_date') == date or daily.get('trade_date') == date.replace('-', '')
    v = sum(b['volume'] for b in bars)
    daily_vol = (daily.get('volume') or 0) * 100 if daily_matched else 0
    tr = daily.get('turnover_rate') if daily_matched else None
    if daily_vol and tr:
        denom = daily_vol / (tr / 100)
        source = 'daily_volume_over_daily_turnover'
    elif item.get('TURNOVERRATE'):
        denom = v / (float(item['TURNOVERRATE']) / 100)
        source = 'csv_volume_over_replay_turnover'
    else:
        raise ValueError(f'No reliable turnover denominator: {code}')
    op = bars[0]['open']
    hi = max(b['high'] for b in bars)
    close = float(daily['close']) if daily_matched and daily.get('close') else bars[-1]['close']
    events = infer_events(bars, op)
    has30 = any(e['type'] == 'up30' for e in events)
    has60 = any(e['type'] == 'up60' for e in events)
    windows = {f'{a}-{b}': window(bars, events, a, b, denom) for a,b in [
        ('09:30','10:00'), ('10:00','10:30'), ('10:30','11:00'), ('11:00','11:30'),
        ('10:30','11:30'), ('11:30','13:30'), ('13:30','14:00'), ('14:00','14:30'),
        ('14:30','15:00'), ('11:30','15:00'), ('10:30','15:00')]}
    nodes = {t: snapshot(bars, events, t, denom, close, op) for t in NODES}
    nextrows = sorted([d for d in fields.get('tushare_ipo_post_listing_performance', {}).get('rows', [])
                       if d.get('trade_date', '') > date.replace('-', '')], key=lambda d:d['trade_date'])
    row = {'code':code, 'name':item.get('SECURITY_NAME_ABBR', code), 'date':date,
           'group':classify(op,hi,close,has30,has60), 'open':op, 'high':hi, 'close':close,
           'csv_close': bars[-1]['close'], 'high_time':max(bars,key=lambda b:b['high'])['t'],
           'high_gain':pct(hi,op), 'close_gain':pct(close,op), 'peak_close_dd':pct(close,hi),
           'shares':denom, 'denominator_source':source, 'csv_turnover':v/denom*100,
           'daily_turnover':tr or item.get('TURNOVERRATE'), 'daily_vol_diff_pct':pct(v,daily_vol),
           'n_bars':len(bars), 'skipped_footer_rows':skipped, 'events':events,
           'windows':windows, 'nodes':nodes, 'auction_turnover':sum(b['volume'] for b in bars if b['t'] <= '09:30') / denom * 100,
           'next_day_close_return': pct(nextrows[0]['close'],close) if nextrows else None,
           'next_day_high_return': pct(nextrows[0]['high'],close) if nextrows else None,
           'bar_source':str(path.relative_to(ROOT)), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    row['second_first_hour_ratio'] = windows['10:30-11:30']['turnover'] / nodes['10:30']['turnover']
    row['am_second_to_first_continuous_rate'] = windows['10:30-11:30']['rate'] / window(bars,events,'09:30','10:30',denom)['rate']
    assert abs(row['auction_turnover'] + windows['09:30-10:00']['turnover'] +
               windows['10:00-10:30']['turnover'] + windows['10:30-11:30']['turnover'] +
               windows['11:30-15:00']['turnover'] - row['csv_turnover']) < 1e-8
    row['post_halt'] = []
    for e in events:
        if e['type'] not in ['up30','up60'] or not e['corroborated']:
            continue
        resume = e['resume_index']
        # Observation after ten observed tradable minute bins, including reopening.
        valid = [b for b in bars[resume:] if b['slot'] in active_slots(bars,events,'15:00')]
        if len(valid) < 10:
            continue
        end = valid[9]['t']
        s = snapshot(bars,events,end,denom,close,op)
        reopen10 = sum(b['volume'] for b in valid[:10])/denom*100
        s.update(event=e['type'], trigger=e['time'], resume=e['resume_time'], reopen10_turnover=reopen10,
                 price_vs_trigger=pct(s['price'], e['threshold']),
                 reopen10_peak_vs_trigger=pct(max(b['high'] for b in valid[:10]),e['threshold']),
                 reopen10_low_vs_trigger=pct(min(b['low'] for b in valid[:10]),e['threshold']))
        row['post_halt'].append(s)
    # Minute VWAP consistency is an audit, not a source modification.
    valid = [b for b in bars if b['volume']>0]
    row['amount_price_bad_rows'] = sum(not b['low']*.995 <= b['amount']/b['volume'] <= b['high']*1.005 for b in valid)
    return row, bars


def outcome_summary(rows):
    if not rows:
        return {'n':0}
    def wilson(k,n):
        z=1.96; p=k/n; den=1+z*z/n
        center=(p+z*z/(2*n))/den; half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
        return [100*(center-half),100*(center+half)]
    k=sum(r['close_return']>0 for r in rows)
    return {'n':len(rows), 'positive_close_n':k, 'positive_close_pct':100*k/len(rows),
            'positive_close_wilson95':wilson(k,len(rows)),
            'peak10_n':sum(r['future_peak']>=10 for r in rows),
            'close_return':stats([r['close_return'] for r in rows]),
            'future_low':stats([r['future_low'] for r in rows]),
            'future_peak':stats([r['future_peak'] for r in rows]),
            'future30_close_return':stats([r['future30_close_return'] for r in rows]),
            'codes':[r['code'] for r in rows]}


def generate():
    OUT.mkdir(parents=True,exist_ok=True)
    items={str(i['SECURITY_CODE']):i for i in read_json(ROOT/'data/offline_tuning/replay_dataset.json')['items']}
    stocks=[]; series={}
    for path in sorted((ROOT/'首日分时走势').glob('*.csv')):
        row,bars=build_stock(path,items.get(path.stem,{}))
        stocks.append(row);series[row['code']]=bars
    stocks.sort(key=lambda r:r['date'])
    hist=[r for r in stocks if r['date']<'2026-09-22']
    node_rows=[]
    for r in stocks:
        for node,s in r['nodes'].items():
            if s:
                node_rows.append(dict(code=r['code'],name=r['name'],date=r['date'],group=r['group'],**s))
    histnodes=[r for r in node_rows if r['date']<'2026-09-22' and r['active']]
    groups={}
    for g in LABELS:
        rs=[r for r in hist if r['group']==g]
        groups[g]={'n':len(rs),'codes':[r['code'] for r in rs],
                   'total_turnover':stats([r['csv_turnover'] for r in rs]),
                   'first_hour':stats([r['nodes']['10:30']['turnover'] for r in rs]),
                   'second_hour':stats([r['windows']['10:30-11:30']['turnover'] for r in rs]),
                   'second_hour_rate':stats([r['windows']['10:30-11:30']['rate'] for r in rs]),
                   'afternoon':stats([r['windows']['11:30-15:00']['turnover'] for r in rs]),
                   'second_first_ratio':stats([r['second_first_hour_ratio'] for r in rs]),
                   'node_stats':{t:{k:stats([r['nodes'][t][k] for r in rs if r['nodes'][t]]) for k in ['turnover','rate30','retention30','bias_vwap']} for t in NODES}}
    # Fixed coarse thresholds for exploratory comparisons, not maximized over a grid.
    rules={
        'all':lambda s:True,
        'cum60':lambda s:s['turnover']>=60,
        'rate_ge_015':lambda s:s['rate30']>=.15,
        'rate_lt_010':lambda s:s['rate30']<.10,
        'retention_ge_050':lambda s:s['retention30'] is not None and s['retention30']>=.5,
        'volume_good':lambda s:s['rate30']>=.15 and (s['retention30'] or 0)>=.5,
        'price_good':lambda s:s['gain_open']>0 and s['bias_vwap']>0,
        'volume_price_good':lambda s:s['rate30']>=.15 and (s['retention30'] or 0)>=.5 and s['gain_open']>0 and s['bias_vwap']>0,
        'weak_below_vwap':lambda s:s['rate30']<.10 and s['bias_vwap']<0,
        'strong_price_low_rate':lambda s:s['rate30']<.10 and s['gain_open']>0 and s['bias_vwap']>0,
        'acceleration_near_high':lambda s:(s['retention30'] or 0)>=1.2 and s['rate30']>=.15 and s['bias_vwap']>0 and s['drawdown_sofar']>=-3,
    }
    bynode={t:{name:outcome_summary([s for s in histnodes if s['node']==t and rule(s)]) for name,rule in rules.items()} for t in NODES}
    periods={label:{t:{name:outcome_summary([s for s in histnodes if s['node']==t and cond(s) and rule(s)]) for name,rule in rules.items()} for t in ['10:30','11:00','11:30','14:00']}
             for label,cond in [('through_july14',lambda s:s['date']<='2026-07-14'),('after_july14',lambda s:s['date']>'2026-07-14'),
                                ('excluding_attention_events',lambda s:s['code'] not in ['920176','920059'])]}
    near_failure=[]
    for r in stocks:
        bars=series[r['code']]; arm=None; peak=r['open']
        for b in bars:
            if b['volume']<=0:
                continue
            if b['high']>=1.3*r['open']-1e-8:
                break
            if arm is None and b['high']>=1.27*r['open']:
                arm=b
            if arm is None:
                continue
            peak=max(peak,b['high'])
            elapsed=len(active_slots(bars,r['events'],b['t'])-active_slots(bars,r['events'],arm['t']))
            if elapsed>=10 and b['close']<=peak*.95:
                ev=snapshot(bars,r['events'],b['t'],r['shares'],r['close'],r['open'])
                near_failure.append(dict(code=r['code'],name=r['name'],date=r['date'],arm=arm['t'],**ev))
                break
    # Every prefix produces exactly the same online inputs: explicit anti-leakage check.
    checks=0
    for r in stocks:
        bars=series[r['code']]
        for node in NODES:
            s=r['nodes'][node]
            prefix=[b for b in bars if b['t']<=node]
            e=infer_events(prefix,r['open'])
            # A halt in progress has no future resume observable. Infer only its known
            # elapsed zero slots from the threshold; exclude halted nodes from decisions.
            if not s or not s['active']:
                continue
            p=snapshot(prefix,e,node,r['shares'],r['close'],r['open'])
            for key in ['turnover','rate30','retention30','vwap','bias_vwap','gain_open','drawdown_sofar']:
                assert s[key]==p[key], (r['code'],node,key,s[key],p[key])
            checks+=1
    result={'as_of':'2026-09-23','n_total':len(stocks),'n_history':len(hist),'range':[hist[0]['date'],hist[-1]['date']],
            'labels':LABELS,'groups':groups,'stocks':stocks,'node_outcomes':bynode,'period_checks':periods,
            'near_halt_failure':{'definition':'First reach open+27%; after >=10 tradable minutes still no up30 and price <= running high*0.95',
                                 'rows':near_failure,'history_outcomes':outcome_summary([r for r in near_failure if r['date']<'2026-09-22'])},
            'validation':{'prefix_node_checks':checks,'historical_group_counts':dict(Counter(r['group'] for r in hist))}}
    (OUT/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for filename,rows in [('node_metrics.csv',node_rows),('stock_summary.csv',[{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in stocks])]:
        with (OUT/filename).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps({'total':len(stocks),'history':len(hist),'groups':{LABELS[k]:v['n'] for k,v in groups.items()},'validation':result['validation']},ensure_ascii=False))
    for r in stocks:
        print(r['code'],r['name'],r['date'],r['group'],*[round(r[k],2) for k in ['high_gain','close_gain','peak_close_dd','csv_turnover']],
              'T60',round(r['nodes']['10:30']['turnover'],2),'H2',round(r['windows']['10:30-11:30']['turnover'],2),
              'PM',round(r['windows']['11:30-15:00']['turnover'],2),
              'volgap',round(r['daily_vol_diff_pct'],3) if r['daily_vol_diff_pct'] is not None else None,
              'events',[(e['type'],e['time'],e['corroborated']) for e in r['events']])


if __name__=='__main__':
    generate()
