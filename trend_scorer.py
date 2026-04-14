from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


CSV_DIR = Path(__file__).resolve().parent / "首日分时走势"


@dataclass(frozen=True)
class MinuteBar:
    dt: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


@dataclass(frozen=True)
class TrendMetrics:
    total_bars: int
    vwap: float
    c_vwap: float
    c_high: float
    hi_pos: float
    tail_30m: float
    volume_first_half_ratio: float
    amplitude: float
    day_direction: float
    open_30m_ratio: float


@dataclass(frozen=True)
class TrendScoreResult:
    code: str
    score: float
    factor: float
    trend_type: str
    metrics: TrendMetrics
    dimension_scores: dict[str, float]
    source: str
    note: str
    listing_date: date | None = None


@dataclass(frozen=True)
class TrendFactorResult:
    factor: float
    industry_factor: float
    market_factor: float
    industry_score_median: float | None
    market_score_median: float | None
    sample_count: int
    note: str
    industry_sample_codes: list[str]
    market_sample_codes: list[str]


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "--", "NaN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_listing_date(value: Any) -> date | None:
    if value in (None, "", "--"):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.split(" ", 1)[0].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _weighted_median(value_weight_pairs: list[tuple[float, float]]) -> float | None:
    valid_pairs = sorted(
        ((value, weight) for value, weight in value_weight_pairs if weight > 0),
        key=lambda item: item[0],
    )
    if not valid_pairs:
        return None

    total_weight = sum(weight for _, weight in valid_pairs)
    threshold = total_weight / 2
    cumulative = 0.0
    for value, weight in valid_pairs:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return valid_pairs[-1][0]


def _get_sample_weight_mode(params: dict[str, Any]) -> str:
    return str(params.get("sample_weight_mode", "static")).strip().lower() or "static"


def _get_sample_half_life_days(params: dict[str, Any]) -> float:
    return max(float(params.get("sample_decay_half_life_days", 20)), 1.0)


def _resolve_reference_date(target_listing_date: str | date | None, records: list[dict[str, Any]]) -> date:
    target_date = _parse_listing_date(target_listing_date)
    if target_date:
        return target_date

    sample_dates = [_parse_listing_date(item.get("LISTING_DATE")) for item in records]
    sample_dates = [item for item in sample_dates if item is not None]
    if sample_dates:
        return max(sample_dates)
    return date.today()


def _get_sample_weight(sample_date: date | None, reference_date: date, params: dict[str, Any]) -> float:
    if sample_date is None:
        return 0.0
    if _get_sample_weight_mode(params) != "time_decay":
        return 1.0

    day_gap = max((reference_date - sample_date).days, 0)
    half_life_days = _get_sample_half_life_days(params)
    return 0.5 ** (day_gap / half_life_days)


