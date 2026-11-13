from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from p2p.domain.config import BusinessRules

SHIPPED_RULES = Path(__file__).parents[2] / "config" / "business_rules.yaml"


def shipped_rules() -> dict:
    return yaml.safe_load(SHIPPED_RULES.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("section", "misspelled"),
    [
        ("reflection", {"max_iteration": 1}),
        ("review", {"sla_hour": 48}),
    ],
)
def test_a_misspelled_rule_is_rejected_by_name(section, misspelled):
    payload = shipped_rules()
    payload[section] = misspelled

    with pytest.raises(ValidationError, match=next(iter(misspelled))):
        BusinessRules.model_validate(payload)


def test_an_unknown_rules_section_is_rejected_by_name():
    payload = {**shipped_rules(), "reflections": {"max_iterations": 1}}

    with pytest.raises(ValidationError, match="reflections"):
        BusinessRules.model_validate(payload)


def test_the_shipped_rules_file_loads():
    rules = BusinessRules.load(SHIPPED_RULES)

    assert rules.reflection.max_iterations == 3
