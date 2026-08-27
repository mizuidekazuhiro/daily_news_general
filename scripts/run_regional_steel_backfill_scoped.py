from __future__ import annotations

import logging
import os

import scripts.run_regional_steel_backfill as regional


_ORIGINAL_LOAD_EXISTING = regional._load_existing_insights


def filter_existing_for_region(
    existing: list[regional.Insight],
    region: str,
) -> list[regional.Insight]:
    region_name = str(region or "").strip()
    if not region_name:
        return existing
    return [item for item in existing if region_name in item.country]


def _load_existing_scoped(notion, db_id: str, max_existing: int):
    existing = _ORIGINAL_LOAD_EXISTING(notion, db_id, max_existing)
    region_name = str(os.getenv("REGIONAL_STEEL_BACKFILL_REGION") or "").strip()
    scoped = filter_existing_for_region(existing, region_name)
    logging.info(
        "regional_existing_scope region=%s all=%s scoped=%s",
        region_name,
        len(existing),
        len(scoped),
    )
    return scoped


def main() -> int:
    regional._load_existing_insights = _load_existing_scoped
    return regional.main()


if __name__ == "__main__":
    raise SystemExit(main())
