from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.run_regional_steel_backfill import explicit_region_evidence, get_region_profile
from scripts.run_regional_steel_backfill_filtered import (
    explicit_region_and_steel_evidence,
    explicit_region_evidence_strict,
    explicit_steel_evidence,
    filter_existing_for_region,
)
from src.intelligence_pipeline import Article


def _article(
    title: str,
    body: str,
    country: list[str] | None = None,
    tags: list[str] | None = None,
) -> Article:
    return Article(
        source="general",
        page_id="11111111-1111-1111-1111-111111111111",
        title=title,
        published_at="2026-08-27",
        importance_score=5.0,
        source_name="test",
        country=country or [],
        tags=tags or [],
        body=body,
        notion_url="",
    )


def test_japan_scope_accepts_domestic_steel_project():
    profile = get_region_profile("Japan")
    article = _article(
        "JFE Steel upgrades Kurashiki works",
        "JFE Steel will invest in new equipment at its Kurashiki district in Japan to improve steelmaking capacity.",
        ["Japan"],
    )
    assert explicit_region_evidence(article, profile)
    assert explicit_region_evidence_strict(article, profile)


def test_japan_scope_does_not_match_nippon_steel_name_alone():
    profile = get_region_profile("Japan")
    article = _article(
        "Nippon Steel advances overseas expansion",
        "Nippon Steel announced an investment at an integrated steel plant in India. The project concerns Indian operations only.",
        ["India", "Japan"],
    )
    assert not explicit_region_evidence_strict(article, profile)


def test_japan_scope_does_not_match_japanese_company_name_alone():
    profile = get_region_profile("Japan")
    article = _article(
        "日本製鉄、インド製鉄所への投資を拡大",
        "インド国内の製鉄所で能力増強を実施する。現地の生産能力と設備投資を拡大する計画だ。",
        ["India", "Japan"],
    )
    assert not explicit_region_evidence_strict(article, profile)


def test_japan_scope_rejects_overseas_domestic_production_wording():
    profile = get_region_profile("Japan")
    article = _article(
        "JSW、インドで大型電炉",
        "インド鉄鋼大手JSWスチールはアンドラプラデシュ州で大型電炉を建設する。需要に応えるため国内生産を増強する。JSWは日本を含めた海外の鉄鋼大手との連携も強める。",
        ["India", "Japan"],
    )
    assert explicit_steel_evidence(article)
    assert not explicit_region_evidence_strict(article, profile)
    assert not explicit_region_and_steel_evidence(article, profile)


def test_japan_scope_rejects_macro_story_where_domestic_market_is_japan_context_only():
    profile = get_region_profile("Japan")
    article = _article(
        "ドル円新常態 企業の海外志向、鮮明に",
        "日本企業が成長機会を海外に求めている。国内市場の縮小を背景に、JFEスチールはインド鉄鋼大手JSWスチールの子会社への出資を決めた。投資先はインドである。",
        ["India", "Japan"],
    )
    assert explicit_steel_evidence(article)
    assert not explicit_region_evidence_strict(article, profile)
    assert not explicit_region_and_steel_evidence(article, profile)


def test_japan_scope_rejects_japanese_steelmaker_overseas_project():
    profile = get_region_profile("Japan")
    article = _article(
        "Thai government and Nippon Steel advance green-industry project",
        "The Japanese steelmaker will work with Thai partners on a low-carbon steel project in Thailand. The investment and plant are in Thailand.",
        ["Japan", "Thailand"],
    )
    assert not explicit_region_evidence_strict(article, profile)
    assert not explicit_region_and_steel_evidence(article, profile)


def test_vietnam_scope_ignores_related_story_tail_on_india_steel_article():
    profile = get_region_profile("Vietnam")
    article = _article(
        "JFEスチール、インド製鉄JVを完了",
        (
            "JFEスチールはインドのJSWスチールと進めてきた一貫製鉄所の合弁事業化を完了した。"
            "対象はオディシャ州の製鉄所で、粗鋼年産450万トンの能力を持ち、熱延鋼板や冷延鋼板を生産する。"
            "成長が続くインド市場の需要取り込みが狙いで、既存設備の活用により早期の収益化を図る。"
            "JFEの製造技術とJSWの運営能力を組み合わせ、高付加価値品の拡大と生産性向上を進める。"
            "新会社はインド国内の一貫製鉄拠点として運営される。"
            "■「より詳しい情報を知りたい」場合はこちら。"
            "JFEスチール、台湾企業のベトナム製鉄所PJに資本参加"
        ),
        ["India", "Vietnam"],
    )
    assert explicit_steel_evidence(article)
    assert not explicit_region_evidence_strict(article, profile)
    assert not explicit_region_and_steel_evidence(article, profile)


