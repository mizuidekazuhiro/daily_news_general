from __future__ import annotations
import json, logging, os, re, smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.final_report_synthesis import build_synthesis_input, validate_final_report_errors
from src.openai_json_client import OpenAIJsonClient, OpenAIJsonError
from src.report_renderer import render_final_report_html
from src.report_selection import SelectionConfig, select_articles

DEFAULTS={"NIKKEI_FINAL_REPORT_MODEL":"gpt-5-mini","NIKKEI_FINAL_REPORT_MAX_OUTPUT_TOKENS":8000,"NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS":1800}
NOISE=["javascript:void(0)","共有","文字サイズ","保存","印刷","自動翻訳","日経の記事利用サービス","Myニュースでまとめ読み","［有料会員限定］"]

def _env_str(n,d=""): v=os.getenv(n); return d if v is None or v.strip()=="" else v.strip()
def _env_bool(n,d=False): v=os.getenv(n); return d if v is None or v.strip()=="" else v.strip().lower() in {"1","true","yes","on"}
def _env_int(n,d=0): v=os.getenv(n); return d if v is None or v.strip()=="" else int(v)

def _clean_text(t:str)->str:
    s=str(t or "")
    for x in NOISE: s=s.replace(x, " ")
    s=re.sub(r"[\t\r ]+", " ", s)
    s=re.sub(r"\n{2,}", "\n", s)
    lines=[ln.strip() for ln in s.split("\n") if ln.strip() and len(ln.strip())>8]
    return "\n".join(lines)

def _normalize_article(a:dict)->dict:
    full=_clean_text(a.get("full_text") or a.get("text") or a.get("article_body") or a.get("body") or "")
    return {**a,"title":a.get("title") or a.get("source_title") or "","full_text":full,"Summary":a.get("Summary") or a.get("summary") or "","Reason to Read":a.get("Reason to Read") or a.get("reason_to_read") or "","Business Implications":a.get("Business Implications") or a.get("business_implications") or ""}

def _format_date(s:str)->str:
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if s and len(s)==8 and s.isdigit() else s



def _load_notion_map(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, str]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        notion_url = str(row.get("notion_url") or "").strip()
        page_id = str(row.get("page_id") or "").strip()
        if not notion_url and page_id:
            notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"
        out[url] = {"notion_url": notion_url, "page_id": page_id}
    return out



def _merge_notion_fields(articles: list[dict], url_map: dict[str, dict[str, str]]) -> list[dict]:
    out = []
    for article in articles:
        url = str(article.get("url") or "").strip()
        merged = {**article, **url_map.get(url, {})}
        notion_url = str(merged.get("notion_url") or "").strip()
        page_id = str(merged.get("page_id") or "").strip()
        if not notion_url and page_id:
            notion_url = f"https://www.notion.so/{page_id.replace('-', '')}"
            merged["notion_url"] = notion_url
        out.append(merged)
    return out



def _build_mail_subject(display_date:str, edition:str)->str:
    edition_label="朝刊" if edition=="morning" else "夕刊"
    return f"日経新聞{edition_label}要約｜{display_date}"

def _display_target_date(selected,in_articles):
    for a in selected:
        if a.get("issue_date"): return _format_date(str(a.get("issue_date")))
    for a in in_articles:
        if a.get("issue_date"): return _format_date(str(a.get("issue_date")))
    return _format_date(_env_str("NIKKEI_TARGET_DATE","auto"))

