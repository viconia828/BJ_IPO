"""
端到端估值验证 — 920012 创达新材(强) vs 920055 隆源股份(弱)
按照方案文档 Part 3 完整走方法一 + 方法二 + 综合定价
"""
import statistics

# ============================================================
# 已采集数据
# ============================================================

stocks = {
    '920012': {
        'name': '创达新材',
        'industry': '半导体封装材料',
        'ipo_price': 19.58,
        'ipo_pe': 14.72,         # 发行价/EPS_TTM
        'eps_ttm': 1.3301,
        'list_date': '2026-04-13',
        'float_shares': 1326.87,  # 万股 (13268667/10000)
        'ipo_amount': 1232.93,    # 万股 (发行量)
        'first_day_open': 43.06,
        'first_day_close': 51.67,
        'first_day_high': 54.93,
        'first_day_low': 42.00,
        'first_day_turn': 88.17,
        # 可比公司 PE_TTM
        'comp_pe_list': [85.25, 487.95, 55.52, 78.59, 89.11, 161.38],
        'comp_names': ['德邦科技','华海诚科','国瓷材料','联瑞新材','上海新阳','中芯国际'],
        # 走势评分(已有)
        'trend_score': 73.3,
        'trend_type': 'B-冲高维持',
    },
    '920055': {
        'name': '隆源股份',
        'industry': '汽车铝压铸件',
        'ipo_price': 24.70,
        'ipo_pe': 11.74,         # 发行价/EPS_TTM
        'eps_ttm': 2.1037,
        'list_date': '2026-03-31',
        'float_shares': 1530.00,  # 万股
        'ipo_amount': 1700.00,    # 万股
        'first_day_open': 36.00,
        'first_day_close': 37.80,
        'first_day_high': 43.11,
        'first_day_low': 36.00,
        'first_day_turn': 93.35,
        # 可比公司 PE_TTM (排除PE<=0的万邦德)
        'comp_pe_list': [44.24, 16.10, 318.54, 21.05, 342.74],
        'comp_names': ['旭升集团','爱柯迪','文灿股份','华阳集团','金固股份'],
        # 走势评分(已有)
        'trend_score': 50.8,
        'trend_type': 'C-冲高回落',
    },
}

# 19只样本VWAP涨幅(首日收盘涨幅用VWAP涨幅近似)
# 方案文档中的VWAP涨幅
all_samples_gain = {
    '920028': 125.7, '920012': 136.8, '920166': 142.3,
    '920011': 91.4,  '920181': 125.0, '920069': 130.4,
    '920159': 105.6, '920055': 54.2,  '920183': 177.5,
    '920078': 411.6, '920168': 82.1,  '920036': 102.7,
    '920050': 204.4, '920086': 367.9, '920076': 178.0,
    '920187': 52.2,  '920180': 203.0, '920119': 186.9,
    '920188': 141.7,
}

# 全部19只的走势综合分
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
# 策略参数 (方案文档默认值)
# ============================================================
bse_discount_factor = 0.75      # 北交所折价系数
float_size_threshold = 2000     # 万股
small_cap_premium = 0.10        # 小盘溢价
pe_low_threshold = 0.30         # PE ratio < 30%
pe_discount_boost = 0.10        # PE因子: +10%
pe_high_threshold = 0.60        # PE ratio > 60%
pe_premium_drag = -0.10         # PE因子: -10%
trend_strong_boost = 0.05       # 走势因子: 综合分>=70
trend_weak_discount = -0.05     # 走势因子: 综合分<40
industry_trend_weight = 0.60    # 行业走势权重
market_sentiment_weight = 0.40  # 市场情绪权重
weight_comparable = 0.50        # 方法一权重
weight_industry_momentum = 0.50 # 方法二权重
price_range_width = 0.15        # 区间宽度 ±15%


