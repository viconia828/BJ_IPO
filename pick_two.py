"""从近期NEW标的中获取首日走势指标，挑选一强一弱"""
import sys, time
sys.path.insert(0, r'C:\Wind\Wind.NET.Client\WindNET\x64')
from WindPy import w
w.start()

# 挑近期的NEW标的 (2025年11月-12月)
candidates = [
    ('920045.BJ', '蘅东光',   '2025-12-31', 31.59),
    ('920121.BJ', '江天科技', '2025-12-25', 21.21),
    ('920035.BJ', '精创电气', '2025-12-02', 12.10),
    ('920124.BJ', '南特科技', '2025-11-27',  8.66),
    ('920091.BJ', '大鹏工业', '2025-11-21',  9.00),
    ('920160.BJ', '北矿检测', '2025-11-18',  6.70),
    ('920003.BJ', '中诚咨询', '2025-11-07', 14.27),
    ('920009.BJ', '丹娜生物', '2025-11-03', 17.10),
]

print(f'{"代码":<12} {"名称":<8} {"上市日":<12} {"发行价":>6} {"开盘涨":>7} {"VWAP涨":>7} {"C/VWAP":>7} {"C/High":>6} {"HiPos":>6} {"尾盘":>6} {"振幅":>6} {"日内方向":>7} bars')
print('-' * 120)

for code, name, date, iss_p in candidates:
    d = w.wsi(code, 'open,high,low,close,volume,amt',
              f'{date} 09:00:00', f'{date} 15:00:00',
              'periodstart=09:00:00;periodend=15:00:00')
    if d.ErrorCode != 0:
        print(f'{code:<12} {name:<8} ERROR={d.ErrorCode}')
        time.sleep(0.3)
        continue

    opens  = [x for x in d.Data[0] if x is not None and x == x]
    highs  = [x for x in d.Data[1] if x is not None and x == x]
    lows   = [x for x in d.Data[2] if x is not None and x == x]
    closes = [x for x in d.Data[3] if x is not None and x == x]
    vols   = d.Data[4]
    amts   = d.Data[5]
    n = len(d.Data[0])

    # filter valid bars
    valid_o, valid_h, valid_l, valid_c, valid_v, valid_a = [], [], [], [], [], []
    for i in range(n):
        o, h, lo, c, v, a = d.Data[0][i], d.Data[1][i], d.Data[2][i], d.Data[3][i], d.Data[4][i], d.Data[5][i]
        if o and o == o and v and v > 0:
            valid_o.append(o); valid_h.append(h); valid_l.append(lo); valid_c.append(c)
            valid_v.append(v); valid_a.append(a)

    if not valid_o:
        print(f'{code:<12} {name:<8} NO VALID DATA')
        continue

    nv = len(valid_o)
    day_open = valid_o[0]
    day_high = max(valid_h)
    day_low  = min(valid_l)
    day_close = valid_c[-1]
    total_vol = sum(valid_v)
    total_amt = sum(valid_a)
    vwap = total_amt / total_vol if total_vol > 0 else day_close

    open_prem = (day_open / iss_p - 1) * 100
    vwap_gain = (vwap / iss_p - 1) * 100
    c_vwap = (day_close / vwap - 1) * 100
    c_high = day_close / day_high * 100
    hi_idx = 0
    for i, h in enumerate(valid_h):
        if h == day_high:
            hi_idx = i
            break
    hi_pos = hi_idx / nv if nv > 0 else 0
    amplitude = (day_high - day_low) / day_open * 100
    day_dir = (day_close / day_open - 1) * 100

    # tail 30min
    tail_n = min(30, nv)
    tail_start = valid_c[nv - tail_n - 1] if nv > tail_n else valid_c[0]
    tail_end = valid_c[-1]
    tail_trend = (tail_end / tail_start - 1) * 100

    print(f'{code:<12} {name:<8} {date:<12} {iss_p:>6.2f} {open_prem:>6.1f}% {vwap_gain:>6.1f}% {c_vwap:>6.1f}% {c_high:>5.1f}% {hi_pos:>5.2f} {tail_trend:>5.1f}% {amplitude:>5.1f}% {day_dir:>6.1f}% {n}')
    time.sleep(0.5)

w.stop()
