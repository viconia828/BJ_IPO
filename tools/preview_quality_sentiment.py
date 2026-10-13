"""Render a saved analysis payload with the new shadow diagnostics, offline."""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
import config_loader
import quality_sentiment as qs
import quality_sentiment_data as data
import report_generator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--as-of", required=True, help="exclusive cutoff date")
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--output", type=Path, help="optional versioned preview directory")
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    payload['analysis_date'] = (qs.day(args.as_of) - timedelta(days=1)).isoformat()
    params = config_loader.load_params()
    params["quality_sentiment_mode"] = "shadow"
    payload["params"] = params
    target = {**payload["ipo_info"], "comparable_data": payload["comparable_data"],
              "industry_primary": payload["industry"]["primary"], "industry_secondary": payload["industry"]["secondary"]}
    history, audit = data.load_history()
    target = data.with_fundamental_review(target)
    result = qs.evaluate(target, history, params, as_of=args.as_of)
    result["input_correction_count"] = len(audit)
    payload["final"] = qs.attach(payload["final"], result)
    payload["notes"].insert(0, "本报告为保存输入的离线预览；旧版价格保留用于对照，新情绪线处于影子验证。")
    out = args.output or ROOT / "outputs/研究产物/最新/个股资质情绪模型_20261012"
    out.mkdir(parents=True, exist_ok=True)
    stem = str(target["SECURITY_CODE"]) + "_个股资质情绪预览"
    context = report_generator._prepare_report_context(payload)
    html = report_generator._build_report_html(context)
    (out / (stem + ".html")).write_text(html, encoding="utf-8")
    (out / (stem + ".md")).write_text(report_generator.build_report_markdown(payload), encoding="utf-8")
    (out / (stem + "_一览.txt")).write_text(report_generator.build_report_overview_text(payload), encoding="utf-8")
    (out / (stem + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if args.pdf:
        # The exporter expects a fresh path; reusing a ready PDF can return stale content.
        staged_pdf = out / (stem + "_" + uuid.uuid4().hex[:12] + ".pdf")
        report_generator._render_pdf_from_html(html, staged_pdf)
        staged_pdf.replace(out / (stem + ".pdf"))
    print(json.dumps({"path": str(out / stem), "anchor": result["anchor"], "sentiment": result["sentiment"], "state": result["state"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
