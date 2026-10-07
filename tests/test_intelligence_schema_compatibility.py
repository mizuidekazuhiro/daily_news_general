"""Guard the documented Structured Outputs subset without live API calls."""
from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator, ValidationError

from src.intelligence_contract import build_schema
from src.intelligence_rules import IntelligenceRule, RuleSet


def schema():
    rules = RuleSet((IntelligenceRule('REQ_TEST', 'test', 'REQUIRE', 'CREATE', 'New plant', 0, 1, ('All',)),), 0)
    return build_schema(rules, countries={'Japan'}, themes={'Capacity Expansion'},
                        event_types={'New Plant'}, levels={'High', 'Medium', 'Low'})


def test_schema_uses_only_supported_structured_output_keywords():
    # https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas
    allowed = {'type', 'properties', 'required', 'additionalProperties', 'items',
               'anyOf', 'enum', 'pattern', 'minItems', 'maxItems'}
    def check(node):
        assert set(node) <= allowed
        if node.get('type') == 'object':
            assert node['additionalProperties'] is False
            assert set(node['required']) == set(node['properties'])
            for child in node['properties'].values():
                check(child)
        if 'items' in node:
            check(node['items'])
        for variant in node.get('anyOf', []):
            check(variant)
    value = schema()
    assert value['type'] == 'object'
    check(value)
    Draft202012Validator.check_schema(value)


@pytest.mark.parametrize('value', ['', '   ', '\n\t'])
def test_nonempty_fields_reject_empty_and_whitespace(value):
    constraint = schema()['properties']['operations']['items']['anyOf'][0]['properties']['insight_key']
    assert constraint == {'type': 'string', 'pattern': r'\S'}
    with pytest.raises(ValidationError):
        Draft202012Validator(constraint).validate(value)
    Draft202012Validator(constraint).validate('company|japan|plant')
