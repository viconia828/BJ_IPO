"""Build auditable company/IPO workpapers and a machine-readable exception queue."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
sys.path.insert(0, str(ROOT / 'tools'))
from collect_peer_fundamentals import compare_independent, write_json
from ifind_helper import now_iso
from peer_fundamentals import iso_day, merge_records, parse_api, parse_mcp
from peer_research import RULES, RULES_VERSION, METRIC_LABELS, business_text, cell, company_diagnostics, pair_diagnostics

FOLDER = ROOT / 'data/peer_research'
STATUS_LABELS = {'pass':'通过','review':'需复核','blocked':'阻断','unknown':'资料不足',
                'missing':'缺少同期值','positive_base':'正基数','profit_to_loss':'由盈转亏',
                'loss_to_profit':'由亏转盈，停算增速','nonpositive_base':'非正基数，停算增速'}
CASE_NOTES = {
 '920202': '按汽车铝合金精密压铸产品复核爱柯迪、嵘泰、晋拓与旭升，重点比客户/车型结构和新能源汽车业务占比。增长先进入同口径盈利底稿，不因行业名再加一个固定倍数。',
 '920157': '先核对太辰光、蘅东光的光连接产品与客户场景；天孚的产品环节、毛利率及增长结构、中航光电的连接器业务结构需要单独解释。光通信标签不能直接使四家公司同权定价。招股书管理层全年预计另存，不混入TTM。',
 '920025': '华冶、腾升、中原辊轴按轧辊/辊环/辊轴产品优先复核，保留NQ市场与流动性差异；合力、凤形先核对铸造模具、耐磨件及业务结构差异。钢铁需求暴露不等于钢铁生产企业的估值口径；需复核钢厂开工、订单、扩产折旧和出口需求。',
 '920285': '以滤芯、净水器件的产品结构、耗材更换、品牌及渠道为复核重点。开能、建霖、德尔玛分别核对净水/家电业务占比；千岸、安克重点作为品牌与跨境渠道参照，渠道相似不足以共享盈利倍数。原材料价格、渠道费用与汇率应与产品毛利一起核验。',
}


def read(path):
    return json.loads(path.read_text('utf-8'))


def fmt(value, divisor=1):
    return '—' if value is None else f'{value/divisor:,.2f}'


def safe(text):
    return str(text or '').replace('|', '／').replace('\n', ' ')


def markdown(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(line.rstrip() for line in lines) + '\n', 'utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', default=(date.today()+timedelta(days=1)).isoformat(), help='排他日期截断')
    args = parser.parse_args()
    if not iso_day(args.as_of):
        parser.error('invalid --as-of')
    universe = read(FOLDER / 'universe.json')
    guidance = read(FOLDER/'management_guidance.json').get('guidance',[]) if (FOLDER/'management_guidance.json').exists() else []
    jobs = {j['id']: j for j in read(FOLDER / 'mcp_jobs.json')}
    if (FOLDER / 'balance_jobs.json').exists():
        jobs.update({j['id']: j for j in read(FOLDER / 'balance_jobs.json')})
    codes = set(universe['codes'])
    periods = {iso_day(p) for p in universe['periods']}
    records, profiles, publications, evidence, warnings, coverage = [], [], [], [], [], []
    for source_folder in [ROOT / 'data/peer_fundamentals', FOLDER]:
        for path in sorted((source_folder / 'mcp').glob('*.json')):
            if path.stem in {'financials', 'dates_20260630'}:
                continue
            envelope = read(path)
            ref = str(path.relative_to(ROOT)).replace('\\', '/')
            parsed = parse_mcp(envelope, ref)
            job = jobs.get(envelope.get('job_id') or path.stem)
            allowed = set(job['codes']) if job else codes
            expected_period = iso_day(job.get('period')) if job else None
            valid_records = [r for r in parsed['financials'] if r['code'] in allowed & codes and r['report_period'] in periods
                             and (not expected_period or r['report_period'] == expected_period)]
            valid_profiles = [p for p in parsed['profiles'] if p['code'] in allowed & codes]
            records += valid_records
            profiles += valid_profiles
            publications += [p for p in parsed['publication_dates'] if p['code'] in allowed & codes and
                             p['report_period'] in periods and (not expected_period or p['report_period'] == expected_period)]
            warnings += [{'evidence': ref, 'warning': w} for w in sorted(set(parsed['warnings']))]
            dropped = len(parsed['financials']) - len(valid_records)
            if dropped:
                warnings.append({'evidence': ref, 'warning': f'{dropped}条返回记录不属于明确请求的代码/报告期，未采用'})
            if job:
                returned = {r['code'] for r in (valid_profiles if job['kind'] == 'profile' else valid_records)}
                coverage.append({'job_id': job['id'], 'expected_codes': len(allowed), 'returned_codes': len(returned),
                                 'missing_codes': sorted(allowed-returned), 'evidence': ref})
            evidence.append({'path': ref, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        for path in sorted((source_folder / 'api').glob('*.json')):
            ref = str(path.relative_to(ROOT)).replace('\\', '/')
            parsed = parse_api(read(path), ref)
            records += [r for r in parsed if r['code'] in codes and r['report_period'] in periods]
            evidence.append({'path': ref, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            if not parsed:
                warnings.append({'evidence': ref, 'warning': 'API无可验证记录'})
    financials = merge_records(records, publications)
    checks = []
    for path in sorted((FOLDER / 'tushare').glob('*_income.json')):
        saved = read(path)
        ref = str(path.relative_to(ROOT)).replace('\\', '/')
        for row in saved.get('rows', []):
            if iso_day(row.get('end_date')) in periods and str(row.get('report_type')) == '1':
                checks.append({'code': saved['code'], 'period': row['end_date'], 'row': row, 'evidence': ref,
                               'retrieved_at': saved.get('retrieved_at')})
        if saved.get('error'):
            warnings.append({'evidence': ref, 'warning': saved['error']})
        evidence.append({'path': ref, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    reviews = []
    precision_path = FOLDER / 'precision_reviews.json'
    if precision_path.exists():
        for review in read(precision_path).get('reviews', []):
            source = ROOT / review['source_file']
            if source.exists() and hashlib.sha256(source.read_bytes()).hexdigest() == review['source_file_sha256']:
                reviews.append(review)
            else:
                warnings.append({'evidence': str(precision_path.relative_to(ROOT)), 'warning': '原始PDF缺失或SHA变化，精度复核不生效：'+review['code']})
    comparisons = compare_independent(financials, checks, reviews)
    # Tushare's actual announcement dates can date current research when iFind
    # supplies none. Later revision dates remain a conservative boundary, not
    # a claim that the present figures existed on the original disclosure date.
    for record in financials:
        if not record['publication_evidence'] and record.get('tushare_publication_dates'):
            dates = [d for d in record['tushare_publication_dates'] if d > record['report_period']]
            if dates:
                record['published_at'] = max(dates)
                record['publication_resolution'] = 'latest_tushare_vintage_for_current_research_only'
                record['publication_evidence'] = [{'source': 'tushare_income', 'evidence': c['evidence'],
                    'retrieved_at': read(ROOT/c['evidence'])['retrieved_at'],
                    'published_at': iso_day(c['row'].get('f_ann_date') or c['row'].get('ann_date'))}
                    for c in checks if c['code'] == record['code'] and iso_day(c['period']) == record['report_period']]
    companies = {}
    for code in sorted(codes):
        company_profiles = [p for p in profiles if p['code'] == code and iso_day(p.get('retrieved_at')) and iso_day(p['retrieved_at']) < args.as_of]
        company_profiles.sort(key=lambda p: str(p.get('retrieved_at') or ''), reverse=True)
        financial = [r for r in financials if r['code'] == code]
        fallback = next((c['name'] for c in universe['cases'].values() if c['code'] == code), code)
        company = {'code': code, 'name': company_profiles[0].get('name') or fallback if company_profiles else fallback,
            'roles': [role for role, key in [('issuer', 'issuer_codes'), ('peer', 'peer_codes')] if code in universe[key]],
            'profiles': company_profiles, 'financials': financial,
            'diagnostics': company_diagnostics(financial, company_profiles, args.as_of, universe['periods'])}
        companies[code] = company
        write_json(FOLDER / f'workpapers/companies/{code}.json', company)
        lines = [f'# {company["name"]}（{code}）底稿', '', f'排他截点：{args.as_of}；币种人民币，金额表为万元。当前研究快照，尚未批准进入估值。',
                 '', '## 主营与产品', '', business_text(company) or '尚无可核验主营资料。', '',
                 '## 跨期财务', '', '|报告期|实际/保守披露日|营收|归母|扣非归母|经营现金净额|毛利率%|冲突|',
                 '|---|---|---:|---:|---:|---:|---:|---|']
        for period in sorted(periods):
            row = next((r for r in financial if r['report_period'] == period), {})
            values = [fmt(cell(row, m), 10000) for m in ['revenue', 'net_profit_parent', 'adjusted_net_profit_parent', 'operating_cash_flow']]
            lines.append('|'+ '|'.join([period, row.get('published_at') or '未知']+values+[fmt(cell(row, 'gross_margin_pct')), '、'.join(row.get('conflicts', [])) or '无已知金额冲突'])+'|')
        d = company['diagnostics']
        lines += ['', '## 同期增长与TTM', '', '|指标|最新同期增长%|基数状态|研究TTM（万元）|', '|---|---:|---|---:|']
        for metric, g in d['growth_yoy'].items():
            lines.append(f'|{METRIC_LABELS[metric]}|{fmt(g["value_pct"])}|{STATUS_LABELS[g["status"]]}|{fmt(d["ttm"].get("values",{}).get(metric),10000)}|')
        lines += ['', '## 营运资金与资产（期末余额，万元）', '', '|报告期|应收账款|存货|归母净资产|总资产|', '|---|---:|---:|---:|---:|']
        for period in ['2025-06-30','2025-12-31','2026-06-30']:
            row=next((r for r in financial if r['report_period']==period),{})
            lines.append('|'+period+'|'+'|'.join(fmt(cell(row,m),10000) for m in ['accounts_receivable','inventory','equity_parent','total_assets'])+'|')
        lines += ['', '## 检验结果', '', '|规则|状态|结果|', '|---|---|---|']
        for item in d['checks']:
            lines.append(f'|{item["rule"]} {item["title"]}|{STATUS_LABELS[item["status"]]}|{safe(item["reason"])}|')
        lines += ['', '## 盈利口径与周期情景', '',
            f'- TTM状态：{safe(d["ttm"].get("reason") or d["ttm"].get("status"))}；组件：{d["ttm"].get("components", [])}。',
            '- 近三年CAGR：'+'；'.join(f'{METRIC_LABELS[m]} {fmt(v)}%' for m,v in d['cagr_three_year_pct'].items())+'。',
            f'- 当前TTM营收×近五年归母净利率中位数的算术草稿：{fmt(d["cycle"]["illustrative_profit_at_median_margin"],10000)}万元。',
            '- 上述草稿未审核业务范围、周期覆盖、恢复时间，也没有配套同行正常化倍数；不得直接替换盈利或F。',
            '- 前瞻利润、发行后稀释总股本、同口径同行倍数待单独留证；不将半年利润乘二，不使用现价倒推盈利。',
            '', '## 证据', '', f'逐字段原始观测、冲突及披露版本见[{code}.json]({code}.json)。']
        refs = {p['evidence'] for p in company_profiles}
        refs |= {v['evidence'] for r in financial for v in r['values'].values()}
        for ref in sorted(refs):
            lines.append(f'- [{ref}](../../../../{ref})')
        markdown(FOLDER / f'workpapers/companies/{code}.md', lines)
    pair_rows = []
    for code, case in universe['cases'].items():
        issuer = companies[case['code']]
        pairs = [pair_diagnostics(issuer, companies[p]) for p in case['candidate_codes']]
        pair_rows += pairs
        case_paper = {'case': case, 'issuer_workpaper': f'../companies/{case["code"]}.json', 'pairs': pairs,
            'pricing_eligible': False, 'fundamental_anchor_changed': False}
        write_json(FOLDER / f'workpapers/issuers/{code}.json', case_paper)
        lines = [f'# {case["name"]}：盈利、可比与周期底稿', '',
            f'发行人财务与检验见[{case["code"]}](../companies/{case["code"]}.md)。候选来自{case["evidence"]}。', '',
            '|候选|主营共同词（仅检索线索）|同业/发行人营收比|最新归母状态|当前角色|', '|---|---|---:|---|---|']
        for pair in pairs:
            lines.append(f'|[{pair["peer_name"]} {pair["peer"]}](../companies/{pair["peer"]}.md)|{safe("、".join(pair["shared_product_terms"]) or "待阅读全文")}|{fmt(pair["peer_to_issuer_revenue_ratio"])}|{pair["profitability_status"]}|候选，待主营审核|')
        if code in CASE_NOTES:
            lines += ['', '## 本案复核重点', '', CASE_NOTES[code]]
        company_guidance = [g for g in guidance if g['code']==case['code']]
        if company_guidance:
            lines += ['', '## 管理层业绩预计（未用于定价）', '', '|累计期间|指标|范围（万元）|来源|', '|---|---|---:|---|']
            for g in company_guidance:
                for metric,values in g['values_cny'].items():
                    lines.append(f'|{g["period"]}|{METRIC_LABELS[metric]}|{fmt(values[0],10000)}–{fmt(values[1],10000)}|[{g["evidence"]}](../../../../{g["evidence"]})|')
            lines += ['', '管理层预计未经审计/审阅，不构成业绩承诺；尚未核验原始公告发布时间，且缺少同年预测同行倍数，不能直接代替TTM或进入历史F。']
        lines += ['', '## 进入估值前的规则', '',
            '1. 先确认核心产品及收入占比、链条位置、客户用途、技术和盈利方式，再分核心可比、辅助参照、题材观察。共同词和行业标签不会自动决定角色。',
            '2. 统一归母/扣非、TTM/同年预测/正常化口径；按发行后稀释总股本计算发行人EPS，同行倍数分母必须一致。',
            '3. 周期利润只做有证据的情景，保留恢复时间与跨市场差异；亏损同行保留业务参照，不能为提高PE而剔除。',
            '4. 本批当前快照不能回填成上市前资料；不以首日涨幅或实际价格校准基本面F。', '',
            '当前没有自动升级核心池、切换情绪模型或改估值参数；明细与待核验项见同名JSON。']
        markdown(FOLDER / f'workpapers/issuers/{code}.md', lines)
    exceptions = [{'code': code, 'name': c['name'], **item} for code,c in companies.items()
                  for item in c['diagnostics']['checks'] if item['status'] != 'pass']
    summary = {'issuers': len(universe['cases']), 'peers': len(universe['peer_codes']), 'companies': len(companies),
        'profiles': sum(bool(c['profiles']) for c in companies.values()), 'expected_company_periods': len(codes)*len(periods),
        'financial_records': len(financials), 'dated_records': sum(bool(r['published_at']) for r in financials),
        'ttm_any_metric_available': sum(c['diagnostics']['ttm'].get('available', False) for c in companies.values()),
        'ttm_parent_profit_available': sum('net_profit_parent' in c['diagnostics']['ttm'].get('values', {}) for c in companies.values()),
        'ttm_adjusted_profit_available': sum('adjusted_net_profit_parent' in c['diagnostics']['ttm'].get('values', {}) for c in companies.values()),
        'independent_unique_fields': len({(c['code'], c['report_period'], c['metric']) for c in comparisons}),
        'independent_version_comparisons': len(comparisons), 'independent_conflicts': sum(not c['consistent'] for c in comparisons),
        'independent_precision_verified_fields': len({(c['code'],c['report_period'],c['metric']) for c in comparisons if c['consistent'] and c.get('verified_precision_review')}),
        'publication_dates_needing_review': sum(r.get('publication_date_needs_review', False) for r in financials),
        'five_year_margin_coverage': sum(c['diagnostics']['cycle']['net_margin_range_pp'] is not None for c in companies.values()),
        'matching_cards': len(pair_rows), 'mcp_jobs_expected': len(jobs), 'mcp_jobs_saved': len({c['job_id'] for c in coverage}),
        'missing_mcp_jobs': sorted(set(jobs)-{c['job_id'] for c in coverage}),
        'checks_by_status': dict(Counter(item['status'] for c in companies.values() for item in c['diagnostics']['checks'])),
        'core_peers_auto_approved': 0, 'pricing_changed': False, 'historical_pit_ready': 0}
    for directory in ['source_excerpts','notices']:
        for path in sorted((FOLDER/directory).glob('*.json')):
            evidence.append({'path':str(path.relative_to(ROOT)).replace('\\','/'),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    write_json(FOLDER / 'catalog.json', {'schema': 'peer_research_catalog_v1', 'rules_version': RULES_VERSION,'generated_at': now_iso(), 'as_of': args.as_of,
        'summary': summary, 'companies': {code: {'name': c['name'], 'path': f'workpapers/companies/{code}.json'} for code,c in companies.items()},
        'cases': universe['cases'], 'evidence': sorted(evidence, key=lambda e:e['path'])})
    write_json(FOLDER / 'checks.json', {'summary': summary, 'exceptions': exceptions, 'coverage': coverage, 'warnings': warnings})
    write_json(FOLDER / 'independent_crosschecks.json', comparisons)
    write_json(FOLDER / 'rules.json', {'version':RULES_VERSION,'purpose':'review_screening_not_fitted_price_factors',
        'rules':{key: {'title': value[0], 'rule': value[1]} for key,value in RULES.items()}})
    lines = ['# 同业与发行人批量研究底稿', '', f'生成时间：{now_iso()}；排他截点：{args.as_of}。', '',
             '当前研究与核验用途，尚未用于定价。数值/版本冲突、不可评价字段均保留，不以采集成功代替业务审核。', '',
             '```json', json.dumps(summary,ensure_ascii=False,indent=2), '```', '', '## 新股底稿入口', '']
    for code, case in universe['cases'].items():
        lines.append(f'- [{case["name"]} {code}](workpapers/issuers/{code}.md)：{len(case["candidate_codes"])}家候选。')
    lines += ['', '## 检验规则', '', '|编号|检验|规则|', '|---|---|---|']
    for key,(title,rule) in RULES.items():
        lines.append(f'|{key}|{title}|{rule}|')
    lines += ['', '异常队列见checks.json，逐字段独立核验见independent_crosschecks.json，证据哈希见catalog.json。']
    markdown(FOLDER / 'review.md', lines)
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == '__main__':
    main()
