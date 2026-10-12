"""Evidence gates and review diagnostics, deliberately separate from price prediction."""
from __future__ import annotations

from datetime import date
import math
from statistics import median, pstdev

from peer_fundamentals import derive_ttm, iso_day, visible_record

RULES_VERSION = 'peer_research_rules_v1'
METRIC_LABELS = {'revenue':'营业收入','net_profit_parent':'归母净利润','adjusted_net_profit_parent':'扣非归母净利润','operating_cash_flow':'经营现金净额'}
# Fixed review thresholds, not fitted against IPO returns or used as price factors.
RULES = {
    'D01': ('必要财务覆盖', '最近三年与最新同期：营收、归母、扣非、经营现金流、毛利率及实际披露日；缺失不填零。'),
    'D02': ('来源与版本冲突', '跨源金额差异超各自显示精度阻断该字段；日期差异单列，保留全部版本。'),
    'D03': ('同期基数', '金额推算同比与当期披露同比相差超过0.10个百分点，暂停TTM拼接。'),
    'G01': ('增长与低基数', '同比基数必须为正；由亏转盈、由盈转亏及同期净利率不超过1%的低基数单列，不能套普通增长倍数。'),
    'G02': ('增长背离', '同期扣非利润与收入同比相差超过20个百分点列为复核线索，不直接扣价。'),
    'G03': ('非经常损益', '最新正归母利润中归母与扣非差额绝对值超过25%，或扣非亏损，复核盈利持续性。'),
    'G04': ('现金实现', '最近三个连续完整年度经营现金净额合计/归母利润合计低于0.8列为复核线索；合计利润非正不计算比率。'),
    'G05': ('营运资金', '同期应收或存货增长超收入增长20个百分点列为复核线索；缺少同期数据即不可判断。'),
    'C01': ('周期覆盖', '至少五个连续年报才计算跨期波动；这仍不能证明覆盖完整行业周期。'),
    'C02': ('利润波动', '五年归母净利率极差至少8个百分点或毛利率极差至少10个百分点，提示检查周期/结构变化。'),
    'C03': ('正常化口径', '正常化利润仅作情景草稿；必须核验业务范围、可持续利润率、恢复时间，并重新计算同行正常化倍数。'),
    'M01': ('业务匹配', '主营产品、收入占比、产业链位置、客户用途、技术及盈利方式逐项留证；共同词仅帮助排序，不自动加入核心池。'),
    'M02': ('市场与亏损', '跨交易市场单列；亏损同行保留业务身份，不能因PE不可用删除。'),
    'V01': ('估值分母一致', 'EPS与PE必须同盈利指标、同期间/预测年度、同合并范围、同币种；发行人EPS使用发行后稀释总股本。'),
    'T01': ('时间边界', '报告期、披露日、采集日、修订版本分开；当前供应商历史快照不准回填成历史PIT或真实事前预测。'),
}

PRODUCT_TERMS = (
    '铝合金压铸', '压铸', '锻造', '汽车零部件', '汽车', '热管理', '电机', '连接器',
    '光纤连接', '光纤', '光通信', '光模块', '光器件', '光学', '轧辊', '耐磨', '铸件', '钢铁',
    '净水', '空气净化', '家用电器', '智能家居', '跨境电商', '电子商务', '消费电子',
    '半导体', '集成电路', '封装', '电子化学品', '电子材料', '有机硅', '胶粘', '树脂', '涂料',
    '印制电路板', '电容器', '铝粉', '钛', '硬质合金', '耐火材料', '润滑', '橡胶', '塑料',
    '医疗器械', '手术', '医用耗材', '诊断', '康复', '药物', '多肽', '乳制品', '乳品',
    '包装', '印刷', '自动化', '焊接', '物流', '模具', '轴承', '轴', '机床', '阀门', '压力容器',
)


def cell(record, metric):
    value = (record or {}).get('values', {}).get(metric)
    if not value or value.get('conflict'):
        return None
    result = value.get('value')
    return result if isinstance(result, (int, float)) and math.isfinite(result) else None


