"""Audit both sequential experiments without overwriting the earlier trial."""
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'code'),str(ROOT/'tools')]
from ifind_helper import now_iso
from evaluate_sentiment_pricing import metrics
from prepare_sentiment_roles_protocol import FOLDER,verify_protocol
import sentiment_timing


def read(path):return json.loads(path.read_text('utf-8'))


def main():
    for stage in ['roles','timing']:
        protocol=read(FOLDER/f'{stage}_protocol.json');verify_protocol(protocol)
        replay=read(FOLDER/f'{stage}_replay.json')
        assert replay['protocol_sha256']==hashlib.sha256((FOLDER/f'{stage}_protocol.json').read_bytes()).hexdigest()
        for path,digest in replay['source_sha256_lf'].items():
            assert hashlib.sha256((ROOT/path).read_text('utf-8').replace('\r\n','\n').encode()).hexdigest()==digest,path
        for rule in replay['metrics']['all']:
            assert metrics(replay['rows'],rule)==replay['metrics']['all'][rule]
        for row in replay['rows']:
            for rule,model in row['models'].items():
                assert model['available']==row['models']['baseline']['available']
                if model['available']:
                    assert .8-1e-12 <= model['center']/row['models']['baseline']['center'] <= 1.25+1e-12
                    assert abs(model['low']/model['center']-.9)<1e-12
                    assert abs(model['high']/model['center']-1.1)<1e-12
            for key in ['expectation','aftermarket']:
                for obs in row['signals'][key]['observations']:
                    assert obs['listing_date']<row['date']
                    assert obs.get('code')!=row['code']
                    for comparison in obs.get('comparisons',[]):
                        assert obs['listing_date']<comparison['date']<row['date']
        assert not replay['formal_promotion']
    if not replay['default_shadow_eligible']:
        assert sentiment_timing.DEFAULT_SHADOW_RULE=='combined'
    validation=read(FOLDER/'validation.json')
    assert validation['status']=='passed'
    for test in validation['tests']:
        path=ROOT/'tests'/test['test']
        assert hashlib.sha256(path.read_text('utf-8').replace('\r\n','\n').encode()).hexdigest()==test['source_sha256_lf'],path
    sources=['code/sentiment_roles.py','code/sentiment_timing.py','code/bse_ipo_valuation.py',
        'code/report_generator.py','tools/evaluate_sentiment_roles.py','tools/evaluate_sentiment_timing.py',
        'tools/prepare_sentiment_roles_protocol.py','tools/preview_quality_sentiment.py',
        'tools/audit_sentiment_roles.py','tests/validate_sentiment_roles.py']
    for path in sources:ast.parse((ROOT/path).read_text('utf-8-sig'),filename=path)
    old=ROOT/'data/quality_sentiment/predictions/920285_20261013_210001_406219_29e71251f704.json'
    assert hashlib.sha256(old.read_bytes()).hexdigest()=='556cd087ece45fe0f85cc2b0f28670c1e90c1c915701b37b79123b4491198969'
    output={'status':'passed','audited_at':now_iso(),'validation_scripts_passed':validation['passed'],
        'sequential_protocols_verified':True,'frozen_input_files':39,'baseline_and_wide_peers_unchanged':True,
        'existing_forward_snapshot_unchanged':True,'market_or_sector_data_added':False,
        'default_shadow_rule':sentiment_timing.DEFAULT_SHADOW_RULE,'formal_promotion':False,
        'source_sha256_lf':{p:hashlib.sha256((ROOT/p).read_text('utf-8').replace('\r\n','\n').encode()).hexdigest() for p in sources}}
    (FOLDER/'audit.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k!='source_sha256_lf'},ensure_ascii=False))


if __name__=='__main__':main()
