from __future__ import annotations

import streamlit as st

from p2p.application.service import CaseService, create_runtime


@st.cache_resource
def service() -> CaseService:
    return CaseService(create_runtime())