def calc_valuation(code, s):
    """完整估值计算"""
    print(f'\n{"="*70}')
    print(f'  {code} {s["name"]} — 端到端估值')
    print(f'{"="*70}')

    # ==================== 方法一 ====================
    print(f'\n--- 方法一：可比公司对比估值法 ---')
    eps = s['ipo_price'] / s['ipo_pe']
    comp_pe_median = statistics.median(s['comp_pe_list'])
    target_pe = comp_pe_median * bse_discount_factor
    target1 = eps * target_pe
    chg1 = (target1 / s['ipo_price'] - 1) * 100

    print(f'  新股 EPS = {s["ipo_price"]}/{s["ipo_pe"]:.2f} = {eps:.4f} 元')
    print(f'  可比公司: {", ".join(s["comp_names"])}')
    print(f'  可比公司 PE_TTM: {[f"{x:.1f}" for x in s["comp_pe_list"]]}')
    print(f'  可比 PE 中位数 = {comp_pe_median:.2f}')
    print(f'  目标 PE = {comp_pe_median:.2f} × {bse_discount_factor} = {target_pe:.2f}')
    print(f'  目标价 = {eps:.4f} × {target_pe:.2f} = {target1:.2f} 元')
    print(f'  预期涨幅 = {chg1:.1f}%')

    # ==================== 方法二 ====================
    print(f'\n--- 方法二：行业新股综合折溢价法 ---')

    # 基础涨幅 = 全市场近期新股首日涨幅中位数(19只)
    # (北交所新股多为小行业，样本不足时用全市场)
    all_gains = list(all_samples_gain.values())
    base_gain = statistics.median(all_gains)
    print(f'  近期19只新股首日VWAP涨幅中位数(全市场) = {base_gain:.1f}%')

    # 因子一: 流通盘
    if s['float_shares'] < float_size_threshold:
        float_factor = 1 + small_cap_premium
        float_note = f'< {float_size_threshold}万股 → +{small_cap_premium*100:.0f}%溢价'
    else:
        float_factor = 1.0
        float_note = f'>= {float_size_threshold}万股 → 无调节'
    print(f'  流通盘因子: {s["float_shares"]:.0f}万股 {float_note} → {float_factor:.2f}')

    # 因子二: PE因子
    # 行业PE用可比公司PE中位数
    industry_pe = comp_pe_median
    pe_ratio = s['ipo_pe'] / industry_pe
    if pe_ratio < pe_low_threshold:
        pe_factor = 1 + pe_discount_boost
        pe_note = f'PE比值 {pe_ratio:.3f} < {pe_low_threshold} → +{pe_discount_boost*100:.0f}%加成'
    elif pe_ratio > pe_high_threshold:
        pe_factor = 1 + pe_premium_drag
        pe_note = f'PE比值 {pe_ratio:.3f} > {pe_high_threshold} → {pe_premium_drag*100:.0f}%折扣'
    else:
        pe_factor = 1.0
        pe_note = f'PE比值 {pe_ratio:.3f} 在 [{pe_low_threshold},{pe_high_threshold}] → 无调节'
    print(f'  PE因子: 发行PE={s["ipo_pe"]:.2f}/行业PE={industry_pe:.2f}={pe_ratio:.3f} {pe_note} → {pe_factor:.2f}')

    # 因子三: 走势因子(双因子加权)
    # 行业走势因子: 取近期同行业标的 → 用全市场19只中位数分映射
    all_score_list = list(all_scores.values())
    score_median = statistics.median(all_score_list)
    print(f'  19只样本走势综合分中位数 = {score_median:.1f}')

    # 行业走势因子(用全市场中位数映射)
    if score_median >= 70:
        ind_trend = 1 + trend_strong_boost
    elif score_median < 40:
        ind_trend = 1 + trend_weak_discount
    else:
        ind_trend = 1.0
    print(f'  行业走势因子 = {ind_trend:.2f} (中位分={score_median:.1f}, {">=70→强" if score_median>=70 else "<40→弱" if score_median<40 else "40-70→中性"})')

    # 市场情绪因子(同样用全市场19只中位数)
    mkt_trend = ind_trend  # 同一组样本
    print(f'  市场情绪因子 = {mkt_trend:.2f}')

    trend_factor = industry_trend_weight * ind_trend + market_sentiment_weight * mkt_trend
    print(f'  走势因子 = {industry_trend_weight}×{ind_trend:.2f} + {market_sentiment_weight}×{mkt_trend:.2f} = {trend_factor:.4f}')

    # 综合调节
    adj_factor = float_factor * pe_factor * trend_factor
    expected_gain = base_gain * adj_factor / 100
    target2 = s['ipo_price'] * (1 + expected_gain)
    chg2 = (target2 / s['ipo_price'] - 1) * 100

    print(f'  调节因子 = {float_factor:.2f} × {pe_factor:.2f} × {trend_factor:.4f} = {adj_factor:.4f}')
    print(f'  预期涨幅 = {base_gain:.1f}% × {adj_factor:.4f} = {chg2:.1f}%')
    print(f'  目标价 = {s["ipo_price"]:.2f} × (1+{expected_gain:.4f}) = {target2:.2f} 元')

    # ==================== 综合定价 ====================
    print(f'\n--- 综合定价 ---')
    target_final = target1 * weight_comparable + target2 * weight_industry_momentum
    chg_final = (target_final / s['ipo_price'] - 1) * 100
    range_low = target_final * (1 - price_range_width)
    range_high = target_final * (1 + price_range_width)
    chg_low = (range_low / s['ipo_price'] - 1) * 100
    chg_high = (range_high / s['ipo_price'] - 1) * 100

    print(f'  方法一目标价 = {target1:.2f} 元 (涨幅 {chg1:.1f}%)')
    print(f'  方法二目标价 = {target2:.2f} 元 (涨幅 {chg2:.1f}%)')
    print(f'  综合目标价 = {target1:.2f}×{weight_comparable} + {target2:.2f}×{weight_industry_momentum} = {target_final:.2f} 元')
    print(f'  综合涨幅 = {chg_final:.1f}%')
    print(f'  估值区间 = {range_low:.2f} ~ {range_high:.2f} 元 (涨幅 {chg_low:.1f}% ~ {chg_high:.1f}%)')

    # ==================== 与实际对比 ====================
    print(f'\n--- 与实际首日收盘对比 ---')
    actual_close = s['first_day_close']
    actual_chg = (actual_close / s['ipo_price'] - 1) * 100
    deviation = (target_final - actual_close) / actual_close * 100
    in_range = range_low <= actual_close <= range_high

    print(f'  实际首日收盘 = {actual_close:.2f} 元 (涨幅 {actual_chg:.1f}%)')
    print(f'  综合目标价 vs 实际: {target_final:.2f} vs {actual_close:.2f} (偏差 {deviation:+.1f}%)')
    print(f'  是否落入估值区间 [{range_low:.2f}, {range_high:.2f}]: {"是 ✓" if in_range else "否 ✗"}')
    print(f'  走势评分 = {s["trend_score"]} ({s["trend_type"]})')

    return {
        'target1': target1, 'chg1': chg1,
        'target2': target2, 'chg2': chg2,
        'target_final': target_final, 'chg_final': chg_final,
        'range_low': range_low, 'range_high': range_high,
        'chg_low': chg_low, 'chg_high': chg_high,
        'actual_close': actual_close, 'actual_chg': actual_chg,
        'deviation': deviation, 'in_range': in_range,
        'adj_factor': adj_factor, 'float_factor': float_factor,
        'pe_factor': pe_factor, 'trend_factor': trend_factor,
        'comp_pe_median': comp_pe_median,
        'base_gain': base_gain,
    }


