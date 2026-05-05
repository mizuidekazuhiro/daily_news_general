from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import hashlib, json, logging, os, re, smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List
import requests
from src.article_enrichment import build_notion_payload, filter_targets, validate_article_json
from src.final_report_synthesis import build_synthesis_input, validate_final_report, validate_final_report_errors
from src.openai_json_client import OpenAIJsonClient
from src.report_renderer import render_final_report_html
from src.report_selection import SelectionConfig, select_articles

NOTION_VERSION = "2022-06-28"

DEFAULTS = {
    "NIKKEI_ENABLE_FINAL_REPORT": True,
    "NIKKEI_ENABLE_ARTICLE_GPT_ENRICHMENT": True,
    "NIKKEI_FORCE_ARTICLE_GPT_REPROCESS": False,
    "NIKKEI_ARTICLE_GPT_MODEL": "gpt-5-mini",
    "NIKKEI_ARTICLE_GPT_MAX_OUTPUT_TOKENS": 1200,
    "NIKKEI_ARTICLE_GPT_TEMPERATURE": 0.2,
    "NIKKEI_ENABLE_FINAL_REPORT_GPT": True,
    "NIKKEI_FINAL_REPORT_MODEL": "gpt-5-mini",
    "NIKKEI_FINAL_REPORT_MAX_OUTPUT_TOKENS": 5000,
    "NIKKEI_FINAL_REPORT_TEMPERATURE": 0.2,
    "NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS": 1800,
    "NIKKEI_FORCE_FINAL_REPORT_REGENERATE": False,
    "NIKKEI_SEND_FINAL_REPORT_MAIL": False,
    "NIKKEI_SAVE_FINAL_REPORT_TO_NOTION": True,
    "NIKKEI_PREVENT_DUPLICATE_FINAL_REPORT_MAIL": True,
    "NIKKEI_FINAL_REPORT_SUBJECT_PREFIX": "【日経新聞ブリーフ】",
    "NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL": False,
}


def _env_str(name: str, default: str | None = None) -> str:
    if default is None:
        default = str(DEFAULTS.get(name, ""))
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip()


def _env_bool(name: str, default: bool | None = None) -> bool:
    if default is None:
        default = bool(DEFAULTS.get(name, False))
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int | None = None) -> int:
    if default is None:
        default = int(DEFAULTS.get(name, 0))
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def _env_float(name: str, default: float | None = None) -> float:
    if default is None:
        default = float(DEFAULTS.get(name, 0.0))
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return float(v)


def _pick(a: dict, keys: list[str], d: str = "") -> str:
    for k in keys:
        v = a.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return d


def _normalize_article(a: dict) -> dict:
    return {
        **a,
        "title": _pick(a, ["title", "source_title", "page_title"]),
        "full_text": _pick(a, ["full_text", "text", "article_body", "body"]),
        "gpt_processed_norm": bool(a.get("gpt_processed") or a.get("GPT Processed") or a.get("gptProcessed")),
        "Summary": _pick(a, ["Summary", "summary"]),
        "Reason to Read": _pick(a, ["Reason to Read", "reason_to_read"]),
        "Business Implications": _pick(a, ["Business Implications", "business_implications"]),
    }


def _notion_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}


def _fetch_db_schema(db_id: str, token: str) -> Dict[str, Any]:
    r = requests.get(f"https://api.notion.com/v1/databases/{db_id}", headers=_notion_headers(token), timeout=30)
    r.raise_for_status()
    return r.json().get("properties", {})


def _article_update_props(summary: str, reason: str, biz: str, model: str) -> Dict[str, Any]:
    return {
        "Summary": {"rich_text": [{"text": {"content": summary[:1800]}}]},
        "Reason to Read": {"rich_text": [{"text": {"content": reason[:1800]}}]},
        "Business Implications": {"rich_text": [{"text": {"content": biz[:1800]}}]},
        "GPT Processed": {"checkbox": True},
        "GPT Model": {"rich_text": [{"text": {"content": model}}]},
        "GPT Processed At": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
    }


