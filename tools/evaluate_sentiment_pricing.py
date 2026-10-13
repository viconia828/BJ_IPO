"""Evaluate fixed feedback rules with identical F, cohorts, and interval widths."""
from pathlib import Path
import hashlib
import json
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'code'), str(ROOT/'tools')]
import config_loader
import sentiment_pricing as pricing
from evaluate_recent_fundamental_cases import install, WARNINGS
from prepare_sentiment_pricing_protocol import FOLDER, verify_inputs


def read(path):
    return json.loads(path.read_text('utf-8'))


def write(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def metrics(rows, rule):
    usable = [r for r in rows if r['models'][rule]['available']]
    if not usable: return {'count':0,'cohort_count':len(rows)}
    errors = [r['models'][rule]['price_error_pct'] for r in usable]
    return {'count':len(usable),'cohort_count':len(rows),
            'positive_price_error_pct':statistics.mean(max(v,0) for v in errors),
            'negative_price_error_pct':statistics.mean(max(-v,0) for v in errors),
            'price_mape_pct':statistics.mean(abs(v) for v in errors),
            'gain_mae_pp':statistics.mean(abs(r['models'][rule]['gain_error_pp']) for r in usable),
            'over_10_count':sum(v>10 for v in errors),'under_10_count':sum(v< -10 for v in errors),
            'fixed_10pct_hits':sum(r['models'][rule]['hit'] for r in usable)}


def main():
    protocol = read(FOLDER/'protocol.json')
    line_ending_equivalent = verify_inputs(protocol)
    constants = dict(window=pricing.WINDOW,reference_window=pricing.REFERENCE_WINDOW,
                     minimum_groups=pricing.MIN_GROUPS,minimum_reference_groups=pricing.MIN_REFERENCE_GROUPS,
                     prior_groups=pricing.PRIOR_GROUPS,clip_log=pricing.CLIP,
                     maximum_correction_log=pricing.MAX_CORRECTION)
    assert constants == protocol['constants'], 'Rule changed after protocol freeze'
    params = config_loader.load_params()
    assert params['quality_sentiment_mode'] == 'shadow' and params['price_range_width'] == .1
    folder = ROOT/'data/fundamental_cases'
    index = read(folder/'index.json')['items']
    cases = {c:read(ROOT/v['path']) for c,v in index.items()}
    inputs = {c:read(folder/'inputs'/f'{c}.json') for c in index}
    history = [install(r,cases,inputs) for r in read(folder/'inputs/recent_history.json')['items']]
    previous = {r['code']:r['models']['ttm_expanded'] for r in read(folder/'recent_replay.json')['rows']}
    rows, unscored = [], []
    for target in history:
        actual, issue = target.get('AVERAGE_PRICE'), target.get('ISSUE_PRICE')
        if not actual or not issue:
            unscored.append({'code':target['SECURITY_CODE'],'reason':'缺少首日成交均价或发行价，无法评分'})
            continue
        results = pricing.evaluate_all(target,history,params,as_of=target['LISTING_DATE'])
        baseline = results['baseline']
        code = target['SECURITY_CODE']
        if code in previous:
            assert baseline['available'] == previous[code]['available']
            if baseline['available']:
                assert abs(baseline['sentiment']['center']-previous[code]['center']) < 1e-10
        models = {}
        for rule, result in results.items():
            sentiment = result['sentiment']
            center = sentiment.get('center')
            models[rule] = {'available':result['available'],'center':center,
                'low':sentiment.get('low'),'high':sentiment.get('high'),
                'price_error_pct':(center/actual-1)*100 if result['available'] else None,
                'gain_error_pp':(center-actual)/issue*100 if result['available'] else None,
                'hit':sentiment['low']<=actual<=sentiment['high'] if result['available'] else None,
                'price_change_pct':result.get('pricing_response',{}).get('price_change_pct',0) if result['available'] else None,
                'reason':sentiment.get('reason')}
            assert result['anchor'] == baseline['anchor'], 'Fundamental changed'
            assert result['available'] == baseline['available'], 'Coverage changed'
        rows.append({'code':code,'name':target.get('SECURITY_NAME_ABBR'),'date':target['LISTING_DATE'],
            'actual_vwap':actual,'issue':issue,'fundamental':baseline['anchor'].get('center'),
            'baseline_state':baseline['state']['code'],'cooling':baseline['state']['code'] in WARNINGS,
            'models':models,'signals':results['combined']['pricing_response']['signals']})
    latest = [r for r in rows if r['code'] in protocol['recent_codes']]
    earlier = [r for r in rows if r['date'] < '2026-09-01']
    cohorts = {'all':rows,'earlier':earlier,'recent10':latest,
               'cooling_all':[r for r in rows if r['cooling']],
               'cooling_recent':[r for r in latest if r['cooling']]}
    rules = ['baseline',*pricing.RULES]
    scores = {name:{rule:metrics(group,rule) for rule in rules} for name,group in cohorts.items()}
    b, c = scores['recent10']['baseline'],scores['recent10']['combined']
    claims = {'recent_positive_error_improved':c['positive_price_error_pct']<b['positive_price_error_pct'],
              'recent_price_mape_improved':c['price_mape_pct']<b['price_mape_pct'],
              'earlier_price_mape_improved':scores['earlier']['combined']['price_mape_pct']<scores['earlier']['baseline']['price_mape_pct'],
              'recent_underestimate_not_worse':c['negative_price_error_pct']<=b['negative_price_error_pct']}
    output = {'schema':'sentiment_response_replay_v1','version':pricing.VERSION,'mode':'shadow',
        'primary_rule':protocol['primary_rule'],'parameter_search':False,'formal_promotion':False,
        'input_hashes_verified':len(protocol['input_sha256']),
        'line_ending_equivalent_inputs':line_ending_equivalent,
        'protocol_sha256':hashlib.sha256((FOLDER/'protocol.json').read_bytes()).hexdigest(),
        'source_sha256_lf':{p:hashlib.sha256((ROOT/p).read_text('utf-8').replace('\r\n','\n').encode()).hexdigest()
            for p in ['code/sentiment_pricing.py','tools/evaluate_sentiment_pricing.py']},
        'metrics':scores,'diagnostic_claims':claims,'rows':rows,'unscored':unscored,'frozen_input_count':len(history),
        'limitations':protocol['limitations']+['组合规则为本轮预先指定主候选，不能把单项最优的回放表现当作新验证结果']}
    write(FOLDER/'replay.json',output)
    labels = {'baseline':'冻结基本面＋原情绪','expectation':'仅预测兑现反馈','aftermarket':'仅承接变化','combined':'组合响应（主候选）'}
    fmt = lambda x: '—' if x is None else f'{x:.2f}'
    lines = ['# 情绪反馈进入首日定价：固定规则实验','',
        '冻结2a501dc的基本面、宽可比池、历史覆盖及情绪基准，仅改变情绪响应。不是与行业修正前的最早模型比较。', '',
        f"最近10只中共同可计算{b['count']}只。主候选高估部分均值{b['positive_price_error_pct']:.2f}%→{c['positive_price_error_pct']:.2f}%；价格MAPE {b['price_mape_pct']:.2f}%→{c['price_mape_pct']:.2f}%；低估部分均值{b['negative_price_error_pct']:.2f}%→{c['negative_price_error_pct']:.2f}%。", '',
        '预测兑现偏差与承接变化都会真实改变情绪价格，基本面区间不变。先按上市日组平均，再以n/(n+3)收缩；总调整限制约-20%至+25%。上涨与下跌对称，不因“降温”标签直接普遍压价。', '',
        '## 同样本结果','',
        '| 区段 | 规则 | 可用/总数 | 高估部分均值% | 低估部分均值% | 价格MAPE% | 涨幅MAE/百分点 | 高估>10% | 低估>10% | ±10%命中 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for cohort, title in [('earlier','9月前'),('recent10','最近10只'),('all','全部'),('cooling_all','固定基准降温'),('cooling_recent','近期基准降温')]:
        for rule in rules:
            m=scores[cohort][rule]
            lines.append(f"| {title} | {labels[rule]} | {m['count']}/{m['cohort_count']} | {fmt(m.get('positive_price_error_pct'))} | {fmt(m.get('negative_price_error_pct'))} | {fmt(m.get('price_mape_pct'))} | {fmt(m.get('gain_mae_pp'))} | {m.get('over_10_count','—')} | {m.get('under_10_count','—')} | {m.get('fixed_10pct_hits','—')} |")
    lines += ['', '高估部分均值对负误差记0，低估部分均值对正误差记0，两者相加为价格MAPE。各组保持相同股票及区间宽度。涨幅误差分母为发行价，价格误差分母为实际首日成交均价。', '',
        '## 最近10只逐股','', '| 股票 | F | 实际均价 | 原情绪 | 兑现反馈 | 承接变化 | 组合 | 组合误差% | 组合调幅% |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in latest:
        m=r['models']
        lines.append(f"| {r['name']} {r['code']} | {fmt(r['fundamental'])} | {fmt(r['actual_vwap'])} | {fmt(m['baseline']['center'])} | {fmt(m['expectation']['center'])} | {fmt(m['aftermarket']['center'])} | {fmt(m['combined']['center'])} | {fmt(m['combined']['price_error_pct'])} | {fmt(m['combined']['price_change_pct'])} |")
    lines += ['', '## 信号与边界', '',
        '- 兑现反馈使用每只历史股票上市前、同一冻结模型的预测；是模型误差反馈，不宣称误差全由情绪造成。',
        '- D2/D3分别对比更早同期限常态，防止把长期普遍存在的上市后折价重复扣除；只读取严格早于目标上市日的收盘。',
        '- 缺少至少两个近期上市日组时该项不调整，原预测保留；没有F或原预测时仍不可用。',
        '- 主候选是组合响应，单项仅作消融。未按最近10只择优，也未做参数搜索。',
        '- 固定基准降温子集与上一轮proxy定义不同：本轮基准为同宽池的ttm_expanded，不混用两份报告的子集指标。', '',
        '## 决定与限制','',
        '组合响应改善了近期高估及价格误差，但宇特、华汇、杰锋等反例仍在。仅兑现反馈近期表现较好，仅承接变化在9月前较好，不能在看完结果后把其中一项改称事先选定的赢家。', '',
        f"冻结输入{len(history)}只，其中{len(unscored)}只缺首日均价或发行价不评分；已在replay.json逐只列明，其余缺F或历史不足仍保留在总数中。", '',
        '保持研究/影子模式，不自动接管正式价格。近期10只已有结果暴露，更早区段也属于历史重建；任何改善只作为机制证据，需要后续真实前向记录。', '',
        *['- '+v for v in output['limitations']], '',
        '复现：`python tools/evaluate_sentiment_pricing.py`。固定协议见protocol.json，逐股时点、输入哈希、单项信号与评分见replay.json。']
    (FOLDER/'review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'metrics':scores,'diagnostic_claims':claims},ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
