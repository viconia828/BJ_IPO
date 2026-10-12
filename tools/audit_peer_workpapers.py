"""Audit saved evidence and the whole workpaper universe without network access."""
from __future__ import annotations
from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'data/peer_research'


def read(path):
    return json.loads(path.read_text('utf-8'))


def main():
    catalog=read(FOLDER/'catalog.json'); universe=read(FOLDER/'universe.json'); checks=read(FOLDER/'checks.json')
    assert hashlib.sha256((ROOT/universe['source']).read_text('utf-8').encode('utf-8')).hexdigest()==universe['source_sha256']
    assert set(catalog['companies'])==set(universe['codes'])
    assert not catalog['summary']['missing_mcp_jobs']
    assert catalog['summary']['mcp_jobs_saved']==catalog['summary']['mcp_jobs_expected']
    assert catalog['summary']['historical_pit_ready']==0
    assert catalog['summary']['pricing_changed'] is False
    evidence={r['path']:r['sha256'] for r in catalog['evidence']}
    for ref,sha in evidence.items():
        path=(ROOT/ref).resolve()
        assert path.is_relative_to(ROOT) and path.is_file(),ref
        assert hashlib.sha256(path.read_bytes()).hexdigest()==sha,ref
    fields=Counter(); failed_jobs=[]; missing_periods=[]; financial_keys=set(); company_map={}
    requested={f'{p[:4]}-{p[4:6]}-{p[6:]}' for p in universe['periods']}
    for code, entry in catalog['companies'].items():
        company=read(FOLDER/entry['path']);company_map[code]=company
        assert company['code']==code and company['diagnostics']['pricing_eligible'] is False
        assert company['diagnostics']['as_of']==catalog['as_of']
        assert not company['diagnostics']['cycle']['normalization_approved']
        found=set()
        for record in company['financials']:
            key=(code,record['report_period'])
            assert key not in financial_keys and record['code']==code
            financial_keys.add(key);found.add(record['report_period'])
            assert record['report_period'] in requested
            assert record['historical_pit_eligible'] is False
            if record['published_at']:
                assert record['published_at']>record['report_period']
            for metric,value in record['values'].items():
                assert math.isfinite(value['value']) and value['rounding_tolerance']>=0
                assert value['evidence'] in evidence
                for observation in value['observations']:
                    assert observation['evidence'] in evidence
                if metric in {'accounts_receivable','inventory','equity_parent','total_assets'}:
                    assert value['period_basis']=='instant'
                fields[metric]+=1
        missing_periods += [{'code':code,'period':period} for period in sorted(requested-found)]
        assert (FOLDER/entry['path']).with_suffix('.md').exists()
    pair_count=0
    for code, case in universe['cases'].items():
        paper=read(FOLDER/f'workpapers/issuers/{code}.json')
        assert set(p['peer'] for p in paper['pairs'])==set(case['candidate_codes'])
        assert all(not p['core_eligible'] for p in paper['pairs'])
        assert not paper['pricing_eligible'] and not paper['fundamental_anchor_changed']
        pair_count+=len(paper['pairs'])
    assert pair_count==catalog['summary']['matching_cards']
    comparisons=read(FOLDER/'independent_crosschecks.json')
    for comparison in comparisons:
        assert math.isclose(comparison['tushare']-comparison['ifind'],comparison['difference'],abs_tol=.01)
        if not comparison['consistent']:
            record=next(r for r in company_map[comparison['code']]['financials'] if r['report_period']==comparison['report_period'])
            assert record['values'][comparison['metric']]['conflict'] is True
    for path in (FOLDER/'mcp').glob('*.json'):
        result=read(path).get('result',{})
        for block in result.get('content',[]):
            if block.get('type')=='text':
                try: parsed=json.loads(block['text'])
                except ValueError:
                    failed_jobs.append(path.name);continue
                if parsed.get('code')!=1:failed_jobs.append(path.name)
        if result.get('isError'):failed_jobs.append(path.name)
    for review in read(FOLDER/'precision_reviews.json')['reviews']:
        assert review['verified'] and review['unit']=='万元' and review['decimals']==2
        source=ROOT/review['source_file']
        assert hashlib.sha256(source.read_bytes()).hexdigest()==review['source_file_sha256']
        excerpt=read(ROOT/review['excerpt'])
        assert excerpt['pdf_page']==review['pdf_page']
        assert f'{review["display_value_cny"]/10000:,.2f}' in excerpt['page_text']
    for guidance in read(FOLDER/'management_guidance.json')['guidance']:
        assert not guidance['pricing_eligible'] and not guidance['historical_pit_eligible']
        assert guidance['published_at'] is None
        for low,high in guidance['values_cny'].values():assert 0<low<=high
    result={'status':'passed','as_of':catalog['as_of'],'companies':len(company_map),
        'evidence_hashes_verified':len(evidence),'financial_records':len(financial_keys),'matching_cards':pair_count,
        'universe_source_normalized_hash_verified':True,
        'field_coverage':dict(sorted(fields.items())),'missing_company_periods':missing_periods,
        'mcp_failed_files':sorted(set(failed_jobs)),
        'unresolved_unique_fields':len({(c['code'],c['report_period'],c['metric']) for c in comparisons if not c['consistent']}),
        'warnings_by_message':dict(Counter(w['warning'] for w in checks['warnings']))}
    assert not failed_jobs,failed_jobs
    (FOLDER/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'missing_company_periods','warnings_by_message'}},ensure_ascii=False))


if __name__=='__main__':main()