def _daily_props(report: dict, selected_count: int, input_hash: str, mail_sent: bool) -> Dict[str, Any]:
    p = {
        "Title": {"title": [{"text": {"content": report.get("report_title", "日経事業ブリーフ")[:150]}}]},
        "Date": {"date": {"start": datetime.now().date().isoformat()}},
        "Article Count": {"number": selected_count},
        "Selected Article Count": {"number": selected_count},
        "Final Report Model": {"rich_text": [{"text": {"content": _env_str("NIKKEI_FINAL_REPORT_MODEL")}}]},
        "Executive Summary": {"rich_text": [{"text": {"content": report.get("executive_summary", "")[:1800]}}]},
        "Key Message": {"rich_text": [{"text": {"content": report.get("today_key_message", "")[:1800]}}]},
        "Input Hash": {"rich_text": [{"text": {"content": input_hash}}]},
        "Mail Sent": {"checkbox": mail_sent},
    }
    if mail_sent:
        p["Mail Sent At"] = {"date": {"start": datetime.now(timezone.utc).isoformat()}}
    return p


def _filter_by_schema(payload: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in payload.items():
        if k in schema:
            out[k] = v
        else:
            logging.warning("notion_property_missing property=%s", k)
    return out


def _notion_update_page(page_id: str, props: Dict[str, Any], token: str) -> None:
    requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=_notion_headers(token), json={"properties": props}, timeout=30).raise_for_status()


def _notion_query_daily(db_id: str, token: str, input_hash: str) -> list[dict]:
    q = {"filter": {"property": "Input Hash", "rich_text": {"equals": input_hash}}}
    r = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query", headers=_notion_headers(token), json=q, timeout=30)
    return [] if r.status_code >= 400 else r.json().get("results", [])


def _notion_blocks(report: dict, selected: list[dict]) -> list[dict]:
    blocks = []
    def add(txt: str):
        for chunk in [txt[i:i+1800] for i in range(0, len(txt), 1800)] or [""]:
            blocks.append({"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":chunk}}]}})
    add("本日の要点\n" + report.get("today_key_message", ""))
    add("全体ブリーフ\n" + report.get("executive_summary", ""))
    add("横断的な業務示唆\n" + report.get("cross_article_implications", ""))
    add("優先確認事項\n" + "\n".join(f"- {x}" for x in report.get("priority_watch_items", [])))
    for i,a in enumerate(selected,1):
        blocks.append({"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":f"A{i}：{a.get('title','')} ","link":None}},{"type":"text","text":{"content":a.get('url',''),"link":{"url":a.get('url','')}}}]}})
    return blocks


