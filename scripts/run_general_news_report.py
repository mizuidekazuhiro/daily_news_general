from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.report_selection import SelectionConfig, select_articles


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    source = Path("logs/nikkei_articles_scored.json")
    if not source.exists():
        logging.error("pipeline_stage=selection final_decision=fail final_decision_reason=missing_scored_articles")
        return 1
    candidates = json.loads(source.read_text(encoding="utf-8"))
    cfg = SelectionConfig(
        mode=os.getenv("GENERAL_REPORT_SELECTION_MODE", "top_importance_rank"),
        top_rank=int(os.getenv("GENERAL_REPORT_TOP_IMPORTANCE_RANK", "5")),
        include_ties=os.getenv("GENERAL_REPORT_INCLUDE_TIES", "true").lower() == "true",
        min_importance_score=float(os.getenv("GENERAL_REPORT_MIN_IMPORTANCE_SCORE", "0")),
    )
    selected, log_data = select_articles(candidates, cfg)
    Path("logs").mkdir(exist_ok=True)
    Path("logs/general_report_selection.json").write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("pipeline_stage=selection target_date=%s selected_article_count=%s", os.getenv("GENERAL_REPORT_TARGET_DATE", "auto"), len(selected))
    logging.info("final_decision=continue final_decision_reason=selection_completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
