from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.final_report_synthesis import build_synthesis_input, validate_final_report_errors
from src.openai_json_client import OpenAIJsonClient, OpenAIJsonError
from src.report_renderer import render_final_report_html
from src.report_selection import SelectionConfig, select_articles

DEFAULTS = {
    "NIKKEI_FINAL_REPORT_MODEL": "gpt-5-mini",
    "NIKKEI_FINAL_REPORT_MAX_OUTPUT_TOKENS": 8000,
    "NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS": 1800,
}
NOISE = [
    "javascript:void(0)", "共有", "文字サイズ", "保存", "印刷", "自動翻訳",
    "日経の記事利用サービス", "Myニュースでまとめ読み", "［有料会員限定］",
]


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else int(value)


def _env_float(name: str, default: float = 0.0) -> float:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else float(value)


def _clean_text(text: str) -> str:
    s = str(text or "")
    for noise in NOISE:
        s = s.replace(noise, " ")
    s = re.sub(r"[\t\r ]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    lines = [line.strip() for line in s.split("\n") if line.strip() and len(line.strip()) > 8]
    return "\n".join(lines)


def _normalize_article(article: dict) -> dict:
    full = _clean_text(article.get("full_text") or article.get("text") or article.get("article_body") or article.get("body") or "")
    return {
        **article,
        "title": article.get("title") or article.get("source_title") or article.get("page_title") or "",
        "full_text": full,
        "Summary": article.get("Summary") or article.get("summary") or "",
        "Reason to Read": article.get("Reason to Read") or article.get("reason_to_read") or "",
        "Business Implications": article.get("Business Implications") or article.get("business_implications") or "",
    }


def _format_date(value: str) -> str:
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}" if value and len(value) == 8 and value.isdigit() else value


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
            merged["notion_url"] = f"https://www.notion.so/{page_id.replace('-', '')}"
        out.append(merged)
    return out


def _build_mail_subject(display_date: str, edition: str) -> str:
    edition_label = "朝刊" if edition == "morning" else "夕刊"
    return f"日経新聞{edition_label}要約｜{display_date}"


def _display_target_date(selected: list[dict], in_articles: list[dict]) -> str:
    for article in selected:
        if article.get("issue_date"):
            return _format_date(str(article.get("issue_date")))
    for article in in_articles:
        if article.get("issue_date"):
            return _format_date(str(article.get("issue_date")))
    return _format_date(_env_str("NIKKEI_TARGET_DATE", "auto"))


def _strip_label_prefix(value: object, labels: tuple[str, ...]) -> str:
    text = str(value or "").strip()
    for label in labels:
        text = re.sub(rf"^\s*{re.escape(label)}\s*[：:]\s*", "", text, flags=re.IGNORECASE)
    return text.strip()


def _normalize_report_labels(report: dict) -> dict:
    out = dict(report or {})
    out["today_key_message"] = _strip_label_prefix(out.get("today_key_message"), ("今日の結論", "本日の結論", "本日の読み筋"))
    out["executive_summary"] = _strip_label_prefix(out.get("executive_summary"), ("背景・文脈", "背景", "文脈"))
    return out


def _build_article_sections_from_input(in_articles: list[dict]) -> list[dict]:
    out = []
    for i, article in enumerate(in_articles, 1):
        summary = (article.get("summary") or "").strip()
        excerpt = (article.get("text_excerpt", "") or "").strip()
        reason = (article.get("reason_to_read") or "").strip()
        implications = (article.get("business_implications") or "").strip()
        one_line = summary or (excerpt[:160] if excerpt else "記事要点を整理中です。")
        why = reason or "記事の変化が需給・投資・価格にどう波及するかを読む意義があります。"
        fact_sentence = summary or excerpt or "記事本文の要点を整理しています。"
        implication_sentence = reason or implications or "需要見通し、投資姿勢、関連コストへの波及が論点になります。"
        outlook_sentence = implications or reason or "取引先・投資先・調達先への影響を分けて読むと、案件前提の変化を把握しやすくなります。"
        out.append({
            "ref_id": f"A{i}",
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "importance_score": article.get("importance_score", 0),
            "what_happened": fact_sentence,
            "one_line_summary": one_line,
            "why_it_matters": why,
            "watch_points": [x for x in [implications, reason] if str(x).strip()][:2],
            "summary_and_implications": f"{fact_sentence} {implication_sentence}\n\n{outlook_sentence}",
            "notion_url": article.get("notion_url", ""),
            "page_id": article.get("page_id", ""),
        })
    return out


