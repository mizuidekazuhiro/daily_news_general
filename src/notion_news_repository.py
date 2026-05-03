from __future__ import annotations

from typing import Dict, Iterable, Set


OPTIONAL_PROPERTIES = {
    "Report Selected", "Report Rank", "Report Selection Reason", "Report Cutoff Score", "Report Date",
    "GPT Model", "GPT Processed At", "GPT Error", "Final Report Model", "Final Report Generated At",
    "Executive Summary", "Key Message", "Input Hash", "Mail Sent", "Mail Sent At",
}

REQUIRED_DAILY_REPORT_PROPERTIES = {"Title", "Date"}


def filter_known_properties(payload: Dict[str, object], available: Iterable[str]) -> Dict[str, object]:
    known: Set[str] = set(available)
    return {k: v for k, v in payload.items() if k in known}


def validate_required(available: Iterable[str]) -> None:
    missing = REQUIRED_DAILY_REPORT_PROPERTIES - set(available)
    if missing:
        raise ValueError(f"Missing required Notion properties: {sorted(missing)}")
