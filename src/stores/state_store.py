import json
from pathlib import Path
from typing import Dict


class ProcessedArticleStore:
    def __init__(self, path: str = "data/processed_articles.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state: Dict[str, dict] = {}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def seen(self, key: str, force_refresh: bool = False) -> bool:
        if force_refresh:
            return False
        return key in self.state

    def mark(self, key: str, payload: dict) -> None:
        self.state[key] = payload

    def save(self) -> None:
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")


def make_dedupe_key(source: str, article_id: str = "", normalized_url: str = "", title: str = "", published_at: str = "") -> str:
    if source and article_id:
        return f"{source}|id|{article_id}"
    if source and normalized_url:
        return f"{source}|url|{normalized_url}"
    return f"{source}|tp|{title}|{published_at}"