# ============================================================
# 执行
# ============================================================
results = {}
for code in ['920012', '920055']:
    results[code] = calc_valuation(code, stocks[code])

# 总结对比
print(f'\n\n{"="*70}')
print(f'  验证总结')
print(f'{"="*70}')
print(f'\n{"项目":<24} {"920012 创达新材":>16} {"920055 隆源股份":>16}')
print(f'{"-"*56}')
r1, r2 = results['920012'], results['920055']
s1, s2 = stocks['920012'], stocks['920055']
rows = [
    ('行业', s1['industry'], s2['industry']),
    ('走势类型', s1['trend_type'], s2['trend_type']),
    ('走势评分', f'{s1["trend_score"]:.1f}', f'{s2["trend_score"]:.1f}'),
    ('发行价', f'{s1["ipo_price"]:.2f}', f'{s2["ipo_price"]:.2f}'),
    ('发行PE', f'{s1["ipo_pe"]:.2f}', f'{s2["ipo_pe"]:.2f}'),
    ('可比PE中位数', f'{r1["comp_pe_median"]:.2f}', f'{r2["comp_pe_median"]:.2f}'),
    ('方法一目标价', f'{r1["target1"]:.2f}', f'{r2["target1"]:.2f}'),
    ('方法二目标价', f'{r1["target2"]:.2f}', f'{r2["target2"]:.2f}'),
    ('综合目标价', f'{r1["target_final"]:.2f}', f'{r2["target_final"]:.2f}'),
    ('估值区间', f'{r1["range_low"]:.2f}~{r1["range_high"]:.2f}', f'{r2["range_low"]:.2f}~{r2["range_high"]:.2f}'),
    ('实际首日收盘', f'{s1["first_day_close"]:.2f}', f'{s2["first_day_close"]:.2f}'),
    ('实际涨幅', f'{r1["actual_chg"]:.1f}%', f'{r2["actual_chg"]:.1f}%'),
    ('偏差', f'{r1["deviation"]:+.1f}%', f'{r2["deviation"]:+.1f}%'),
    ('落入区间?', '是 ✓' if r1['in_range'] else '否 ✗', '是 ✓' if r2['in_range'] else '否 ✗'),
]
for label, v1, v2 in rows:
    print(f'{label:<24} {v1:>16} {v2:>16}')
