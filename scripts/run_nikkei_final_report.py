from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List

import requests

from src.article_enrichment import build_notion_payload, filter_targets, validate_article_json
from src.final_report_synthesis import build_synthesis_input, validate_final_report
from src.openai_json_client import OpenAIJsonClient
from src.report_renderer import render_final_report_html
from src.report_selection import SelectionConfig, select_articles

NOTION_VERSION = "2022-06-28"


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def _article_text(a: dict) -> str:
    for k in ("full_text", "text", "article_body", "body"):
        v = (a.get(k) or "").strip()
        if v:
            return v
    return ""


def _build_fallback_report(selected: list[dict], target_date: str) -> dict:
    sections = []
    for i, a in enumerate(selected, 1):
        sections.append({"ref_id": f"A{i}", "title": a.get("title", ""), "url": a.get("url", ""), "importance_score": a.get("importance_score", 0), "one_line_summary": a.get("Summary") or "要約なし", "why_it_matters": a.get("Reason to Read") or "要確認", "business_action_hint": a.get("Business Implications") or "要確認"})
    return {"report_title": f"日経事業ブリーフ {target_date}", "executive_summary": "選定記事の要点を集約しました。", "today_key_message": "重要度上位の記事群を優先確認してください。", "cross_article_implications": "需要・供給・投資の変化を横断で確認する必要があります。", "priority_watch_items": ["上位記事の一次情報を確認", "需要家の計画変化を照合", "価格・政策変更の有無を追跡"], "article_sections": sections}


def _notion_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}


def _notion_update_page(page_id: str, props: Dict[str, Any], token: str) -> None:
    requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=_notion_headers(token), json={"properties": props}, timeout=30).raise_for_status()


