import csv

def parse_csv(filepath):
    rows = []
    with open(filepath, 'r', encoding='gbk', errors='ignore') as f:
        reader = csv.reader(f)
        header = None
        for line in reader:
            if not line or len(line) < 7:
                continue
            stripped = [c.strip() for c in line]
            if stripped[0] == 'DateTime' or stripped[0] == '' or 'Wind' in stripped[0]:
                if header is None:
                    header = stripped
                continue
            dt = stripped[0]
            try:
                o = float(stripped[1]) if stripped[1] != 'NaN' else None
                h = float(stripped[2]) if stripped[2] != 'NaN' else None
                l = float(stripped[3]) if stripped[3] != 'NaN' else None
                c = float(stripped[4]) if stripped[4] != 'NaN' else None
                v = float(stripped[5]) if stripped[5] != '0' and stripped[5] != '' else 0
                a = float(stripped[6]) if stripped[6] != '0' and stripped[6] != '' else 0
            except:
                continue
            if o is not None and c is not None:
                rows.append({'dt': dt, 'open': o, 'high': h, 'low': l, 'close': c, 'vol': v, 'amt': a})
    return rows

def classify_4type(hi_pos, c_vwap, ampl, day_dir):
    if ampl < 15 and abs(c_vwap) < 3:
        return 'D'
    elif hi_pos < 0.10:
        return 'A'
    elif hi_pos >= 0.15 and c_vwap >= 0:
        return 'B'
    elif hi_pos >= 0.15 and c_vwap < 0:
        return 'C'
    else:
        if c_vwap >= 0:
            return 'B'
        elif day_dir < -5:
            return 'A'
        else:
            return 'C'

def compute_indicators(rows, issue_price, label):
    if not rows:
        print(f'{label}: no valid rows')
        return
    
    total_vol = sum(r['vol'] for r in rows)
    total_amt = sum(r['amt'] for r in rows)
    vwap = total_amt / total_vol if total_vol > 0 else rows[0]['open']
    
    open_price = rows[0]['open']
    close_price = rows[-1]['close']
    
    day_high = max(r['high'] for r in rows if r['high'] is not None)
    day_low = min(r['low'] for r in rows if r['low'] is not None)
    
    hi_idx = 0
    for i, r in enumerate(rows):
        if r['high'] is not None and r['high'] == day_high:
            hi_idx = i
            break
    
    n = len(rows)
    hi_pos = hi_idx / (n - 1) if n > 1 else 0
    
    vwap_chg = (vwap / issue_price - 1) * 100
    open_premium = (open_price / issue_price - 1) * 100
    c_vwap = (close_price / vwap - 1) * 100
    c_high = (close_price / day_high) * 100
    
    mid = n // 2
    first_half_vol = sum(r['vol'] for r in rows[:mid])
    first_half_ratio = (first_half_vol / total_vol * 100) if total_vol > 0 else 50
    
    if n >= 30:
        close_30ago = rows[-30]['close']
        tail_trend = (close_price / close_30ago - 1) * 100
    else:
        tail_trend = 0
    
    amplitude = ((day_high - day_low) / open_price) * 100
    day_dir = (close_price / open_price - 1) * 100
    
    wtype = classify_4type(hi_pos, c_vwap, amplitude, day_dir)
    type_names = {'A': 'A-高开回落', 'B': 'B-冲高维持', 'C': 'C-冲高回落', 'D': 'D-贴线震荡'}
    
    print(f'=== {label} (发行价={issue_price}) ===')
    print(f'  有效Bars: {n}')
    print(f'  Open: {open_price}, Close: {close_price}, High: {day_high}, Low: {day_low}')
    print(f'  VWAP: {vwap:.2f}')
    print(f'  VWAP涨幅: {vwap_chg:.1f}%')
    print(f'  开盘溢价: {open_premium:.1f}%')
    print(f'  C/VWAP: {c_vwap:+.1f}%')
    print(f'  C/High: {c_high:.1f}%')
    print(f'  高点位置: {hi_pos:.2f} (bar {hi_idx}/{n})')
    print(f'  前半量占比: {first_half_ratio:.1f}%')
    print(f'  尾30m趋势: {tail_trend:+.1f}%')
    print(f'  振幅: {amplitude:.1f}%')
    print(f'  日方向: {day_dir:+.1f}%')
    print(f'  走势类型: {type_names[wtype]}')
    print()