def _load_csv_bars(code: str) -> list[MinuteBar]:
    file_path = CSV_DIR / f"{code}.csv"
    if not file_path.exists():
        return []

    rows: list[MinuteBar] = []
    for encoding in ("gbk", "utf-8", "utf-8-sig"):
        try:
            with file_path.open("r", encoding=encoding, errors="ignore", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    open_price = _safe_float(row.get("open"))
                    high_price = _safe_float(row.get("high"))
                    low_price = _safe_float(row.get("low"))
                    close_price = _safe_float(row.get("close"))
                    volume = _safe_float(row.get("volume"))
                    amount = _safe_float(row.get("amount"))
                    if None in {open_price, high_price, low_price, close_price}:
                        continue
                    rows.append(
                        MinuteBar(
                            dt=str(row.get("DateTime", "")).strip(),
                            open=open_price,
                            high=high_price,
                            low=low_price,
                            close=close_price,
                            volume=volume or 0.0,
                            amount=amount or 0.0,
                        )
                    )
            if rows:
                return rows
        except UnicodeDecodeError:
            rows = []
            continue
    return rows


def _compute_metrics(bars: list[MinuteBar]) -> TrendMetrics | None:
    if not bars:
        return None

    total_bars = len(bars)
    total_volume = sum(bar.volume for bar in bars)
    total_amount = sum(bar.amount for bar in bars)
    vwap = total_amount / total_volume if total_volume > 0 else bars[-1].close

    day_high = max(bar.high for bar in bars)
    day_low = min(bar.low for bar in bars)
    close_price = bars[-1].close
    open_price = bars[0].open

    hi_idx = 0
    for index, bar in enumerate(bars):
        if bar.high == day_high:
            hi_idx = index
            break

    tail_start_index = max(0, total_bars - 30)
    tail_start_close = bars[tail_start_index].close
    first_half_index = total_bars // 2
    first_half_volume = sum(bar.volume for bar in bars[:first_half_index])
    open_30m_volume = sum(bar.volume for bar in bars[: min(30, total_bars)])

    return TrendMetrics(
        total_bars=total_bars,
        vwap=vwap,
        c_vwap=(close_price / vwap - 1) * 100 if vwap else 0.0,
        c_high=(close_price / day_high) * 100 if day_high else 0.0,
        hi_pos=hi_idx / total_bars if total_bars else 0.0,
        tail_30m=(close_price / tail_start_close - 1) * 100 if tail_start_close else 0.0,
        volume_first_half_ratio=(first_half_volume / total_volume) * 100 if total_volume else 50.0,
        amplitude=((day_high - day_low) / open_price) * 100 if open_price else 0.0,
        day_direction=(close_price / open_price - 1) * 100 if open_price else 0.0,
        open_30m_ratio=(open_30m_volume / total_volume) * 100 if total_volume else 0.0,
    )


def classify_trend(metrics: TrendMetrics) -> str:
    if metrics.amplitude < 15 and abs(metrics.c_vwap) < 3:
        return "D-贴线震荡"
    if metrics.hi_pos < 0.10:
        return "A-高开回落"
    if metrics.hi_pos >= 0.15 and metrics.c_vwap >= 0 and metrics.c_high >= 75:
        return "B-冲高维持"
    if metrics.hi_pos >= 0.15 and (metrics.c_vwap < 0 or metrics.c_high < 75):
        return "C-冲高回落"
    return "B-冲高维持" if metrics.c_vwap >= 0 else "C-冲高回落"


class TrendDimension:
    def __init__(self, name: str, weight: float) -> None:
        self.name = name
        self.weight = weight

    def score(self, metrics: TrendMetrics, turnover_total: float | None) -> float | None:
        raise NotImplementedError


class CloseVWAPDimension(TrendDimension):
    def score(self, metrics: TrendMetrics, turnover_total: float | None) -> float:
        _ = turnover_total
        value = metrics.c_vwap
        if value >= 10:
            return 95
        if value >= 5:
            return 80
        if value >= 1:
            return 65
        if value >= -1:
            return 50
        if value >= -5:
            return 35
        if value >= -10:
            return 20
        return 5


class PriceRetentionDimension(TrendDimension):
    def score(self, metrics: TrendMetrics, turnover_total: float | None) -> float:
        _ = turnover_total
        value = metrics.c_high
        if value >= 95:
            return 95
        if value >= 90:
            return 80
        if value >= 85:
            return 65
        if value >= 80:
            return 45
        if value >= 75:
            return 25
        return 10


class HighTimingDimension(TrendDimension):
    def score(self, metrics: TrendMetrics, turnover_total: float | None) -> float:
        _ = turnover_total
        value = metrics.hi_pos
        if value >= 0.80:
            return 95
        if value >= 0.50:
            return 75
        if value >= 0.25:
            return 55
        if value >= 0.10:
            return 35
        return 15


class ClosingMomentumDimension(TrendDimension):
    def score(self, metrics: TrendMetrics, turnover_total: float | None) -> float:
        _ = turnover_total
        value = metrics.tail_30m
        if value >= 5:
            return 95
        if value >= 2:
            return 75
        if value >= 0.5:
            return 60
        if value >= -0.5:
            return 45
        if value >= -2:
            return 30
        return 15


class VolumeRhythmDimension(TrendDimension):
    def score(self, metrics: TrendMetrics, turnover_total: float | None) -> float:
        _ = turnover_total
        value = metrics.volume_first_half_ratio
        if 65 <= value < 75:
            return 80
        if 75 <= value < 80:
            return 65
        if 60 <= value < 65:
            return 55
        if 80 <= value < 85:
            return 50
        if 85 <= value < 90:
            return 35
        return 20


class TurnoverDimension(TrendDimension):
    def score(self, metrics: TrendMetrics, turnover_total: float | None) -> float | None:
        if turnover_total is None:
            return None
        if turnover_total >= 80 and metrics.open_30m_ratio >= 40:
            return 90
        if turnover_total >= 60 and metrics.open_30m_ratio >= 30:
            return 75
        if turnover_total >= 40 and metrics.open_30m_ratio >= 20:
            return 55
        if turnover_total >= 40 and metrics.open_30m_ratio < 20:
            return 40
        return 20


class TrendScorer:
    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params
        self.dimensions: list[TrendDimension] = [
            CloseVWAPDimension("close_vwap", float(params.get("wsi_weight_close_vwap", 0.30))),
            PriceRetentionDimension("price_retention", float(params.get("wsi_weight_price_retention", 0.25))),
            HighTimingDimension("high_timing", float(params.get("wsi_weight_high_timing", 0.20))),
            ClosingMomentumDimension("closing_momentum", float(params.get("wsi_weight_closing_momentum", 0.15))),
            VolumeRhythmDimension("volume_rhythm", float(params.get("wsi_weight_volume_rhythm", 0.10))),
            TurnoverDimension("turnover", float(params.get("wsi_weight_turnover", 0.0))),
        ]

    def score_single(
        self,
        code: str,
        turnover_total: float | None = None,
        listing_date: date | None = None,
    ) -> TrendScoreResult | None:
        bars = _load_csv_bars(code)
        metrics = _compute_metrics(bars)
        if not metrics:
            return None

        dimension_scores: dict[str, float] = {}
        weighted_sum = 0.0
        weight_sum = 0.0
        for dimension in self.dimensions:
            if dimension.weight <= 0:
                continue
            score = dimension.score(metrics, turnover_total)
            if score is None:
                continue
            dimension_scores[dimension.name] = score
            weighted_sum += score * dimension.weight
            weight_sum += dimension.weight

        if weight_sum <= 0:
            return None

        total_score = weighted_sum / weight_sum
        factor = score_to_factor(total_score, self.params)
        return TrendScoreResult(
            code=code,
            score=round(total_score, 1),
            factor=factor,
            trend_type=classify_trend(metrics),
            metrics=metrics,
            dimension_scores=dimension_scores,
            source="csv",
            note="使用本地首日分时 CSV 评分。",
            listing_date=listing_date,
        )

    def score_batch(self, records: list[dict[str, Any]]) -> list[TrendScoreResult]:
        results: list[TrendScoreResult] = []
        for record in records:
            code = str(record.get("SECURITY_CODE", "")).strip()
            turnover_total = _safe_float(record.get("TURNOVERRATE"))
            listing_date = _parse_listing_date(record.get("LISTING_DATE"))
            result = self.score_single(code, turnover_total=turnover_total, listing_date=listing_date)
            if result:
                results.append(result)
        return results


def score_to_factor(score: float | None, params: dict[str, Any]) -> float:
    if score is None:
        return 1.0
    strong_threshold = float(params.get("trend_strong_threshold", 70))
    weak_threshold = float(params.get("trend_weak_threshold", 40))
    if score >= strong_threshold:
        return 1 + float(params.get("trend_strong_boost", 0.05))
    if score < weak_threshold:
        return 1 + float(params.get("trend_weak_discount", -0.05))
    return 1.0


def _filter_historical_records(
    records: list[dict[str, Any]],
    target_code: str | None,
    target_listing_date: str | date | None,
) -> list[dict[str, Any]]:
    target_date = _parse_listing_date(target_listing_date)
    filtered: list[dict[str, Any]] = []
    for record in records:
        code = str(record.get("SECURITY_CODE", "")).strip()
        record_date = _parse_listing_date(record.get("LISTING_DATE"))
        if target_code and code == target_code:
            continue
        if target_date and record_date and record_date >= target_date:
            continue
        if target_date and record_date is None:
            continue
        filtered.append(record)
    filtered.sort(key=lambda item: _parse_listing_date(item.get("LISTING_DATE")) or date.min, reverse=True)
    return filtered


def _pick_industry_records(
    industry: dict[str, Any] | str,
    records: list[dict[str, Any]],
    min_samples: int,
) -> tuple[list[dict[str, Any]], str]:
    if isinstance(industry, str):
        primary = industry
        secondary = None
    else:
        primary = industry.get("primary")
        secondary = industry.get("secondary")

    if secondary and secondary != "未分类":
        secondary_records = [item for item in records if item.get("industry_secondary") == secondary]
        if len(secondary_records) >= min_samples:
            return secondary_records, "二级行业"

    if primary and primary != "未分类":
        primary_records = [item for item in records if item.get("industry_primary") == primary]
        if len(primary_records) >= min_samples:
            return primary_records, "一级行业"

    return records, "全市场"


def _summarize_scores(
    results: list[TrendScoreResult],
    params: dict[str, Any],
    reference_date: date,
) -> tuple[float | None, float, list[str], str]:
    if not results:
        return None, 1.0, [], "中位数"

    if _get_sample_weight_mode(params) == "time_decay":
        pairs = [(result.score, _get_sample_weight(result.listing_date, reference_date, params)) for result in results]
        summary_score = _weighted_median(pairs)
        label = f"时间衰减中位数（半衰期 {_get_sample_half_life_days(params):.0f} 天）"
    else:
        summary_score = _median([result.score for result in results])
        label = "中位数"

    return summary_score, score_to_factor(summary_score, params), [result.code for result in results], label


def get_trend_factor(
    industry: dict[str, Any] | str,
    recent_ipos: list[dict[str, Any]],
    params: dict[str, Any],
    target_code: str | None = None,
    target_listing_date: str | date | None = None,
) -> TrendFactorResult:
    historical_records = _filter_historical_records(recent_ipos, target_code, target_listing_date)
    if not historical_records:
        return TrendFactorResult(
            factor=1.0,
            industry_factor=1.0,
            market_factor=1.0,
            industry_score_median=None,
            market_score_median=None,
            sample_count=0,
            note="无可用历史样本，走势因子按中性 1.0 处理。",
            industry_sample_codes=[],
            market_sample_codes=[],
        )

    scorer = TrendScorer(params)
    limit = int(params.get("trend_score_stocks", 5))
    min_samples = int(params.get("min_industry_samples", 2))
    reference_date = _resolve_reference_date(target_listing_date, historical_records)

    industry_candidates, industry_scope = _pick_industry_records(industry, historical_records, min_samples)
    industry_results = scorer.score_batch(industry_candidates[:limit])
    if len(industry_results) < min_samples:
        industry_scope = "全市场"
        industry_results = scorer.score_batch(historical_records[:limit])

    market_results = scorer.score_batch(historical_records[:limit])

    industry_score_median, industry_factor, industry_codes, score_label = _summarize_scores(
        industry_results,
        params,
        reference_date,
    )
    market_score_median, market_factor, market_codes, _ = _summarize_scores(
        market_results,
        params,
        reference_date,
    )

    factor = (
        float(params.get("industry_trend_weight", 0.60)) * industry_factor
        + float(params.get("market_sentiment_weight", 0.40)) * market_factor
    )
    note = (
        f"走势样本仅使用上市日早于标的的历史新股；行业样本口径={industry_scope}，"
        f"评分统计={score_label}，"
        f"行业中位分={industry_score_median if industry_score_median is not None else '-'}，"
        f"市场中位分={market_score_median if market_score_median is not None else '-'}。"
    )

    return TrendFactorResult(
        factor=round(factor, 4),
        industry_factor=industry_factor,
        market_factor=market_factor,
        industry_score_median=industry_score_median,
        market_score_median=market_score_median,
        sample_count=len(historical_records),
        note=note,
        industry_sample_codes=industry_codes,
        market_sample_codes=market_codes,
    )
