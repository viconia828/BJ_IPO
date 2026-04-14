"""获取920012/920055的行业和可比公司PE"""
import sys
sys.path.insert(0, r'C:\Wind\Wind.NET.Client\WindNET\x64')
from WindPy import w
w.start()

# 1. 尝试更多行业字段
for code in ['920012.BJ','920055.BJ']:
    print(f'===== {code} =====')
    for f in ['industry_csrc12','industry_csrc12_n','exch_eng',
              'ipo_industry','sec_type','industry_citic_n',
              'industry_sw_n','sec_status']:
        d = w.wss(code, f)
        if d.ErrorCode == 0 and d.Data[0][0] is not None:
            print(f'  {f}: {d.Data[0][0]}')
    print()

# 2. 920012 创达新材 - 化工新材料(依据上市文件)
# 查化工行业几家可比公司的PE_TTM
# 先试几个北交所化工股
chem_codes = '832149.BJ,836239.BJ,836263.BJ,833266.BJ,835368.BJ'
d = w.wss(chem_codes, 'sec_name,pe_ttm,close,mkt_cap_ard')
if d.ErrorCode == 0:
    print('化工相关:')
    for i, c in enumerate(chem_codes.split(',')):
        nm = d.Data[0][i]
        pe = d.Data[1][i]
        cl = d.Data[2][i]
        mc = d.Data[3][i]
        if nm:
            print(f'  {c} {nm}: PE_TTM={pe}, 收盘={cl}, 市值={mc}')

# 3. 920055 隆源股份 - 金属粉末/新材料(铜基合金粉末)
# 查新材料/粉末冶金相关
metal_codes = '835305.BJ,833751.BJ,838924.BJ,834021.BJ,835174.BJ'
d = w.wss(metal_codes, 'sec_name,pe_ttm,close,mkt_cap_ard')
if d.ErrorCode == 0:
    print('新材料/金属粉末相关:')
    for i, c in enumerate(metal_codes.split(',')):
        nm = d.Data[0][i]
        pe = d.Data[1][i]
        cl = d.Data[2][i]
        mc = d.Data[3][i]
        if nm:
            print(f'  {c} {nm}: PE_TTM={pe}, 收盘={cl}, 市值={mc}')

w.stop()
