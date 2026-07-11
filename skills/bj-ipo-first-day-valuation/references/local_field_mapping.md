# Local Field Mapping

Use this reference before validating any Xueqiu-derived rule.

## Existing Local Sources

- `data/offline_tuning/replay_dataset.json`: replay sample set.
- `data/offline_tuning/replay_items/<code>.json`: per-sample cached fields.
- `data/offline_tuning/subscription_history_sample.csv`: subscription and allocation history.
- `首日分时走势/<code>.csv`: first-day intraday cache for average price and trend scoring.
- `策略参数.txt`: current model parameters.
- `code/valuation_engine.py`: formal valuation methods.
- `code/param_tuning.py`: replay scoring and parameter search.

## Direct Mappings

- Issue price: local IPO/announcement fields and replay item issue price.
- First-day average price: `AVERAGE_PRICE` or intraday-derived average price.
- First-day return: average-price return preferred; close return only as fallback.
- Float market cap: issue price times actual first-day float shares when available.
- Total market cap: issue price times total shares.
- Issue PE: announcement/prospectus parsed PE.
- Industry: `industry_mapping` and method-two secondary industry.
- Recent IPO sentiment: method-three first-day and post-listing profit-effect fields.
- Subscription heat: `subscription_history_sample.csv` funding/account pool fields.

## Derivable Mappings

- Author price-range hit: compare article `extracted.price_range` with realized first-day average price.
- Author implied return range: `(price_range / issue_price - 1)`.
- Author target PE hit: compare `target_pe` with realized first-day average price implied PE when EPS is available.
- Small-float premium: bucket by float market cap and issue price.
- Recent sector heat: aggregate prior samples in the same secondary industry before listing date.

## Existing Validation Script

Use `tools/validate_xueqiu_author_ranges.py` for the first-pass explicit range check.

The script:

- Reads `data/xueqiu_corpus/index.json` and the single-article JSON files.
- Maps articles to local IPO samples by title target and explicit `BJ920xxx` stock labels.
- Splits multi-stock preview articles into stock-specific evidence windows.
- Excludes articles published on or after the local listing date.
- Uses `AVERAGE_PRICE` from replay data, or reads `首日分时走势/<code>.csv` when replay lacks the average price.
- Compares the author explicit price range with realized first-day average price.

The result is intentionally narrow: it evaluates explicit author price intervals, not the full qualitative heuristic set.

## Manual Or Weak Mappings

- Theme labels such as semiconductor, robot, AI, consumer, or medical.
- Phrases like "预期竞价被超顶", "看点较少", "非科技不受待见".
- Business-quality tags such as国产替代,隐形冠军,客户优质.

Store these as manual labels or text features before using them in a model.

## Leakage Rules

- Use only article body available before the target listing date.
- Exclude comments and author replies after listing.
- Exclude articles whose title or body is clearly a收评,午评,复盘, or上市后再思考 unless the validation task is explicitly post-listing analysis.
- For multi-stock preview articles, split the rule evidence by stock and listing date before scoring.
