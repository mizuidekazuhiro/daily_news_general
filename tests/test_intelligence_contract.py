from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

import src.intelligence_pipeline as pipeline
import src.intelligence_policy as policy
import src.intelligence_processing as processing
import src.intelligence_rules as rules
from src.intelligence_contract import build_schema, generate_operations_json, validate_operations
from src.intelligence_safety import _ORIGINAL_NORMALIZE_OPERATIONS
from src.openai_json_client import OpenAIJsonClient, OpenAIJsonError


def article(number=1):
    return pipeline.Article(source="general", page_id=f"{number:08d}-1111-1111-1111-111111111111",
                            title="Company opens a steel plant in India", published_at="2026-10-06",
                            importance_score=8, source_name="test", country=["India"], tags=[],
                            body="Company opened a new steel plant in India. Production has started.", notion_url="")


def noop(a):
    return {"action": "noop", "article_refs": [a.ref()], "rule_reason": "No durable new event."}


def create(a):
    return {"action": "create", "matched_existing_key": None, "insight_key": "company|india|new-plant",
            "insight": "Company plant", "company": "Company", "country": ["India"],
            "theme": ["Capacity Expansion"], "event_type": "New Plant", "importance": "High",
            "confidence": "High", "key_facts": "Company opened a new steel plant in India.",
            "what_changed": "Production started.", "business_implication": "New steel supply.",
            "watch_items": "Ramp-up.", "article_refs": [a.ref()], "rule_checks": {},
            "rule_hits": [], "rule_reason": "New durable plant event."}


def response(raw, status="completed", reason=None, refusal=False):
    return SimpleNamespace(id="test-response", status=status,
                           output_text=json.dumps(raw) if not isinstance(raw, str) else raw,
                           incomplete_details=SimpleNamespace(reason=reason) if reason else None,
                           output=[SimpleNamespace(content=[SimpleNamespace(type="refusal")])] if refusal else [])


class Responses:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def client(values):
    result = OpenAIJsonClient.__new__(OpenAIJsonClient)
    result.client = SimpleNamespace(responses=Responses(values))
    return result


@pytest.fixture(autouse=True)
def isolated_policy():
    old = rules.get_active_rules()
    rules.set_active_rules(rules.RuleSet(rules=(), create_min_score=0))
    yield
    if old is None:
        rules.clear_active_rules()
    else:
        rules.set_active_rules(old)


def generate(tmp_path, c, candidates=None):
    candidates = candidates or [article()]
    return generate_operations_json(client=c, candidates=candidates, existing=[],
                                    normalize=policy.policy_normalize_operations,
                                    diagnostics_path=tmp_path / "attempts.json", model="gpt-5-mini",
                                    system_prompt=policy.policy_prompt_system(),
                                    user_prompt=json.dumps({"new_articles": [a.to_prompt() for a in candidates]}),
                                    max_output_tokens=7000, temperature=0.2)


def test_valid_all_noops_and_strict_schema(tmp_path):
    a = article(); raw = {"operations": [noop(a)]}; c = client([response(raw)])
    assert generate(tmp_path, c) == raw
    request = c.client.responses.calls[0]
    assert request["text"]["format"]["strict"] is True
    assert "temperature" not in request
    assert len(c.client.responses.calls) == 1
    assert json.loads((tmp_path / "attempts.json").read_text())["attempts"][0]["valid"]


def test_context_retained_on_one_regeneration(tmp_path):
    raw = {"operations": [noop(article())]}
    c = client([response('{"operations":'), response(raw)])
    assert generate(tmp_path, c) == raw
    first, second = c.client.responses.calls
    assert second["input"][:2] == first["input"]
    assert second["text"] == first["text"]
    assert second["max_output_tokens"] == 7000
    assert len(second["input"]) == 4
    assert len(json.loads((tmp_path / "attempts.json").read_text())["attempts"]) == 2


@pytest.mark.parametrize("bad", [
    {"operations": [{"action": "create", "record": {"id": "invented"}}]},
    {"operations": []},
    {"operations": [{"action": "noop", "article_refs": [], "rule_reason": "skip"}]},
    {"operations": [noop(article(99))]},
    {"operations": [noop(article()), noop(article())]},
])
def test_invalid_outputs_never_return_success(tmp_path, bad):
    c = client([response(bad), response(bad)])
    with pytest.raises(OpenAIJsonError):
        generate(tmp_path, c)
    assert len(c.client.responses.calls) == 2


def test_unclassified_candidate_is_not_accepted(tmp_path):
    raw = {"operations": [noop(article())]}
    c = client([response(raw), response(raw)])
    with pytest.raises(OpenAIJsonError, match="unclassified_articles"):
        generate(tmp_path, c, [article(), article(2)])


def test_incomplete_json_is_rejected_even_when_parsable(tmp_path):
    raw = {"operations": [noop(article())]}
    c = client([response(raw, "incomplete", "max_output_tokens")] * 2)
    with pytest.raises(OpenAIJsonError, match="response_not_completed"):
        generate(tmp_path, c)
    saved = json.loads((tmp_path / "attempts.json").read_text())
    assert saved["attempts"][0]["incomplete_reason"] == "max_output_tokens"


