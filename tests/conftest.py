import hashlib
import sys
from pathlib import Path

import pytest
import streamlit as st
from langgraph.checkpoint.memory import InMemorySaver

from p2p.application.service import CaseService, create_runtime
from p2p.domain.config import BusinessRules

# The app's default database, relative to the working directory: a developer's demo database
# when the suite runs from a checkout. The suite must leave it exactly as it found it.
# Only the database file itself is watched. SQLite creates and removes its -wal and -shm files
# whenever another process opens or closes the database, and a running app's first command
# creates p2p.db.commands.lock next to a database seeded with `make seed`.
DEFAULT_DATABASE = "data/demo/p2p.db"
FileState = tuple[int, int, str] | None
_BEFORE = pytest.StashKey[FileState]()


def _default_database_state() -> FileState:
    path = Path.cwd() / DEFAULT_DATABASE
    if not path.is_file():
        return None
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


def pytest_sessionstart(session):
    session.config.stash[_BEFORE] = _default_database_state()


def pytest_sessionfinish(session):
    """Fail the run if the default database file was created, changed or removed during it."""
    before = session.config.stash.get(_BEFORE, None)
    after = _default_database_state()
    if before == after:
        return
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        change = "created" if before is None else "removed" if after is None else "modified"
        reporter.write("\n")
        reporter.write_sep("=", "the default database changed during the test session", red=True)
        reporter.write_line(f"{change}: {Path.cwd() / DEFAULT_DATABASE}")
        reporter.write_line(
            "Either the suite wrote to it, or another process did: an app running on the same "
            "database may be the writer."
        )


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
