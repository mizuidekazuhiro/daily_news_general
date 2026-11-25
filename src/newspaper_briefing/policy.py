"""Use grants are scoped to a source, acquisition path, and use."""

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit


USES = {"acquire", "save", "ai", "email"}


def https_url(value: str) -> bool:
    if not isinstance(value, str) or any(ch.isspace() or ord(ch) < 32 or ch in '<>\\' for ch in value):
        return False
    try:
        parts = urlsplit(value)
        return bool(parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
                    and parts.port in {None, 443})
    except ValueError:
        return False


@dataclass(frozen=True)
class Grant:
    id: str
    kind: str
    url: str
    checked_on: str
    basis_urls: list[str]
    permissions: dict[str, bool]
    constraints: str
    name: str = ""
    ref_prefix: str = ""
    adapter: str = ""
    user_confirmation: str = ""

    def allows(self, *uses: str) -> bool:
        return all(self.permissions[use] for use in uses)


Grants = dict[tuple[str, str], Grant]


def read_policy(path: Path | None) -> Grants:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    grants = {}
    for entry in raw["sources"]:
        grant = Grant(**entry)
        key = (grant.kind, grant.id)
        date.fromisoformat(grant.checked_on)
        confirmed = isinstance(grant.user_confirmation, str) and bool(grant.user_confirmation.strip())
        if (key in grants or grant.kind not in {"headline", "rss_description", "primary"}
                or not https_url(grant.url) or not (grant.basis_urls or confirmed)
                or not all(https_url(url) for url in grant.basis_urls)
                or set(grant.permissions) != USES or any(type(value) is not bool for value in grant.permissions.values())
                or not grant.constraints):
            raise ValueError(f"不正な利用条件設定 ({grant.id})")
        if grant.kind == "primary" and (not grant.name or not https_url(grant.ref_prefix)
                                         or not grant.ref_prefix.endswith("/")):
            raise ValueError(f"一次情報の参照先設定が不正です ({grant.id})")
        grants[key] = grant
    return grants


def approved(grants: Grants, kind: str, source: str, url: str, *uses: str) -> bool:
    grant = grants.get((kind, source))
    return bool(grant and grant.url == url and grant.allows(*uses))
