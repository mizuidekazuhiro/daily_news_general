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

def _display_target_date(selected,in_articles):
    for a in selected:
        if a.get("issue_date"): return _format_date(str(a.get("issue_date")))
    for a in in_articles:
        if a.get("issue_date"): return _format_date(str(a.get("issue_date")))
    return _format_date(_env_str("NIKKEI_TARGET_DATE","auto"))

def _build_article_sections_from_input(in_articles:list[dict])->list[dict]:
    out=[]
    for i,a in enumerate(in_articles,1):
        out.append({
            "ref_id": f"A{i}",
            "title": a.get("title",""),
            "url": a.get("url",""),
            "importance_score": a.get("importance_score",0),
            "one_line_summary": a.get("summary") or (a.get("text_excerpt","")[:160] or "本文確認対象"),
            "why_it_matters": a.get("reason_to_read") or "需給・投資・政策影響の確認対象。",
            "business_action_hint": a.get("business_implications") or "価格・需給・政策・投資判断への影響を確認。",
        })
    return out

def _generate_report(client,input_payload,retry=False):
    model=_env_str("NIKKEI_FINAL_REPORT_MODEL",DEFAULTS["NIKKEI_FINAL_REPORT_MODEL"])
    prompt=(
        "JSONのみ。Markdown禁止。説明文禁止。"
        "必須キー: report_title,today_key_message,executive_summary,cross_article_implications,article_sections。"
        "article_sectionsは入力articlesと同じ件数。"
        "ref_idはA1,A2...の順。url/title/importance_scoreは入力値をそのまま保持。"
        "article_sectionsの各要素キー: ref_id,title,url,importance_score,one_line_summary,why_it_matters,business_action_hint。"
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
    data=json.loads(Path("logs/nikkei_articles_scored.json").read_text(encoding="utf-8"))
    norm=[_normalize_article(a) for a in data]
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
    report=parsed if success else {"report_title":f"日経事業ブリーフ {display_date}","today_key_message":"最終GPT生成に失敗したため、重要記事の簡易一覧を表示します。記事本文・重要度・一致ルールをもとに確認してください。","executive_summary":"最終GPT生成に失敗したため、重要記事の簡易一覧を表示します。記事本文・重要度・一致ルールをもとに確認してください。","cross_article_implications":"重要記事の横断確認を実施してください。","priority_watch_items":["価格","需給","政策"],"article_sections":_build_article_sections_from_input(in_articles)}

    raw_log={"model":_env_str("NIKKEI_FINAL_REPORT_MODEL",DEFAULTS["NIKKEI_FINAL_REPORT_MODEL"]),"finish_reason":finish,"raw_response_text":raw,"parsed_json":parsed,"parsed_top_level_keys":list(parsed.keys()) if isinstance(parsed,dict) else [],"validation_errors":errs,"retry_used":retry_used,"retry_raw_response_text":retry_raw,"retry_parsed_json":retry_parsed,"retry_validation_errors":retry_errs,"retry_parsed_top_level_keys":list(retry_parsed.keys()) if isinstance(retry_parsed,dict) else [],"recovered_missing_article_sections":recovered_missing_article_sections,"final_validation_errors_after_recovery":(errs if success else (retry_errs or errs))}
    Path("logs/nikkei_final_report_gpt_raw.json").write_text(json.dumps(raw_log,ensure_ascii=False,indent=2),encoding="utf-8")

    html=render_final_report_html(Path("templates/nikkei_final_report_email.html"), report, display_date)
    Path("logs/nikkei_final_report.html").write_text(html,encoding="utf-8")
    recipients=[x.strip() for x in re.split(r"[,;\n]",os.getenv("MAIL_TO","")) if x.strip()]
    mail_enabled=_env_bool("NIKKEI_SEND_FINAL_REPORT_MAIL",True); fallback_allowed=_env_bool("NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL",True)
    can_send=mail_enabled and bool(recipients) and (not fallback or fallback_allowed)
    reason="" if can_send else ("no_mail_recipients" if not recipients else "fallback_mail_blocked_by_NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL_false" if fallback and not fallback_allowed else "mail_disabled_by_NIKKEI_SEND_FINAL_REPORT_MAIL_false")
    prefix="【日経朝刊ブリーフ】" if _env_str("NIKKEI_EDITION","")=="morning" else "【日経夕刊ブリーフ】"
    subj=f"{prefix}{display_date}｜重要{len(sel)}件"; subj=("[fallback] "+subj) if fallback else subj
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
