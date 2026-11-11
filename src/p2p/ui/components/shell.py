from __future__ import annotations

from pathlib import Path

import streamlit as st

from p2p.ui.components.chips import h, render_html
from p2p.ui.identity import current_reviewer, initials

STYLES_PATH = Path(__file__).resolve().parents[1] / "styles.css"


def load_styles() -> None:
    render_html(f"<style>{STYLES_PATH.read_text(encoding='utf-8')}</style>")


def top_bar(page: str) -> None:
    reviewer = current_reviewer()
    render_html(
        f"""
        <div class="topbar">
          <div class="breadcrumb">Operations / <strong>{h(page)}</strong></div>
          <div class="topbar-right">
            <span class="operator-avatar" title="{h(reviewer)}" aria-label="Reviewer {h(reviewer)}">{h(initials(reviewer))}</span>
          </div>
        </div>
        """,
    )


def page_header(eyebrow: str, title: str, description: str) -> None:
    render_html(
        f"""
        <div class="page-header">
          <div>
            <div class="eyebrow">{h(eyebrow)}</div>
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
    render_html(f'<div class="field-errors">{items}</div>')


def show_failure(message: str, exc: Exception) -> None:
    st.error(message)
    with st.expander("Technical details"):
        st.code(f"{type(exc).__name__}: {exc}", language=None)


def navigate_to_page(page: str) -> None:
    st.session_state["active_page"] = page
    st.session_state["desktop_navigation"] = page
    st.session_state["mobile_navigation"] = page