def _generate_report(client, input_payload: dict, retry: bool = False):
    model = _env_str("NIKKEI_FINAL_REPORT_MODEL", DEFAULTS["NIKKEI_FINAL_REPORT_MODEL"])
    prompt = (
        "JSONのみ。Markdown禁止。説明文禁止。"
        "メールは毎朝3分で読む新聞ブリーフ。"
        "必須キー: report_title,today_key_message,executive_summary,cross_article_implications,integrated_insights,article_sections,watchlist。"
        "article_sectionsは入力articlesと同じ件数。"
        "ref_idはA1,A2...の順。url/title/importance_scoreは入力値をそのまま保持。"
        "article_sectionsの各要素キー: ref_id,title,url,importance_score,what_happened,why_it_matters,watch_points,summary_and_implications。"
        "today_key_messageは自然な2〜3文で最重要テーマを具体的に書く。先頭に『今日の結論』『本日の結論』等の見出し語を付けない。"
        "executive_summaryは背景・文脈を2文以内で書き、先頭に『背景・文脈』等の見出し語を付けない。"
        "integrated_insightsは『注目すべき変化』として表示されるlist[str]で3〜5個。"
        "integrated_insightsは記事本文に基づく具体表現を使い、同じ示唆の重複を避ける。"
        "article_sectionsではwhat_happened=要約、why_it_matters=なぜ重要か、watch_points=影響と見るべき点として具体的に書く。"
        "watch_pointsは一般論だけにせず、命令調（確認してください・備えてください等）を避ける。"
        "what_happenedは2〜3文、why_it_mattersは1〜2文、watch_pointsは0〜3個。"
        "不明な点は不明と書き、記事にない事実を作らない。"
        "watchlistは0〜5個。重要時のみ出力し、不要なら空配列。"
        "禁止表現: 『確認対象』『追加確認』『備えよ』『攻勢』『再配分』『経済安全保障化』『商機を生む』『優位性が高い』『R&D強化』『人材投資』『国際提携』『商社目線』『ビジネス目線』『So What』『アクション』『重要シグナル』『今日の記事群から見える流れ』『注目ポイント』。"
        "固定分類に無理に当てはめず、記事群に即した自然なビジネスブリーフ文体で書く。"
        "出力前に自己チェック: today_key_messageが2〜3文か、integrated_insightsが3〜5個か、各項目が2文以内か、"
        "具体性があるか、一般論だけで終わっていないか、禁止表現がないか、示唆重複がないか、"
        "記事にない事実を作っていないか、不明点を不明と書いているか確認する。"
    )
    if retry:
        prompt += " 前回は構造検証に失敗。必須キーとarticle_sectionsをすべて含めること。"

    parsed = {}
    raw = ""
    finish_reason = ""
    parse_failed = False
    try:
        response = client.client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(input_payload, ensure_ascii=False)},
            ],
            max_output_tokens=_env_int("NIKKEI_FINAL_REPORT_MAX_OUTPUT_TOKENS", 8000),
        )
        raw = getattr(response, "output_text", "") or ""
        finish_reason = getattr(response, "status", "") or ""
        try:
            parsed = json.loads(raw)
        except Exception:
            parse_failed = True
            raise OpenAIJsonError("parse_failed")
        parsed = _normalize_report_labels(parsed)
        errs = validate_final_report_errors(parsed, len(input_payload["articles"]))
        return parsed, raw, finish_reason, errs, parse_failed
    except Exception as exc:
        return {}, raw or str(exc), finish_reason, [str(exc)], parse_failed


