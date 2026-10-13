"""Build reviewable four-case earnings, broad-peer and cycle workpapers offline."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
from fundamental_anchor import VERSION, build_reviewed_anchor, ttm
from ifind_helper import now_iso
from peer_fundamentals import iso_day, parse_api, parse_mcp
import config_loader
import quality_sentiment as qs
import quality_sentiment_data as qdata

FOLDER = ROOT / 'data/fundamental_cases'
PERIODS = ['2025-06-30', '2025-12-31', '2026-06-30']


def read(path):
    return json.loads(path.read_text('utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def ref(code, page):
    return f'data/fundamental_cases/evidence/{code}_p{page}.json'


def extra_company(code):
    raw = FOLDER / 'raw/stage3' if (FOLDER / 'raw/stage3' / f'{code}_income_full.json').exists() else FOLDER / 'raw'
    income_path, fina_path = [raw / f'{code}_{api}.json' for api in ('income_full', 'fina_indicator')]
    if not income_path.exists() or not fina_path.exists():
        return {'code': code, 'records': [], 'diagnostics': {'supplement': 'missing'}}
    inc, fina = read(income_path), read(fina_path)
    api_rows = [row for path in (raw / 'api').glob('*.json')
                for row in parse_api(read(path), path.relative_to(ROOT).as_posix()) if row['code'] == code]
    mcp_rows = [row for path in raw.glob('mcp_*.json')
                for row in parse_mcp(read(path), path.relative_to(ROOT).as_posix())['financials'] if row['code'] == code]
    records = []
    periods = sorted(set(PERIODS + ['2023-12-31', '2024-12-31']))
    for period in periods:
        incomes = [r for r in inc['rows'] if iso_day(r['end_date']) == period and r.get('report_type') == '1']
        indicators = [r for r in fina['rows'] if iso_day(r['end_date']) == period]
        if not incomes:
            continue
        row = max(incomes, key=lambda r: r.get('f_ann_date') or r.get('ann_date') or '')
        indicator = max(indicators, key=lambda r: r.get('ann_date') or '') if indicators else {}
        available = max(inc['retrieved_at'][:10], fina['retrieved_at'][:10])
        values = {}
        for metric, key in [('revenue', 'revenue'), ('net_profit_parent', 'n_income_attr_p'),
                            ('adjusted_net_profit_parent', 'profit_dedt')]:
            value = indicator.get(key) if key == 'profit_dedt' else row.get(key)
            paths = [(fina_path if key == 'profit_dedt' else income_path).relative_to(ROOT).as_posix()]
            comparisons = [r for r in api_rows + mcp_rows if r['report_period'] == period and metric in r['values']]
            conflict = any(value is None or abs(r['values'][metric]['value'] - value) >
                           max(.01, r['values'][metric]['rounding_tolerance'] + .005) for r in comparisons)
            paths += [r['evidence'] for r in comparisons]
            available = max([available] + [r['retrieved_at'][:10] for r in comparisons])
            values[metric] = {'value': value, 'blocked': conflict, 'evidence': paths,
                              'independent_amount_crosschecked': bool(comparisons),
                              'note': 'Tushare单源；未将MCP失败标为复核通过' if not comparisons else 'Tushare与iFind金额交叉核验'}
        published = max(iso_day(row.get('f_ann_date') or row['ann_date']),
                        iso_day(indicator.get('ann_date')) or '0001-01-01')
        records.append({'period': period, 'currency': 'CNY', 'scope': 'consolidated',
                        'period_basis': 'FY' if period.endswith('12-31') else 'YTD',
                        'published_at': published, 'available_not_before': available, 'values': values})
    return {'code': code, 'records': records, 'diagnostics': {'supplement': True}}


def company(code):
    if (FOLDER / 'raw/stage3' / f'{code}_income_full.json').exists():
        return extra_company(code)
    path = ROOT / 'data/peer_research/workpapers/companies' / f'{code}.json'
    if not path.exists():
        return extra_company(code)
    obj = read(path)
    block_ttm = bool(obj['diagnostics'].get('ttm', {}).get('basis_warnings'))
    records = []
    for source in obj['financials']:
        period = source['report_period']
        values = {metric: {'value': cell.get('value'), 'blocked': bool(cell.get('conflict')) or block_ttm,
                           'evidence': sorted({path.relative_to(ROOT).as_posix(), cell['evidence']}),
                           'note': '同期基数异常，暂停拼接' if block_ttm else ''}
                  for metric, cell in source['values'].items() if metric in ['revenue', 'net_profit_parent', 'adjusted_net_profit_parent']}
        records.append({'period': period, 'currency': 'CNY', 'scope': source['statement_scope'],
                        'period_basis': 'FY' if period.endswith('12-31') else 'YTD',
                        'published_at': source.get('published_at'),
                        'available_not_before': source.get('available_not_before') or source['retrieved_at'],
                        'values': values})
    return {'code': code, 'name': obj['name'], 'records': records, 'diagnostics': obj['diagnostics']}


def formatted(value):
    return f'{value:.2f}' if isinstance(value, (float, int)) else '不可用'


def report(case, result, baseline):
    lines = [f"# {case['name']}（{case['code']}）基本面复核", '',
             '当前证据重建，影子研究；不代表当时已经作出的预测。宽可比池，匹配差异以固定档位表达。', '',
             f"估值截点（排他）：{case['valuation_cutoff']}；资料可用不早于：{case['available_not_before']}。", '',
             '## 主营与盈利', '', case['business'], '',
             f"发行后股本：{case['shares']['post_issue_base']:,}股；绿鞋全额情景：{case['shares']['post_issue_if_greenshoe']:,}股。", '',
             '| 同口径TTM | 利润（万元） | 发行后EPS |', '|---|---:|---:|']
    for metric, label in [('net_profit_parent', '归母'), ('adjusted_net_profit_parent', '扣非归母')]:
        p = ttm(case['issuer'], case['earnings_end'], metric, case['valuation_cutoff'], reconstruction=True)
        value = p.get('value')
        lines.append(f"| {label} | {formatted(value/1e4 if value is not None else None)} | {formatted(value/case['shares']['post_issue_base'] if value is not None else None)} |")
    lines += ['', '计算：2025全年＋2026上半年－2025上半年；归母与扣非分别配对同行市值/同口径利润。', '',
              f"盈利增长与兑现：{case['forecast']['reason']}", '',
              '## 宽可比池', '', '| 公司 | 匹配档位 | 权重 | 归母PE | 扣非PE | 判断依据 |', '|---|---|---:|---:|---:|---|']
    for p in result.get('peer_details', []):
        parent, adj = [p['metrics'].get(m, {}) for m in ('net_profit_parent', 'adjusted_net_profit_parent')]
        lines.append(f"| {p['name']} {p['code']} | {p['match']} | {p['weight']} | {formatted(parent.get('pe'))} | {formatted(adj.get('pe'))} | {p['reason']}；{adj.get('reason', '')} |")
    lines += ['', 'A股跨市场折价沿用0.55，仅用于SH/SZ参照；BJ同行系数1，不重复折价；NQ缺少可验证的报价/流动性口径，仍留业务底稿。', '',
              '## F与不确定性', '', f"旧代理F：{formatted(baseline.get('center'))}。",
              f"新F：{formatted(result.get('center'))}；情景区间：{formatted(result.get('low'))}—{formatted(result.get('high'))}。",
              f"有效扣非PE可比：{result.get('coverage', {}).get('usable', 0)}/{len(case['peers'])}；状态：{result.get('reason', '可用于影子研究')}。", '',
              '情景区间反映可比四分位离散、归母/扣非口径和绿鞋摊薄，不与固定±10%历史命中率混比。', '',
              '## 周期与前瞻', '', case['cycle']['classification'] + '。' + case['cycle']['reason'], '',
              '正常化利润：未批准。未同步复核同行正常化盈利及恢复期时，不用其TTM PE套发行人正常利润。', '',
              case.get('unquoted_reference', ''), '', '## 原始页与口径调整', '']
    for e in case['evidence']:
        lines.append(f'- [{e}]({ROOT.joinpath(e).as_posix()})')
    for o in case.get('primary_overrides', []):
        lines.append(f"- {o['period']} {o['metric']}：{o['before']} → {o['value']}元；招股书PDF第{o['page']}页。原批量底稿不覆盖。")
    lines += ['', '## 限制', ''] + ['- ' + s for s in result.get('limitations', [])]
    return '\n'.join(line.rstrip() for line in lines).strip() + '\n'


def main():
    decisions = read(FOLDER / 'review_decisions.json')
    params = config_loader.load_params()
    history, _ = qdata.load_history()
    history = {r['SECURITY_CODE']: r for r in history}
    entries = {}
    for code, decision in decisions['cases'].items():
        decision = deepcopy(decision)
        input_path = FOLDER / 'inputs' / f'{code}.json'
        if input_path.exists():
            target = read(input_path)
        else:
            if code in history:
                source = history[code]
            else:
                payload = read(ROOT / '.tmp/industry_classifier_20261012/analysis_920285.json')
                source = {**payload['ipo_info'], 'comparable_data': payload['comparable_data'],
                          'industry_primary': payload['industry']['primary'], 'industry_secondary': payload['industry']['secondary']}
            keys = ['SECURITY_CODE', 'SECURITY_NAME_ABBR', 'ISSUE_PRICE', 'AFTER_ISSUE_PE', 'LISTING_DATE',
                    'AVERAGE_PRICE', 'CLOSE_PRICE', 'verified_float_shares', 'industry_primary', 'industry_secondary', 'comparable_data']
            target = {k: source.get(k) for k in keys}
            for peer_code, *_ in decision['peers']:
                quote_path = FOLDER / 'raw' / f'{peer_code}_daily_basic.json'
                if quote_path.exists():
                    for row in read(quote_path)['rows']:
                        target['comparable_data'].append({'code': peer_code, 'trade_date': row['trade_date'],
                            'mkt_cap': row['total_mv'] / 1e4, 'pe_ttm': row['pe_ttm'],
                            'close': row['close'], 'source': quote_path.relative_to(ROOT).as_posix()})
            write(input_path, target)
        issuer = company(code + '.BJ')
        overrides = []
        for period, metric, value, page in decision.pop('overrides'):
            record = next(r for r in issuer['records'] if r['period'] == period)
            original = record['values'].get(metric, {})
            overrides.append({'period': period, 'metric': metric, 'before': original.get('value'), 'value': value, 'page': page})
            record['values'][metric] = {'value': value, 'blocked': False, 'evidence': [ref(code, page)],
                                        'note': '本轮原始招股书复核值；原数据保留，按原文单位精度读取'}
            record['available_not_before'] = decisions['reviewed_at']
        share_base, share_max = decision.pop('shares')
        case = {**decision, 'schema': VERSION, 'code': code, 'issuer': issuer,
                'source': f'data/fundamental_cases/cases/{code}.json',
                'available_not_before': decisions['reviewed_at'], 'earnings_end': decision.get('earnings_end', '2026-06-30'),
                'valuation_cutoff': iso_day(target.get('LISTING_DATE')) or '2026-10-13',
                'excludes_ipo_sentiment': True, 'historical_pit_eligible': False,
                'shares': {'basis': 'post_issue_diluted', 'post_issue_base': share_base,
                           'post_issue_if_greenshoe': share_max, 'evidence': [ref(code, p) for p in decision['share_pages']]},
                'primary_overrides': overrides, 'peers': [],
                'limitations': ['市场统一折价仍为既有研究参数，需单列敏感性',
                                '前瞻与周期正常化尚无完整同口径同行估值，未加入主F',
                                '部分扣非利润仅有供应商来源，披露日/历史版本未达到真实PIT要求']}
        case['evidence'] = sorted({ref(code, p) for p in decision['business_pages'] + decision['share_pages']
                                  + decision['cycle']['pages'] + decision['forecast']['pages']
                                  + [o['page'] for o in overrides]})
        for peer_code, name, match, reason in decision['peers']:
            peer = company(peer_code)
            if peer_code == '874637.NQ':
                peer = {**company('920201.BJ'), 'code': peer_code}
            case['peers'].append({**peer, 'name': name, 'match': match, 'reason': reason,
                                 'match_evidence': decision.get('peer_evidence', {}).get(peer_code)
                                     or [ref(code, p) for p in decision['business_pages']]})
        for path in case['evidence']:
            evidence = read(ROOT / path)
            source_pdf = ROOT / evidence['source_file']
            if hashlib.sha256(source_pdf.read_bytes()).hexdigest() != evidence['source_sha256']:
                raise ValueError('Original PDF changed: ' + path)
        # The baseline must retain its ORIGINAL peer pool, even after supplements.
        baseline_source = history.get(code, target)
        clean_target = {k: v for k, v in baseline_source.items() if k not in
                        {'fundamental_review', 'fundamental_case', 'fundamental_snapshot', 'sentiment_snapshot'}}
        result = build_reviewed_anchor(case, target['comparable_data'], case['valuation_cutoff'], params, reconstruction=True)
        baseline = qs.build_anchor(clean_target, case['valuation_cutoff'], params)
        write(FOLDER / 'cases' / f'{code}.json', case)
        write(FOLDER / 'results' / f'{code}.json', {'old': baseline, 'matched': result})
        (FOLDER / 'cases' / f'{code}.md').write_text(report(case, result, baseline), encoding='utf-8')
        entries[code] = {'path': case['source'], 'sha256': hashlib.sha256((ROOT / case['source']).read_bytes()).hexdigest(),
                         'available_not_before': case['available_not_before'], 'new_anchor_available': result['available'],
                         'old_center': baseline.get('center'), 'new_center': result.get('center'),
                         'candidate_count': len(case['peers']), 'usable': result.get('coverage', {}).get('usable', 0)}
    write(FOLDER / 'index.json', {'schema': VERSION, 'generated_at': now_iso(), 'mode': 'shadow', 'items': entries})
    print(json.dumps(entries, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
