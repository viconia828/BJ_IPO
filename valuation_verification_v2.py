"""
端到端估值验证 v2 — 分行业因子修正版
按开源证券5大行业分组，使用行业子集基础涨幅+双因子走势
920012 创达新材(强) vs 920055 隆源股份(弱)
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import statistics

# ============================================================
# 19只样本 — 行业分组（映射至开源证券5大行业）
# ============================================================
# 行业映射依据：Wind GICS L2 + CSRC L2 + 开源证券附录C二级行业列表

industry_map = {
    # --- 高端装备: 机械设备, 汽车零部件, 金属制品, 国防军工, 电力设备, 仪器仪表, 电池 ---
    '920011': '高端装备',  # 晨光电机   GICS:资本货物(电机) → 机械设备
    '920055': '高端装备',  # 隆源股份   GICS:汽车与汽车零部件(铝压铸件) → 汽车零部件
    '920168': '高端装备',  # 通宝光电   GICS:汽车与汽车零部件(汽车照明) → 汽车零部件
    '920086': '高端装备',  # 科马材料   GICS:汽车与汽车零部件(精密模具) → 机械设备
    '920187': '高端装备',  # 通领科技   GICS:汽车与汽车零部件(电气安全) → 机械设备
    '920119': '高端装备',  # 美德乐     GICS:资本货物(检测设备) → 仪器仪表
    '920188': '高端装备',  # 悦龙科技   GICS:资本货物(液压件) → 机械设备
    # --- 信息技术: 电子, 技术服务, 通信, 半导体制造 ---
    '920012': '信息技术',  # 创达新材   GICS:技术硬件与设备(半导体封装材料) → 半导体/电子
    '920181': '信息技术',  # 赛英电子   GICS:半导体与半导体生产设备 → 电子/半导体
    # --- 化工新材: 化学制品, 金属新材料, 非金属材料, 橡胶和塑料制品, 电池材料, 纺织 ---
    '920028': '化工新材',  # 新恒泰     GICS:材料(改性塑料) → 橡胶和塑料制品业
    '920078': '化工新材',  # 族兴新材   GICS:材料/CSRC:石油化学塑胶塑料(铝合金回收) → 金属新材料
    '920076': '化工新材',  # 国亮新材   GICS:材料(铝合金) → 金属新材料
    # --- 消费服务: 食品饮料, 农业节水, 消费电子等 ---
    '920183': '消费服务',  # 海菲曼     GICS:耐用消费品(高端耳机) → 消费电子
    '920159': '消费服务',  # 农大科技   GICS:材料(节水灌溉) → 农业节水
    '920036': '消费服务',  # 觅睿科技   GICS:耐用消费品(智能安防摄像头) → 消费电子
    # --- 医药生物: 中药, 医疗器械, 生物制品 ---
    '920166': '医药生物',  # 海圣医疗   GICS:医疗保健设备与服务 → 医疗器械
    '920069': '医药生物',  # 普昂医疗   GICS:医疗保健设备与服务 → 医疗器械
    '920050': '医药生物',  # 爱舍伦     GICS:医疗保健设备与服务 → 医疗器械
    '920180': '医药生物',  # 爱得科技   GICS:医疗保健/CSRC:机械设备仪表 → 医疗器械
}

# ============================================================
# 19只样本 VWAP涨幅 + 走势综合分
# ============================================================
all_samples_gain = {
    '920028': 125.7, '920012': 136.8, '920166': 142.3,
    '920011': 91.4,  '920181': 125.0, '920069': 130.4,
    '920159': 105.6, '920055': 54.2,  '920183': 177.5,
    '920078': 411.6, '920168': 82.1,  '920036': 102.7,
    '920050': 204.4, '920086': 367.9, '920076': 178.0,
    '920187': 52.2,  '920180': 203.0, '920119': 186.9,
    '920188': 141.7,
}

all_scores = {
    '920028': 86.3, '920012': 73.3, '920166': 64.5,
    '920011': 58.5, '920181': 54.0, '920069': 51.5,
    '920159': 51.5, '920055': 50.8, '920183': 43.0,
    '920078': 42.5, '920168': 36.0, '920036': 35.5,
    '920050': 35.0, '920086': 34.2, '920076': 34.0,
    '920187': 33.0, '920180': 32.0, '920119': 27.8,
    '920188': 22.0,
}

# ============================================================
# 按行业汇总
# ============================================================
industry_groups = {}
for code, ind in industry_map.items():
    if ind not in industry_groups:
        industry_groups[ind] = []
    industry_groups[ind].append(code)

print('='*70)
print('  19只样本按开源证券5大行业分组')
print('='*70)
for ind in ['高端装备','信息技术','化工新材','消费服务','医药生物']:
    codes = industry_groups[ind]
    gains = sorted([all_samples_gain[c] for c in codes])
    scores = sorted([all_scores[c] for c in codes])
    gain_med = statistics.median(gains)
    score_med = statistics.median(scores)
    print(f'\n【{ind}】({len(codes)}只)')
    for c in codes:
        print(f'  {c} gain={all_samples_gain[c]:.1f}% score={all_scores[c]:.1f}')
    print(f'  涨幅中位数={gain_med:.1f}%  走势分中位数={score_med:.1f}')

# 全市场
all_gains = list(all_samples_gain.values())
all_score_list = list(all_scores.values())
market_gain_median = statistics.median(all_gains)
market_score_median = statistics.median(all_score_list)
print(f'\n【全市场】({len(all_gains)}只)')
print(f'  涨幅中位数={market_gain_median:.1f}%  走势分中位数={market_score_median:.1f}')

# ============================================================
# 标的数据
# ============================================================
stocks = {
    '920012': {
        'name': '创达新材',
        'industry': '半导体封装材料',
        'industry_group': '信息技术',
        'ipo_price': 19.58,
        'ipo_pe': 14.72,
        'eps_ttm': 1.3301,
        'float_shares': 1326.87,
        'first_day_close': 51.67,
        'comp_pe_list': [85.25, 487.95, 55.52, 78.59, 89.11, 161.38],
        'comp_names': ['德邦科技','华海诚科','国瓷材料','联瑞新材','上海新阳','中芯国际'],
        'trend_score': 73.3,
        'trend_type': 'B-冲高维持',
    },
    '920055': {
        'name': '隆源股份',
        'industry': '汽车铝压铸件',
        'industry_group': '高端装备',
        'ipo_price': 24.70,
        'ipo_pe': 11.74,
        'eps_ttm': 2.1037,
        'float_shares': 1530.00,
        'first_day_close': 37.80,
        'comp_pe_list': [44.24, 16.10, 318.54, 21.05, 342.74],
        'comp_names': ['旭升集团','爱柯迪','文灿股份','华阳集团','金固股份'],
        'trend_score': 50.8,
        'trend_type': 'C-冲高回落',
    },
}

# ============================================================
# 策略参数
# ============================================================
bse_discount_factor = 0.75
float_size_threshold = 2000     # 万股
small_cap_premium = 0.10
pe_low_threshold = 0.30
pe_discount_boost = 0.10
pe_high_threshold = 0.60
pe_premium_drag = -0.10
trend_strong_boost = 0.05       # 走势因子: 综合分>=70
trend_weak_discount = -0.05     # 走势因子: 综合分<40
industry_trend_weight = 0.60
market_sentiment_weight = 0.40
weight_comparable = 0.50
weight_industry_momentum = 0.50
price_range_width = 0.15


def score_to_factor(score_median):
    """走势评分中位数 → 走势因子"""
    if score_median >= 70:
        return 1 + trend_strong_boost, '>=70→强势+5%'
    elif score_median < 40:
        return 1 + trend_weak_discount, '<40→弱势-5%'
    else:
        return 1.0, '40~70→中性'


def calc_valuation(code, s):
    """完整估值计算（分行业版）"""
    ind_group = s['industry_group']
    ind_codes = industry_groups[ind_group]

    # 行业子集数据
    ind_gains = [all_samples_gain[c] for c in ind_codes]
    ind_scores = [all_scores[c] for c in ind_codes]
    ind_gain_median = statistics.median(ind_gains)
    ind_score_median = statistics.median(ind_scores)

    print(f'\n\n{"="*70}')
    print(f'  {code} {s["name"]} — 端到端估值（分行业版）')
    print(f'  所属行业组: {ind_group}（{len(ind_codes)}只同行业样本）')
    print(f'{"="*70}')

    # ==================== 方法一 ====================
    print(f'\n--- 方法一：可比公司对比估值法（不变）---')
    eps = s['ipo_price'] / s['ipo_pe']
    comp_pe_median = statistics.median(s['comp_pe_list'])
    target_pe = comp_pe_median * bse_discount_factor
    target1 = eps * target_pe
    chg1 = (target1 / s['ipo_price'] - 1) * 100

    print(f'  EPS = {s["ipo_price"]}/{s["ipo_pe"]:.2f} = {eps:.4f} 元')
    print(f'  可比公司 PE 中位数 = {comp_pe_median:.2f}')
    print(f'  目标 PE = {comp_pe_median:.2f} × {bse_discount_factor} = {target_pe:.2f}')
    print(f'  目标价 = {eps:.4f} × {target_pe:.2f} = {target1:.2f} 元（+{chg1:.1f}%）')

    # ==================== 方法二（分行业修正版）====================
    print(f'\n--- 方法二：行业新股综合折溢价法（分行业修正版）---')

    # 基础涨幅 = 同行业子集中位数
    print(f'  ▶ 基础涨幅: 同行业({ind_group})VWAP涨幅中位数')
    print(f'    同行业标的: {", ".join(ind_codes)}')
    print(f'    涨幅: {[f"{all_samples_gain[c]:.1f}%" for c in ind_codes]}')
    print(f'    中位数 = {ind_gain_median:.1f}%')
    base_gain = ind_gain_median

    # 因子一: 流通盘
    if s['float_shares'] < float_size_threshold:
        float_factor = 1 + small_cap_premium
        float_note = f'{s["float_shares"]:.0f}万 < {float_size_threshold}万 → +{small_cap_premium*100:.0f}%'
    else:
        float_factor = 1.0
        float_note = f'{s["float_shares"]:.0f}万 >= {float_size_threshold}万 → 无调节'
    print(f'  ▶ 流通盘因子: {float_note} → {float_factor:.2f}')

    # 因子二: PE因子
    industry_pe = comp_pe_median
    pe_ratio = s['ipo_pe'] / industry_pe
    if pe_ratio < pe_low_threshold:
        pe_factor = 1 + pe_discount_boost
        pe_note = f'{s["ipo_pe"]:.2f}/{industry_pe:.2f}={pe_ratio:.3f} < {pe_low_threshold} → +{pe_discount_boost*100:.0f}%'
    elif pe_ratio > pe_high_threshold:
        pe_factor = 1 + pe_premium_drag
        pe_note = f'{pe_ratio:.3f} > {pe_high_threshold} → {pe_premium_drag*100:.0f}%'
    else:
        pe_factor = 1.0
        pe_note = f'{pe_ratio:.3f} 在 [{pe_low_threshold},{pe_high_threshold}] → 无调节'
    print(f'  ▶ PE因子: {pe_note} → {pe_factor:.2f}')

    # 因子三: 走势因子(双因子加权)
    print(f'  ▶ 走势因子（行业60% + 市场40%）:')

    # 行业走势因子
    ind_tf, ind_label = score_to_factor(ind_score_median)
    print(f'    行业走势: {ind_group}走势分中位数={ind_score_median:.1f} → {ind_label} → {ind_tf:.2f}')

    # 市场情绪因子
    mkt_tf, mkt_label = score_to_factor(market_score_median)
    print(f'    市场情绪: 全市场走势分中位数={market_score_median:.1f} → {mkt_label} → {mkt_tf:.2f}')

    trend_factor = industry_trend_weight * ind_tf + market_sentiment_weight * mkt_tf
    print(f'    走势因子 = {industry_trend_weight}×{ind_tf:.2f} + {market_sentiment_weight}×{mkt_tf:.2f} = {trend_factor:.4f}')

    # 综合调节
    adj_factor = float_factor * pe_factor * trend_factor
    expected_gain = base_gain * adj_factor / 100
    target2 = s['ipo_price'] * (1 + expected_gain)
    chg2 = (target2 / s['ipo_price'] - 1) * 100

    print(f'  ▶ 综合调节 = {float_factor:.2f} × {pe_factor:.2f} × {trend_factor:.4f} = {adj_factor:.4f}')
    print(f'  ▶ 预期涨幅 = {base_gain:.1f}% × {adj_factor:.4f} = {chg2:.1f}%')
    print(f'  ▶ 目标价 = {s["ipo_price"]:.2f} × (1+{expected_gain:.4f}) = {target2:.2f} 元')

    # ==================== 综合定价 ====================
    print(f'\n--- 综合定价 ---')
    target_final = target1 * weight_comparable + target2 * weight_industry_momentum
    chg_final = (target_final / s['ipo_price'] - 1) * 100
    range_low = target_final * (1 - price_range_width)
    range_high = target_final * (1 + price_range_width)

    print(f'  方法一 = {target1:.2f} 元 (+{chg1:.1f}%)')
    print(f'  方法二 = {target2:.2f} 元 (+{chg2:.1f}%)')
    print(f'  综合 = {target1:.2f}×{weight_comparable} + {target2:.2f}×{weight_industry_momentum} = {target_final:.2f} 元 (+{chg_final:.1f}%)')
    print(f'  区间 = {range_low:.2f} ~ {range_high:.2f} 元')

    # ==================== 与实际对比 ====================
    print(f'\n--- 与实际首日收盘对比 ---')
    actual = s['first_day_close']
    actual_chg = (actual / s['ipo_price'] - 1) * 100
    dev_m1 = (target1 - actual) / actual * 100
    dev_m2 = (target2 - actual) / actual * 100
    dev_final = (target_final - actual) / actual * 100
    in_range = range_low <= actual <= range_high

    print(f'  实际首日收盘 = {actual:.2f} 元 (+{actual_chg:.1f}%)')
    print(f'  方法一偏差: {target1:.2f} vs {actual:.2f} → {dev_m1:+.1f}%')
    print(f'  方法二偏差: {target2:.2f} vs {actual:.2f} → {dev_m2:+.1f}%')
    print(f'  综合偏差:   {target_final:.2f} vs {actual:.2f} → {dev_final:+.1f}%')
    print(f'  落入区间 [{range_low:.2f}, {range_high:.2f}]: {"是 ✓" if in_range else "否 ✗"}')

    return {
        'target1': target1, 'chg1': chg1,
        'target2': target2, 'chg2': chg2,
        'target_final': target_final, 'chg_final': chg_final,
        'range_low': range_low, 'range_high': range_high,
        'actual': actual, 'actual_chg': actual_chg,
        'dev_m1': dev_m1, 'dev_m2': dev_m2, 'dev_final': dev_final,
        'in_range': in_range,
        'base_gain': base_gain, 'ind_gain_median': ind_gain_median,
        'ind_score_median': ind_score_median,
        'float_factor': float_factor, 'pe_factor': pe_factor,
        'trend_factor': trend_factor, 'adj_factor': adj_factor,
        'comp_pe_median': comp_pe_median,
        'ind_tf': ind_tf, 'mkt_tf': mkt_tf,
    }


# ============================================================
# 执行
# ============================================================
results = {}
for code in ['920012', '920055']:
    results[code] = calc_valuation(code, stocks[code])

# ============================================================
# v1 vs v2 对比
# ============================================================
print(f'\n\n{"="*70}')
print(f'  v1(全市场) vs v2(分行业) 对比')
print(f'{"="*70}')

# v1 数据（旧计算结果）
v1 = {
    '920012': {'target2': 51.99, 'target_final': 69.48, 'dev_final': 34.5},
    '920055': {'target2': 65.59, 'target_final': 67.70, 'dev_final': 79.1},
}

print(f'\n{"指标":<28} {"920012创达新材":>16} {"920055隆源股份":>16}')
print('-' * 60)
r1, r2 = results['920012'], results['920055']
rows = [
    ('所属行业组', stocks['920012']['industry_group'], stocks['920055']['industry_group']),
    ('行业样本数', str(len(industry_groups['信息技术'])), str(len(industry_groups['高端装备']))),
    ('', '', ''),
    ('[v1] 基础涨幅(全市场)', '136.8%', '136.8%'),
    ('[v2] 基础涨幅(分行业)', f'{r1["ind_gain_median"]:.1f}%', f'{r2["ind_gain_median"]:.1f}%'),
    ('', '', ''),
    ('[v1] 行业走势因子', '1.00', '1.00'),
    ('[v2] 行业走势因子', f'{r1["ind_tf"]:.2f}', f'{r2["ind_tf"]:.2f}'),
    ('[v2] 市场情绪因子', f'{r1["mkt_tf"]:.2f}', f'{r2["mkt_tf"]:.2f}'),
    ('[v2] 走势因子(加权)', f'{r1["trend_factor"]:.4f}', f'{r2["trend_factor"]:.4f}'),
    ('', '', ''),
    ('[v1] 方法二目标价', f'{v1["920012"]["target2"]:.2f}', f'{v1["920055"]["target2"]:.2f}'),
    ('[v2] 方法二目标价', f'{r1["target2"]:.2f}', f'{r2["target2"]:.2f}'),
    ('', '', ''),
    ('[v1] 综合目标价', f'{v1["920012"]["target_final"]:.2f}', f'{v1["920055"]["target_final"]:.2f}'),
    ('[v2] 综合目标价', f'{r1["target_final"]:.2f}', f'{r2["target_final"]:.2f}'),
    ('实际首日收盘', f'{r1["actual"]:.2f}', f'{r2["actual"]:.2f}'),
    ('', '', ''),
    ('[v1] 综合偏差', f'+{v1["920012"]["dev_final"]:.1f}%', f'+{v1["920055"]["dev_final"]:.1f}%'),
    ('[v2] 综合偏差', f'{r1["dev_final"]:+.1f}%', f'{r2["dev_final"]:+.1f}%'),
    ('[v1] 方法二偏差', '+0.6%', '+73.5%'),
    ('[v2] 方法二偏差', f'{r1["dev_m2"]:+.1f}%', f'{r2["dev_m2"]:+.1f}%'),
]
for label, v1_, v2_ in rows:
    print(f'{label:<28} {v1_:>16} {v2_:>16}')
