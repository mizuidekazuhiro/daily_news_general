import re
from urllib.parse import urlsplit, urlunsplit
from typing import Optional


def normalize_nikkei_url(url: str) -> str:
    if not url:
        return ""
    p = urlsplit(url)
    path = re.sub(r"/+", "/", p.path).rstrip("/") or "/"
    return urlunsplit((p.scheme or "https", p.netloc.lower(), path, "", ""))


def extract_article_id(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/article/([A-Z0-9]{8,})", url)
    if m:
        return m.group(1)
    m = re.search(r"/([A-Z0-9]{8,})/?$", url)
    return m.group(1) if m else None


def make_body_excerpt(body_text: Optional[str], limit: int = 180) -> Optional[str]:
    if not body_text:
        return None
    compact = re.sub(r"\s+", " ", body_text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def classify_section_from_text(text: str) -> Optional[str]:
    section_map = {
        "経済": ["経済", "macro"],
        "企業": ["企業", "決算", "m&a"],
        "金融": ["金融", "金利", "為替", "日銀"],
        "国際": ["国際", "米国", "中国", "インド", "中東"],
        "産業": ["産業", "鉄鋼", "半導体", "電力", "物流"],
    }
    src = (text or "").lower()
    for section, keys in section_map.items():
        if any(k.lower() in src for k in keys):
            return section
    return None
