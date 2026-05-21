"""Run direct site updates with date-template expansion for ListPageUrls.

This wrapper keeps the existing direct_site_updates.py collection logic intact and
only expands date placeholders in each site's configured ListPageUrls before the
normal collection flow runs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

import direct_site_updates as base


def _target_dt(cfg: Dict[str, Any], now_dt: datetime) -> datetime:
    timezone = cfg.get("timezone")
    if timezone is None:
        return now_dt
    return now_dt.astimezone(timezone)


def expand_url_date_template(url: str, cfg: Dict[str, Any], now_dt: datetime) -> str:
    """Expand date placeholders in a direct-site URL.

    Supported placeholders:
    - {yyyy}, {yy}, {mm}, {m}, {dd}, {d}
    - strftime tokens, for example %Y/%m/%d

    Fixed URLs without placeholders are returned unchanged.
    """
    if not url:
        return url

    dt = _target_dt(cfg, now_dt)
    expanded = url
    replacements = {
        "{yyyy}": dt.strftime("%Y"),
        "{yy}": dt.strftime("%y"),
        "{mm}": dt.strftime("%m"),
        "{m}": str(dt.month),
        "{dd}": dt.strftime("%d"),
        "{d}": str(dt.day),
    }
    for key, value in replacements.items():
        expanded = expanded.replace(key, value)

    if "%" in expanded:
        try:
            expanded = dt.strftime(expanded)
        except ValueError:
            logging.warning(
                "site name=%s url template strftime failed url=%s",
                cfg.get("SiteName"),
                url,
            )
            return url

    return expanded


def expand_list_page_urls(cfg: Dict[str, Any], now_dt: datetime) -> List[str]:
    expanded_urls: List[str] = []
    seen = set()
    for raw_url in cfg.get("ListPageUrls", []):
        expanded = base.normalize_url(expand_url_date_template(raw_url, cfg, now_dt))
        if expanded in seen:
            continue
        seen.add(expanded)
        expanded_urls.append(expanded)
    return expanded_urls


_original_collect_site_items = base.collect_site_items


def collect_site_items_with_expanded_urls(cfg: Dict[str, Any], now_dt: datetime):
    patched_cfg = dict(cfg)
    original_urls = list(cfg.get("ListPageUrls", []))
    patched_cfg["ListPageUrls"] = expand_list_page_urls(cfg, now_dt)
    logging.info(
        "site name=%s list url templates expanded original_urls=%s expanded_urls=%s",
        cfg.get("SiteName"),
        original_urls,
        patched_cfg["ListPageUrls"],
    )
    return _original_collect_site_items(patched_cfg, now_dt)


base.collect_site_items = collect_site_items_with_expanded_urls


if __name__ == "__main__":
    base.main()
