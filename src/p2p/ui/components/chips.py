from __future__ import annotations

from html import escape

import streamlit as st

from p2p.ui.viewmodels import ACTION_STATUS_KEYS, label_action


def render_html(markup: str) -> None:
    st.html(markup)


def h(value: object) -> str:
    return escape(str(value), quote=True)


def icon(name: str) -> str:
    return f'<span class="p2p-icon" aria-hidden="true">{h(name)}</span>'


def status_pill(key: str, label: str) -> str:
    css_key = key.replace("_", "-")
    return f'<span class="p2p-status-pill p2p-status-{h(css_key)}">{h(label)}</span>'


def action_pill(action: str) -> str:
    return status_pill(ACTION_STATUS_KEYS.get(action, "processing"), label_action(action))