def _simple_props(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in payload.items():
        if isinstance(v, bool): out[k] = {"checkbox": v}
        elif isinstance(v, (int, float)): out[k] = {"number": v}
        else: out[k] = {"rich_text": [{"text": {"content": str(v)[:1800]}}]}
    return out


def _send_mail(subject: str, html: str) -> bool:
    to = [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_TO", "")) if x.strip()]
    cc = [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_CC", "")) if x.strip()]
    bcc = [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_BCC", "")) if x.strip()]
    recipients = to + cc + bcc
    if not recipients:
        logging.info("mail_sent=false mail_skipped_reason=no_recipients")
        return False
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = os.getenv("MAIL_FROM", "")
    msg["To"] = ", ".join(to)
    if cc: msg["Cc"] = ", ".join(cc)
    with smtplib.SMTP(os.getenv("MAIL_HOST", "smtp.gmail.com"), int(os.getenv("MAIL_PORT", "587")), timeout=30) as s:
        s.starttls(); s.login(os.getenv("MAIL_USER") or os.getenv("MAIL_FROM", ""), os.getenv("MAIL_PASSWORD", "")); s.sendmail(os.getenv("MAIL_FROM", ""), recipients, msg.as_string())
    return True




def _notion_query_daily(db_id: str, token: str, input_hash: str) -> list[dict]:
    payload={"filter":{"property":"Input Hash","rich_text":{"equals":input_hash}}}
    r=requests.post(f"https://api.notion.com/v1/databases/{db_id}/query",headers=_notion_headers(token),json=payload,timeout=30)
    if r.status_code>=400: return []
    return r.json().get("results",[])

def _notion_create_daily_report(db_id: str, token: str, report: dict, selected: list[dict], input_hash: str, mail_sent: bool) -> None:
    links="\n".join([f"[A{i}：{a.get('title','')}]({a.get('url','')})" for i,a in enumerate(selected,1)])
    body=f"本日の要点\n{report.get('today_key_message','')}\n\n全体ブリーフ\n{report.get('executive_summary','')}\n\n横断的な業務示唆\n{report.get('cross_article_implications','')}\n\n優先確認事項\n"+"\n".join(f"- {x}" for x in report.get('priority_watch_items',[]))+f"\n\n参考リンク\n{links}"
    props={"Title":{"title":[{"text":{"content":report.get('report_title','日経事業ブリーフ')}}]},"Date":{"date":{"start":datetime.now().date().isoformat()}},"Article Count":{"number":len(selected)},"Selected Article Count":{"number":len(selected)},"Final Report Model":{"rich_text":[{"text":{"content":os.getenv('NIKKEI_FINAL_REPORT_MODEL','gpt-5.1-mini')}}]},"Executive Summary":{"rich_text":[{"text":{"content":report.get('executive_summary','')[:1800]}}]},"Key Message":{"rich_text":[{"text":{"content":report.get('today_key_message','')[:1800]}}]},"Input Hash":{"rich_text":[{"text":{"content":input_hash}}]},"Mail Sent":{"checkbox":mail_sent},"Mail Sent At":{"date":{"start":datetime.now(timezone.utc).isoformat() if mail_sent else None}}}
    requests.post("https://api.notion.com/v1/pages",headers=_notion_headers(token),json={"parent":{"database_id":db_id},"properties":props,"children":[{"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":body[:1900]}}]}}]},timeout=30).raise_for_status()

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not _env_bool("NIKKEI_ENABLE_FINAL_REPORT", True):
        logging.info("pipeline_scope=nikkei_only final_decision=skip final_decision_reason=disabled_by_env")
        return 0
    input_json = Path("logs/nikkei_articles_scored.json")
    logging.info("pipeline_scope=nikkei_only nikkei_final_report_input_json=%s", input_json.as_posix())
    data = json.loads(input_json.read_text(encoding="utf-8"))
    cfg = SelectionConfig(mode=os.getenv("NIKKEI_REPORT_SELECTION_MODE", "top_importance_rank"), top_rank=int(os.getenv("NIKKEI_REPORT_TOP_IMPORTANCE_RANK", "5")), include_ties=_env_bool("NIKKEI_REPORT_INCLUDE_TIES", True), min_importance_score=float(os.getenv("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", "0")))
    selected, sel_log = select_articles(data, cfg)
    Path("logs").mkdir(exist_ok=True)
    Path("logs/nikkei_report_selection.json").write_text(json.dumps(sel_log, ensure_ascii=False, indent=2), encoding="utf-8")

    token = os.getenv("NOTION_TOKEN", "")
    client = OpenAIJsonClient(api_key=os.getenv("OPENAI_API_KEY", "")) if os.getenv("OPENAI_API_KEY") else None
    targets = [dict(a, full_text=_article_text(a)) for a in selected]
    enrich_targets, skipped = filter_targets(targets, _env_bool("NIKKEI_FORCE_ARTICLE_GPT_REPROCESS", False))
    fails=[]; processed=0
    if _env_bool("NIKKEI_ENABLE_ARTICLE_GPT_ENRICHMENT", True) and client:
        for a in enrich_targets:
            try:
                out = client.generate_json(model=os.getenv("NIKKEI_ARTICLE_GPT_MODEL", "gpt-5.1-mini"), system_prompt="出力はJSONのみ。", user_prompt=json.dumps({"title":a.get("title"),"url":a.get("url"),"source":a.get("source"),"edition":a.get("edition"),"issue_date":a.get("issue_date"),"published_at":a.get("published_at"),"importance_score":a.get("importance_score"),"priority":a.get("priority"),"matched_rules":a.get("matched_rules"),"tags":a.get("tags"),"full_text":a.get("full_text"),"text_length":len(a.get("full_text",""))}, ensure_ascii=False), max_output_tokens=int(os.getenv("NIKKEI_ARTICLE_GPT_MAX_OUTPUT_TOKENS", "1200")), temperature=float(os.getenv("NIKKEI_ARTICLE_GPT_TEMPERATURE", "0.2")))
                if not validate_article_json(out): raise ValueError("invalid article json")
                a["Summary"], a["Reason to Read"], a["Business Implications"] = out["summary"], out["reason_to_read"], out["business_implications"]
                processed += 1
                if token and a.get("page_id"):
                    p = build_notion_payload(out, os.getenv("NIKKEI_ARTICLE_GPT_MODEL", "gpt-5.1-mini")); p["GPT Processed At"] = datetime.now(timezone.utc).isoformat()
                    try: _notion_update_page(a["page_id"], _simple_props(p), token)
                    except Exception as e: logging.warning("notion_article_update_warning page_id=%s error=%s", a.get("page_id"), e)
            except Exception as e:
                fails.append({"title":a.get("title"),"page_id":a.get("page_id"),"error":str(e)})

    Path("logs/nikkei_article_enrichment_failed.json").write_text(json.dumps(fails, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("logs/nikkei_article_enrichment_summary.json").write_text(json.dumps({"article_gpt_enabled": _env_bool("NIKKEI_ENABLE_ARTICLE_GPT_ENRICHMENT", True), "article_gpt_model": os.getenv("NIKKEI_ARTICLE_GPT_MODEL", "gpt-5.1-mini"), "article_gpt_target_count": len(enrich_targets), "article_gpt_skipped_count": skipped, "article_gpt_processed_count": processed, "article_gpt_failed_count": len(fails)}, ensure_ascii=False, indent=2), encoding="utf-8")

    selected = [a for a in selected if a.get("url")]
    fallback_used = False
    try:
        if _env_bool("NIKKEI_ENABLE_FINAL_REPORT_GPT", True) and client:
            input_articles = build_synthesis_input([{**a, "summary": a.get("Summary") or a.get("summary"), "reason_to_read": a.get("Reason to Read") or a.get("reason_to_read"), "business_implications": a.get("Business Implications") or a.get("business_implications")} for a in selected])
            report = client.generate_json(model=os.getenv("NIKKEI_FINAL_REPORT_MODEL", "gpt-5.1-mini"), system_prompt="JSONのみ", user_prompt=json.dumps(input_articles, ensure_ascii=False), max_output_tokens=int(os.getenv("NIKKEI_FINAL_REPORT_MAX_OUTPUT_TOKENS", "1800")), temperature=float(os.getenv("NIKKEI_FINAL_REPORT_TEMPERATURE", "0.2")))
            if not validate_final_report(report, len(selected)): raise ValueError("invalid final report")
        else:
            fallback_used = True; report = _build_fallback_report(selected, os.getenv("NIKKEI_TARGET_DATE", "auto"))
    except Exception as e:
        logging.warning("final_report_gpt_error=%s", e); fallback_used = True; report = _build_fallback_report(selected, os.getenv("NIKKEI_TARGET_DATE", "auto"))

    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), report, os.getenv("NIKKEI_TARGET_DATE", "auto"))
    Path("logs/nikkei_final_report.html").write_text(html, encoding="utf-8")
    input_hash = hashlib.sha256(json.dumps({"target_date": os.getenv("NIKKEI_TARGET_DATE", "auto"), "article_urls": [x.get("url") for x in selected], "report": report}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    duplicate_enabled = _env_bool("NIKKEI_PREVENT_DUPLICATE_FINAL_REPORT_MAIL", True)
    daily_db=os.getenv("NOTION_DAILY_NEWS_DB_ID","")
    already_sent=False
    if duplicate_enabled and token and daily_db:
        for r in _notion_query_daily(daily_db, token, input_hash):
            props=r.get("properties",{})
            if props.get("Mail Sent",{}).get("checkbox") is True: already_sent=True
    logging.info("duplicate_mail_check_enabled=%s input_hash=%s fallback_used=%s", duplicate_enabled, input_hash, fallback_used)
    mail_sent = False; mail_skipped_reason = ""
    if already_sent:
        mail_skipped_reason="duplicate_input_hash"
    elif _env_bool("NIKKEI_SEND_FINAL_REPORT_MAIL", True):
        subject = f"{os.getenv('NIKKEI_FINAL_REPORT_SUBJECT_PREFIX','【日経事業ブリーフ】')}{datetime.now().strftime('%Y-%m-%d')}（{len(selected)}件）"
        mail_sent = _send_mail(subject, html)
    else:
        mail_skipped_reason = "mail_disabled"

    if _env_bool("NIKKEI_SAVE_FINAL_REPORT_TO_NOTION", True) and token and daily_db:
        try:
            _notion_create_daily_report(daily_db, token, report, selected, input_hash, mail_sent)
        except Exception as e:
            logging.warning("notion_final_report_save_warning=%s", e)

    Path("logs/nikkei_final_report_summary.json").write_text(json.dumps({"final_report_gpt_enabled": _env_bool("NIKKEI_ENABLE_FINAL_REPORT_GPT", True), "final_report_model": os.getenv("NIKKEI_FINAL_REPORT_MODEL", "gpt-5.1-mini"), "final_report_input_article_count": len(selected), "final_report_generated": True, "mail_sent": mail_sent, "mail_skipped_reason": mail_skipped_reason, "input_hash": input_hash, "fallback_used": fallback_used}, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("logs/nikkei_final_report_failed.json").write_text("[]", encoding="utf-8")
    logging.info("mail_sent=%s mail_skipped_reason=%s", mail_sent, mail_skipped_reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
