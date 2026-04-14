"""查询19只样本股的证监会行业和GICS行业"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from WindPy import w
w.start()

codes = ['920028','920012','920166','920011','920181','920069',
         '920159','920055','920183','920078','920168','920036',
         '920050','920086','920076','920187','920180','920119','920188']

names = ['新恒泰','创达新材','海圣医疗','晨光电机','赛英电子','普昂医疗',
         '农大科技','隆源股份','海菲曼','族兴新材','通宝光电','觅睿科技',
         '爱舍伦','科马材料','国亮新材','通领科技','爱得科技','美德乐','悦龙科技']

wind_codes = [c + '.BJ' for c in codes]
codes_str = ','.join(wind_codes)

# 证监会行业 L1 + L2
r1 = w.wss(codes_str, 'industry_csrc', 'industryType=1')
r2 = w.wss(codes_str, 'industry_csrc', 'industryType=2')

# GICS L1 + L2
r3 = w.wss(codes_str, 'industry_gics', 'industryType=1')
r4 = w.wss(codes_str, 'industry_gics', 'industryType=2')

print(f'{"代码":<10}{"简称":<10}{"CSRC_L1":<12}{"CSRC_L2":<25}{"GICS_L1":<12}{"GICS_L2":<20}', flush=True)
print('-' * 89, flush=True)
for i, (c, n) in enumerate(zip(codes, names)):
    csrc1 = r1.Data[0][i] if r1.Data[0][i] else '-'
    csrc2 = r2.Data[0][i] if r2.Data[0][i] else '-'
    gics1 = r3.Data[0][i] if r3.Data[0][i] else '-'
    gics2 = r4.Data[0][i] if r4.Data[0][i] else '-'
    print(f'{c:<10}{n:<10}{csrc1:<12}{csrc2:<25}{gics1:<12}{gics2:<20}', flush=True)

w.close()
print('\nDone', flush=True)
