from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
import sys
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parents[1]
CODE_DIR = ROOT_DIR / "code"
TOOLS_DIR = ROOT_DIR / "tools"
for path in (CODE_DIR, TOOLS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import config_loader
import param_tuning
import tune_params


DEFAULT_DATASET = ROOT_DIR / "data" / "offline_tuning" / "replay_dataset.json"
DEFAULT_PARAMS = ROOT_DIR / "策略参数.txt"
DEFAULT_CONTEXT = ROOT_DIR / "调参" / "valuation_auto_shadow_context_latest.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按当前正式参数刷新估值 shadow latest 上下文。")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--params", type=Path, default=DEFAULT_PARAMS)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    params = config_loader.load_params(args.params)
    dataset = param_tuning.load_replay_dataset(args.dataset)
    metrics = param_tuning.evaluate_replay_targets(dataset, params)
    reference_date = param_tuning._auto_tune_reference_date(dataset)
    auto_score = param_tuning._score_auto_metrics(metrics, params, reference_date)
    candidate_groups = param_tuning.build_auto_tune_candidate_groups(params, dataset)
    candidate_keys = sorted(
        {
            key
            for _, candidates in candidate_groups
            for candidate in candidates
            for key in candidate
        }
    )
    entry = {
        "name": "formal_params",
        "overrides": {},
        "metrics": metrics,
        "auto_score": auto_score,
    }
    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reference_date": reference_date.isoformat() if isinstance(reference_date, date) else str(reference_date),
        "stage_level": None,
        "changed_overrides": {},
        "best_is_baseline": True,
        "baseline": entry,
        "best": entry,
        "formal_acceptance_guard": param_tuning._formal_acceptance_guard(metrics, metrics),
        "time_slice_gate": {
            "status": "manual_formal_params_validated",
            "passed": True,
            "report": str(ROOT_DIR / "docs" / "工作日志" / "20260712_方法二小样本置信度降权.md"),
        },
        "model_contract": {
            "version": param_tuning.AUTO_TUNE_MODEL_CONTRACT_VERSION,
            "evaluation_scope": dataset.get("evaluation_scope"),
            "candidate_keys": candidate_keys,
            "latest_model_compatible": True,
            "structural_flags": {
                key: params.get(key)
                for key in param_tuning.LATEST_MODEL_STRUCTURAL_FLAGS
            },
        },
        "local_learning_rerank": {},
    }
    context_args = SimpleNamespace(
        params_file=str(args.params),
        dataset_path=str(args.dataset),
        auto_shadow_context_path=str(args.context),
    )
    path = tune_params._write_auto_shadow_context(context_args, dataset, result, "no_change")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
