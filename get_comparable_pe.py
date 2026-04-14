"""获取可比公司PE中位数"""
import sys, statistics
sys.path.insert(0, r'C:\Wind\Wind.NET.Client\WindNET\x64')
from WindPy import w
w.start()

# ======= 920012 创达新材 - 半导体封装材料 =======
# 可比公司(参考华源证券研报): 德邦科技、华海诚科、国瓷材料、联瑞新材等
# 以及A股电子封装材料/半导体材料公司
semi_codes = [
    '688035.SH',  # 德邦科技 - 电子封装材料
    '688535.SH',  # 华海诚科 - 环氧塑封料
    '300285.SZ',  # 国瓷材料 - 电子陶瓷材料
    '688300.SH',  # 联瑞新材 - 电子级硅微粉
    '300236.SZ',  # 上海新阳 - 半导体材料
    '688981.SH',  # 中芯集成 - 半导体
]

print('===== 920012 创达新材 可比公司 (半导体封装材料) =====')
pe_list_semi = []
for c in semi_codes:
    d = w.wss(c, 'sec_name,pe_ttm,close')
    if d.ErrorCode == 0:
        nm, pe, cl = d.Data[0][0], d.Data[1][0], d.Data[2][0]
        tag = ''
        if pe and pe > 0:
            pe_list_semi.append(pe)
        else:
            tag = ' (PE<=0, 排除)'
        print(f'  {c} {nm}: PE_TTM={pe}, 收盘={cl}{tag}')
if pe_list_semi:
    med = statistics.median(pe_list_semi)
    print(f'  --- PE_TTM中位数: {med:.2f} ---')

# ======= 920055 隆源股份 - 汽车零部件/铝压铸件 =======
# 可比公司: 旭升集团、爱柯迪、文灿股份、广东鸿图等铝压铸件
auto_codes = [
    '603305.SH',  # 旭升集团 - 铝合金精密压铸件
    '600876.SH',  # 洛阳玻璃 - skip
    '600699.SH',  # 均胜电子 - 汽车零部件
    '603876.SH',  # 鼎胜新材
    '002906.SZ',  # 华阳集团 - 汽车电子
    '603335.SH',  # 迪生力
]
# 换更精准的铝压铸件
auto_codes = [
    '603305.SH',  # 旭升集团 - 铝合金精密压铸件
    '600933.SH',  # 爱柯迪 - 铝合金压铸件
    '603348.SH',  # 文灿股份 - 铝合金精密压铸件
    '002082.SZ',  # 万丰奥威 - 铝合金车轮/压铸件
    '603329.SH',  # 上海雅仕
]
auto_codes = [
    '603305.SH',  # 旭升集团
    '600933.SH',  # 爱柯迪
    '603348.SH',  # 文灿股份
    '002082.SZ',  # 万丰奥威
    '002906.SZ',  # 华阳集团
    '002488.SZ',  # 金固股份
]

print()
print('===== 920055 隆源股份 可比公司 (汽车铝压铸件) =====')
pe_list_auto = []
for c in auto_codes:
    d = w.wss(c, 'sec_name,pe_ttm,close')
    if d.ErrorCode == 0:
        nm, pe, cl = d.Data[0][0], d.Data[1][0], d.Data[2][0]
        tag = ''
        if pe and pe > 0:
            pe_list_auto.append(pe)
        else:
            tag = ' (PE<=0, 排除)'
        print(f'  {c} {nm}: PE_TTM={pe}, 收盘={cl}{tag}')
if pe_list_auto:
    med = statistics.median(pe_list_auto)
    print(f'  --- PE_TTM中位数: {med:.2f} ---')

w.stop()
