import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from news_digest import normalize_translations_payload, parse_translation_response


def test_normalize_translations_payload_list():
    payload = ["A", "B"]
    assert normalize_translations_payload(payload) == ["A", "B"]


def test_normalize_translations_payload_object_list():
    payload = {"translations": ["A", "B"]}
    assert normalize_translations_payload(payload) == ["A", "B"]


def test_normalize_translations_payload_numeric_dict():
    payload = {"0": "A", "2": "C", "1": "B"}
    assert normalize_translations_payload(payload) == ["A", "B", "C"]


def test_normalize_translations_payload_nested_numeric_dict():
    payload = {"translations": {"1": "B", "0": "A"}}
    assert normalize_translations_payload(payload) == ["A", "B"]


def test_parse_translation_response_accepts_array_only():
    content = json.dumps(["A", "B"])
    assert parse_translation_response(content, 2) == ["A", "B"]


def test_parse_translation_response_accepts_object():
    content = json.dumps({"translations": ["A", "B"]})
    assert parse_translation_response(content, 2) == ["A", "B"]