def _build_article_sections_from_input(in_articles:list[dict])->list[dict]:
    out=[]
    for i,a in enumerate(in_articles,1):
        summary = (a.get("summary") or "").strip()
        excerpt = (a.get("text_excerpt","") or "").strip()
        reason = (a.get("reason_to_read") or "").strip()
        implications = (a.get("business_implications") or "").strip()
        one_line = summary or (excerpt[:160] if excerpt else "記事要点を整理中です。")
        why = reason or "記事の変化が需給・投資・価格にどう波及するかを読む意義があります。"
        action = implications or "案件前提、取引先の投資姿勢、関連コストの動きを並べて把握したい内容です。"
        fact_sentence = summary or excerpt or "記事本文の要点を整理しています。"
        implication_sentence = reason or implications or "商社としては、需要見通し、取引先の投資姿勢、関連コストへの波及を見ておきたい内容です。"
        outlook_sentence = implications or reason or "取引先・投資先・調達先への影響を分けて読むと、案件前提の変化を把握しやすくなります。"
        out.append({
            "ref_id": f"A{i}",
            "title": a.get("title",""),
            "url": a.get("url",""),
            "importance_score": a.get("importance_score",0),
            "what_happened": fact_sentence,
            "one_line_summary": one_line,
            "why_it_matters": why,
            "watch_points": [x for x in [implications, reason] if str(x).strip()][:2],
            "business_action_hint": action,
            "summary_and_implications": (
                f"{fact_sentence} {implication_sentence}\n\n"
                f"{outlook_sentence}"
            ),
            "notion_url": a.get("notion_url", ""),
            "page_id": a.get("page_id", ""),
        })
    return out

def _generate_report(client,input_payload,retry=False):
    model=_env_str("NIKKEI_FINAL_REPORT_MODEL",DEFAULTS["NIKKEI_FINAL_REPORT_MODEL"])
    prompt=(
        "JSONのみ。Markdown禁止。説明文禁止。"
        "メールは毎朝3分で読む新聞ブリーフ。"
        "必須キー: report_title,today_key_message,executive_summary,cross_article_implications,integrated_insights,article_sections,watchlist。"
        "article_sectionsは入力articlesと同じ件数。"
        "ref_idはA1,A2...の順。url/title/importance_scoreは入力値をそのまま保持。"
        "article_sectionsの各要素キー: ref_id,title,url,importance_score,what_happened,why_it_matters,watch_points,summary_and_implications。"
        "today_key_messageは自然な2〜3文とし、見出し語や命令調を避ける。"
        "today_key_messageは『今日の記事群から見える流れ』として表示される前提で、自然な2〜3文で書く。"
        "executive_summaryは『背景・文脈』として表示される前提で書く。integrated_insightsは『注目すべき変化』として表示されるlist[str]で3〜5個。"
        "integrated_insightsは記事本文に基づく具体表現を使い、同じ示唆の重複を避ける。"
        "article_sectionsではwhat_happened=要約、why_it_matters=なぜ重要か、watch_points=影響と見るべき点として具体的に書く。"
        "watch_pointsは一般論だけにせず、命令調（確認してください・備えてください等）を避ける。"
        "what_happenedは2〜3文、why_it_mattersは1〜2文、watch_pointsは0〜3個。"
        "不明な点は不明と書き、記事にない事実を作らない。"
        "watchlistは0〜5個。重要時のみ出力し、不要なら空配列。"
        "禁止表現: 『確認対象』『追加確認』『備えよ』『攻勢』『再配分』『経済安全保障化』『商機を生む』『優位性が高い』『R&D強化』『人材投資』『国際提携』『商社目線』『ビジネス目線』『So What』『アクション』『本日の結論』『重要シグナル』。"
        "固定分類に無理に当てはめず、記事群に即した自然なビジネスブリーフ文体で書く。"
        "出力前に自己チェック: today_key_messageが2〜3文か、integrated_insightsが3〜5個か、各項目が2文以内か、"
        "具体性があるか、一般論だけで終わっていないか、禁止表現がないか、示唆重複がないか、"
        "記事にない事実を作っていないか、不明点を不明と書いているか確認する。"
    )
    if retry:
        prompt += " 前回はarticle_sections欠落のため失敗。必ずarticle_sectionsを含めること。"
    parsed={}; raw=""; finish_reason=""; parse_failed=False
    try:
        resp=client.client.responses.create(model=model,input=[{"role":"system","content":prompt},{"role":"user","content":json.dumps(input_payload,ensure_ascii=False)}],max_output_tokens=_env_int("NIKKEI_FINAL_REPORT_MAX_OUTPUT_TOKENS",8000))
        raw=getattr(resp,"output_text","") or ""
        finish_reason=getattr(resp,"status","") or ""
        try:
            parsed=json.loads(raw)
        except Exception:
            parse_failed=True
            raise OpenAIJsonError("parse_failed")
        errs=validate_final_report_errors(parsed, len(input_payload["articles"]))
        return parsed, raw, finish_reason, errs, parse_failed
    except Exception as e:
        return {}, raw or str(e), finish_reason, [str(e)], parse_failed

