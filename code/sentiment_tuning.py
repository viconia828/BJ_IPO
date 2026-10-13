"""Console tuning of the default sentiment shadow, with fixed fundamentals."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics

import quality_sentiment as qs
import quality_sentiment_data as inputs
import sentiment_pricing as pricing
from sentiment_timing import DEFAULT_SHADOW_RULE

VERSION = 'sentiment_console_v1'
PARAMETER = 'quality_sentiment_half_life_days'
HALF_LIVES = (4.0, 8.0, 12.0, 16.0)
COOLING = {'cooling_watch', 'cooling_confirmed', 'weak_aftermarket'}
LABELS = {'legacy': '当前正式价', 'original': '原个股情绪',
          'current': '当前组合影子', 'candidate': '训练选定候选'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode('utf-8')).hexdigest()


def prepare_history(dataset, *, root=inputs.ROOT, cutoff=None):
    """Use refreshed local outcomes and reviewed broad peers; mark reconstruction."""
    cutoff = qs.day(cutoff) if cutoff is not None else qs.live_cutoff()
    if cutoff is None:
        raise ValueError('无效回放截止日')
    history, corrections = inputs.normalize_history(deepcopy(dataset.get('items') or []), root)
    usable, excluded, sources, seen = [], [], {}, set()
    for row in history:
        code, day = str(row.get('SECURITY_CODE') or ''), qs.day(row.get('LISTING_DATE'))
        if not day or day >= cutoff:
            excluded.append({'code': code, 'reason': '上市日期缺失或尚未完成首日交易'})
            continue
        if code in seen:
            raise ValueError(f'回放代码重复：{code}')
        seen.add(code)
        case = row.get('fundamental_case') or {}
        if case.get('integrity_error'):
            raise ValueError(f'{code}: {case["integrity_error"]}')
        if case:
            path = root / 'data/fundamental_cases/inputs' / f'{code}.json'
            saved = json.loads(path.read_text('utf-8'))
            if (str(saved.get('SECURITY_CODE')) != code
                    or qs.number(saved.get('ISSUE_PRICE')) != qs.number(row.get('ISSUE_PRICE'))
                    or qs.day(saved.get('LISTING_DATE')) != day):
                raise ValueError(f'{code}: 已复核可比输入与回放发行批次不一致')
            row['comparable_data'] = saved['comparable_data']
            row['_fundamental_reconstruction'] = True
            row['_fundamental_stage'] = 'matched'
            sources[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        usable.append(row)
    return usable, {'as_of_exclusive': str(cutoff), 'corrections': corrections,
                    'excluded': excluded, 'reviewed_peer_sources': sources,
                    'history_sha256': digest(usable),
                    'context': '历史重建；已复核宽池固定，不是新的真实样本外验证'}


def summarize_price(center, low, high, row, reason=None):
    center, actual, issue = (qs._positive(v) for v in
                             (center, row.get('AVERAGE_PRICE'), row.get('ISSUE_PRICE')))
    scored = bool(center and actual and issue)
    return {'available': center is not None, 'scored': scored, 'center': center,
            'low': low, 'high': high,
            'price_error_pct': (center / actual - 1) * 100 if scored else None,
            'gain_error_pp': (center - actual) / issue * 100 if scored else None,
            'hit': low <= actual <= high if scored and low is not None and high is not None else None,
            'reason': reason if center is None else (None if scored else '缺发行价或首日均价，不评分')}


def score(rows, model):
    samples = [r['models'][model] for r in rows if r['models'][model]['scored']]
    errors = [r['price_error_pct'] for r in samples]
    mean = lambda values: statistics.mean(values) if values else None
    return {'count': len(samples), 'total': len(rows),
            'price_mape_pct': mean([abs(e) for e in errors]),
            'over_pct': mean([max(e, 0) for e in errors]),
            'under_pct': mean([max(-e, 0) for e in errors]),
            'gain_mae_pp': mean([abs(r['gain_error_pp']) for r in samples]),
            'hits': sum(r['hit'] is True for r in samples)}


def split_codes(history, train_ratio, min_train_samples):
    if not 0 < train_ratio < 1 or min_train_samples < 1:
        raise ValueError('训练比例须在0与1之间，最少训练样本须为正数')
    known = [r for r in history if qs._positive(r.get('AVERAGE_PRICE')) and qs._positive(r.get('ISSUE_PRICE'))]
    dates = sorted({str(qs.day(r['LISTING_DATE'])) for r in known})
    cut = max(1, min(len(dates) - 1, int(len(dates) * train_ratio)))
    training_dates = set(dates[:cut])
    train = {r['SECURITY_CODE'] for r in known if str(qs.day(r['LISTING_DATE'])) in training_dates}
    validation = {r['SECURITY_CODE'] for r in known} - train
    # Insufficient coverage is reported by the guard; never split a listing day.
    return train, validation, {r['SECURITY_CODE'] for r in known[-10:]}


def _run(history, params):
    results = {}
    for target in history:
        all_results = pricing.evaluate_all(target, history, params, as_of=target['LISTING_DATE'])
        results[target['SECURITY_CODE']] = (all_results['baseline'], all_results[DEFAULT_SHADOW_RULE])
    return results


def acceptance_guard(rows, *, min_train_samples=8):
    checks = {}
    for group, minimum in [('train', min_train_samples), ('validation', 5)]:
        selected = [r for r in rows if r['split'] == group]
        b, c = score(selected, 'current'), score(selected, 'candidate')
        checks[group + '_coverage'] = b['count'] >= minimum and c['count'] == b['count']
        checks[group + '_mape'] = (c['price_mape_pct'] is not None and b['price_mape_pct'] is not None
                                  and c['price_mape_pct'] <= b['price_mape_pct'] + 1e-10)
    for group, selected, field, tolerance in [
        ('all', rows, 'price_mape_pct', 0),
        ('recent10', [r for r in rows if r['recent']], 'price_mape_pct', 0),
        ('cooling', [r for r in rows if r['cooling']], 'over_pct', 0),
        ('earlier_under', [r for r in rows if not r['recent']], 'under_pct', 1),
    ]:
        b, c = score(selected, 'current'), score(selected, 'candidate')
        checks[group] = (b[field] is not None and c[field] is not None
                         and b['count'] == c['count'] and c[field] <= b[field] + tolerance + 1e-10)
    checks['same_stock_coverage'] = all(r['models']['current']['scored'] == r['models']['candidate']['scored'] for r in rows)
    return {'passed': all(checks.values()), 'checks': checks,
            'failed': [key for key, passed in checks.items() if not passed]}


def tune(history, params, *, train_ratio=.7, min_train_samples=8, progress=None):
    """Choose on training only, then validate that one candidate; no holdout rerank."""
    if DEFAULT_SHADOW_RULE != 'combined':
        raise ValueError('默认影子规则已改变，请先更新调参契约')
    if params.get('quality_sentiment_mode', 'shadow') != 'shadow':
        raise ValueError('情绪影子调参要求quality_sentiment_mode=shadow')
    current = qs._positive(params.get(PARAMETER, 8))
    if current is None:
        raise ValueError('情绪半衰期须为正数')
    frozen_params, frozen_inputs = digest(params), digest(history)
    train, validation, recent = split_codes(history, train_ratio, min_train_samples)
    current_results = _run(history, params)
    rows = []
    for target in history:
        code = target['SECURITY_CODE']
        original, response = current_results[code]
        models = {}
        for name, result in [('original', original), ('current', response)]:
            s = result['sentiment']
            models[name] = summarize_price(s.get('center'), s.get('low'), s.get('high'), target, s.get('reason'))
        rows.append({'code': code, 'name': target.get('SECURITY_NAME_ABBR'),
                     'date': target['LISTING_DATE'], 'actual_vwap': target.get('AVERAGE_PRICE'),
                     'fundamental': original['anchor'], 'models': models,
                     'split': 'train' if code in train else ('validation' if code in validation else 'unscored'),
                     'recent': code in recent, 'cooling': original['state']['code'] in COOLING,
                     'state': original['state'], 'response': response['pricing_response']})
    entries = []
    variants = sorted(set((*HALF_LIVES, current)))
    for i, value in enumerate(variants, 1):
        if progress:
            progress(i, len(variants), value)
        results = current_results if value == current else _run(history, {**params, PARAMETER: value})
        candidate_rows = []
        for target, row in zip(history, rows):
            code = row['code']
            result = results[code][1]
            if result['anchor'] != row['fundamental']:
                raise ValueError(f'{code}: 搜索改变了基本面锚')
            s = result['sentiment']
            model = summarize_price(s.get('center'), s.get('low'), s.get('high'), target, s.get('reason'))
            candidate_rows.append({**row, 'models': {**row['models'], 'candidate': model},
                                   'candidate_response': result['pricing_response']})
        metrics = score([r for r in candidate_rows if r['split'] == 'train'], 'candidate')
        entries.append({'half_life': value, 'training': metrics, 'rows': candidate_rows})
    # Prefer current settings on ties. Validation never selects a different candidate.
    best = min(entries, key=lambda e: (round(e['training']['price_mape_pct'], 10)
                                      if e['training']['price_mape_pct'] is not None else math.inf,
                                      e['half_life'] != current, e['half_life']))
    rows = best['rows']
    guard = acceptance_guard(rows, min_train_samples=min_train_samples)
    overrides = {PARAMETER: best['half_life']} if best['half_life'] != current and guard['passed'] else {}
    if frozen_inputs != digest(history) or frozen_params != digest(params):
        raise ValueError('搜索过程中输入发生变更')
    return {'schema': VERSION, 'rule': DEFAULT_SHADOW_RULE, 'version': pricing.VERSION,
            'mode': 'shadow', 'formal_promotion': False, 'parameter': PARAMETER,
            'current_half_life': current, 'selected_half_life': best['half_life'],
            'search_space': variants, 'selection': 'training_price_mape_only_then_one_candidate_guard',
            'train_ratio': train_ratio, 'min_train_samples': min_train_samples,
            'parameter_snapshot': deepcopy(params), 'history_sha256': frozen_inputs,
            'candidates': [{k: v for k, v in e.items() if k != 'rows'} for e in entries],
            'guard': guard, 'changed_overrides': overrides, 'rows': rows,
            'acceptance_status': 'pending' if overrides else 'no_change'}


def add_legacy_comparison(result, history, params):
    import param_tuning
    pool = param_tuning._build_recent_pool(history)
    targets = {r['SECURITY_CODE']: r for r in history}
    for row in result['rows']:
        target = targets[row['code']]
        final, *_ = param_tuning._evaluate_replay_prediction(target, params, pool, param_tuning.COMPOSITE_EVALUATION_SCOPE)
        row['models']['legacy'] = summarize_price(final.get('target_price') if final.get('available') else None,
            final.get('range_low'), final.get('range_high'), target, final.get('reason'))
    groups = {'all': result['rows'],
              'train': [r for r in result['rows'] if r['split'] == 'train'],
              'validation': [r for r in result['rows'] if r['split'] == 'validation'],
              'recent10': [r for r in result['rows'] if r['recent']],
              'cooling': [r for r in result['rows'] if r['cooling']]}
    result['metrics'] = {name: {model: score(rows, model) for model in LABELS} for name, rows in groups.items()}
    # Old composite has different availability; also show a strictly common set.
    common = [r for r in result['rows'] if all(r['models'][m]['scored'] for m in LABELS)]
    result['metrics']['common_all'] = {model: score(common, model) for model in LABELS}


def render(result):
    fmt = lambda value: '—' if value is None else f'{value:.2f}'
    lines = ['# 情绪组合影子自动调参', '',
             f"默认规则：{result['rule']} / {result['version']}；当前半衰期{result['current_half_life']:g}天，训练选定{result['selected_half_life']:g}天。",
             f"状态：{result['acceptance_status']}；保护条件：{'通过' if result['guard']['passed'] else '未通过'}。", '',
             '只搜索情绪半衰期；基本面、宽池、反馈规则和区间宽度固定。原个股情绪与当前组合是同一基本面；当前正式价仍是旧综合定价。', '',
             '按上市日整组切分，训练价格MAPE选出一个候选，后段仅检验，不再换选。历史资料经过重建，且近期样本已暴露，检验段也不宣称为新的样本外。', '',
             '每个价格误差=(预测价/实际首日成交均价−1)×100%；高估/低估分别把另一方向记0后取均值。首日缺均价不使用收盘替代。', '',
             '| 区段 | 模型 | 评分/总数 | 价格MAPE% | 高估% | 低估% | 涨幅MAE/百分点 | 同宽命中 |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    names = {'all': '全部', 'train': '训练', 'validation': '后段检验', 'recent10': '最近10只',
             'cooling': '当前基准降温', 'common_all': '四模型共同样本'}
    for group, models in result.get('metrics', {}).items():
        for model, m in models.items():
            lines.append(f"|{names[group]}|{LABELS[model]}|{m['count']}/{m['total']}|{fmt(m['price_mape_pct'])}|{fmt(m['over_pct'])}|{fmt(m['under_pct'])}|{fmt(m['gain_mae_pp'])}|{m['hits']}|")
    lines += ['', '不同模型覆盖可能不同；比较旧正式价与影子请使用“四模型共同样本”。候选与当前组合逐股覆盖必须一致。', '',
              '## 候选与检验', '', '| 半衰期/天 | 训练评分数 | 训练价格MAPE% |', '|---:|---:|---:|']
    for e in result['candidates']:
        lines.append(f"|{e['half_life']:g}|{e['training']['count']}|{fmt(e['training']['price_mape_pct'])}|")
    lines += ['', '未通过项：' + ('、'.join(result['guard']['failed']) or '无'), '',
              '保护条件：训练至少指定数量、后段至少5只；同股覆盖不减少；训练/后段/全部/最近10只MAPE不恶化，固定降温组高估不恶化，更早样本低估增加不超过1个百分点。', '',
              '## 逐股对照', '',
              '| 股票 | 分段 | 基本面F | 实际均价 | 正式价 | 原情绪 | 当前组合 | 候选 | 当前误差% | 候选误差% |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in result['rows']:
        m = r['models']
        values = [r['fundamental'].get('center'), r['actual_vwap'],
                  *[m[k]['center'] for k in LABELS], m['current']['price_error_pct'], m['candidate']['price_error_pct']]
        lines.append(f"|{r['name']} {r['code']}|{r['split']}|" + '|'.join(fmt(v) for v in values) + '|')
    lines += ['', '## 未评分与排除', '']
    for r in result['rows']:
        if not r['models']['current']['scored']:
            lines.append(f"- {r['code']}：{r['models']['current']['reason']}")
    for r in result.get('input_audit', {}).get('excluded', []):
        lines.append(f"- {r['code']}：{r['reason']}")
    lines += ['', '完整区间、两路反馈、冻结F、缺失原因及输入哈希保存在同名JSON与inputs.json中。',
              '接受只更新quality_sentiment_half_life_days；下次估值报告读取同一策略参数。保持shadow，不写回任何历史前向预测。', '']
    return '\n'.join(lines)
