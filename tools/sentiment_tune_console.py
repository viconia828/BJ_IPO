"""Interactive adapter for the bounded-combined shadow tuning lane."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import uuid

import config_loader
import sentiment_tuning as tuning


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def accept(result, params_path, original_sha256, write_updates):
    """Recheck the exact file and narrow write contract at the confirmation boundary."""
    path = Path(params_path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != original_sha256:
        raise ValueError('参数文件在回放后发生变化，请重新运行后再接受')
    params = config_loader.load_params(path)
    updates = result.get('changed_overrides') or {}
    if params.get('quality_sentiment_mode', 'shadow') != 'shadow':
        raise ValueError('当前参数不在shadow模式，不能从影子入口写回')
    if (not result['guard']['passed'] or set(updates) != {tuning.PARAMETER}
            or updates[tuning.PARAMETER] != result['selected_half_life']
            or updates[tuning.PARAMETER] not in result['search_space']):
        raise ValueError('候选不符合情绪影子写回契约')
    write_updates(path, updates)
    result['acceptance_status'] = 'accepted_shadow'


def run(args, params, dataset, *, write_updates):
    params_path = Path(args.params_file)
    before = hashlib.sha256(params_path.read_bytes()).hexdigest()
    print('情绪组合影子自动调参：combined；基本面、宽池、反馈规则和区间宽度固定。', flush=True)
    print('只搜索情绪半衰期4/8/12/16天及当前值；不会运行旧综合参数搜索或研究候选重排。', flush=True)
    try:
        if config_loader.load_params(params_path) != params:
            raise ValueError('参数文件在加载数据集时发生变化，请重新运行')
        history, audit = tuning.prepare_history(dataset)
        result = tuning.tune(history, params, train_ratio=args.train_ratio,
                             min_train_samples=args.min_train_samples,
                             progress=lambda i, n, v: print(f'[{i}/{n}] 组合影子：半衰期{v:g}天', flush=True))
        tuning.add_legacy_comparison(result, history, params)
    except (ValueError, OSError, KeyError, ArithmeticError) as exc:
        print(f'情绪影子调参失败，未写入参数：{exc}', flush=True)
        return 1
    result['input_audit'] = audit
    result['generated_at'] = datetime.now().astimezone().isoformat()
    root = Path(__file__).resolve().parents[1]
    result['provenance'] = {
        'params_path': str(params_path.resolve()), 'params_sha256': before,
        'dataset_path': str(Path(args.dataset_path).resolve()), 'dataset_content_sha256': tuning.digest(dataset),
        'sources_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                            [root / 'code' / name for name in ('sentiment_tuning.py', 'sentiment_pricing.py',
                             'sentiment_timing.py', 'quality_sentiment.py', 'quality_sentiment_data.py',
                             'fundamental_anchor.py', 'param_tuning.py')] +
                            [Path(__file__), root/'tools/tune_params.py', root/'tools/manual_tune_prompt.py']},
    }
    directory = Path(args.output_dir) if args.output_dir else root / '输出/调参'
    directory.mkdir(parents=True, exist_ok=True)
    stem = 'sentiment_auto_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:6]
    path, md, snapshot = (directory / (stem + suffix) for suffix in ('.json', '.md', '.inputs.json'))
    write_json(snapshot, {'history': history, 'params': params, 'audit': audit})
    result['input_snapshot'] = {'path': str(snapshot.resolve()), 'sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest()}
    result['acceptance_status'] = 'not_accepted' if result['changed_overrides'] else 'no_change'

    def save():
        write_json(path, result)
        md.write_text(tuning.render(result), encoding='utf-8')

    save()
    print('情绪影子自动调参完成。')
    print(f"当前半衰期：{result['current_half_life']:g}天；训练选定：{result['selected_half_life']:g}天。")
    for group, label in [('all', '全部'), ('validation', '后段检验'), ('recent10', '最近10只'), ('cooling', '固定降温')]:
        models = result['metrics'][group]
        for model in ['current', 'candidate']:
            m = models[model]
            fmt = lambda v: '—' if v is None else f'{v:.2f}%'
            print(f"{label} / {tuning.LABELS[model]}：{m['count']}/{m['total']}只；"
                  f"价格MAPE {fmt(m['price_mape_pct'])}，高估 {fmt(m['over_pct'])}，低估 {fmt(m['under_pct'])}")
    print('检验：' + ('通过' if result['guard']['passed'] else '未通过：' + '、'.join(result['guard']['failed'])))
    print(f'逐股报告：{md.resolve()}\n完整结果：{path.resolve()}')
    print('当前正式价、原情绪、当前组合和候选的逐股价格/区间/误差均已保存；历史重建不作为新样本外。')
    if not result['changed_overrides']:
        print('保留当前组合影子参数。')
        return 0
    print(f"建议仅修改：{tuning.PARAMETER}={result['selected_half_life']:g}；继续保持shadow。")
    if args.sentiment_evaluate_only:
        print('本次仅评估，未写入参数。')
        return 0
    try:
        choice = input('接受情绪影子参数？[y/N]：').strip().lower()
    except EOFError:
        choice = ''
    if choice not in {'y', 'yes'}:
        print('未接受，参数保持原值。')
        return 0
    try:
        accept(result, params_path, before, write_updates)
    except ValueError as exc:
        result['acceptance_status'] = 'rejected_stale_or_invalid'
        save()
        print(f'未写入：{exc}')
        return 1
    save()
    record = Path(args.auto_record_path)
    record.parent.mkdir(parents=True, exist_ok=True)
    with record.open('a', encoding='utf-8') as handle:
        handle.write(f"\n## {result['generated_at']} 情绪组合影子自动调参（已接受）\n\n"
                     f"- {tuning.PARAMETER}: {result['current_half_life']:g} → {result['selected_half_life']:g}\n"
                     f"- 仅影子参数；模式仍为shadow；正式价格未切换。\n- 报告：{md.resolve()}\n")
    print('已写入情绪半衰期并记录；下次估值报告使用新影子参数。')
    return 0