def growth(current, previous):
    if current is None or previous is None:
        return {'value_pct': None, 'status': 'missing'}
    if previous <= 0:
        return {'value_pct': None, 'status': 'loss_to_profit' if current > 0 else 'nonpositive_base'}
    return {'value_pct': (current / previous - 1) * 100,
            'status': 'profit_to_loss' if current < 0 else 'positive_base'}


def check(rule, status, reason, evidence=None):
    assert rule in RULES and status in {'pass', 'review', 'blocked', 'unknown'}
    return {'rule': rule, 'title': RULES[rule][0], 'status': status, 'reason': reason, 'evidence': evidence or []}


def company_diagnostics(records, profiles, as_of, expected_periods):
    visible = {r['report_period']: r for r in records if visible_record(r, as_of)}
    requested = sorted(iso_day(p) for p in expected_periods)
    latest_expected = requested[-1]
    latest = visible.get(latest_expected, {})
    prior = visible.get(f'{int(latest_expected[:4])-1}{latest_expected[4:]}', {})
    annual_end = int(latest_expected[:4]) - (not latest_expected.endswith('12-31'))
    annual3 = [f'{year}-12-31' for year in range(annual_end-2, annual_end+1)]
    annual5 = [f'{year}-12-31' for year in range(annual_end-4, annual_end+1)]
    required = sorted(set(annual3 + [latest_expected, f'{int(latest_expected[:4])-1}{latest_expected[4:]}']))
    metrics = ['revenue', 'net_profit_parent', 'adjusted_net_profit_parent', 'operating_cash_flow', 'gross_margin_pct']
    missing = [{'period': p, 'metric': m} for p in required for m in metrics if cell(visible.get(p), m) is None]
    checks = [check('D01', 'blocked' if missing else 'pass', f'必要字段缺失或未过日期/冲突校验：{len(missing)}项', missing)]
    numeric = [{'period': r['report_period'], 'fields': r.get('conflicts')} for r in records if r.get('conflicts')]
    dates = [{'period': r['report_period'], 'ifind': r.get('published_at'), 'other': r.get('tushare_publication_dates')}
             for r in records if r.get('publication_date_needs_review')]
    checks.append(check('D02', 'blocked' if numeric else 'review' if dates else 'pass',
                        f'数值/披露冲突记录{len(numeric)}；跨源日期差异{len(dates)}', numeric + dates))
    ttm = derive_ttm(records, as_of)
    basis_checkable = all(cell(latest, m) is not None for m in ['reported_revenue_yoy_pct', 'reported_profit_yoy_pct'])
    checks.append(check('D03', 'blocked' if ttm.get('basis_warnings') else 'pass' if ttm.get('available') and basis_checkable else 'unknown',
                        ttm.get('reason', '已检查披露同比' if basis_checkable else '缺少当期披露同比，无法独立确认同期基数'), ttm.get('basis_warnings')))
    yoy = {metric: growth(cell(latest, metric), cell(prior, metric))
           for metric in metrics if not metric.endswith('_pct')}
    base_status = [yoy[m]['status'] for m in ['net_profit_parent', 'adjusted_net_profit_parent']]
    prior_revenue = cell(prior, 'revenue')
    low_base = [m for m in ['net_profit_parent', 'adjusted_net_profit_parent']
                if prior_revenue and prior_revenue > 0 and cell(prior, m) is not None and 0 < cell(prior, m)/prior_revenue <= .01]
    checks.append(check('G01', 'unknown' if 'missing' in base_status else 'review' if low_base or any(s != 'positive_base' for s in base_status) else 'pass',
                        '归母/扣非同期增长状态：' + '、'.join(base_status) + '；上年净利率不超过1%的低基数：' + '、'.join(low_base),
                        [{'period': latest_expected, 'growth': yoy}]))
    revenue_yoy, adj_yoy = yoy['revenue']['value_pct'], yoy['adjusted_net_profit_parent']['value_pct']
    divergence = adj_yoy - revenue_yoy if revenue_yoy is not None and adj_yoy is not None else None
    checks.append(check('G02', 'unknown' if divergence is None else 'review' if abs(divergence) > 20 else 'pass',
                        f'扣非同比减收入同比：{divergence}个百分点'))
    profit, adjusted = cell(latest, 'net_profit_parent'), cell(latest, 'adjusted_net_profit_parent')
    nonrecurring = (profit-adjusted) / abs(profit) if profit and adjusted is not None else None
    checks.append(check('G03', 'unknown' if profit is None or adjusted is None else
                        'review' if adjusted < 0 or profit <= 0 or abs(nonrecurring or 0) > .25 else 'pass',
                        f'归母与扣非差额/归母绝对值：{nonrecurring}；扣非利润：{adjusted}元'))
    cash_cells = [cell(visible.get(p), 'operating_cash_flow') for p in annual3]
    profit_cells = [cell(visible.get(p), 'net_profit_parent') for p in annual3]
    cash_ratio = None
    if all(v is not None for v in cash_cells + profit_cells) and sum(profit_cells) > 0:
        cash_ratio = sum(cash_cells) / sum(profit_cells)
    checks.append(check('G04', 'unknown' if cash_ratio is None else 'review' if cash_ratio < .8 else 'pass',
                        f'三年累计经营现金流/累计归母：{cash_ratio}', annual3))
    working = {m: growth(cell(latest, m), cell(prior, m)) for m in ['accounts_receivable', 'inventory']}
    working_gaps = [v['value_pct'] - revenue_yoy for v in working.values() if v['value_pct'] is not None and revenue_yoy is not None]
    checks.append(check('G05', 'unknown' if len(working_gaps) < 2 else 'review' if max(working_gaps) > 20 else 'pass',
                        f'应收/存货增长与营收比较；可比较{len(working_gaps)}/2项', [working]))
    margins, gross_margins = [], []
    for period in annual5:
        row = visible.get(period)
        revenue, net = cell(row, 'revenue'), cell(row, 'net_profit_parent')
        if revenue is not None and revenue > 0 and net is not None:
            margins.append(net / revenue * 100)
        gm = cell(row, 'gross_margin_pct')
        if gm is not None:
            gross_margins.append(gm)
    complete_cycle_series = len(margins) == 5
    checks.append(check('C01', 'pass' if complete_cycle_series else 'unknown',
                        f'连续五年有效归母净利率覆盖{len(margins)}/5；仍须检查行业景气与业务范围', annual5))
    margin_range = max(margins)-min(margins) if complete_cycle_series else None
    gross_range = max(gross_margins)-min(gross_margins) if len(gross_margins) == 5 else None
    volatile = (margin_range is not None and margin_range >= 8) or (gross_range is not None and gross_range >= 10)
    checks.append(check('C02', 'unknown' if margin_range is None else 'review' if volatile else 'pass',
                        f'五年归母净利率极差{margin_range}、毛利率极差{gross_range}个百分点；波动不等于已确认周期'))
    ttm_revenue = ttm.get('values', {}).get('revenue') if ttm.get('available') else None
    illustrative = ttm_revenue * median(margins) / 100 if complete_cycle_series and ttm_revenue is not None else None
    checks.append(check('C03', 'blocked', '业务范围、可持续利润率、恢复期、同行正常化倍数未审核，情景草稿禁止用于定价'))
    checks.append(check('T01', 'blocked', '本批为当前供应商历史快照；不可用于历史事前PIT回放'))
    cagr = {}
    # Precisely three elapsed years, not three reports mistaken for three years.
    for metric in ['revenue', 'net_profit_parent', 'adjusted_net_profit_parent']:
        start = cell(visible.get(f'{annual_end-3}-12-31'), metric)
        end = cell(visible.get(f'{annual_end}-12-31'), metric)
        cagr[metric] = ((end/start)**(1/3)-1)*100 if start and start > 0 and end is not None and end > 0 else None
    return {'rules_version': RULES_VERSION, 'as_of': as_of, 'expected_latest_period': latest_expected, 'latest_visible_period': max(visible, default=None),
        'checks': checks, 'missing_fields': missing, 'growth_yoy': yoy, 'cagr_three_year_pct': cagr,
        'cash_conversion_three_year': cash_ratio, 'nonrecurring_share': nonrecurring,
        'working_capital_growth': working, 'ttm': ttm,
        'cycle': {'five_annual_periods': annual5, 'net_margin_range_pp': margin_range,
                  'gross_margin_range_pp': gross_range, 'high_variation_review': volatile,
                  'full_cycle_verified': False, 'business_scope_verified': False,
                  'illustrative_profit_at_median_margin': illustrative, 'normalization_approved': False},
        'historical_pit_eligible': False, 'pricing_eligible': False}


