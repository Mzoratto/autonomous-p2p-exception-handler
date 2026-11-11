from pathlib import Path

import pytest
import streamlit as st
from langgraph.checkpoint.memory import InMemorySaver

from p2p.application.service import CaseService, create_runtime
from p2p.domain.config import BusinessRules


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
