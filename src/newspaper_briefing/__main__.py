"""Run a standalone, preview-first newspaper briefing."""

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
import sys

from .bulletins import individual_entries, select_issue_entries
from .delivery import deliver
from .editions import COMPARISON_MISSING, DEFAULT_STORY_LIMIT, evening_entries, finalized_snapshot, morning_snapshot
from .evidence import matches_story, story_evidence, verified_document
from .event_ai import refine
from .feeds import JST, collect, diagnostics, load_settings, read_capture, timestamp
from .output import render, story_row, write_json, write_text
from .primary import collect_primary, empty_primary, read_primary
from .stories import cluster, rank, select_articles
from .summaries import build_summaries, morning_overview


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "newspaper_briefing"


def run(args: argparse.Namespace) -> tuple[int, Path]:
    settings = load_settings(args.config, args.source_policy)
    if args.as_of:
        end = args.as_of
    elif args.offline_input:
        end = None
    else:
        end = datetime.now(JST).replace(microsecond=0)
    if args.offline_input:
        capture = read_capture(args.offline_input, settings)
    else:
        capture = collect(settings)
    end = end or capture.captured_at.replace(microsecond=0)
    path = args.output_dir / end.date().isoformat() / args.edition / end.strftime("%H%M%S")
    morning = args.edition == "morning"
    snapshot = None if morning else morning_snapshot(args.snapshot, args.output_dir, end, settings)
    start = end - timedelta(hours=24 if morning or snapshot else (args.window_hours or 12))
    if snapshot:
        start = min(start, timestamp(snapshot["as_of"]) - timedelta(hours=24))
    ok, articles, undated, popularity, counts = select_articles(capture, settings, start, end)
    details = diagnostics(capture, settings, counts)
    details.update(window_start=start, as_of=end)
    partial = any(row["state"] != "ok" for row in details["publishers"]) or any(r.failure for r in capture.feeds)
    collection_state = ("partial" if partial else "ok") if ok else "failed"
    if args.headlines_only or not ok:
        primary = empty_primary("disabled" if args.headlines_only else "collection_failed")
    elif args.primary_input:
        try:
            primary = read_primary(args.primary_input)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            primary = empty_primary("input_failed")
            primary["sources"] = [{"failures": [type(exc).__name__]}]
    elif not args.offline_input or args.collect_primary:
        primary = collect_primary(articles, settings, start, end)
    else:
        primary = empty_primary("offline")
    primary["documents"] = [doc for doc in primary["documents"] if verified_document(doc, settings, end)]
    details["primary"] = {
        "state": primary["state"], "sources": primary["sources"],
        "matching": [{"source": doc["source"], "link": doc["link"], "issued_at": doc["issued_at"]}
                     for doc in primary["documents"] if any(matches_story(doc, list(a.records)) for a in articles)],
    }

    use_ai = args.ai and not args.offline_input
    ranked = rank(cluster(articles, settings) if ok else [], settings, end)
    events, details["event_matching_ai"] = refine([row["story"] for row in ranked], settings, use_ai and ok)
    if details["event_matching_ai"]["merged"]:
        ranked = rank(events, settings, end)
    entries = [{"scored": score, "row": story_row(score, settings, popularity) | {"rank": position},
                "published_at": min(article.published_at for article in score["story"].articles)}
               for position, score in enumerate(ranked, 1)]

    def evidence(entry: dict) -> list[dict]:
        if "facts" not in entry:
            entry["facts"] = story_evidence(entry["scored"], settings, primary["documents"], end, not args.headlines_only)
        return entry["facts"]

    comparison, compare_state, changes = "", "morning", {}
    if morning:
        selected = select_issue_entries(entries, evidence, args.limit)
    else:
        compare_state = ("matched" if snapshot else "missing") if ok else "collection_failed"
        if compare_state == "matched":
            comparison = f"同日朝版 {snapshot['as_of']} の掲載内容との差分"
        elif compare_state == "missing":
            comparison = f"差分比較なし。{COMPARISON_MISSING}。"
        selected, changes = evening_entries(entries, snapshot, end, evidence, collection_state, limit=args.limit)
    ai_state = build_summaries(selected, evidence, use_ai and not args.publication_mail)
    top = [entry["row"] for entry in selected]
    for entry in selected:
        entry["row"]["published_at"] = entry["published_at"]
    publishers = details["publishers"]
    missing = [row["name"] for row in publishers if row["state"] == "missing"]
    preview = {channel: {"state": "preview"} for channel in ("email", "notion")}
    report = {
        "version": 2, "subject": f"新聞横断ニュース｜{'朝' if morning else '夕'}｜{end.date().isoformat()}",
        "edition": args.edition, "as_of": end, "window_start": start, "captured_at": capture.captured_at,
        "collection_state": collection_state, "comparison": comparison,
        "comparison_state": compare_state, "changes": changes, "overview": morning_overview(selected) if morning else [],
        "coverage": {"available": len(publishers) - len(missing), "total": len(publishers), "missing": missing,
                     "partial": [row["name"] for row in publishers if row["state"] == "partial"]},
        "baseline_as_of": snapshot["as_of"] if snapshot else None,
        "article_count": len(articles), "story_count": len(entries), "undated_count": len(undated),
        "categories": dict(Counter(entry["row"]["category"] for entry in entries)),
        "top_categories": dict(Counter(row["category"] for row in top)),
        "ai_state": ai_state, "offline": bool(args.offline_input), "stories": top,
        "delivery": preview,
    }
    md_page, mail, cited = render(report)
    ranking_rows = [entry["row"] for entry in entries]
    for field, rows in (("ranked_stories", ranking_rows), ("selected_stories", top)):
        story_counts = Counter(publisher for row in rows for publisher in {source["publisher"] for source in row["sources"]})
        for publisher in publishers:
            publisher[field] = story_counts[publisher["id"]]
    report["mail_sha256"] = mail["sha256"]
    write_json(path / "capture.json", capture.to_json())
    write_json(path / "primary.json", primary)
    write_json(path / "ranking.json", ranking_rows)
    write_json(path / "undated.json", [asdict(record) for record in undated])
    write_text(path / "report.md", md_page)
    write_text(path / "report.html", mail["html"])
    write_text(path / "report.txt", mail["text"])
    write_json(path / "mail.json", mail)
    if morning:
        if ok:
            write_json(path / "snapshot.json", finalized_snapshot(individual_entries(selected), end))
        else:
            (path / "snapshot.json").unlink(missing_ok=True)
    enabled = {k for k, flag in (("email", args.send_email), ("notion", args.write_notion)) if flag}
    if enabled and args.offline_input:
        report["delivery"].update({channel: {"state": "blocked", "reason": "オフライン入力"} for channel in enabled})
    elif enabled and not args.publication_mail:
        report["delivery"] = deliver(report, DEFAULT_OUTPUT / "delivery.sqlite3", mail, enabled)
    if args.publication_mail:
        from .publication import write_publication

        digest, results, cited = write_publication(
            path / "delivery", report, entries, settings, args.snapshot, args.output_dir, args.limit,
            stored_primary=primary if args.offline_input or args.headlines_only else None,
            supplement=not args.headlines_only,
            use_ai=use_ai,
            email_ledger=DEFAULT_OUTPUT / "delivery.sqlite3" if args.send_email and not args.offline_input else None,
        )
        report.update(publication_mail_sha256=digest, delivery=results)
    for publisher in publishers:
        publisher["published_stories"] = cited[publisher["id"]]
        publisher["publication_stage"] = ("published" if publisher["published_stories"] else
                                          "selection" if publisher["ranked_stories"] else "collection")
    write_json(path / "diagnostics.json", details)
    write_json(path / "report.json", report)
    if not ok:
        return 2, path
    return (3 if any(r["state"] in {"blocked", "unknown"} for r in report["delivery"].values()) else 0), path


