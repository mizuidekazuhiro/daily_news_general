from __future__ import annotations
import sys, hashlib, json, logging, os, re, smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict
import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.article_enrichment import build_notion_payload, filter_targets, validate_article_json
from src.final_report_synthesis import build_synthesis_input, validate_final_report_errors
from src.openai_json_client import OpenAIJsonClient
from src.report_renderer import render_final_report_html
from src.report_selection import SelectionConfig, select_articles

DEFAULTS={"NIKKEI_FINAL_REPORT_MODEL":"gpt-5-mini","NIKKEI_FINAL_REPORT_MAX_OUTPUT_TOKENS":5000,"NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS":1800,"NIKKEI_SEND_FINAL_REPORT_MAIL":True,"NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL":False}

def _env_str(n,d=""): v=os.getenv(n); return d if v is None or v.strip()=="" else v.strip()
def _env_bool(n,d=False): v=os.getenv(n); return d if v is None or v.strip()=="" else v.strip().lower() in {"1","true","yes","on"}
def _env_int(n,d=0): v=os.getenv(n); return d if v is None or v.strip()=="" else int(v)

def _normalize_article(a:dict)->dict:
    return {**a,"title":a.get("title") or a.get("source_title") or a.get("page_title") or "","full_text":a.get("full_text") or a.get("text") or a.get("article_body") or a.get("body") or "","Summary":a.get("Summary") or a.get("summary") or "","Reason to Read":a.get("Reason to Read") or a.get("reason_to_read") or "","Business Implications":a.get("Business Implications") or a.get("business_implications") or ""}

def _format_date(v:str)->str:
    s=str(v or "").strip();
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s)==8 and s.isdigit() else s

def _resolve_target_date(selected:list[dict])->str:
    for a in selected:
        if a.get("issue_date"): return _format_date(str(a.get("issue_date")))
    p=Path("logs/nikkei_final_report_input.json")
    if p.exists():
        try:
            for a in json.loads(p.read_text(encoding="utf-8")).get("articles",[]):
                if a.get("issue_date"): return _format_date(str(a.get("issue_date")))
        except Exception: pass
    return _format_date(_env_str("NIKKEI_TARGET_DATE","auto"))

def _build_fallback(selected, display_date):
    secs=[]
    for i,a in enumerate(selected,1):
        excerpt=(a.get("full_text") or "").strip()[:450] or "本文確認対象"
        secs.append({"ref_id":f"A{i}","title":a.get("title",""),"url":a.get("url",""),"importance_score":a.get("importance_score",0),"one_line_summary":a.get("Summary") or excerpt[:180],"why_it_matters":a.get("Reason to Read") or f"重要度スコア{a.get('importance_score',0)}。{','.join(a.get('matched_rules') or []) or '需給・投資・政策'}の確認対象。","business_action_hint":a.get("Business Implications") or "価格・需給・政策・投資判断への影響を確認。","text_excerpt":excerpt})
    return {"report_title":f"日経事業ブリーフ {display_date}","executive_summary":"最終GPT生成に失敗したため、重要記事の簡易一覧を表示します。記事本文・重要度・一致ルールをもとに確認してください。","today_key_message":"最終GPT生成に失敗したため、重要記事の簡易一覧を表示します。記事本文・重要度・一致ルールをもとに確認してください。","cross_article_implications":"重要度上位記事を横断確認してください。","priority_watch_items":["価格動向","需給動向","政策動向"],"article_sections":secs}

def _fallback_mail_decision(fallback_used, fallback_mail_allowed, already_sent, send_enabled, has_recipients):
    if not send_enabled: return "mail_disabled_by_NIKKEI_SEND_FINAL_REPORT_MAIL_false"
    if not has_recipients: return "no_mail_recipients"
    if already_sent: return "duplicate_input_hash"
    if fallback_used and not fallback_mail_allowed: return "fallback_mail_blocked_by_NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL_false"
    return "send"

