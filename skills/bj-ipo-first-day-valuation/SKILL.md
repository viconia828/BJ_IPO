---
name: bj-ipo-first-day-valuation
description: Collect and use Xueqiu first-day IPO valuation articles to extract reusable Beijing Stock Exchange IPO listing-day heuristics, map them to this repo's local data, validate first-day valuation ranges, and connect local-only ranges to listing-day sell guidance. Use when asked to gather Xueqiu valuation corpus, summarize authors' pricing methods, build or update the BJ IPO valuation skill, test author heuristics against local replay data, or decide listing-day actions from the opening price and intraday trend.
---

# BJ IPO First-Day Valuation

## Workflow

1. Locate the project root containing `code/valuation_engine.py`, `tools/refresh_xueqiu_reference.py`, and `data/offline_tuning/`.
2. For the normal manual refresh workflow, read `references/corpus_schema.md`, save Xueqiu articles as MHTML or TXT under the root `xueqiu/` folder, then run:

```powershell
添加新股首日走势.bat 3 --no-pause
```

This BAT option must remain local-only. It must not invoke `tools/xueqiu_corpus_collect.cjs`, open Xueqiu, log in, or handle verification pages. It imports the files, refreshes author ranges and coverage, rebuilds author-rule scores, reruns author/model and local-only studies, and refreshes the valuation shadow reports.

3. Inspect `data/xueqiu_corpus/index.json` before using the corpus. Treat `quality.readable=false` and `blocked_by_verification=true` records as collection failures, not author evidence.
4. To run the same manual refresh directly from Python, use:

```powershell
python -X utf8 tools\refresh_xueqiu_reference.py --input-dir xueqiu
```

5. Use `tools/xueqiu_corpus_collect.cjs` only for an explicitly requested research collection run. It is not part of the normal BAT refresh path. If used, stop on slider/CAPTCHA or WAF pages and never bypass verification.
6. For rule abstraction, read `references/heuristic_taxonomy.md`. Extract only reusable decision rules, not one-off prose.
7. For local validation, read `references/local_field_mapping.md`. Map every proposed rule to local fields before scoring it.
8. For a first-pass scorable range check, run:

```powershell
python -X utf8 tools\validate_xueqiu_author_ranges.py
```

9. To distill author gains into local-only proxy logic, run:

```powershell
python -X utf8 tools\analyze_xueqiu_author_logic_distillation.py --target scan_sample
```

10. To evaluate the local-only proxy strategy without changing production valuation code, run:

```powershell
python -X utf8 tools\evaluate_local_proxy_strategy.py --target scan_sample
```

11. To connect the conservative and rolling local-only ranges to listing-day sell guidance, read `docs/设计文档/10_估值区间与首日盘中卖点联动模型.md`, then run:

```powershell
python -X utf8 tools\evaluate_intraday_valuation_guidance.py
```

12. When a local mood fallback appears stale after the previous IPO breaks expectations, evaluate regime-break shadow ranges:

```powershell
python -X utf8 tools\evaluate_regime_break_fallback.py
```

13. During valuation auto-tuning, rerank the core candidate pool with local-only conservative dynamic ranges, regime-break fallback, and a low-weight rolling-center line. The reranked winner must replace `best` and `changed_overrides` and become the next stage center. Keep author predictions empty in this path and calculate proxy thresholds, tiers, and rolling estimates walk-forward by listing date. After tuning, use `tools/run_valuation_shadow_pipeline.py` with `调参/valuation_auto_shadow_context_latest.json` for the full diagnostic report.
14. Validate against replay/offline tuning data with上市前可得 information only. Keep article publication time before listing date; exclude comments, 收评, and after-listing additions from predictive rules. At each intraday decision node, use only bars available by that time; connect previous-IPO feedback only from earlier completed trading days.

## Corpus Rules

- Preserve source attribution: author, user ID, status ID, URL, title, and publication time.
- Avoid long verbatim quotations in final reports. Summarize author logic and cite article IDs/URLs.
- Keep raw collection separate from model logic. Do not import web scraping code into `code/valuation_engine.py`.
- If Xueqiu shows a slider/CAPTCHA page, stop the current author and report the blocked status. Do not attempt to bypass it.

## Rule Quality Gates

For each candidate heuristic, record:

- `source_articles`: article IDs that support the rule.
- `author_scope`: one author, multiple authors, or cross-author consensus.
- `local_fields`: exact local fields needed to evaluate it.
- `availability`: `available`, `derivable`, `manual_label`, or `unmapped`.
- `leakage_check`: why the rule is上市前可得.
- `expected_effect`: direction of correction, such as uplift, haircut, or risk filter.

Only promote a rule into a model or backtest when it passes the leakage check and has a local-field mapping.

## Validation Standard

Compare each author-derived rule set against the current baseline:

- First-day average price inside valuation range.
- Directional hit rate by realized first-day average-price return bucket.
- Low-score avoidance of weak/破发 samples.
- Top-bucket average and median first-day return.
- Stability by author, listing month, and industry.

Report both improvements and failures. A vivid author phrase is not evidence unless it survives local replay.

For local-only distillation, treat author targets as teacher labels only. Do not use author target prices, article text, or author phrases as proxy prediction inputs. Prefer local replay fields, pre-listing recent BSE IPO mood, industry history, supply fields, and model availability/uncertainty.

For listing-day guidance, keep the valuation layer and action layer separate. Use the conservative range as the risk anchor and the rolling range as a research mood anchor. When both ranges are below the opening price or both are above it, treat that as stronger evidence than a single-line signal. Do not mechanically increase the opening sell fraction from 30% to 50% merely because the previous IPO was weak; use previous weakness primarily to shorten the repair deadline to 9:35. Keep the 50% branch as an extreme-risk comparison until forward samples support it.

If both ranges come from local sentiment fallback and the previous IPO has already broken its expected lower bound, label the range `low_regime_break`. Use it only as an expectation-break signal for the action layer; do not present its center or bounds as a credible fundamental valuation.

## Current Result

As of 2026-07-11 20:05, the local-only manual refresh path is integrated into BAT option 3 and local learning now affects valuation auto-tuning itself. Each stage reranks about 20 core candidates with weights of 45% core score, 35% conservative local range, 15% regime-break, and 5% rolling center. In the current three-stage replay, stage 3 core scoring proposed `bse_discount_factor=0.60` plus `sentiment_decay_half_life_days=12`; the local reranker rejected that extra refinement and retained the stage-2 center at `bse_discount_factor=0.625` without changing the half-life. The conservative line used 21 recent samples with 27.5% weighted hit rate and 14.7% average width; the rolling research line reached 36.2% weighted hit rate. Author inputs were disabled and proxy features were calculated walk-forward. A full manual-corpus replay still has 41 readable pre-listing samples out of 42; `920117` remains the only uncovered sample.
