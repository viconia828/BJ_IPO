"""Resumable broad peer research collection; no production valuation mutation."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
sys.path.insert(0, str(ROOT / 'tools'))
from ifind_helper import IFindClient, fetch_financials, now_iso
from collect_peer_fundamentals import cases as initial_cases, crosscheck_tushare, write_json

FOLDER = ROOT / 'data/peer_research'
PERIODS = [f'{year}1231' for year in range(2019, 2026)] + ['20250630', '20260630']


def build_universe():
    source = ROOT / 'data/offline_tuning/replay_dataset.json'
    dataset = json.loads(source.read_text('utf-8'))
    cases = {}
    for item in dataset['items']:
        code = item['SECURITY_CODE']
        cases[code] = {'code': code + '.BJ', 'name': item['SECURITY_NAME_ABBR'],
            'listing_date': item.get('LISTING_DATE'), 'industry': item.get('industry_primary'),
            'chain_tags': item.get('chain_tags', []),
            'candidate_codes': sorted(set(item.get('comparable_codes', []))),
            'evidence': f'data/offline_tuning/replay_items/{code}.json',
            'peer_selection_status': 'prospectus_candidates_pending_business_review'}
    for code, case in initial_cases().items():
        if code not in cases:
            cases[code] = {'code': code + '.BJ', **case,
                'evidence': '公告文件/920285_远大健科_招股说明书.pdf',
                'peer_selection_status': 'prospectus_candidates_pending_business_review'}
    peers = sorted({c for case in cases.values() for c in case['candidate_codes']})
    issuers = sorted({c['code'] for c in cases.values()})
    codes = sorted(set(peers + issuers))
    return {'schema': 'peer_research_universe_v1', 'created_at': now_iso(),
        'source': str(source.relative_to(ROOT)), 'source_sha256': hashlib.sha256(source.read_text('utf-8').encode('utf-8')).hexdigest(),
        'source_sha256_basis': 'UTF-8 text with LF newlines',
        'cases': cases, 'peer_codes': peers, 'issuer_codes': issuers, 'codes': codes,
        'periods': sorted(PERIODS), 'purpose': 'research_workpapers_not_pricing_or_historical_pit'}


def build_jobs(universe, size=25):
    jobs = []
    # Separate trading venues: NQ names must not be silently rewritten as BJ codes.
    groups = [[c for c in universe['codes'] if c.endswith('.NQ')],
              [c for c in universe['codes'] if not c.endswith('.NQ')]]
    for group in groups:
        for start in range(0, len(group), size):
            codes = group[start:start + size]
            tag = hashlib.sha256(','.join(codes).encode()).hexdigest()[:10]
            jobs.append({'id': 'profiles_' + tag, 'kind': 'profile', 'codes': codes,
                'tool': 'mcp__ifind_stock__get_stock_info',
                'query': '查询以下证券的证券简称、主营业务、主营产品名称、所属申万行业：' + '、'.join(codes) + '。逐家返回，保留完整证券代码；未知值请留空。'})
            for period in universe['periods']:
                jobs.append({'id': f'financials_{period}_{tag}', 'kind': 'financial',
                    'codes': codes, 'period': period, 'tool': 'mcp__ifind_stock__get_stock_financials',
                    'query': '查询' + '、'.join(codes) + f'，仅{period}这一个报告期的营业收入、归属于母公司所有者的净利润、扣除非经常性损益后的归母净利润、经营活动产生的现金流量净额、销售毛利率、定期报告实际披露日期。合并报表、累计口径、金额人民币元。请明确返回报告期与单位，不要其他期间或单季度指标。'})
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'api', 'tushare'])
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    path = FOLDER / 'universe.json'
    if args.action == 'init':
        universe = build_universe()
        write_json(path, universe)
        jobs = build_jobs(universe)
        write_json(FOLDER / 'mcp_jobs.json', jobs)
        balance_jobs = []
        for job in jobs:
            if job['kind'] != 'profile':
                continue
            for period in ['20250630', '20251231', '20260630']:
                balance_jobs.append({'id': f'balance_{period}_' + job['id'].split('_',1)[1],
                    'kind': 'balance', 'codes': job['codes'], 'period': period,
                    'tool': 'mcp__ifind_stock__get_stock_financials',
                    'query': '查询' + '、'.join(job['codes']) + f'，仅{period}报告期的应收账款、存货、资产总计、归属于母公司所有者权益合计、定期报告实际披露日期。合并报表，金额人民币元，明确报告期与单位，不要其他报告期。'})
        write_json(FOLDER / 'balance_jobs.json', balance_jobs)
        print(json.dumps({'issuers': len(universe['cases']), 'peers': len(universe['peer_codes']),
            'unique_companies': len(universe['codes']), 'periods': len(PERIODS), 'mcp_jobs': len(jobs)}, ensure_ascii=False))
        return
    universe = json.loads(path.read_text('utf-8'))
    if args.action == 'api':
        client = IFindClient()
        codes = universe['codes']
        for start in range(0, len(codes), 25):
            results = fetch_financials(codes[start:start+25], universe['periods'], FOLDER / 'api',
                                       refresh=args.refresh, client=client)
            print(json.dumps({'batch': start // 25 + 1, 'companies': len(codes[start:start+25]),
                'responses': len(results), 'success': sum(r.get('response', {}).get('errorcode') == 0 for r in results),
                'cache_hits': sum(r.get('cache_hit', False) for r in results)}), flush=True)
    else:
        codes = [c for c in universe['codes'] if not c.endswith('.NQ')]
        for index, code in enumerate(codes):
            checks = crosscheck_tushare([code], universe['periods'], FOLDER / 'tushare', refresh=args.refresh)
            print(json.dumps({'completed': index + 1, 'total': len(codes), 'code': code,
                'records': sum('row' in c for c in checks), 'errors': [c.get('reason') for c in checks if c.get('status') == 'unavailable']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
