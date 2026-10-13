"""Freeze sequential role and timing experiments; never overwrite a protocol."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code'),str(ROOT/'tools')]
from ifind_helper import now_iso
from prepare_sentiment_pricing_protocol import verify_inputs

FOLDER = ROOT/'data/sentiment_pricing/roles'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protocol(protocol):
    old = json.loads((ROOT/'data/sentiment_pricing/protocol.json').read_text('utf-8'))
    verify_inputs(old)
    for path,digest in protocol['frozen_sources'].items():
        assert sha(ROOT/path) == digest, 'Frozen comparison changed: '+path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['roles','timing'],default='roles')
    args=parser.parse_args()
    FOLDER.mkdir(parents=True,exist_ok=True)
    path=FOLDER/f'{args.stage}_protocol.json'
    if path.exists():
        print('Protocol exists; preserving original.'); return
    sources=['data/sentiment_pricing/protocol.json','data/sentiment_pricing/replay.json']
    if args.stage=='timing':
        assert (FOLDER/'roles_replay.json').exists(), 'Finish and inspect the role experiment first'
        sources+=['data/sentiment_pricing/roles/roles_protocol.json','data/sentiment_pricing/roles/roles_replay.json']
    protocol={'schema':'sentiment_roles_protocol_v1','stage':args.stage,'frozen_at':now_iso(),
        'reference_commit':'438d3937638e1b2d38437bf9efa9ca8aba3ca99a',
        'frozen_sources':{p:sha(ROOT/p) for p in sources},
        'unchanged':['基本面和宽池','旧情绪基准','最近3组信号原始取样','承接的同期限参照','±10%区间','约-20%至+25%调幅上限'],
        'role_rule':'兑现e决定方向；同向承接a补充sign(e)*min(abs(e),abs(a))，反向或缺失a只提示风险不调价；e不可用时不以a单独出价',
        'timing_rule':None if args.stage=='roles' else '最新两个实际上市日组均有兑现预测且各自原始log误差同向超过ln1.10时，使用这两组均值乘2/(2+3)；否则回到三组。上涨下跌对称，最新一组反向即解除两组模式；不向更早补缺失组',
        'timing_material_log':None if args.stage=='roles' else __import__('math').log(1.10),
        'primary_rule':'role_split' if args.stage=='roles' else 'role_adaptive',
        'control':'combined' if args.stage=='roles' else 'role_split',
        'sample_policy':'沿用全部63条有首日结果、共同预测48条及已知近期10条；缺失2条另列，时间顺序及群组不变',
        'subsets':'沿用基准降温；冲突子集使用旧e/a异号定义；第二步另外展示实际触发两组模式的集合，不用后续结果选股',
        'guardrails':{'earlier_underestimate_max_increase_pp':1.0,
                      'overall_mape_must_not_worsen':True,'recent_mape_must_not_worsen':True,
                      'cooling_positive_error_must_not_worsen':True},
        'decision':'只有主候选相对直接上一版通过全部保护条件才可替换默认影子展示；正式价格始终不变',
        'no_grid_search':True,'market_or_sector_data_added':False,'historical_pit_eligible':False,
        'limitation':'规则由已知失败案例启发，本轮全部回放属于再次使用既有样本，不是独立验证；未按结果选择参数'}
    verify_protocol(protocol)
    with path.open('x',encoding='utf-8',newline='\n') as out:
        json.dump(protocol,out,ensure_ascii=False,indent=2,allow_nan=False); out.write('\n')
    print('Frozen',args.stage,'protocol')


if __name__=='__main__': main()
