"""查询19只样本股的申万行业分类"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from WindPy import w
w.start()
print('Wind started', flush=True)

codes = ['920028','920012','920166','920011','920181','920069',
         '920159','920055','920183','920078','920168','920036',
         '920050','920086','920076','920187','920180','920119','920188']

names = ['新恒泰','创达新材','海圣医疗','晨光电机','赛英电子','普昂医疗',
         '农大科技','隆源股份','海菲曼','族兴新材','通宝光电','觅睿科技',
         '爱舍伦','科马材料','国亮新材','通领科技','爱得科技','美德乐','悦龙科技']

wind_codes = [c + '.BJ' for c in codes]
codes_str = ','.join(wind_codes)

# SW level 2
r = w.wss(codes_str, 'industry_sw', 'industryType=2')
print(f'ErrorCode: {r.ErrorCode}', flush=True)

if r.ErrorCode == 0:
    print('\n=== 申万二级行业 ===', flush=True)
    for c, n, ind in zip(codes, names, r.Data[0]):
        print(f'{c} {n}: {ind}', flush=True)

# SW level 1
r2 = w.wss(codes_str, 'industry_sw', 'industryType=1')
if r2.ErrorCode == 0:
    print('\n=== 申万一级行业 ===', flush=True)
    for c, n, ind in zip(codes, names, r2.Data[0]):
        print(f'{c} {n}: {ind}', flush=True)

w.close()
print('\nDone', flush=True)