def business_text(company):
    return '；'.join(dict.fromkeys(str(p.get('business') or '') + ' ' + str(p.get('products') or '')
                                  for p in company.get('profiles', [])))


def pair_diagnostics(issuer, peer):
    issuer_text, peer_text = business_text(issuer), business_text(peer)
    left = [t for t in PRODUCT_TERMS if t in issuer_text]
    right = [t for t in PRODUCT_TERMS if t in peer_text]
    shared = [t for t in left if t in right]
    references = sorted({p['evidence'] for c in [issuer, peer] for p in c.get('profiles', [])})
    def latest_value(c, metric):
        end = c.get('diagnostics', {}).get('expected_latest_period')
        return next((cell(r, metric) for r in c.get('financials', []) if r['report_period'] == end
                     and visible_record(r, c['diagnostics']['as_of'])), None)
    issuer_rev, peer_rev = latest_value(issuer, 'revenue'), latest_value(peer, 'revenue')
    scale = peer_rev / issuer_rev if issuer_rev and issuer_rev > 0 and peer_rev is not None else None
    peer_profit = latest_value(peer, 'net_profit_parent')
    return {'issuer': issuer['code'], 'peer': peer['code'], 'peer_name': peer['name'],
        'role': 'candidate_pending_review', 'core_eligible': False,
        'business_evidence': references, 'issuer_product_terms': left, 'peer_product_terms': right,
        'shared_product_terms': shared, 'peer_to_issuer_revenue_ratio': scale,
        'same_market': issuer['code'].split('.')[-1] == peer['code'].split('.')[-1],
        'lossmaking_peer_retained': peer_profit is not None and peer_profit <= 0,
        'profitability_status': '未知' if peer_profit is None else '亏损' if peer_profit < 0 else '盈亏平衡' if peer_profit == 0 else '盈利',
        'checks': [check('M01', 'review' if issuer_text and peer_text else 'unknown',
             '共同词：' + ('、'.join(shared) or '无；需阅读全文') + '。仍需产品收入占比、技术/客户、产业链与盈利方式证据', references),
             check('M02', 'review' if not peer['code'].endswith('.BJ') or (peer_profit is not None and peer_profit <= 0) else 'pass',
                   '保留各市场与亏损候选；没有按PE高低筛选可比池')],
        'remaining_evidence': ['主要产品收入占比', '产业链位置及客户用途', '技术及盈利模式', '核心/辅助/题材角色复核', '历史可得版本']}


