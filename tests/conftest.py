import hashlib
import sys
from pathlib import Path

import pytest
import streamlit as st
from langgraph.checkpoint.memory import InMemorySaver

from p2p.application.service import CaseService, create_runtime
from p2p.domain.config import BusinessRules

# The app's default database, relative to the working directory: a developer's demo database
# when the suite runs from a checkout. The suite must leave these files exactly as it found them.
DEFAULT_DATABASE_FILES = "data/demo/p2p.db*"
FileState = tuple[int, int, str]
_BEFORE = pytest.StashKey[dict[str, FileState]]()


def _default_database_files() -> dict[str, FileState]:
    states = {}
    for path in sorted(Path.cwd().glob(DEFAULT_DATABASE_FILES)):
        if path.is_file():
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            states[str(path)] = (stat.st_size, stat.st_mtime_ns, digest)
    return states


def pytest_sessionstart(session):
    session.config.stash[_BEFORE] = _default_database_files()


def pytest_sessionfinish(session):
    """Fail the run if it created, changed or removed any of the default database files."""
    before = session.config.stash.get(_BEFORE, {})
    after = _default_database_files()
    touched = sorted(
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
    )
    if not touched:
        return
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write("\n")
        reporter.write_sep("=", "the test session touched the default database", red=True)
        for path in touched:
            change = (
                "created" if path not in before else "removed" if path not in after else "modified"
            )
            reporter.write_line(f"{change}: {path}")


@pytest.fixture(scope="session", autouse=True)
def _default_database_url(tmp_path_factory):
    """Anything that falls back to P2P_DATABASE_URL, a spawned process included, gets a temp file."""
    url = f"sqlite:///{tmp_path_factory.mktemp('default-database') / 'p2p.db'}"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("P2P_DATABASE_URL", url)
        yield


@pytest.fixture(autouse=True)
def _restore_main_module():
    """AppTest's script runner installs app.py as __main__ and leaves it there. A process
    started later with the spawn method would then run app.py on startup, and with it the app."""
    main = sys.modules["__main__"]
    yield
    sys.modules["__main__"] = main


@pytest.fixture(autouse=True)
def _clear_streamlit_resource_cache():
    """AppTest instances in one process share st.cache_resource; isolate each test."""
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


@pytest.fixture
def rules() -> BusinessRules:
    return BusinessRules.load(Path(__file__).parents[1] / "config" / "business_rules.yaml")


@pytest.fixture
def app(tmp_path):
    runtime = create_runtime(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        demo_mode=True,
        checkpointer=InMemorySaver(),
    )
    yield CaseService(runtime)
    runtime.close()
