import csv

def calc_scores(csvfile, name):
    rows = []
    with open(csvfile, encoding='gbk', errors='ignore') as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get('close', '').strip():
                rows.append(r)
    
    closes = [float(r['close']) for r in rows]
    highs  = [float(r['high']) for r in rows]
    vols   = [float(r['volume']) for r in rows]
    
    n = len(closes)
    day_close = closes[-1]
    day_high  = max(highs)
    
    # VWAP
    total_amt = sum(float(r['amount']) for r in rows)
    total_vol = sum(vols)
    vwap = total_amt / total_vol if total_vol else day_close
    
    # Indicators
    c_vwap = (day_close / vwap - 1) * 100
    c_h = day_close / day_high * 100
    hi_idx = highs.index(max(highs))
    hi_pos = hi_idx / n
    tail_start = max(0, n - 30)
    t30m = (closes[-1] / closes[tail_start] - 1) * 100
    mid = n // 2
    v1h = sum(vols[:mid]) / total_vol * 100 if total_vol else 50
    
    # Dim1: C/VWAP
    if c_vwap >= 10: s1 = 95
    elif c_vwap >= 5: s1 = 80
    elif c_vwap >= 1: s1 = 65
    elif c_vwap >= -1: s1 = 50
    elif c_vwap >= -5: s1 = 35
    elif c_vwap >= -10: s1 = 20
    else: s1 = 5
    
    # Dim2: C/High
    if c_h >= 95: s2 = 95
    elif c_h >= 90: s2 = 80
    elif c_h >= 85: s2 = 65
    elif c_h >= 80: s2 = 45
    elif c_h >= 75: s2 = 25
    else: s2 = 10
    
    # Dim3: HiPos
    if hi_pos >= 0.80: s3 = 95
    elif hi_pos >= 0.50: s3 = 75
    elif hi_pos >= 0.25: s3 = 55
    elif hi_pos >= 0.10: s3 = 35
    else: s3 = 15
    
    # Dim4: Tail 30m
    if t30m >= 5: s4 = 95
    elif t30m >= 2: s4 = 75
    elif t30m >= 0.5: s4 = 60
    elif t30m >= -0.5: s4 = 45
    elif t30m >= -2: s4 = 30
    else: s4 = 15
    
    # Dim5: Volume rhythm
    if 65 <= v1h < 75: s5 = 80
    elif 75 <= v1h < 80: s5 = 65
    elif 60 <= v1h < 65: s5 = 55
    elif 80 <= v1h < 85: s5 = 50
    elif 85 <= v1h < 90: s5 = 35
    else: s5 = 20
    
    total = 0.30*s1 + 0.25*s2 + 0.20*s3 + 0.15*s4 + 0.10*s5
    
    print(f"=== {name} ===")
    print(f"C/VWAP={c_vwap:+.1f}% -> {s1}")
    print(f"C/High={c_h:.1f}% -> {s2}")
    print(f"HiPos={hi_pos:.2f} -> {s3}")
    print(f"Tail30m={t30m:+.1f}% -> {s4}")
    print(f"VolRhythm(v1h)={v1h:.1f}% -> {s5}")
    print(f"Weighted(5dim)={total:.1f}")
    print()

calc_scores("首日分时走势/920086.csv", "920086 科马材料")
calc_scores("首日分时走势/920050.csv", "920050 爱舍伦")
