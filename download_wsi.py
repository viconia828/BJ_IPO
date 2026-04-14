"""
批量下载北交所新股首日 WSI 分时数据并保存为 CSV
----------------------------------------------
- 数据源: Wind WSI (1分钟K线)
- 输出目录: 首日分时走势/{code}.csv
- 编码: GBK (与已有的 920086/920050 一致)
- 已有 CSV 的标的自动跳过
"""

import sys, os, time, csv

sys.path.insert(0, r'C:\Wind\Wind.NET.Client\WindNET\x64')
from WindPy import w

# ============ 标的列表 (19只) ============
stocks = [
    ('920078.BJ', '族兴新材', '2026-03-18', 6.98),
    ('920086.BJ', '科马材料', '2026-01-15', 11.66),
    ('920180.BJ', '爱得科技', '2026-02-10', 7.67),
    ('920119.BJ', '美德乐',   '2026-01-30', 41.88),
    ('920076.BJ', '国亮新材', '2026-01-22', 10.76),
    ('920183.BJ', '海菲曼',   '2026-03-04', 19.71),
    ('920050.BJ', '爱舍伦',   '2026-01-17', 15.98),
    ('920166.BJ', '海圣医疗', '2026-02-12', 12.64),
    ('920012.BJ', '创达新材', '2026-04-13', 19.58),
    ('920069.BJ', '普昂医疗', '2026-03-27', 18.38),
    ('920188.BJ', '悦龙科技', '2026-03-30', 14.04),
    ('920028.BJ', '新恒泰',   '2026-03-20', 9.4),
    ('920181.BJ', '赛英电子', '2026-04-10', 28.0),
    ('920159.BJ', '农大科技', '2026-01-28', 25.0),
    ('920036.BJ', '觅睿科技', '2026-03-09', 21.52),
    ('920011.BJ', '晨光电机', '2026-04-08', 15.5),
    ('920168.BJ', '通宝光电', '2026-02-26', 16.17),
    ('920055.BJ', '隆源股份', '2026-03-31', 24.7),
    ('920187.BJ', '通领科技', '2026-03-05', 29.62),
]

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '首日分时走势')
os.makedirs(OUT_DIR, exist_ok=True)

def code_short(code):
    """920078.BJ -> 920078"""
    return code.split('.')[0]

def save_csv(code, name, date, data):
    """将 Wind WSI 返回的 data 保存为 CSV"""
    fpath = os.path.join(OUT_DIR, f'{code_short(code)}.csv')
    times  = data.Times
    opens  = data.Data[0]
    highs  = data.Data[1]
    lows   = data.Data[2]
    closes = data.Data[3]
    vols   = data.Data[4]
    amts   = data.Data[5]
    n = len(times)

    with open(fpath, 'w', newline='', encoding='gbk') as f:
        writer = csv.writer(f)
        writer.writerow(['DateTime', 'open', 'high', 'low', 'close', 'volume', 'amount'])
        for i in range(n):
            dt_str = times[i].strftime('%Y/%m/%d %H:%M') if hasattr(times[i], 'strftime') else str(times[i])
            row = [dt_str, opens[i], highs[i], lows[i], closes[i], vols[i], amts[i]]
            writer.writerow(row)

    print(f'  [OK] {fpath}  ({n} bars)')

def main():
    print('正在启动 Wind...')
    w.start()
    print('Wind 已连接\n')

    ok_count = 0
    skip_count = 0
    err_count = 0

    for code, name, date, iss_p in stocks:
        short = code_short(code)
        fpath = os.path.join(OUT_DIR, f'{short}.csv')

        # 已有 CSV 则跳过
        if os.path.exists(fpath):
            print(f'[SKIP] {short} {name} — 已有 CSV')
            skip_count += 1
            continue

        print(f'[下载] {short} {name}  上市日={date} ...', end='', flush=True)
        d = w.wsi(code, 'open,high,low,close,volume,amt',
                  f'{date} 09:00:00', f'{date} 15:00:00',
                  'periodstart=09:00:00;periodend=15:00:00')

        if d.ErrorCode != 0:
            print(f'  [ERROR] 错误码={d.ErrorCode}')
            err_count += 1
        else:
            save_csv(code, name, date, d)
            ok_count += 1

        time.sleep(0.5)   # 降低请求频率

    print(f'\n===== 完成 =====')
    print(f'成功: {ok_count}  跳过: {skip_count}  失败: {err_count}')
    print(f'输出目录: {OUT_DIR}')

    w.stop()

if __name__ == '__main__':
    main()
