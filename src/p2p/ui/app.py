from __future__ import annotations

import streamlit as st

from p2p.ui.components.chips import h, render_html
from p2p.ui.components.shell import load_styles, render_flash, top_bar
from p2p.ui.pages import cases, overview, process, review
from p2p.ui.runtime import service

NAV_ITEMS = ["Overview", "Process Invoice", "Cases", "Review Queue"]


st.set_page_config(
    page_title="P2P Exception Control Center",
    page_icon=":material/fact_check:",
    layout="wide",
    initial_sidebar_state="auto",
)


def sync_navigation(source: str, target: str) -> None:
    selected = st.session_state[source]
    st.session_state["active_page"] = selected
    st.session_state[target] = selected


def main() -> None:
    app = service()
    load_styles()
    render_flash()
    active_page = st.session_state.setdefault("active_page", NAV_ITEMS[0])
    st.session_state.setdefault("desktop_navigation", active_page)
    st.session_state.setdefault("mobile_navigation", active_page)
    runtime = app.runtime
    with st.sidebar:
        render_html(
            """
            <div class="brand">
              <div class="brand-mark">P2P</div>
              <div><div class="brand-name">P2P Control</div><div class="brand-kicker">Exception operations</div></div>
            </div>
            """,
        )
        st.radio(
            "Navigate",
            NAV_ITEMS,
            key="desktop_navigation",
            on_change=sync_navigation,
            args=("desktop_navigation", "mobile_navigation"),
            label_visibility="collapsed",
        )
        mode = "Demo" if runtime.demo_mode else "Live"
        rules_name = runtime.rules_path.name if runtime.rules_path else "—"
        render_html(
            f"""
            <div class="runtime-card">
              <div class="runtime-label">Runtime</div>
              <div class="runtime-row"><span>Mode</span><span>{h(mode)}</span></div>
              <div class="runtime-row"><span>Model</span><span title="{h(runtime.model_name)}">{h(runtime.model_name)}</span></div>
              <div class="runtime-row"><span>Rules</span><span title="{h(rules_name)}">{h(rules_name)}</span></div>
              <div class="runtime-row"><span>Reflection limit</span><span>{h(runtime.rules.reflection.max_iterations)}</span></div>
            </div>
            """,
        )
    with st.container(key="mobile-navigation"):
        st.radio(
            "Mobile navigation",
            NAV_ITEMS,
            key="mobile_navigation",
            on_change=sync_navigation,
            args=("mobile_navigation", "desktop_navigation"),
            horizontal=True,
            label_visibility="collapsed",
        )
    page = st.session_state["active_page"]
    top_bar(page)
    {
        "Overview": overview.render,
        "Process Invoice": process.render,
        "Cases": cases.render,
        "Review Queue": review.render,
    }[page](app)


if __name__ == "__main__":
    main()
