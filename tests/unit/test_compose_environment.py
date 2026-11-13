"""Compose must pass every documented setting: the image holds no env files."""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).parents[2]


def documented_variables() -> set[str]:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Environment variables", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([A-Z0-9_]+)` \|", section, flags=re.MULTILINE))


def test_every_documented_variable_reaches_the_container():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    passed = set(compose["services"]["p2p-handler"]["environment"])
    documented = documented_variables()

    assert len(documented) >= 10, "README environment table not found"
    assert documented - passed == set()