def test_vietnam_scope_keeps_real_event_before_related_story_tail():
    profile = get_region_profile("Vietnam")
    article = _article(
        "Vietnam imposes provisional duty on Chinese hot-rolled steel",
        (
            "Vietnam's government imposed a provisional anti-dumping duty on hot-rolled steel imports from China. "
            "The policy changes import conditions for the Vietnamese steel market and affects domestic mills and buyers. "
            "The measure applies to steel entering Vietnam and is part of the country's trade-remedy policy. "
            "■「より詳しい情報を知りたい」場合はこちら。"
            "関連記事: Indian steelmakers expand capacity."
        ),
        ["Vietnam", "China"],
    )
    assert explicit_steel_evidence(article)
    assert explicit_region_evidence_strict(article, profile)
    assert explicit_region_and_steel_evidence(article, profile)


def test_india_profile_still_rejects_indian_owner_nationality_only():
    profile = get_region_profile("India")
    article = _article(
        "Tata Steel IJmuiden support package",
        "The Netherlands package was agreed with Tata's Indian owners and applies to the IJmuiden plant in Europe.",
        ["India", "EU"],
    )
    assert not explicit_region_evidence(article, profile)


def test_steel_gate_accepts_japan_eaf_project():
    profile = get_region_profile("Japan")
    article = _article(
        "日本製鉄、九州製鉄所に大型電炉",
        "日本国内の製鉄所で高炉から電炉へ転換し、年間約200万トンの鋼材を生産する。",
        ["Japan"],
    )
    assert explicit_steel_evidence(article)
    assert explicit_region_and_steel_evidence(article, profile)


def test_steel_gate_rejects_unrelated_japan_factory_expansion():
    profile = get_region_profile("Japan")
    article = _article(
        "JX金属、半導体材料の国内生産を10倍へ",
        "日本国内の工場で光通信向け半導体材料の生産能力を増強する。AIデータセンター需要に対応する。",
        ["Japan"],
        ["Japan", "Capacity Expansion", "Data Center"],
    )
    assert explicit_region_evidence_strict(article, profile)
    assert not explicit_steel_evidence(article)
    assert not explicit_region_and_steel_evidence(article, profile)


def test_steel_gate_accepts_raw_material_story():
    profile = get_region_profile("Japan")
    article = _article(
        "Japan steel industry secures coking coal supply",
        "Steelmakers signed a long-term coking coal supply agreement for blast-furnace operations in Japan.",
        ["Japan"],
    )
    assert explicit_region_and_steel_evidence(article, profile)


def test_noisy_steel_tag_alone_does_not_pass():
    article = _article(
        "Major low-carbon project receives support",
        "The project will build new data-center equipment and start commercial operations in 2028.",
        ["Japan"],
        ["Green Steel", "Steel Plant Investment"],
    )
    assert not explicit_steel_evidence(article)


def test_existing_insights_are_scoped_to_active_region():
    profile = get_region_profile("Japan")
    existing = [
        SimpleNamespace(insight_key="india-only", country=["India"]),
        SimpleNamespace(insight_key="japan-only", country=["Japan"]),
        SimpleNamespace(insight_key="cross-border", country=["Japan", "United States"]),
        SimpleNamespace(insight_key="empty", country=[]),
    ]
    selected = filter_existing_for_region(existing, profile)
    assert [x.insight_key for x in selected] == ["japan-only", "cross-border"]


def test_filtered_wrapper_can_run_as_script_without_module_import_failure():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("NOTION_TOKEN", None)
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "run_regional_steel_backfill_filtered.py")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "ModuleNotFoundError" not in combined
    assert "NOTION_TOKEN and OPENAI_API_KEY are required" in combined
