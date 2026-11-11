from __future__ import annotations

from pathlib import Path

import streamlit as st

from p2p.ui.components.chips import h, render_html
from p2p.ui.components.shell import load_styles, render_flash
from p2p.ui.runtime import service

UI_DIR = Path(__file__).parent
PAGES_DIR = UI_DIR / "pages"
ASSETS_DIR = UI_DIR / "assets"


def build_pages(pending_review: int) -> dict[str, st.Page]:
    review_title = f"Review queue · {pending_review}" if pending_review else "Review queue"
    return {
        "overview": st.Page(
            PAGES_DIR / "overview.py",
            title="Overview",
            icon=":material/space_dashboard:",
            url_path="",
            default=True,
        ),
        "process": st.Page(
            PAGES_DIR / "process.py",
            title="Process invoice",
            icon=":material/upload_file:",
            url_path="process",
        ),
        "review": st.Page(
            PAGES_DIR / "review.py",
            title=review_title,
            icon=":material/fact_check:",
            url_path="review",
        ),
        "cases": st.Page(
            PAGES_DIR / "cases.py",
            title="Cases",
            icon=":material/receipt_long:",
            url_path="cases",
        ),
        "about": st.Page(
            PAGES_DIR / "about.py",
            title="About",
            icon=":material/info:",
            url_path="about",
        ),
    }


def runtime_card() -> None:
    runtime = service().runtime
    mode = "Demo" if runtime.demo_mode else "Live"
    rules_name = runtime.rules_path.name if runtime.rules_path else "—"
    rows = [
        ("Mode", mode),
        ("Model", runtime.model_name),
        ("Rules", rules_name),
        ("Reflection limit", runtime.rules.reflection.max_iterations),
    ]
    items = "".join(
        f'<div class="p2p-runtime-row"><span>{h(key)}</span><span title="{h(value)}">{h(value)}</span></div>'
        for key, value in rows
    )
    render_html(
        f'<div class="p2p-runtime-card"><div class="p2p-runtime-label">Runtime</div>{items}</div>'
    )


def main() -> None:
    st.set_page_config(
        page_title="P2P Exception Control Center",
        page_icon=":material/fact_check:",
        layout="wide",
        initial_sidebar_state="auto",
    )
    load_styles()
    app = service()
    pending_review = app.pending_review_count()
    st.session_state["p2p_pending_review"] = pending_review
    pages = build_pages(pending_review)
    st.session_state["p2p_pages"] = pages
    nav = st.navigation(list(pages.values()), position="sidebar")
    st.logo(
        str(ASSETS_DIR / "brand.svg"),
        icon_image=str(ASSETS_DIR / "brand-mark.svg"),
        size="large",
    )
    with st.sidebar:
        runtime_card()
    render_flash()
    nav.run()


if __name__ == "__main__":
    main()
