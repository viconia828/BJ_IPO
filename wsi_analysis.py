import sys, time
sys.path.insert(0, r'C:\Wind\Wind.NET.Client\WindNET\x64')
from WindPy import w
w.start()

stocks = [
    ('920078.BJ','ZXXC','2026-03-18',6.98),
    ('920086.BJ','KMCL','2026-01-15',11.66),
    ('920180.BJ','ADKJ','2026-02-10',7.67),
    ('920119.BJ','MDLR','2026-01-30',41.88),
    ('920076.BJ','GLXC','2026-01-22',10.76),
    ('920183.BJ','HFM','2026-03-04',19.71),
    ('920050.BJ','ASL','2026-01-17',15.98),
    ('920166.BJ','HSYL','2026-02-12',12.64),
    ('920012.BJ','CDXC','2026-04-13',19.58),
    ('920069.BJ','PAYL','2026-03-27',18.38),
    ('920188.BJ','YLKJ','2026-03-30',14.04),
    ('920028.BJ','XHT','2026-03-20',9.4),
    ('920181.BJ','SYDZ','2026-04-10',28.0),
    ('920159.BJ','NDKJ','2026-01-28',25.0),
    ('920036.BJ','MRKJ','2026-03-09',21.52),
    ('920011.BJ','CGDJ','2026-04-08',15.5),
    ('920168.BJ','TBGD','2026-02-26',16.17),
    ('920055.BJ','LYGF','2026-03-31',24.7),
    ('920187.BJ','TLKJ','2026-03-05',29.62),
]

results = []
for code, name, date, iss_p in stocks:
    d = w.wsi(code, 'open,high,low,close,volume,amt',
              f'{date} 09:00:00', f'{date} 15:00:00',
              'periodstart=09:00:00;periodend=15:00:00')
    if d.ErrorCode != 0:
        print(f'ERROR {code}: {d.ErrorCode}')
        continue
    opens  = d.Data[0]
    highs  = d.Data[1]
    lows   = d.Data[2]
    closes = d.Data[3]
    vols   = d.Data[4]
    amts   = d.Data[5]
    n = len(opens)
    
    day_open  = opens[0]
    day_high  = max(highs)
    day_low   = min(lows)
    day_close = closes[-1]
    total_vol = sum(vols)
    total_amt = sum(amts)
    vwap = total_amt / total_vol if total_vol > 0 else 0
    
    open_prem = (day_open / iss_p - 1) * 100
    c_vwap = (day_close / vwap - 1) * 100
    c_h = day_close / day_high * 100
    high_idx = highs.index(day_high)
    high_pos = high_idx / n
    mid = n // 2
    vol_1h = sum(vols[:mid]) / total_vol * 100
    last30_start = closes[n-31] if n > 30 else closes[0]
    last30_end = closes[-1]
    last30_trend = (last30_end / last30_start - 1) * 100
    amplitude = (day_high - day_low) / day_open * 100
    day_dir = (day_close / day_open - 1) * 100
    total_gain = (vwap / iss_p - 1) * 100
    
    results.append((code, name, iss_p, total_gain, open_prem, c_vwap, c_h, high_pos, vol_1h, last30_trend, amplitude, day_dir))
    time.sleep(0.3)

results.sort(key=lambda x: x[3], reverse=True)

header = f"{'Code':<12} {'Name':<6} {'IssP':>6} {'VWAPg':>7} {'OpenP':>7} {'C/VWP':>7} {'C/H%':>6} {'HiPos':>6} {'V1H%':>6} {'T30m':>6} {'Ampl':>6} {'DayD':>7}"
print(header)
print('-' * len(header))
for r in results:
    code,nm,ip,tg,op,cv,ch,hp,vh,lt,amp,dd = r
    print(f'{code:<12} {nm:<6} {ip:>6.2f} {tg:>6.1f}% {op:>6.1f}% {cv:>6.1f}% {ch:>5.1f}% {hp:>5.2f} {vh:>5.1f}% {lt:>5.1f}% {amp:>5.1f}% {dd:>6.1f}%')

w.stop()
