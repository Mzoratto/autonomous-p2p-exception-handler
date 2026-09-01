import pytest

from p2p.application.service import resolve_rules_path


def test_rules_path_resolves_from_packaged_runtime_working_directory(tmp_path, monkeypatch):
    rules_dir = tmp_path / "config"
    rules_dir.mkdir()
    expected = rules_dir / "business_rules.yaml"
    expected.write_text("test: true\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("P2P_RULES_PATH", raising=False)

    assert resolve_rules_path() == expected


def test_explicit_missing_rules_path_has_actionable_error(tmp_path):
    missing = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError, match="Configured P2P rules file does not exist"):
        resolve_rules_path(missing)
