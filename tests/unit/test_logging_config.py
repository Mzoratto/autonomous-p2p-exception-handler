import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from p2p.ui.app import configure_logging


@pytest.fixture(autouse=True)
def no_log_level(tmp_path, monkeypatch):
    """A working directory without env files, no level set and a fresh once-per-process cache."""
    monkeypatch.chdir(tmp_path)
    # setenv first so monkeypatch restores the variable's absence after a file has set it.
    monkeypatch.setenv("P2P_LOG_LEVEL", "INFO")
    monkeypatch.delenv("P2P_LOG_LEVEL")
    configure_logging.cache_clear()
    yield
    configure_logging.cache_clear()


@contextmanager
def bare_root_logger() -> Iterator[logging.Logger]:
    """basicConfig does nothing while the root logger has handlers, and pytest attaches its own."""
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    root.handlers.clear()
    try:
        yield root
    finally:
        root.handlers[:] = saved
        root.setLevel(level)


def test_log_level_from_env_local_applies_before_env(tmp_path):
    (tmp_path / ".env.local").write_text("P2P_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    (tmp_path / ".env").write_text("P2P_LOG_LEVEL=ERROR\n", encoding="utf-8")

    with bare_root_logger() as root:
        configure_logging()
        assert root.level == logging.DEBUG


def test_process_environment_wins_over_env_files(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text("P2P_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("P2P_LOG_LEVEL", "warning")

    with bare_root_logger() as root:
        configure_logging()
        assert root.level == logging.WARNING


def test_invalid_log_level_fails_every_time_and_names_the_setting(monkeypatch):
    monkeypatch.setenv("P2P_LOG_LEVEL", "VERBOSE")

    messages = []
    with bare_root_logger():
        for _rerun in (1, 2):  # Streamlit reruns the script on every interaction.
            with pytest.raises(ValueError) as caught:
                configure_logging()
            messages.append(str(caught.value))

    for message in messages:
        assert "P2P_LOG_LEVEL='VERBOSE'" in message
        assert "DEBUG, INFO, WARNING, ERROR, CRITICAL" in message