@pytest.mark.parametrize("value", [RuntimeError("request failed"), response("", refusal=True)])
def test_requests_and_refusals_are_not_regenerated(tmp_path, value):
    c = client([value])
    with pytest.raises(OpenAIJsonError):
        generate(tmp_path, c)
    assert len(c.client.responses.calls) == 1


def test_rules_snapshot_controls_required_checks(tmp_path):
    rule = rules.IntelligenceRule("REQ_TEST", "test", "REQUIRE", "CREATE", "New plant", 0, 1, ("All",))
    rules.set_active_rules(rules.RuleSet((rule,), 0))
    raw = {"operations": [create(article())]}
    c = client([response(raw), response(raw)])
    with pytest.raises(OpenAIJsonError, match="schema"):
        generate(tmp_path, c)
    raw["operations"][0].update(rule_checks={"REQ_TEST": True}, rule_hits=["REQ_TEST"])
    assert generate(tmp_path, client([response(raw)])) == raw


def test_numeric_integrity_rejection_not_valid_noop(tmp_path):
    op = create(article()); op["key_facts"] = "Company opened a 999 Mtpa plant."
    c = client([response({"operations": [op]})] * 2)
    with pytest.raises(OpenAIJsonError, match="integrity_rejected"):
        generate(tmp_path, c)


def test_update_wrong_identity_and_ref_date_are_rejected(tmp_path):
    op = create(article()); op.update(action="update", matched_existing_key="missing")
    with pytest.raises(OpenAIJsonError, match="update_identity_mismatch"):
        generate(tmp_path, client([response({"operations": [op]})] * 2))
    op = noop(article()); op["article_refs"][0]["published_at"] = "2000-01-01"
    with pytest.raises(OpenAIJsonError, match="published_at_mismatch"):
        generate(tmp_path, client([response({"operations": [op]})] * 2))


def test_shared_legacy_interface_unchanged():
    c = client([response({"report_title": "legacy"})])
    assert c.generate_json(model="gpt-5-mini", system_prompt="s", user_prompt="u",
                           max_output_tokens=50, temperature=0.2) == {"report_title": "legacy"}
    assert "text" not in c.client.responses.calls[0]


class Notion:
    def __init__(self):
        self.updates = []; self.creates = []

    def update_page(self, page_id, props):
        self.updates.append((page_id, props)); return {"id": page_id}

    def create_page(self, db, props):
        self.creates.append((db, props)); return {"id": "99999999-9999-9999-9999-999999999999"}


def production_entrypoint(monkeypatch, tmp_path, c, candidates):
    # Register existing globals for restoration, then import the REAL runner
    # including safety -> policy -> processing patches in production order.
    for name in ("_prompt_system", "normalize_operations", "_properties_for_operation", "apply_operations",
                 "_load_existing_insights", "_load_nikkei_articles", "_load_general_articles"):
        monkeypatch.setattr(pipeline, name, getattr(pipeline, name))
    monkeypatch.setattr(policy, "_PATCHED", False)
    monkeypatch.setattr(processing, "_PATCHED", False)
    monkeypatch.setattr(processing, "_LINKED_MIGRATION_DONE", False)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/run_intelligence_pipeline.py"),
                   run_name="contract_test_entrypoint")
    notion = Notion()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOTION_TOKEN", "test-not-real")
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    monkeypatch.setenv("INTELLIGENCE_DRY_RUN", "false")
    monkeypatch.setattr(pipeline, "NotionClient", lambda token: notion)
    monkeypatch.setattr(pipeline, "OpenAIJsonClient", lambda key: c)
    monkeypatch.setattr(pipeline, "_load_existing_insights", lambda *args: [])
    monkeypatch.setattr(pipeline, "_load_nikkei_articles", lambda *args: [])
    monkeypatch.setattr(pipeline, "_load_general_articles", lambda *args: candidates)
    return notion


@pytest.mark.parametrize("mode", ["invalid", "incomplete", "noop", "create", "repair", "empty"])
def test_production_entrypoint_no_invalid_processed_markers(tmp_path, monkeypatch, mode):
    a = article(); good = {"operations": [create(a) if mode == "create" else noop(a)]}
    bad = response({"operations": [{"action": "create", "record": {}}]})
    values = [bad, bad] if mode == "invalid" else [response(good, "incomplete", "max_output_tokens")] * 2 if mode == "incomplete" else [bad, response(good)] if mode == "repair" else [response(good)]
    c = client(values)
    notion = production_entrypoint(monkeypatch, tmp_path, c, [] if mode == "empty" else [a])
    status = pipeline.main()
    if mode in {"invalid", "incomplete"}:
        assert status == 1
        assert not notion.updates and not notion.creates
    else:
        assert status == 0
        assert len(notion.updates) == (0 if mode == "empty" else 1)
        assert len(notion.creates) == (1 if mode == "create" else 0)
    if mode == "empty":
        assert c.client.responses.calls == []


def test_technical_noops_not_marked_in_backfill_apply():
    notion = Notion()
    ops = [{**noop(article()), "policy_reason": "missing_rule_checks:REQ_TEST"},
           {**noop(article(2)), "safety_reason": "unsupported_numeric_or_duration_claim:999"}]
    result = processing.processing_apply_operations(notion, "db", ops, [], "test", False)
    assert len(result["errors"]) == 2
    assert result["noops"] == 0 and result["applied"] == []
    assert notion.updates == []
