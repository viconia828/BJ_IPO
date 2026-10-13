"""Freeze feedback rules and existing inputs before computing candidate results."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code')]
from ifind_helper import now_iso
import sentiment_pricing as response

FOLDER = ROOT/'data/sentiment_pricing'
BASELINE_COMMIT = '2a501dcb30370138666036a8753dfe241f7e72fc'


def verify_inputs(protocol):
    """Raw evidence is exact; tracked source/config may differ only by CRLF."""
    equivalent = []
    for path, digest in protocol['input_sha256'].items():
        current = (ROOT/path).read_bytes()
        if hashlib.sha256(current).hexdigest() == digest:
            continue
        if Path(path).suffix in {'.py','.txt'}:
            committed = subprocess.check_output(['git','show',f"{protocol['baseline_commit']}:{path}"],cwd=ROOT)
            if current.replace(b'\r\n',b'\n') == committed.replace(b'\r\n',b'\n'):
                equivalent.append(path); continue
        raise ValueError('Frozen input changed: '+path)
    return equivalent


def main():
    FOLDER.mkdir(exist_ok=True)
    path = FOLDER/'protocol.json'
    if path.exists():
        print('Protocol already frozen; no overwrite.'); return
    files = subprocess.check_output(['git','ls-tree','-r','--name-only',BASELINE_COMMIT,
        'data/fundamental_cases/cases','data/fundamental_cases/inputs',
        'data/fundamental_cases/index.json','data/fundamental_cases/recent_protocol.json',
        'code/quality_sentiment.py','code/fundamental_anchor.py','策略参数.txt'],cwd=ROOT).decode().splitlines()
    # Git may quote non-ASCII paths; list the parameter path explicitly instead.
    files = [p for p in files if not p.startswith('"')]
    if '策略参数.txt' not in files: files.append('策略参数.txt')
    hashes = {}
    for filename in files:
        committed = subprocess.check_output(['git','show',f'{BASELINE_COMMIT}:{filename}'],cwd=ROOT)
        current = (ROOT/filename).read_bytes()
        assert committed.replace(b'\r\n',b'\n') == current.replace(b'\r\n',b'\n'), filename
        hashes[filename] = hashlib.sha256(current).hexdigest()
    recent = json.loads((ROOT/'data/fundamental_cases/recent_protocol.json').read_text('utf-8'))['codes']
    protocol = {'schema':'sentiment_response_protocol_v1','frozen_at':now_iso(),
        'baseline_commit':BASELINE_COMMIT,'input_sha256':hashes,
        'fundamentals_policy':'保持宽可比池、盈利、匹配权重及历史覆盖不变；只改情绪响应',
        'primary_rule':'combined','ablation_rules':['expectation','aftermarket'],
        'constants':{'window':response.WINDOW,'reference_window':response.REFERENCE_WINDOW,
            'minimum_groups':response.MIN_GROUPS,'minimum_reference_groups':response.MIN_REFERENCE_GROUPS,
            'prior_groups':response.PRIOR_GROUPS,'clip_log':response.CLIP,
            'maximum_correction_log':response.MAX_CORRECTION},
        'rules':[
            '预测兑现：最近3个实际上市日组，首日均价相对冻结基准逐时点预测的log误差；组内等权、组间等权，至少2组且覆盖2/3，不向前补齐',
            '承接变化：严格早于目标日期的D2/D3收盘相对首日均价log收益，减去前20个上市日组中相同D2/D3期限常态；各期限至少5组参照',
            '异常值先截断在正负ln2，误差及承接变化分别乘n/(n+3)，组合相加后截断正负ln1.25，价格响应约-20%至+25%',
            '缺少任一信号则该项响应为0并显示不可用；基本面/基准预测缺失不创造价格；上涨下跌对称，不用行业细分分桶',
            '固定±10%区间；原始首日涨幅不用作净情绪排名；只改变最终情绪倍数，不修改基本面F'],
        'recent_codes':recent,'earlier_block':'上市日早于2026-09-01',
        'cooling_subset':'冻结基准状态cooling_watch/cooling_confirmed/weak_aftermarket；不按候选输出重选',
        'metrics':['positive_price_error_pct','negative_price_error_pct','price_mape_pct','gain_mae_pp',
                   'over_10_count','under_10_count','fixed_10pct_hits'],
        'selection_policy':'不做参数搜索，不根据最新10只表现替换主候选；单项仅用于诊断',
        'promotion_policy':'研究及shadow；须检验高估改善是否以低估恶化换取，不自动升级正式定价',
        'historical_pit_eligible':False,
        'limitations':['最近10只结果已知，属于诊断样本；更早区段也是历史重建，不是真正独立留出',
                       '本轮未加入大盘/行业日度收益，避免缺少统一时点资料时制造修正',
                       '原双区间模型不是本轮因果对照，本轮对照为同F同覆盖的当前情绪基准']}
    with path.open('x',encoding='utf-8',newline='\n') as handle:
        json.dump(protocol,handle,ensure_ascii=False,indent=2,allow_nan=False); handle.write('\n')
    print('Frozen',len(hashes),'unchanged inputs; three fixed response variants')


if __name__ == '__main__': main()
