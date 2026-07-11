# Author Heuristic Taxonomy

Use this reference when converting Xueqiu article prose into reusable first-day valuation rules.

## Valuation Anchor

Rules based on explicit valuation anchors:

- Comparable PE/PS/PB and target multiple.
- Market-cap range and corresponding first-day price range.
- Discount or premium versus A-share comparables.
- Whether发行 PE leaves enough valuation repair room.

Keep the author's numeric range separate from the local model's computed range.

## Liquidity Supply

Rules about tradable supply and capital needed to move the stock:

- First-day float market cap.
- Issue price level: low-price attraction or high-price pressure.
- Online issuance size and number of shares.
- Old-share transfer and actual first-day float.
- Small-cap/low-float emotional premium.

## Sector Heat

Rules about whether the theme is currently favored:

- Semiconductor, robot, AI, low-altitude, new energy, auto parts, medical, consumer, or other themes.
-国产替代, specialized niche leader, invisible champion, or policy tailwind.
- Whether same-sector listed comparables are trending.

Map these to local industry, comparable-company momentum, and recent IPO sector performance when possible.

## Fundamental Quality

Rules about business durability:

- Revenue and profit growth.
- Gross margin, product mix, and pricing power.
- Customer concentration and dependence on a single major customer.
- Cash flow, capacity utilization,产销率, and募投 reasonableness.
- Export, tariff, FX, and cyclical exposure.

Separate "quality supports valuation" from "quality only explains risk".

## Market Mood

Rules about broad or near-end IPO sentiment:

- Recent BSE IPO first-day return.
- Next-day/third-day profit effect of recent new stocks.
- Same-day A-share or overseas risk appetite.
- Whether recent secondary stocks are赚钱 or bleeding.

Use local method-three sentiment fields before inventing a new proxy.

## Listing Microstructure

Rules about listing-day behavior:

- Auction may be overbid or weak.
- Expected intraday turnover.
- Opening spike versus sustainable average price.
- Whether high attention causes抢筹, or whether non-tech labels suppress demand.

Do not train on上市后 comments or收评 for上市前 prediction.

## Risk Filter

Rules that should reduce or block a bullish estimate:

- High issue price and weak retail participation.
- High发行 PE without growth.
- Profit decline, slowing growth, or one-off earnings.
- Customer concentration, export dependence, cyclicality, price war.
- Comparable companies falling or sector sentiment cooling.

Treat a risk filter as a directional haircut unless the article gives an explicit target range.

## Local-Only Proxy Distillation

When the goal is to use author logic without connecting to Xueqiu, use author targets only as teacher labels for analysis. Do not use article prose, author target prices, or author phrases as prediction inputs.

First-pass local proxy groups:

- `liquidity_elasticity`: issue price, first-day float market cap, online issue size.
- `valuation_tolerance`:发行 PE, industry PE,发行 PE/industry PE.
- `recent_mood`: prior BSE IPO first-day average-price changes before the target listing date.
- `sector_proxy`: prior same-industry or same-secondary-industry IPO performance.
- `subscription_attention`: top-apply market cap and online issue size.
- `supply_overhang`: old-share ratio and first-day float supply.
- `model_uncertainty`: local model unavailable, too few method legs, or current-vs-scan-best divergence.

Current observation:

- These local fields are useful for ranking and deciding interval width/fallback.
- They do not yet fully reproduce author center prices.
- Prefer dynamic width and explicit fallback candidates before promoting center-price correction.
