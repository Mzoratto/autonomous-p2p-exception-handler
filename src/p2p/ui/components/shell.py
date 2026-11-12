from __future__ import annotations

from functools import cache
from pathlib import Path
from urllib.parse import urlencode

import streamlit as st

from p2p.ui.components.chips import h, icon, render_html
from p2p.ui.identity import current_reviewer, initials
from p2p.ui.runtime import service

STYLES_PATH = Path(__file__).resolve().parents[1] / "styles.css"


@cache
def _styles() -> str:
    return STYLES_PATH.read_text(encoding="utf-8")


def load_styles() -> None:
    render_html(f"<style>{_styles()}</style>")


def switch_to(page: str, **query_params: str) -> None:
    """Open a registered page; call from the script body, not from a widget callback."""
    st.switch_page(st.session_state["p2p_pages"][page], query_params=query_params or None)


def top_bar(crumbs: list[str], path: str) -> None:
    separator = f'<span class="p2p-crumb-sep">{icon("chevron_right")}</span>'
    trail = separator.join(
        f'<span class="p2p-crumb{" p2p-crumb-current" if index == len(crumbs) - 1 else ""}">'
        f"{h(crumb)}</span>"
        for index, crumb in enumerate(crumbs)
    )
    query = urlencode(st.query_params.to_dict())
    location = f"{path}?{query}" if query else path
    mode = "Demo" if service().runtime.demo_mode else "Live"
    waiting = st.session_state.get("p2p_pending_review", 0)
    reviewer = current_reviewer()
    render_html(
        f"""
        <div class="p2p-topbar">
          <div class="p2p-topbar-left">
            <nav class="p2p-breadcrumb" aria-label="Breadcrumb">{trail}</nav>
            <span class="p2p-location">{icon("link")}{h(location)}</span>
          </div>
          <div class="p2p-topbar-right">
            <span class="p2p-mode-pill"><i></i>{h(mode)} mode · {waiting} awaiting review</span>
            <span class="p2p-avatar" title="Signed in as {h(reviewer)}">{h(initials(reviewer))}</span>
          </div>
        </div>
        """,
    )


def page_header(eyebrow: str, title: str, description: str) -> None:
    render_html(
        f"""
        <div class="p2p-page-header">
          <div>
            <div class="p2p-eyebrow">{h(eyebrow)}</div>
            <h1>{h(title)}</h1>
            <p>{h(description)}</p>
          </div>
        </div>
        """,
    )


def render_flash() -> None:
    flash = st.session_state.pop("flash", None)
    if not flash:
        return
    if flash["kind"] == "toast":
        st.toast(flash["text"], icon=":material/check_circle:")
    else:
        st.error(flash["text"])


def render_field_errors(rows: list[tuple[str, str]]) -> None:
    st.error(f"{len(rows)} field(s) need fixing before this invoice can be processed")
    items = "".join(f"<code>{h(field)}</code><span>{h(message)}</span>" for field, message in rows)
    render_html(f'<div class="p2p-field-errors">{items}</div>')


def show_failure(message: str, exc: Exception) -> None:
    st.error(message)
    with st.expander("Technical details"):
        st.code(f"{type(exc).__name__}: {exc}", language=None)


SETUP_ERROR_KEY = "p2p_setup_error"


def setup_screen(title: str) -> None:
    """Replace the app with the two ways out of a live-mode configuration fault."""
    with st.container(key="setup-screen"):
        st.title(title)
        st.markdown(
            "The dashboard can't call the model with the current settings. Either:\n\n"
            "1. Set a valid `OPENAI_API_KEY` in `.env.local` and restart the app, or\n"
            "2. Set `DEMO_MODE=true` to run on the built-in mock model."
        )
        if st.session_state.get(SETUP_ERROR_KEY) == "auth":
            st.caption(
                "While a case was being processed, the model service rejected the key or "
                "denied it access to the configured model."
            )
            if st.button("Try again", type="primary", key="setup_try_again"):
                st.session_state.pop(SETUP_ERROR_KEY, None)
                st.rerun()
    st.stop()


def mark_authentication_failure() -> None:
    """Show the setup screen on the next run; call when a command raises ModelAuthenticationError."""
    st.session_state[SETUP_ERROR_KEY] = "auth"
    st.rerun()