def _send_mail(subject: str, html: str) -> bool:
    to = [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_TO", "")) if x.strip()]
    cc = [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_CC", "")) if x.strip()]
    bcc = [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_BCC", "")) if x.strip()]
    rec = to+cc+bcc
    if not rec: return False
    msg = MIMEText(html, "html", "utf-8"); msg["Subject"] = subject; msg["From"] = os.getenv("MAIL_FROM", ""); msg["To"] = ", ".join(to)
    if cc: msg["Cc"] = ", ".join(cc)
    with smtplib.SMTP(os.getenv("MAIL_HOST", "smtp.gmail.com"), int(os.getenv("MAIL_PORT", "587")), timeout=30) as s:
        s.starttls(); s.login(os.getenv("MAIL_USER") or os.getenv("MAIL_FROM", ""), os.getenv("MAIL_PASSWORD", "")); s.sendmail(os.getenv("MAIL_FROM", ""), rec, msg.as_string())
    return True




def _fallback_mail_decision(fallback_used: bool, fallback_mail_allowed: bool, already_sent: bool, send_enabled: bool) -> str:
    if already_sent:
        return "duplicate_input_hash"
    if fallback_used and not fallback_mail_allowed:
        return "fallback_mail_blocked"
    if not send_enabled:
        return "mail_disabled"
    return "send"

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not _env_bool("NIKKEI_ENABLE_FINAL_REPORT"): return 0
    data = json.loads(Path("logs/nikkei_articles_scored.json").read_text(encoding="utf-8"))
    normalized = [_normalize_article(a) for a in data]
    cfg = SelectionConfig(mode=_env_str("NIKKEI_REPORT_SELECTION_MODE", "top_importance_rank"), top_rank=_env_int("NIKKEI_REPORT_TOP_IMPORTANCE_RANK", 5), include_ties=_env_bool("NIKKEI_REPORT_INCLUDE_TIES", True), min_importance_score=_env_float("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", 0))
    selected, sel_log = select_articles(normalized, cfg)
    selected_working = [dict(a) for a in selected]
    Path("logs").mkdir(exist_ok=True)
    Path("logs/nikkei_report_selection.json").write_text(json.dumps(sel_log, ensure_ascii=False, indent=2), encoding="utf-8")

    token = _env_str("NOTION_TOKEN", ""); article_db = _env_str("NOTION_ARTICLE_DB_ID", ""); daily_db = _env_str("NOTION_DAILY_NEWS_DB_ID", "")
    client = OpenAIJsonClient(api_key=_env_str("OPENAI_API_KEY", "")) if _env_str("OPENAI_API_KEY", "") else None
    article_schema = _fetch_db_schema(article_db, token) if token and article_db else {}
    notion_ok = notion_ng = 0

    gpt_candidates = [dict(a, full_text=a.get("full_text", ""), gpt_processed=a.get("gpt_processed_norm", False)) for a in selected_working]
    targets, skipped = filter_targets(gpt_candidates, _env_bool("NIKKEI_FORCE_ARTICLE_GPT_REPROCESS"))
    fails=[]; processed=0
    if _env_bool("NIKKEI_ENABLE_ARTICLE_GPT_ENRICHMENT") and client:
        sp = """あなたは日本語の業務分析アシスタント。出力はJSONのみ。schema:{summary,reason_to_read,business_implications}。summaryは120-220字で本文事実のみ。reason_to_readは80-160字で具体。business_implicationsは180-320字で記事由来論点のみ。数字/企業/国名/価格/数量の捏造禁止。Markdown禁止。"""
        for t in targets:
            try:
                out = client.generate_json(model=_env_str("NIKKEI_ARTICLE_GPT_MODEL"), system_prompt=sp, user_prompt=json.dumps({"title":t.get("title"),"url":t.get("url"),"source":t.get("source"),"edition":t.get("edition"),"issue_date":t.get("issue_date"),"importance_score":t.get("importance_score"),"priority":t.get("priority"),"matched_rules":t.get("matched_rules"),"full_text":t.get("full_text")}, ensure_ascii=False), max_output_tokens=_env_int("NIKKEI_ARTICLE_GPT_MAX_OUTPUT_TOKENS"), temperature=_env_float("NIKKEI_ARTICLE_GPT_TEMPERATURE"))
                if not validate_article_json(out): raise ValueError("invalid article json")
                for x in selected_working:
                    if x.get("url") == t.get("url"):
                        x["Summary"], x["Reason to Read"], x["Business Implications"] = out["summary"], out["reason_to_read"], out["business_implications"]
                processed += 1
                if token and t.get("page_id"):
                    props = _filter_by_schema(_article_update_props(out["summary"], out["reason_to_read"], out["business_implications"], _env_str("NIKKEI_ARTICLE_GPT_MODEL")), article_schema)
                    try: _notion_update_page(t["page_id"], props, token); notion_ok += 1
                    except Exception as e: notion_ng += 1; fails.append({"page_id":t.get("page_id"),"error":str(e)})
            except Exception as e:
                fails.append({"title":t.get("title"),"error":str(e)})

    # remove missing url
    selected_working = [x for x in selected_working if x.get("url")]
    final_failed=[]; fallback_used=False; final_gpt_success=False
    try:
        if _env_bool("NIKKEI_ENABLE_FINAL_REPORT_GPT") and client:
            final_report_article_text_chars = _env_int("NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS")
            in_articles = build_synthesis_input(
                [
                    {
                        **a,
                        "summary": a.get("Summary"),
                        "reason_to_read": a.get("Reason to Read"),
                        "business_implications": a.get("Business Implications"),
                    }
                    for a in selected_working
                ],
                text_chars=final_report_article_text_chars,
            )
            final_report_input = {
                "target_date": _env_str("NIKKEI_TARGET_DATE", "auto"),
                "edition": _env_str("NIKKEI_EDITION", ""),
                "article_count": len(in_articles),
                "article_text_chars": final_report_article_text_chars,
                "articles": in_articles,
            }
            Path("logs/nikkei_final_report_input.json").write_text(
                json.dumps(final_report_input, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            sp2 = """あなたは日本語の業務分析アシスタントです。出力はJSONのみ。
入力された重要記事の title / importance_score / matched_rules / text_excerpt を根拠に、日経ブリーフを作成してください。
記事本文にない数字、企業名、国名、価格、数量、時期を捏造しないでください。
商社・素材・エネルギー・物流・金融・政策リスクの観点で横断整理してください。
「商社目線の読み」という表現は禁止です。

必ず以下のJSON schemaで返してください。
{
  "report_title": "string",
  "executive_summary": "string",
  "today_key_message": "string",
  "cross_article_implications": "string",
  "priority_watch_items": ["string"],
  "article_sections": [
    {
      "ref_id": "A1",
      "title": "string",
      "url": "string",
      "importance_score": 0,
      "one_line_summary": "string",
      "why_it_matters": "string",
      "business_action_hint": "string"
    }
  ]
}

article_sections は入力記事数と同じ数にしてください。
ref_id は A1, A2, ... の順にしてください。
url は入力されたurlを必ず保持してください。"""
            report = client.generate_json(
                model=_env_str("NIKKEI_FINAL_REPORT_MODEL"),
                system_prompt=sp2,
                user_prompt=json.dumps(final_report_input, ensure_ascii=False),
                max_output_tokens=_env_int("NIKKEI_FINAL_REPORT_MAX_OUTPUT_TOKENS"),
                temperature=_env_float("NIKKEI_FINAL_REPORT_TEMPERATURE"),
            )
            validation_errors = validate_final_report_errors(report, len(selected_working))
            if validation_errors:
                raise ValueError("invalid final report: " + "; ".join(validation_errors[:12]))
            final_gpt_success=True
        else:
            fallback_used=True; report={"report_title":f"日経事業ブリーフ {_env_str('NIKKEI_TARGET_DATE','auto')}","executive_summary":"最終GPT無効のため簡易レポート。","today_key_message":"上位記事を確認。","cross_article_implications":"共通論点を確認。","priority_watch_items":["A1確認","A2確認","A3確認"],"article_sections":[{"ref_id":f"A{i}","title":a.get("title",""),"url":a.get("url",""),"importance_score":a.get("importance_score",0),"one_line_summary":a.get("Summary",""),"why_it_matters":a.get("Reason to Read",""),"business_action_hint":a.get("Business Implications","")} for i,a in enumerate(selected_working,1)]}
    except Exception as e:
        fallback_used=True; final_failed.append({"stage":"final_report_gpt","error":str(e)})
        report={"report_title":f"日経事業ブリーフ {_env_str('NIKKEI_TARGET_DATE','auto')}","executive_summary":"最終GPT失敗のため簡易レポート。","today_key_message":"上位記事を確認。","cross_article_implications":"共通論点を確認。","priority_watch_items":["A1確認","A2確認","A3確認"],"article_sections":[{"ref_id":f"A{i}","title":a.get("title",""),"url":a.get("url",""),"importance_score":a.get("importance_score",0),"one_line_summary":a.get("Summary",""),"why_it_matters":a.get("Reason to Read",""),"business_action_hint":a.get("Business Implications","")} for i,a in enumerate(selected_working,1)]}

    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), report, _env_str("NIKKEI_TARGET_DATE", "auto")); html_path="logs/nikkei_final_report.html"; Path(html_path).write_text(html, encoding="utf-8")
    input_hash = hashlib.sha256(json.dumps({"target_date":_env_str("NIKKEI_TARGET_DATE","auto"),"selected":selected_working,"report":report},ensure_ascii=False,sort_keys=True).encode()).hexdigest()

    duplicate_enabled=_env_bool("NIKKEI_PREVENT_DUPLICATE_FINAL_REPORT_MAIL"); already_sent=False
    if duplicate_enabled and token and daily_db:
        for r in _notion_query_daily(daily_db, token, input_hash):
            if r.get("properties",{}).get("Mail Sent",{}).get("checkbox") is True: already_sent=True

    fallback_mail_allowed=_env_bool("NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL")
    mail_enabled=_env_bool("NIKKEI_SEND_FINAL_REPORT_MAIL")
    decision = _fallback_mail_decision(fallback_used, fallback_mail_allowed, already_sent, mail_enabled)
    mail_sent=False; mail_reason=""; subj=""

    to = [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_TO", "")) if x.strip()]
    cc = [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_CC", "")) if x.strip()]
    bcc = [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_BCC", "")) if x.strip()]
    mail_recipient_count=len(to)+len(cc)+len(bcc)

    edition = _env_str("NIKKEI_EDITION", "").lower()
    if edition == "morning":
        default_prefix = "【日経朝刊ブリーフ】"
    elif edition == "evening":
        default_prefix = "【日経夕刊ブリーフ】"
    else:
        default_prefix = "【日経新聞ブリーフ】"

    prefix = _env_str("NIKKEI_FINAL_REPORT_SUBJECT_PREFIX", default_prefix)
    subj=f"{prefix}{datetime.now().strftime('%Y-%m-%d')}｜重要{len(selected_working)}件"
    if fallback_used:
        subj="[fallback] "+subj

    if decision=="send":
        mail_sent=_send_mail(subj, html); mail_reason="" if mail_sent else "no_recipients_or_send_failed"
    else:
        mail_reason=decision

    notion_final_saved=False
    if _env_bool("NIKKEI_SAVE_FINAL_REPORT_TO_NOTION") and token and daily_db:
        try:
            schema=_fetch_db_schema(daily_db, token)
            props=_filter_by_schema(_daily_props(report, len(selected_working), input_hash, mail_sent), schema)
            children=_notion_blocks(report, selected_working)
            requests.post("https://api.notion.com/v1/pages",headers=_notion_headers(token),json={"parent":{"database_id":daily_db},"properties":props,"children":children},timeout=30).raise_for_status()
            notion_final_saved=True
        except Exception as e:
            final_failed.append({"stage":"notion_daily_save","error":str(e)})

    Path("logs/nikkei_article_enrichment_summary.json").write_text(json.dumps({"selected_article_count":len(selected_working),"article_gpt_candidate_count":len(gpt_candidates),"article_gpt_skipped_count":skipped,"article_gpt_target_count":len(targets),"article_gpt_processed_count":processed,"article_gpt_failed_count":len(fails)},ensure_ascii=False,indent=2),encoding="utf-8")
    Path("logs/nikkei_article_enrichment_failed.json").write_text(json.dumps(fails,ensure_ascii=False,indent=2),encoding="utf-8")
    Path("logs/nikkei_final_report_summary.json").write_text(json.dumps({"pipeline_scope":"nikkei_only","selected_article_count":len(selected_working),"article_gpt_candidate_count":len(gpt_candidates),"article_gpt_skipped_count":skipped,"article_gpt_target_count":len(targets),"article_gpt_processed_count":processed,"article_gpt_failed_count":len(fails),"final_report_gpt_success":final_gpt_success,"fallback_used":fallback_used,"fallback_mail_allowed":fallback_mail_allowed,"notion_article_update_success_count":notion_ok,"notion_article_update_failed_count":notion_ng,"notion_final_report_saved":notion_final_saved,"mail_enabled":mail_enabled,"mail_recipient_count":mail_recipient_count,"mail_subject":subj,"mail_sent":mail_sent,"mail_skipped_reason":mail_reason,"input_hash":input_hash,"html_output_path":html_path},ensure_ascii=False,indent=2),encoding="utf-8")
    Path("logs/nikkei_final_report_failed.json").write_text(json.dumps(final_failed,ensure_ascii=False,indent=2),encoding="utf-8")

    print(f"final_report_model: {_env_str('NIKKEI_FINAL_REPORT_MODEL')}")
    print(f"final_report_input_article_count: {len(selected_working)}")
    print(f"final_report_article_text_chars: {_env_int('NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS')}")
    print(f"final_report_gpt_success: {final_gpt_success}")
    print(f"fallback_used: {fallback_used}")
    print(f"mail_enabled: {mail_enabled}")
    print(f"mail_recipient_count: {mail_recipient_count}")
    print(f"mail_subject: {subj}")
    print(f"mail_sent: {mail_sent}")
    print(f"mail_skipped_reason: {mail_reason}")
    print(f"fallback_used: {fallback_used}")
    print(f"fallback_mail_allowed: {fallback_mail_allowed}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