def _split_recipients(env_name: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;\n]", os.getenv(env_name, "")) if x.strip()]


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    logs = Path("logs")
    logs.mkdir(exist_ok=True)
    scored_path = logs / "nikkei_articles_scored.json"
    mail_enabled = _env_bool("NIKKEI_SEND_FINAL_REPORT_MAIL", True)

    if not scored_path.exists():
        payload = {
            "final_report_skipped": True,
            "final_report_skip_reason": "missing_scored_articles_json",
            "mail_enabled": mail_enabled,
            "mail_send_allowed": False,
            "mail_sent": False,
            "mail_skipped_reason": "missing_scored_articles_json",
            "exit_code": 1 if mail_enabled else 0,
        }
        (logs / "nikkei_final_report_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("final_report_skipped: true")
        print("final_report_skip_reason: missing_scored_articles_json")
        print("mail_sent: false")
        return payload["exit_code"]

    data = json.loads(scored_path.read_text(encoding="utf-8"))
    norm = [_normalize_article(a) for a in data]
    norm = _merge_notion_fields(norm, _load_notion_map(logs / "nikkei_save_results.json"))

    selection_cfg = SelectionConfig(
        mode=_env_str("NIKKEI_REPORT_SELECTION_MODE", "top_importance_rank"),
        top_rank=_env_int("NIKKEI_REPORT_TOP_IMPORTANCE_RANK", 5),
        include_ties=_env_bool("NIKKEI_REPORT_INCLUDE_TIES", False),
        min_importance_score=_env_float("NIKKEI_MIN_IMPORTANCE_SCORE_FOR_REPORT", 5.0),
    )
    selected, selection_log = select_articles(norm, selection_cfg)
    selection_log["selected_article_titles"] = [x.get("title", "") for x in selected]
    selection_log["selected_article_scores"] = [x.get("importance_score", 0) for x in selected]
    (logs / "nikkei_report_selection.json").write_text(json.dumps(selection_log, ensure_ascii=False, indent=2), encoding="utf-8")

    if not selected:
        summary = {
            "final_report_skipped": True,
            "final_report_skip_reason": "no_articles_meet_min_importance_score",
            "mail_sent": False,
            "mail_send_allowed": False,
            "mail_skipped_reason": "no_articles_meet_min_importance_score",
            "selection": selection_log,
        }
        (logs / "nikkei_final_report_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("final_report_skipped: true")
        print("final_report_skip_reason: no_articles_meet_min_importance_score")
        return 0

    in_articles = build_synthesis_input(
        [{**a, "summary": a.get("Summary"), "reason_to_read": a.get("Reason to Read"), "business_implications": a.get("Business Implications")} for a in selected],
        text_chars=_env_int("NIKKEI_FINAL_REPORT_ARTICLE_TEXT_CHARS", 1800),
    )
    report_input = {
        "target_date": _env_str("NIKKEI_TARGET_DATE", "auto"),
        "edition": _env_str("NIKKEI_EDITION", ""),
        "article_count": len(in_articles),
        "articles": in_articles,
    }
    (logs / "nikkei_final_report_input.json").write_text(json.dumps(report_input, ensure_ascii=False, indent=2), encoding="utf-8")

    client = OpenAIJsonClient(api_key=_env_str("OPENAI_API_KEY", "")) if _env_str("OPENAI_API_KEY", "") else None
    parsed = {}
    raw = ""
    finish = ""
    errs: list[str] = []
    retry_raw = ""
    retry_parsed = {}
    retry_errs: list[str] = []
    retry_used = False
    success = False
    recovered_missing_article_sections = False

    if client:
        parsed, raw, finish, errs, _ = _generate_report(client, report_input, retry=False)
        core_ok = isinstance(parsed, dict) and all(parsed.get(k) for k in ["report_title", "today_key_message", "executive_summary", "cross_article_implications"])
        if core_ok and "missing_article_sections" in errs:
            parsed["article_sections"] = _build_article_sections_from_input(in_articles)
            recovered_missing_article_sections = True
            errs = validate_final_report_errors(parsed, len(in_articles))
        if not errs:
            success = True
        else:
            retry_used = True
            retry_parsed, retry_raw, _, retry_errs, _ = _generate_report(client, report_input, retry=True)
            retry_core_ok = isinstance(retry_parsed, dict) and all(retry_parsed.get(k) for k in ["report_title", "today_key_message", "executive_summary", "cross_article_implications"])
            if retry_core_ok and "missing_article_sections" in retry_errs:
                retry_parsed["article_sections"] = _build_article_sections_from_input(in_articles)
                recovered_missing_article_sections = True
                retry_errs = validate_final_report_errors(retry_parsed, len(in_articles))
            if not retry_errs:
                parsed = retry_parsed
                success = True

    display_date = _display_target_date(selected, in_articles)
    fallback = not success
    report = parsed if success else {
        "report_title": f"日経事業ブリーフ {display_date}",
        "today_key_message": "最終GPT生成に失敗したため、重要度基準を満たした記事の要点を暫定表示します。",
        "executive_summary": "記事本文に基づく要点のみを表示しています。",
        "cross_article_implications": "",
        "integrated_insights": ["最終GPT生成に失敗したため、記事単位の要点を中心に表示しています。"],
        "watchlist": [],
        "article_sections": _build_article_sections_from_input(in_articles),
    }
    report = _normalize_report_labels(report)
    report["article_sections"] = _merge_notion_fields(
        report.get("article_sections", []),
        {str(a.get("url") or "").strip(): {"notion_url": a.get("notion_url", ""), "page_id": a.get("page_id", "")} for a in norm},
    )

    raw_log = {
        "model": _env_str("NIKKEI_FINAL_REPORT_MODEL", DEFAULTS["NIKKEI_FINAL_REPORT_MODEL"]),
        "finish_reason": finish,
        "raw_response_text": raw,
        "parsed_json": parsed,
        "parsed_top_level_keys": list(parsed.keys()) if isinstance(parsed, dict) else [],
        "validation_errors": errs,
        "retry_used": retry_used,
        "retry_raw_response_text": retry_raw,
        "retry_parsed_json": retry_parsed,
        "retry_validation_errors": retry_errs,
        "retry_parsed_top_level_keys": list(retry_parsed.keys()) if isinstance(retry_parsed, dict) else [],
        "recovered_missing_article_sections": recovered_missing_article_sections,
        "final_validation_errors_after_recovery": errs if success else (retry_errs or errs),
    }
    (logs / "nikkei_final_report_gpt_raw.json").write_text(json.dumps(raw_log, ensure_ascii=False, indent=2), encoding="utf-8")

    # The email is intentionally concise. Full issue inventory remains in Notion/logs;
    # only the selected report articles are rendered in the email.
    html = render_final_report_html(Path("templates/nikkei_final_report_email.html"), report, display_date, all_articles=selected)
    (logs / "nikkei_final_report.html").write_text(html, encoding="utf-8")

    to_recipients = _split_recipients("MAIL_TO")
    cc_recipients = _split_recipients("MAIL_CC")
    bcc_recipients = _split_recipients("MAIL_BCC")
    all_recipients = list(dict.fromkeys(to_recipients + cc_recipients + bcc_recipients))
    fallback_allowed = _env_bool("NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL", True)
    can_send = mail_enabled and bool(to_recipients) and (not fallback or fallback_allowed)
    if not can_send:
        if not mail_enabled:
            reason = "mail_disabled_by_NIKKEI_SEND_FINAL_REPORT_MAIL_false"
        elif not to_recipients:
            reason = "no_mail_recipients"
        else:
            reason = "fallback_mail_blocked_by_NIKKEI_ALLOW_FALLBACK_FINAL_REPORT_MAIL_false"
    else:
        reason = ""

    subject = _build_mail_subject(display_date, _env_str("NIKKEI_EDITION", ""))
    sent = False
    smtp_error = ""
    if can_send:
        try:
            msg = MIMEText(html, "html", "utf-8")
            msg["Subject"] = subject
            msg["From"] = os.getenv("MAIL_FROM", "")
            msg["To"] = ",".join(to_recipients)
            if cc_recipients:
                msg["Cc"] = ",".join(cc_recipients)
            with smtplib.SMTP(os.getenv("MAIL_HOST", "smtp.gmail.com"), int(os.getenv("MAIL_PORT", "587")), timeout=30) as smtp:
                smtp.starttls()
                smtp.login(os.getenv("MAIL_USER") or os.getenv("MAIL_FROM", ""), os.getenv("MAIL_PASSWORD", ""))
                smtp.sendmail(os.getenv("MAIL_FROM", ""), all_recipients, msg.as_string())
            sent = True
        except Exception as exc:
            reason = "smtp_send_failed"
            smtp_error = f"{type(exc).__name__}: {exc}"
            logging.exception("Failed to send Nikkei final report mail")

    exit_code = 0
    if mail_enabled and not sent:
        exit_code = 1

    summary = {
        "final_report_gpt_success": success,
        "final_report_retry_success": retry_used and success,
        "fallback_used": fallback,
        "mail_sent": sent,
        "mail_send_allowed": can_send,
        "mail_subject": subject,
        "mail_skipped_reason": reason,
        "smtp_error": smtp_error,
        "final_report_validation_errors": errs or retry_errs,
        "selection": selection_log,
        "notion_final_report_skipped_reason": "missing_NOTION_DAILY_NEWS_DB_ID" if not _env_str("NOTION_DAILY_NEWS_DB_ID", "") else "not_implemented_in_this_runner",
        "exit_code": exit_code,
    }
    (logs / "nikkei_final_report_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (logs / "nikkei_final_report_failed.json").write_text(
        json.dumps([] if success else [{"stage": "final_report_gpt", "error": ";".join(errs or retry_errs)}], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"final_report_gpt_success: {success}")
    print(f"final_report_validation_errors: {errs or retry_errs}")
    print(f"mail_sent: {sent}")
    print(f"exit_code: {exit_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
