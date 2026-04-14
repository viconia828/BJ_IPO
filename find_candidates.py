"""查找北交所920系新股"""
import sys, datetime, time
sys.path.insert(0, r'C:\Wind\Wind.NET.Client\WindNET\x64')
from WindPy import w
w.start()

existing = {'920011','920012','920028','920036','920050','920055','920069',
            '920076','920078','920086','920119','920159','920166','920168',
            '920180','920181','920183','920187','920188'}

results = []
# batch of 30
for batch_start in range(1, 300, 30):
    batch_end = min(batch_start + 30, 300)
    codes = [f'920{i:03d}.BJ' for i in range(batch_start, batch_end)]
    d = w.wss(','.join(codes), 'sec_name,ipo_date,ipo_price')
    if d.ErrorCode != 0:
        continue
    for i, c in enumerate(codes):
        nm = d.Data[0][i]
        dt = d.Data[1][i]
        pr = d.Data[2][i]
        if nm and dt and isinstance(dt, datetime.datetime) and dt.year >= 2025:
            num = c.split('.')[0]
            results.append((c, num, nm, dt, pr, num in existing))
    time.sleep(0.3)

results.sort(key=lambda x: x[3], reverse=True)
print(f'北交所920系新股 (共{len(results)}只):')
print(f'{"代码":<12} {"名称":<10} {"上市日期":<12} {"发行价":>8} 状态')
print('-' * 60)
for c, num, nm, dt, pr, ex in results:
    tag = '已有' if ex else 'NEW'
    p = f'{pr:.2f}' if pr else 'N/A'
    print(f'{c:<12} {nm:<10} {dt.strftime("%Y-%m-%d"):<12} {p:>8} {tag}')

w.stop()
