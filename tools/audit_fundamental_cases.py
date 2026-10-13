"""Audit source chains, four-case coverage and frozen sentiment implementation."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
from ifind_helper import now_iso
from peer_fundamentals import iso_day
FOLDER = ROOT / 'data/fundamental_cases'


def read(path):
    return json.loads(path.read_text('utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_refs(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {'evidence', 'match_evidence'} and isinstance(value, list):
                yield from (v for v in value if isinstance(v, str) and not iso_day(v))
            yield from collect_refs(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from collect_refs(value)


def core_hashes(source):
    names = {'_weight', '_structural_normalize', '_estimate', '_state', 'evaluate'}
    return {node.name: hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
            for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name in names}


def main():
    errors, warnings, hashes = [], [], {}
    index = read(FOLDER / 'index.json')
    code_checks = {}
    for code, entry in index['items'].items():
        path = ROOT / entry['path']
        if digest(path) != entry['sha256']:
            errors.append('case hash ' + code)
        case = read(path)
        if case.get('historical_pit_eligible') is not False:
            errors.append('false PIT status ' + code)
        candidate_codes = [p['code'] for p in case['peers']]
        if len(candidate_codes) != len(set(candidate_codes)):
            errors.append('duplicate peer ' + code)
        for reference in sorted(set(collect_refs(case))):
            source = ROOT / reference
            if not source.is_file():
                errors.append('missing source ' + reference)
            else:
                hashes[reference] = digest(source)
        code_checks[code] = {'candidates': len(candidate_codes), 'primary_overrides': len(case['primary_overrides']),
                             'normalization_approved': case['cycle']['normalization_approved']}
    for path in (FOLDER/'evidence').glob('*.json'):
        saved = read(path)
        source = ROOT / saved['source_file']
        if source.is_file():
            if digest(source) != saved['source_sha256']:
                errors.append('PDF changed ' + saved['source_file'])
        else:
            warnings.append('PDF not present; excerpt retained: ' + saved['source_file'])
        hashes[path.relative_to(ROOT).as_posix()] = digest(path)
    for path in list((FOLDER/'raw').rglob('*.json')) + list((FOLDER/'inputs').glob('*.json')):
        hashes[path.relative_to(ROOT).as_posix()] = digest(path)
    before = subprocess.check_output(['git', 'show', 'ad515ba04e0d7a7d99d7a7a2dbd038279b8865b4:code/quality_sentiment.py'], cwd=ROOT).decode('utf-8')
    unchanged = core_hashes(before) == core_hashes((ROOT/'code/quality_sentiment.py').read_text('utf-8'))
    if not unchanged:
        errors.append('sentiment algorithm changed during fundamental ablation')
    failed_mcp, success_mcp = [], []
    for path in (FOLDER/'raw').glob('mcp_*.json'):
        result = read(path).get('result', {})
        good = False
        for block in result.get('content', []):
            if block.get('type') == 'text':
                try: good = good or json.loads(block['text']).get('code') == 1
                except (ValueError, AttributeError): pass
        (success_mcp if good else failed_mcp).append(path.name)
    output = {'status': 'passed' if not errors else 'failed', 'audited_at': now_iso(), 'errors': errors,
              'warnings': warnings, 'cases': code_checks, 'evidence_hashes': hashes,
              'sentiment_core_unchanged': unchanged, 'sentiment_core_ast_sha256': core_hashes(before),
              'mcp_supplement_success': success_mcp, 'mcp_supplement_failed': failed_mcp,
              'historical_pit_ready': False, 'mode': 'shadow'}
    (FOLDER/'audit.json').write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in output.items() if k not in {'evidence_hashes', 'sentiment_core_ast_sha256'}}, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