def main()->int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data=json.loads(Path("logs/nikkei_articles_scored.json").read_text(encoding="utf-8"))
    normalized=[_normalize_article(a) for a in data]
    cfg=SelectionConfig(mode="top_importance_rank", top_rank=_env_int("NIKKEI_REPORT_TOP_IMPORTANCE_RANK",5), include_ties=_env_bool("NIKKEI_REPORT_INCLUDE_TIES",False), min_importance_score=0)
    selected, sel_log=select_articles(normalized,cfg); selected=[dict(a) for a in selected][:5]
    Path("logs").mkdir(exist_ok=True)
    Path("logs/nikkei_report_selection.json").write_text(json.dumps({**sel_log,"report_selection_mode":"top_importance_rank","report_selected_count":len(selected),"report_top_rank":5,"report_include_ties":False},ensure_ascii=False,indent=2),encoding="utf-8")
    client=OpenAIJsonClient(api_key=_env_str("OPENAI_API_KEY","")) if _env_str("OPENAI_API_KEY","") else None
    in_articles=build_synthesis_input(selected,text_chars=_env_int("NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS",1800))
    final_report_input={"target_date":_env_str("NIKKEI_TARGET_DATE","auto"),"edition":_env_str("NIKKEI_EDITION",""),"article_count":len(in_articles),"article_text_chars":_env_int("NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS",1800),"articles":in_articles}
    Path("logs/nikkei_final_report_input.json").write_text(json.dumps(final_report_input,ensure_ascii=False,indent=2),encoding="utf-8")
    final_failed=[]; fallback_used=False; final_gpt_success=False; retry_used=False; retry_success=False; report={}
    if client:
        for attempt in [1,2]:
            try:
                retry_used = attempt==2
                sp="JSONのみ。article_sectionsはA1-A5を必ず含む。"
                rp=client.generate_json(model=_env_str("NIKKEI_FINAL_REPORT_MODEL","gpt-5-mini"),system_prompt=sp,user_prompt=json.dumps(final_report_input,ensure_ascii=False),max_output_tokens=_env_int("NIKKEI_FINAL_REPORT_MAX_OUTPUT_TOKENS",8000),temperature=0.2)
                errs=validate_final_report_errors(rp, len(selected))
                if errs: raise ValueError("validation failed: "+";".join(errs))
                report=rp; final_gpt_success=True; retry_success = attempt==2; break
            except Exception as e:
                final_failed.append({"stage":"final_report_gpt_retry" if attempt==2 else "final_report_gpt","error":str(e)})
    if not final_gpt_success:
        fallback_used=True
        report=_build_fallback(selected,_resolve_target_date(selected))
    display_target_date=_resolve_target_date(selected)
    html=render_final_report_html(Path("templates/nikkei_final_report_email.html"), report, display_target_date)
    Path("logs/nikkei_final_report.html").write_text(html,encoding="utf-8")
    recipients=[x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_TO", "")) if x.strip()] + [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_CC", "")) if x.strip()] + [x.strip() for x in re.split(r"[,;\n]", os.getenv("MAIL_BCC", "")) if x.strip()]
    decision=_fallback_mail_decision(fallback_used,_env_bool("NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL",False),False,_env_bool("NIKKEI_SEND_FINAL_REPORT_MAIL",True),bool(recipients))
    mail_send_allowed=decision=="send"; mail_sent=False; mail_reason="" if mail_send_allowed else decision
    prefix="【日経朝刊ブリーフ】" if _env_str("NIKKEI_EDITION","")=="morning" else "【日経夕刊ブリーフ】"
    subj=f"{prefix}{display_target_date}｜重要{len(selected)}件"; subj=("[fallback] "+subj) if fallback_used else subj
    if mail_send_allowed:
        try:
            msg=MIMEText(html,"html","utf-8"); msg["Subject"]=subj; msg["From"]=os.getenv("MAIL_FROM",""); msg["To"]=os.getenv("MAIL_TO","")
            with smtplib.SMTP(os.getenv("MAIL_HOST","smtp.gmail.com"), int(os.getenv("MAIL_PORT","587")), timeout=30) as s:
                s.starttls(); s.login(os.getenv("MAIL_USER") or os.getenv("MAIL_FROM",""), os.getenv("MAIL_PASSWORD","")); s.sendmail(os.getenv("MAIL_FROM",""), recipients, msg.as_string())
            mail_sent=True
        except Exception as e:
            mail_reason="smtp_send_failed"; final_failed.append({"stage":"smtp_send","error_type":type(e).__name__,"error":str(e),"MAIL_HOST":os.getenv("MAIL_HOST","smtp.gmail.com"),"MAIL_PORT":os.getenv("MAIL_PORT","587"),"recipient_count":len(recipients)})
    summary={"final_report_skipped":False,"final_report_input_article_count":len(selected),"final_report_gpt_success":final_gpt_success,"final_report_retry_used":retry_used,"final_report_retry_success":retry_success,"fallback_used":fallback_used,"fallback_mail_allowed":_env_bool("NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL",False),"display_target_date":display_target_date,"mail_enabled":_env_bool("NIKKEI_SEND_FINAL_REPORT_MAIL",True),"mail_send_allowed":mail_send_allowed,"mail_recipient_count":len(recipients),"mail_subject":subj,"mail_sent":mail_sent,"mail_skipped_reason":mail_reason,"notion_final_report_saved":False,"notion_final_report_skipped_reason":"missing_NOTION_DAILY_NEWS_DB_ID" if not _env_str("NOTION_DAILY_NEWS_DB_ID","") else ""}
    Path("logs/nikkei_final_report_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    Path("logs/nikkei_final_report_failed.json").write_text(json.dumps(final_failed,ensure_ascii=False,indent=2),encoding="utf-8")
    gpt_reason=final_failed[-1]["error"] if final_failed else ""
    print(f"final_report_model: {_env_str('NIKKEI_FINAL_REPORT_MODEL','gpt-5-mini')}")
    print("final_report_skipped: false")
    print(f"final_report_input_article_count: {len(selected)}")
    print(f"final_report_gpt_success: {final_gpt_success}")
    print(f"final_report_gpt_error_reason: {gpt_reason}")
    print(f"final_report_validation_errors: {[x.get('error','') for x in final_failed if 'validation failed' in x.get('error','')]}")
    print(f"final_report_failed: {final_failed}")
    print(f"final_report_retry_used: {retry_used}")
    print(f"final_report_retry_success: {retry_success}")
    print(f"fallback_used: {fallback_used}")
    print(f"display_target_date: {display_target_date}")
    print(f"mail_enabled: {summary['mail_enabled']}")
    print(f"fallback_mail_allowed: {summary['fallback_mail_allowed']}")
    print(f"mail_send_allowed: {mail_send_allowed}")
    print(f"mail_recipient_count: {len(recipients)}")
    print(f"mail_subject: {subj}")
    print(f"mail_sent: {mail_sent}")
    print(f"mail_skipped_reason: {mail_reason}")
    return 0

if __name__=="__main__": raise SystemExit(main())