def main()->int:
    logging.basicConfig(level=logging.INFO)
    scored_path = Path("logs/nikkei_articles_scored.json")
    if not scored_path.exists():
        Path("logs").mkdir(exist_ok=True)
        mail_enabled = _env_bool("NIKKEI_SEND_FINAL_REPORT_MAIL", True)
        payload = {
            "final_report_skipped": True,
            "final_report_skip_reason": "missing_scored_articles_json",
            "mail_enabled": mail_enabled,
            "mail_send_allowed": False,
            "mail_sent": False,
            "mail_skipped_reason": "missing_scored_articles_json",
            "exit_code": 0,
        }
        Path("logs/nikkei_final_report_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("final_report_skipped: true")
        print("final_report_skip_reason: missing_scored_articles_json")
        print("mail_sent: false")
        return 0
    data=json.loads(scored_path.read_text(encoding="utf-8"))
    norm=[_normalize_article(a) for a in data]
    notion_map = _load_notion_map(Path("logs/nikkei_save_results.json"))
    norm=_merge_notion_fields(norm, notion_map)
    sel,log=select_articles(norm, SelectionConfig(mode="top_importance_rank", top_rank=5, include_ties=False, min_importance_score=0))
    sel=sel[:5]
    log["report_selected_count"]=len(sel); log["selected_article_titles"]= [x.get("title","") for x in sel]; log["selected_article_scores"]= [x.get("importance_score",0) for x in sel]
    Path("logs").mkdir(exist_ok=True)
    Path("logs/nikkei_report_selection.json").write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding="utf-8")
    in_articles=build_synthesis_input([{**a,"summary":a.get("Summary"),"reason_to_read":a.get("Reason to Read"),"business_implications":a.get("Business Implications")} for a in sel], text_chars=_env_int("NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS",1800))
    report_input={"target_date":_env_str("NIKKEI_TARGET_DATE","auto"),"edition":_env_str("NIKKEI_EDITION",""),"article_count":len(in_articles),"articles":in_articles}
    Path("logs/nikkei_final_report_input.json").write_text(json.dumps(report_input,ensure_ascii=False,indent=2),encoding="utf-8")
    client=OpenAIJsonClient(api_key=_env_str("OPENAI_API_KEY","")) if _env_str("OPENAI_API_KEY","") else None

    parsed={}; raw=""; finish=""; errs=[]; retry_raw=""; retry_parsed={}; retry_errs=[]; retry_used=False; success=False
    recovered_missing_article_sections=False
    if client:
        parsed, raw, finish, errs, _ = _generate_report(client, report_input, retry=False)
        core_ok = isinstance(parsed, dict) and all(parsed.get(k) for k in ["report_title","today_key_message","executive_summary","cross_article_implications"])
        if core_ok and "missing_article_sections" in errs:
            parsed["article_sections"] = _build_article_sections_from_input(in_articles)
            recovered_missing_article_sections=True
            errs=validate_final_report_errors(parsed, len(in_articles))
        if not errs:
            success=True
        else:
            retry_used=True
            retry_parsed, retry_raw, _, retry_errs, _ = _generate_report(client, report_input, retry=True)
            retry_core_ok = isinstance(retry_parsed, dict) and all(retry_parsed.get(k) for k in ["report_title","today_key_message","executive_summary","cross_article_implications"])
            if retry_core_ok and "missing_article_sections" in retry_errs:
                retry_parsed["article_sections"] = _build_article_sections_from_input(in_articles)
                recovered_missing_article_sections=True
                retry_errs=validate_final_report_errors(retry_parsed, len(in_articles))
            if not retry_errs:
                parsed=retry_parsed; success=True

    display_date=_display_target_date(sel,in_articles)
    fallback=not success
    report=parsed if success else {"report_title":f"日経事業ブリーフ {display_date}","today_key_message":"最終GPT生成に失敗したため、重要記事の要点を整理して表示します。","executive_summary":"重要記事の変化点を確認し、影響範囲を見直してください。","cross_article_implications":"必要に応じて投資・事業・リスク観点で追加確認してください。","integrated_insights":["最終GPT生成に失敗したため、重要記事の要点を暫定表示しています。","記事本文とリンク先を確認し、判断に必要な追加情報を補完してください。"],"article_sections":_build_article_sections_from_input(in_articles)}
    report["article_sections"] = _merge_notion_fields(report.get("article_sections", []), {str(a.get("url") or "").strip(): {"notion_url": a.get("notion_url", ""), "page_id": a.get("page_id", "")} for a in norm})

    raw_log={"model":_env_str("NIKKEI_FINAL_REPORT_MODEL",DEFAULTS["NIKKEI_FINAL_REPORT_MODEL"]),"finish_reason":finish,"raw_response_text":raw,"parsed_json":parsed,"parsed_top_level_keys":list(parsed.keys()) if isinstance(parsed,dict) else [],"validation_errors":errs,"retry_used":retry_used,"retry_raw_response_text":retry_raw,"retry_parsed_json":retry_parsed,"retry_validation_errors":retry_errs,"retry_parsed_top_level_keys":list(retry_parsed.keys()) if isinstance(retry_parsed,dict) else [],"recovered_missing_article_sections":recovered_missing_article_sections,"final_validation_errors_after_recovery":(errs if success else (retry_errs or errs))}
    Path("logs/nikkei_final_report_gpt_raw.json").write_text(json.dumps(raw_log,ensure_ascii=False,indent=2),encoding="utf-8")

    all_articles = [{**a, "summary": a.get("Summary") or a.get("summary") or "", "reason_to_read": a.get("Reason to Read") or a.get("reason_to_read") or "", "business_implications": a.get("Business Implications") or a.get("business_implications") or ""} for a in norm]
    html=render_final_report_html(Path("templates/nikkei_final_report_email.html"), report, display_date, all_articles=all_articles)
    Path("logs/nikkei_final_report.html").write_text(html,encoding="utf-8")
    recipients=[x.strip() for x in re.split(r"[,;\n]",os.getenv("MAIL_TO","")) if x.strip()]
    mail_enabled=_env_bool("NIKKEI_SEND_FINAL_REPORT_MAIL",True); fallback_allowed=_env_bool("NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL",True)
    can_send=mail_enabled and bool(recipients) and (not fallback or fallback_allowed)
    reason="" if can_send else ("no_mail_recipients" if not recipients else "fallback_mail_blocked_by_NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL_false" if fallback and not fallback_allowed else "mail_disabled_by_NIKKEI_SEND_FINAL_REPORT_MAIL_false")
    subj=_build_mail_subject(display_date, _env_str("NIKKEI_EDITION",""))
    sent=False
    if can_send:
        try:
            msg=MIMEText(html,"html","utf-8"); msg["Subject"]=subj; msg["From"]=os.getenv("MAIL_FROM",""); msg["To"]=",".join(recipients)
            with smtplib.SMTP(os.getenv("MAIL_HOST","smtp.gmail.com"),int(os.getenv("MAIL_PORT","587")),timeout=30) as s:
                s.starttls(); s.login(os.getenv("MAIL_USER") or os.getenv("MAIL_FROM",""), os.getenv("MAIL_PASSWORD","")); s.sendmail(os.getenv("MAIL_FROM",""), recipients, msg.as_string())
            sent=True
        except Exception as e:
            reason="smtp_send_failed"
    summary={"final_report_gpt_success":success,"final_report_retry_success":retry_used and success,"fallback_used":fallback,"mail_sent":sent,"mail_send_allowed":can_send,"mail_subject":subj,"mail_skipped_reason":reason,"final_report_validation_errors":errs or retry_errs,"notion_final_report_skipped_reason":"missing_NOTION_DAILY_NEWS_DB_ID" if not _env_str("NOTION_DAILY_NEWS_DB_ID","") else ""}
    Path("logs/nikkei_final_report_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    Path("logs/nikkei_final_report_failed.json").write_text(json.dumps([] if success else [{"stage":"final_report_gpt","error":";".join(errs or retry_errs)}],ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"final_report_gpt_success: {success}")
    print(f"final_report_validation_errors: {errs or retry_errs}")
    print(f"mail_sent: {sent}")
    return 0

if __name__=="__main__": raise SystemExit(main())
