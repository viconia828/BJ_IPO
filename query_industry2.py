"""查询920012行业分类 - 尝试多种Wind字段"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from WindPy import w
w.start()

code = '920012.BJ'

# 验证代码可用
r0 = w.wss(code, 'sec_name')
print(f'Name: {r0.Data[0][0]}', flush=True)

# 证监会行业
r1 = w.wss(code, 'industry_csrc', 'industryType=2')
print(f'CSRC L2: {r1.Data[0][0]}', flush=True)

r1b = w.wss(code, 'industry_csrc', 'industryType=1')
print(f'CSRC L1: {r1b.Data[0][0]}', flush=True)

# Wind行业
r2 = w.wss(code, 'industry2', 'industryType=1')
print(f'Wind L1: {r2.Data[0][0]}', flush=True)

r2b = w.wss(code, 'industry2', 'industryType=2')
print(f'Wind L2: {r2b.Data[0][0]}', flush=True)

# GICS
r3 = w.wss(code, 'industry_gics', 'industryType=1')
print(f'GICS L1: {r3.Data[0][0]}', flush=True)

# 申万 2021
r4 = w.wss(code, 'industry_sw', 'industryType=1;industryStandard=2021')
print(f'SW2021 L1: {r4.Data[0][0]}', flush=True)

# 申万 不带standard参数
r5 = w.wss(code, 'industry_sw')
print(f'SW default: {r5.Data[0][0]}', flush=True)

w.close()
print('Done', flush=True)
