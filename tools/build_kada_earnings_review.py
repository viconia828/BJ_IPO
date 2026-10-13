"""Audit current earnings, forecast delivery and symmetric cycle sensitivities."""
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code'), str(ROOT/'tools')]
import config_loader
from fundamental_anchor import build_reviewed_anchor, ttm
from build_fundamental_cases import FOLDER, read, write, formatted


def main():
    case = read(FOLDER/'cases/920025.json')
    inputs = read(FOLDER/'inputs/920025.json')
    params = config_loader.load_params()
    cutoff = case['valuation_cutoff']
    options = {'reconstruction': True}
    current = build_reviewed_anchor(case, inputs['comparable_data'], cutoff, params, **options)
    cycle = build_reviewed_anchor(case, inputs['comparable_data'], cutoff, params, stage='cycle_reference', **options)
    bridges = []
    for metric, forecast in [('net_profit_parent', case['forecast']['parent_profit']),
                             ('adjusted_net_profit_parent', case['forecast']['adjusted_profit'])]:
        annual = ttm(case['issuer'], '2025-12-31', metric, cutoff, **options)['value']
        records = {r['period']: r for r in case['issuer']['records']}
        h1 = records['2026-06-30']['values'][metric]['value']
        last_h1 = records['2025-06-30']['values'][metric]['value']
        need, last_h2 = forecast - h1, annual - last_h1
        bridges.append({'metric': metric, 'fy2025': annual, 'h1_2025': last_h1, 'h1_2026': h1,
                        'h1_growth_pct': (h1/last_h1-1)*100, 'ttm': annual+h1-last_h1,
                        'fy2026_reviewed_forecast': forecast, 'h2_2026_required': need,
                        'h2_2025': last_h2, 'h2_required_growth_pct': (need/last_h2-1)*100,
                        'forward_price_available': False, 'forward_reason': '没有相同2026预测口径、同一事前日期的同行倍数'})
    both = set(current.get('peer_codes', [])) & set(cycle.get('peer_codes', []))
    matched = deepcopy(case)
    matched['peers'] = [p for p in matched['peers'] if p['code'] in both]
    common = {stage: build_reviewed_anchor(matched, inputs['comparable_data'], cutoff, params, stage=stage, **options)
              for stage in ['matched','cycle_reference']}
    loo = {}
    for code in current.get('peer_codes', []):
        item = deepcopy(case); item['peers'] = [p for p in item['peers'] if p['code'] != code]
        r = build_reviewed_anchor(item, inputs['comparable_data'], cutoff, params, **options)
        loo[code] = {'available': r['available'], 'center': r.get('center'), 'reason': r.get('reason')}
    out = {'code':'920025', 'as_of_exclusive':cutoff, 'historical_pit_eligible':False,
           'pricing_mode':'shadow', 'earnings_bridge':bridges, 'current_ttm':current,
           'historical_margin_scenario':cycle, 'cycle_common_positive_peer_codes':sorted(both),
           'cycle_common_pool_comparison':common, 'leave_one_out':loo,
           'normalization_approved':False,
           'reason':'周期历史利润率造成有效同行集合变化，共同正盈利池仅1家；不能将压力场景当可持续估值中枢'}
    write(FOLDER/'kada_cycle.json',out)
    lines=['# 凯达重工：盈利桥接与周期口径', '',
           '宽池扩至8家；当前扣非PE有效2家，恢复可计算，但不是高置信度估值。周期压力参照与2026审核预测分开，不进入正式定价。', '',
           '## 发行后股本与盈利分母', '',
           '发行后2.25亿股，绿鞋全额2.34亿股。金额为万元；中报原文保留万元两位小数精度。', '',
           '| 口径 | 2025实际 | 2026H1实际 | H1同比% | 当前TTM | 2026审核预测 | H2需兑现 | H2同比需% |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for b in bridges:
        label='归母' if b['metric']=='net_profit_parent' else '扣非归母'
        lines.append(f"| {label} | {b['fy2025']/1e4:.2f} | {b['h1_2026']/1e4:.2f} | {b['h1_growth_pct']:.2f} | {b['ttm']/1e4:.2f} | {b['fy2026_reviewed_forecast']/1e4:.2f} | {b['h2_2026_required']/1e4:.2f} | {b['h2_required_growth_pct']:.2f} |")
    lines += ['', 'TTM=上年全年+本年半年−上年半年。2026审核预测附有订单、交付、成本及汇率假设，不能称为已实现利润，也不能乘同行TTM PE。', '',
              '## 当前TTM与双方历史利润率参照', '',
              '| 同行 | 当前扣非TTM/万元 | 2023–2025扣非利润率中位数% | 历史利润率情景利润/万元 | 当前扣非PE | 情景PE |',
              '|---|---:|---:|---:|---:|---:|']
    cycle_by={p['code']:p for p in cycle['peer_details']}
    for p in current['peer_details']:
        a=p['metrics']['adjusted_net_profit_parent']; b=cycle_by[p['code']]['metrics']['adjusted_net_profit_parent']
        ap=a['profit']; bp=b['profit']
        amount=lambda v: formatted(v/1e4 if v is not None else None)
        lines.append(f"| {p['name']} {p['code']} | {amount(ap.get('value'))} | {formatted(bp.get('median_margin')*100 if bp.get('median_margin') is not None else None)} | {amount(bp.get('value'))} | {formatted(a.get('pe'))} | {formatted(b.get('pe'))} |")
    lines += ['', f"当前同口径F={formatted(current.get('center'))}元；场景范围{formatted(current.get('low'))}–{formatted(current.get('high'))}元。",
              f"三年利润率压力参照F={formatted(cycle.get('center'))}元，不能据此上调中枢。它并没有自动消除高PE：部分同行历史利润率仍很低，且正盈利可比由凤形/博亚变为合力/博亚/中重。", '',
              '两种口径共同正盈利同行只有博亚，低于至少两家规则；移除当前两家中的任一家，TTM基准也失效。该敏感性说明参照仍薄弱，不能用漂亮的单点替代不确定性。', '',
              'NQ轧辊企业继续保留行业盈利观察，缺少流动性/报价映射时不套用A股倍数；亏损年度不会从三年利润率中删除。', '',
              '## 周期解释及兑现检查', '',
              '- 2026H1研发费用增加204.42万元；汇兑由去年同期收益329.03万元转为损失375.49万元，税前差额704.52万元。不能将税前差额直接加回扣非归母利润，也不能据此认定钢铁周期见底。',
              '- 2026审核预测的主营增长较依赖辊环收入：4247.07→6562.72万元；轧辊收入41565.44→42542.53万元。跟踪订单交付、产品结构和毛利兑现。',
              '- 募投完全达产新增折旧摊销风险为2121.76万元/年。预测本身已计入部分折旧，不把全部风险额再机械扣一遍；需以投产时间及已计入数做后续差额复核。',
              '- 可持续正常化仍未批准：需要订单/产能、原料传导、海外客户、可持续利润率及同行相同恢复时点的证据。三年中位数只作固定压力口径，不伪装成完整周期。', '',
              '原始依据：凯达招股书PDF第7、18、202、204–208、274、312页；新增同行年度报告及iFind API/MCP、Tushare交叉资料均在evidence/raw目录，SHA链见audit.json。', '',
              '本底稿与首日实际价格无关；首日结果只用于独立回放评分。']
    (FOLDER/'kada_earnings_review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Kada reviewed: current',current.get('center'),'cycle reference',cycle.get('center'),'common peers',len(both))


if __name__=='__main__': main()