def validate_valuation_basis(eps, multiple, as_of):
    """A hard gate for a future F implementation; never manufactures an EPS/PE."""
    errors = []
    for field in ['profit_metric', 'period', 'basis', 'scope', 'currency']:
        if not eps.get(field) or eps.get(field) != multiple.get(field):
            errors.append('basis_mismatch:' + field)
    if eps.get('share_basis') != 'post_issue_diluted':
        errors.append('post_issue_diluted_shares_required')
    for label, descriptor in [('eps', eps), ('multiple', multiple)]:
        for field in ['published_at', 'retrieved_at', 'valuation_date']:
            value = iso_day(descriptor.get(field))
            if not value or not iso_day(as_of) or value >= iso_day(as_of):
                errors.append(label + ':invalid_or_future_' + field)
        profit = descriptor.get('profit_value')
        if not isinstance(profit, (float, int)) or isinstance(profit, bool) or not math.isfinite(profit) or profit <= 0:
            errors.append(label + ':nonpositive_profit')
        if descriptor.get('basis') in {'forward', 'normalized'} and descriptor.get('evidence_reviewed') is not True:
            errors.append(label + ':unreviewed_assumptions')
    return {'eligible': not errors, 'errors': errors, 'status': 'basis_consistency_only_not_full_pricing_approval'}