# Parse CSV files
rows_086 = parse_csv(r'C:\Users\Administrator\Desktop\北交所新股估值\首日分时走势\920086.csv')
rows_050 = parse_csv(r'C:\Users\Administrator\Desktop\北交所新股估值\首日分时走势\920050.csv')

compute_indicators(rows_086, 11.66, '920086 科马材料')
compute_indicators(rows_050, 15.98, '920050 爱舍伦')

# Reclassify all 17 samples
print('=' * 60)
print('全部17只样本重新分类（四类体系）')
print('=' * 60)
print()

samples = [
    ('920078', '族兴新材', 411.6, 372.8, -1.1, 82.3, 0.40, 87.3, -2.5, 34.5, 7.0),
    ('920180', '爱得科技', 203.0, 225.9, -8.6, 81.7, 0.00, 73.1, -1.7, 19.1, -15.1),
    ('920119', '美德乐', 186.9, 222.3, -8.9, 76.8, 0.00, 60.4, -0.7, 25.4, -18.9),
    ('920076', '国亮新材', 178.0, 207.7, -6.2, 81.7, 0.00, 74.4, 0.1, 20.1, -15.3),
    ('920183', '海菲曼', 177.5, 159.6, -2.5, 82.6, 0.58, 81.7, -2.2, 28.3, 4.2),
    ('920166', '海圣医疗', 142.3, 121.5, 12.6, 79.1, 0.78, 77.0, -0.0, 58.7, 23.2),
    ('920188', '悦龙科技', 141.7, 183.9, -11.0, 75.5, 0.00, 69.6, -2.6, 24.8, -24.2),
    ('920012', '创达新材', 136.8, 119.9, 11.4, 94.1, 0.30, 88.4, 2.8, 30.0, 20.0),
    ('920069', '普昂医疗', 130.4, 112.2, 2.7, 87.0, 0.16, 85.0, -0.5, 31.1, 11.5),
    ('920028', '新恒泰', 125.7, 123.4, 7.0, 99.6, 0.98, 79.1, 11.3, 12.0, 8.1),
    ('920181', '赛英电子', 125.0, 114.3, -1.9, 90.1, 0.11, 78.2, -0.6, 14.3, 3.0),
    ('920159', '农大科技', 105.6, 99.6, 2.9, 81.1, 0.55, 82.9, -2.7, 36.3, 6.0),
    ('920036', '觅睿科技', 102.7, 126.8, -5.3, 83.2, 0.00, 66.0, -0.4, 18.0, -15.4),
    ('920011', '晨光电机', 91.4, 93.5, -2.1, 90.6, 0.63, 67.8, -0.5, 11.8, -3.2),
    ('920168', '通宝光电', 82.1, 82.4, -6.6, 87.0, 0.02, 67.9, -1.3, 14.3, -6.8),
    ('920055', '隆源股份', 54.2, 45.7, -0.7, 87.7, 0.50, 81.3, -1.3, 19.7, 5.0),
    ('920187', '通领科技', 52.2, 51.9, -7.1, 84.8, 0.01, 70.7, 0.2, 17.7, -6.9),
]

type_names = {'A': 'A-高开回落', 'B': 'B-冲高维持', 'C': 'C-冲高回落', 'D': 'D-贴线震荡'}
type_count = {'A': 0, 'B': 0, 'C': 0, 'D': 0}

for s in samples:
    code, name, vwap_chg, open_prem, c_vwap, c_high, hi_pos, v1h, tail30, ampl, day_dir = s
    wtype = classify_4type(hi_pos, c_vwap, ampl, day_dir)
    type_count[wtype] += 1
    print(f'  {code} {name:<6s}  VWAP涨幅={vwap_chg:6.1f}%  HiPos={hi_pos:.2f}  C/VWAP={c_vwap:+5.1f}%  振幅={ampl:5.1f}%  -> {type_names[wtype]}')

print()
print('分布统计:')
for t in ['A', 'B', 'C', 'D']:
    print(f'  {type_names[t]}: {type_count[t]}/17 ({type_count[t]/17*100:.0f}%)')
