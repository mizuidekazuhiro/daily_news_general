from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SelectionConfig:
    mode: str = "top_importance_rank"
    top_rank: int = 5
    include_ties: bool = True
    min_importance_score: float = 0.0


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
    ranked = rank_candidates([c for c in candidates if c.get("importance_score") is not None])
    if cfg.mode == "threshold":
        selected = [c for c in ranked if _num(c.get("importance_score")) >= cfg.min_importance_score]
        cutoff = cfg.min_importance_score
    else:
        positive = [c for c in ranked if _num(c.get("importance_score")) > 0]
        pool = positive if len(positive) >= cfg.top_rank else ranked
        if not pool:
            selected = []
            cutoff = None
        elif len(pool) <= cfg.top_rank:
            selected = pool
            cutoff = _num(pool[-1].get("importance_score"))
        else:
            cutoff = _num(pool[cfg.top_rank - 1].get("importance_score"))
            selected = [c for c in pool if _num(c.get("importance_score")) > cutoff]
            ties = [c for c in pool if _num(c.get("importance_score")) == cutoff]
            selected.extend(ties if cfg.include_ties else ties[: max(0, cfg.top_rank - len(selected))])
    return selected, {
        "report_selection_mode": cfg.mode,
        "report_candidate_count": len(ranked),
        "report_selected_count": len(selected),
        "report_top_rank": cfg.top_rank,
        "report_include_ties": cfg.include_ties,
        "report_cutoff_importance_score": cutoff,
        "selected_article_titles": [x.get("title", "") for x in selected],
        "selected_article_scores": [_num(x.get("importance_score")) for x in selected],
        "excluded_article_count": max(0, len(ranked) - len(selected)),
    }
