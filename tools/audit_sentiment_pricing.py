"""Check frozen F, unchanged baseline, source/score provenance and price coverage."""
from pathlib import Path
import ast
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code'),str(ROOT/'tools')]
from ifind_helper import now_iso
from prepare_sentiment_pricing_protocol import FOLDER, verify_inputs
from evaluate_sentiment_pricing import metrics
from sentiment_pricing import RULES


def main():
    read = lambda p: json.loads(p.read_text('utf-8'))
    protocol = read(FOLDER/'protocol.json')
    equivalent = verify_inputs(protocol)
    replay = read(FOLDER/'replay.json')
    assert replay['protocol_sha256'] == hashlib.sha256((FOLDER/'protocol.json').read_bytes()).hexdigest()
    for path, digest in replay['source_sha256_lf'].items():
        assert hashlib.sha256((ROOT/path).read_text('utf-8').replace('\r\n','\n').encode()).hexdigest() == digest, path
    for rule in ['baseline',*RULES]:
        assert metrics(replay['rows'],rule) == replay['metrics']['all'][rule]
    for row in replay['rows']:
        base = row['models']['baseline']
        for rule in RULES:
            model = row['models'][rule]
            assert base['available'] == model['available']
            if model['available']:
                assert .8-1e-12 <= model['center']/base['center'] <= 1.25+1e-12
                assert abs(model['low']/model['center']-.9)<1e-12
                assert abs(model['high']/model['center']-1.1)<1e-12
        for signal in ['expectation','aftermarket']:
            for obs in row['signals'][signal]['observations']:
                assert obs['listing_date'] < row['date'] and obs['code'] != row['code']
                for comparison in obs.get('comparisons',[]):
                    assert obs['listing_date'] < comparison['date'] < row['date']
    old_snapshot = ROOT/'data/quality_sentiment/predictions/920285_20261013_210001_406219_29e71251f704.json'
    assert hashlib.sha256(old_snapshot.read_bytes()).hexdigest() == '556cd087ece45fe0f85cc2b0f28670c1e90c1c915701b37b79123b4491198969'
    validation = read(FOLDER/'validation.json')
    assert validation['status'] == 'passed'
    for test in validation['tests']:
        source = (ROOT/'tests'/test['test']).read_text('utf-8').replace('\r\n','\n').encode()
        assert hashlib.sha256(source).hexdigest() == test['source_sha256_lf'], test['test']
    sources = ['code/sentiment_pricing.py','code/bse_ipo_valuation.py','code/report_generator.py',
               'tools/prepare_sentiment_pricing_protocol.py','tools/evaluate_sentiment_pricing.py',
               'tools/audit_sentiment_pricing.py','tools/preview_quality_sentiment.py','tests/validate_sentiment_pricing.py']
    for path in sources: ast.parse((ROOT/path).read_text('utf-8-sig'),filename=path)
    output = {'status':'passed','audited_at':now_iso(),'frozen_inputs_verified':len(protocol['input_sha256']),
        'line_ending_equivalent_inputs':equivalent,'unchanged_baseline_and_fundamentals':True,
        'unchanged_forward_snapshot':True,'validation_scripts_passed':validation['passed'],
        'full_cohort':len(replay['rows']),'unscored':replay['unscored'],'mode':'shadow','formal_promotion':False,
        'source_sha256_lf':{p:hashlib.sha256((ROOT/p).read_text('utf-8').replace('\r\n','\n').encode()).hexdigest() for p in sources}}
    (FOLDER/'audit.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k not in {'source_sha256_lf','unscored'}},ensure_ascii=False))


if __name__ == '__main__': main()
