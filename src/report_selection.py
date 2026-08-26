from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class SelectionConfig:
    mode: str = "top_importance_rank"
    top_rank: int = 5
    include_ties: bool = True
    min_importance_score: float = 5.0


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def rank_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda x: (
            -_num(x.get("importance_score"), -10**9),
            -_num(x.get("priority"), 0),
            str(x.get("published_at") or x.get("issue_date") or ""),
            str(x.get("title") or ""),
        ),
        reverse=False,
    )


def select_articles(candidates: List[Dict[str, Any]], cfg: SelectionConfig) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    ranked = rank_candidates(
        [
            c for c in candidates
            if c.get("importance_score") is not None
            and not bool(c.get("exclude_candidate"))
            and _num(c.get("importance_score")) >= cfg.min_importance_score
        ]
    )

    if cfg.mode == "threshold":
        selected = ranked
        cutoff = cfg.min_importance_score
    else:
        if not ranked:
            selected = []
            cutoff = None
        elif cfg.top_rank <= 0 or len(ranked) <= cfg.top_rank:
            selected = ranked
            cutoff = _num(ranked[-1].get("importance_score")) if ranked else None
        else:
            cutoff = _num(ranked[cfg.top_rank - 1].get("importance_score"))
            selected = [c for c in ranked if _num(c.get("importance_score")) > cutoff]
            ties = [c for c in ranked if _num(c.get("importance_score")) == cutoff]
            selected.extend(ties if cfg.include_ties else ties[: max(0, cfg.top_rank - len(selected))])

    if cfg.mode != "threshold" and not cfg.include_ties and cfg.top_rank > 0:
        selected = selected[: cfg.top_rank]

    return selected, {
        "report_selection_mode": cfg.mode,
        "report_candidate_count": len(ranked),
        "report_selected_count": len(selected),
        "report_top_rank": cfg.top_rank,
        "report_include_ties": cfg.include_ties,
        "report_min_importance_score": cfg.min_importance_score,
        "report_cutoff_importance_score": cutoff,
        "selected_article_titles": [x.get("title", "") for x in selected],
        "selected_article_scores": [_num(x.get("importance_score")) for x in selected],
        "excluded_article_count": max(0, len(candidates) - len(selected)),
    }
