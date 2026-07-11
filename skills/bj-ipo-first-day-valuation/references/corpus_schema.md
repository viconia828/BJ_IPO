# Xueqiu Corpus Schema

Use this reference when collecting, validating, or repairing `data/xueqiu_corpus/`.

## Files

- `data/xueqiu_corpus/articles/<user_id>_<status_id>.json`: one article per file.
- `data/xueqiu_corpus/articles.jsonl`: readable article records plus any explicit blocked boundary records included by the latest collector run.
- `data/xueqiu_corpus/index.json`: run options, author stats, quality stats, and article index.
- `outputs/xueqiu_corpus_collect_*.json`: full run reports.
- `outputs/xueqiu_manual_mhtml_import_*.json`: legacy reports for manually imported MHTML captures.
- `outputs/xueqiu_manual_article_import_*.json`: reports for manually imported MHTML/TXT captures.

## Article Fields

- `source`: fixed `xueqiu`.
- `user_id`, `author_name`.
- `status_id`, `url`, `canonical_url`.
- `title`, `page_title`.
- `created_at_ms`, `created_at_iso`, `created_at_text`, `collected_at`.
- `manual_import`, `manual_source_file`: present when an article was repaired from a manually saved MHTML or TXT page.
- `manual_text_import`: present when a hand-copied TXT article was normalized into the corpus schema.
- `matched_keywords`: keyword hits used for recall.
- `article_type`: one of `listing_valuation`, `listing_preview`, `first_day_price_analysis`, `subscription_strategy`, or `other`.
- `text`: cleaned article body. Do not treat footer, comments, or verification text as article evidence.
- `text_length`.
- `stock_mentions`: detected stock name/code mentions.
- `extracted`: first-pass regex extraction.
- `quality`: readability and collection status.

## Extracted Fields

- `issue_price`: issue price in yuan.
- `target_pe`: explicit author PE multiple, when present.
- `float_market_cap`, `total_market_cap`: issue-time market caps in 100 million yuan.
- `market_cap_range_text`: raw text fragments for author market-cap ranges.
- `price_range`: explicit author first-day or corresponding price range cached by the importer.
- `comparable_companies`: names parsed from comparable-company lines.
- `listing_date_hints`: date strings from title/body.
- `first_day_view`: sentences about first-day price, auction, sentiment, or expected return.
- `risk_phrases`: extracted risk-related sentences.
- `author_rule_phrases`: candidate reusable heuristic phrases.

## Quality Fields

- `readable`: true only when the body is usable as article evidence.
- `blocked_by_verification`: true when the page is a slider/CAPTCHA/network block.
- `has_issue_price`, `has_valuation`, `has_first_day`: coarse coverage flags.
- `suspected_truncated`: true when the body may be incomplete.

Use only `quality.readable=true` records for rule abstraction unless explicitly repairing collection failures.

## Validation-Derived Ranges

Validation scripts may derive a scorable range even when importer-level `extracted.price_range` is empty:

- `lower_bound_price_capped20_range`: `不低于 XX 元` style, converted to `[XX, 1.2 * XX]`.
- `target_single_price_fixed10_range`: target or midpoint price, converted to `XX ±10%`.
- `target_pe_range_implied_price` / `generic_pe_range_implied_price`: PE range converted with local `ISSUE_PRICE / AFTER_ISSUE_PE`.

Always keep `forecast_kind` in reports. Do not treat PE-implied or single-price ranges as equivalent to explicit author price quotes.