def parse_as_of(value: str) -> datetime:
    try:
        return timestamp(value).replace(microsecond=0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ISO 8601 形式でタイムゾーンを指定してください") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="新聞横断ニュースのローカルプレビュー")
    parser.add_argument("--edition", choices=("morning", "evening"), required=True)
    parser.add_argument("--as-of", type=parse_as_of, help="基準日時（例 2026-11-24T08:00:00+09:00）")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "newspaper_briefing.json")
    parser.add_argument("--source-policy", type=Path, default=ROOT / "config" / "newspaper_briefing_source_policy.json")
    parser.add_argument("--snapshot", type=Path, help="夕版で比較する同日の朝版 snapshot.json")
    parser.add_argument("--offline-input", type=Path, help="保存済み capture.json または RSS フィクスチャ")
    parser.add_argument("--limit", type=int, default=DEFAULT_STORY_LIMIT, help="掲載する出来事の上限（既定 %(default)s 件）")
    parser.add_argument("--window-hours", type=int, choices=range(1, 25), metavar="1–24", help="朝版がない夕版の新着期間")
    parser.add_argument("--headlines-only", action="store_true", help="補足資料を使わず比較用の出力を作る")
    primary = parser.add_mutually_exclusive_group()
    primary.add_argument("--primary-input", type=Path, help="保存済み primary.json を再現に使う")
    primary.add_argument("--collect-primary", action="store_true", help="保存済みフィードに対応する許可済み一次情報だけを取得する")
    parser.add_argument("--ai", action="store_true", help="許可済みの材料で出来事の照合・掲載項目の選択をAIで補助する")
    parser.add_argument("--preview", action="store_true", help="配送フラグとの併用を防ぐ")
    parser.add_argument("--publication-mail", action="store_true", help="メール掲載可能な根拠で同じランキングから本文も生成する")
    parser.add_argument("--send-email", action="store_true", help="専用 SMTP 宛先へのメール配信を有効化")
    parser.add_argument("--write-notion", action="store_true", help="専用 Notion DB への書き込みを有効化")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit は1以上の整数で指定してください")
    if args.snapshot and args.edition != "evening":
        parser.error("--snapshot は夕版で指定してください")
    if args.preview and (args.send_email or args.write_notion):
        parser.error("--preview と配送フラグは併用できません")
    if args.publication_mail and args.write_notion:
        parser.error("--publication-mail と --write-notion は併用できません")
    if args.headlines_only and (args.primary_input or args.collect_primary):
        parser.error("--headlines-only と一次情報の指定は併用できません")
    if args.primary_input and not args.offline_input:
        parser.error("--primary-input は --offline-input と併用してください")
    try:
        code, path = run(args)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"入力または保存先を確認してください ({type(exc).__name__})", file=sys.stderr)
        return 2
    print(f"プレビュー {path.resolve()}")
    if code == 2:
        print("有効な媒体フィードを取得できませんでした。diagnostics.json に取得状況を保存しました。", file=sys.stderr)
    elif code == 3:
        print("配信結果を report.json で確認してください。", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
