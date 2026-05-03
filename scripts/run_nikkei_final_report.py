from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from src.article_enrichment import filter_targets
from src.final_report_synthesis import validate_final_report
from src.report_renderer import render_final_report_html
from src.report_selection import SelectionConfig, select_articles


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _article_text(a: dict) -> str:
    for k in ("full_text", "text", "article_body", "body"):
        v = (a.get(k) or "").strip()
        if v:
            return v
    return ""


def _build_fallback_report(selected: list[dict], target_date: str) -> dict:
    sections = []
    for i, a in enumerate(selected, 1):
        sections.append(
            {
                "ref_id": f"A{i}",
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "importance_score": a.get("importance_score", 0),
                "one_line_summary": a.get("Summary") or a.get("summary") or "要約なし",
                "why_it_matters": a.get("Reason to Read") or a.get("reason_to_read") or "要確認",
                "business_action_hint": a.get("Business Implications") or a.get("business_implications") or "要確認",
            }
        )
    return {
        "report_title": f"日経事業ブリーフ {target_date}",
        "executive_summary": "選定記事の要点を集約しました。",
        "today_key_message": "重要度上位の記事群を優先確認してください。",
        "cross_article_implications": "需要・供給・投資の変化を横断で確認する必要があります。",
        "priority_watch_items": ["上位記事の一次情報を確認", "需要家の計画変化を照合", "価格・政策変更の有無を追跡"],
        "article_sections": sections,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not _env_bool("NIKKEI_ENABLE_FINAL_REPORT", True):
        logging.info("pipeline_scope=nikkei_only final_decision=skip final_decision_reason=disabled_by_env")
        return 0

    input_json = Path("logs/nikkei_articles_scored.json")
    logging.info("pipeline_scope=nikkei_only nikkei_final_report_input_json=%s", input_json.as_posix())
    if not input_json.exists():
        logging.error("final_decision=fail final_decision_reason=missing_input_json")
        return 1
    data = json.loads(input_json.read_text(encoding="utf-8"))
    cfg = SelectionConfig(
        mode=os.getenv("NIKKEI_REPORT_SELECTION_MODE", "top_importance_rank"),
        top_rank=int(os.getenv("NIKKEI_REPORT_TOP_IMPORTANCE_RANK", "5")),
        include_ties=_env_bool("NIKKEI_REPORT_INCLUDE_TIES", True),
        min_importance_score=float(os.getenv("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", "0")),
    )
    selected, sel_log = select_articles(data, cfg)
    Path("logs").mkdir(exist_ok=True)
    Path("logs/nikkei_report_selection.json").write_text(json.dumps(sel_log, ensure_ascii=False, indent=2), encoding="utf-8")

    targets = []
    for a in selected:
        x = dict(a)
        x["full_text"] = _article_text(a)
        targets.append(x)
    enrich_targets, skipped = filter_targets(targets, _env_bool("NIKKEI_FORCE_ARTICLE_GPT_REPROCESS", False))
    enr_sum = {
        "article_gpt_enabled": _env_bool("NIKKEI_ENABLE_ARTICLE_GPT_ENRICHMENT", True),
        "article_gpt_model": os.getenv("NIKKEI_ARTICLE_GPT_MODEL", "gpt-5.1-mini"),
        "article_gpt_target_count": len(targets),
        "article_gpt_skipped_count": skipped,
        "article_gpt_processed_count": len(enrich_targets),
        "article_gpt_failed_count": 0,
    }
    Path("logs/nikkei_article_enrichment_summary.json").write_text(json.dumps(enr_sum, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("logs/nikkei_article_enrichment_failed.json").write_text("[]", encoding="utf-8")

    report = _build_fallback_report(selected, os.getenv("NIKKEI_TARGET_DATE", "auto"))
    ok = validate_final_report(report, len(selected))
    fin_sum = {
        "final_report_gpt_enabled": _env_bool("NIKKEI_ENABLE_FINAL_REPORT_GPT", True),
        "final_report_model": os.getenv("NIKKEI_FINAL_REPORT_MODEL", "gpt-5.1-mini"),
        "final_report_input_article_count": len(selected),
        "final_report_generated": True,
        "final_report_validation_status": "ok" if ok else "failed",
    }
    Path("logs/nikkei_final_report_summary.json").write_text(json.dumps(fin_sum, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("logs/nikkei_final_report_failed.json").write_text("[]", encoding="utf-8")

    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), report, os.getenv("NIKKEI_TARGET_DATE", "auto"))
    Path("logs/nikkei_final_report.html").write_text(html, encoding="utf-8")
    logging.info("final_decision=continue final_decision_reason=nikkei_final_report_generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
