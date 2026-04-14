import sys
sys.path.insert(0, r'C:\Wind\Wind.NET.Client\WindNET\x64')
from WindPy import w
w.start()

# try different field names
tests = [
    'sec_name,ipo_date,ipo_price',
    'name,ipo_date,ipo_price',
    'sec_name,ipo_date,ipo_iniprice',
    'windcode,sec_name',
]
for fields in tests:
    d = w.wss('920012.BJ', fields)
    print(f'{fields}: err={d.ErrorCode} data={d.Data}')

w.stop()
