"""Reviewer identity for the dashboard; there is no login model."""

from __future__ import annotations

import os

DEFAULT_REVIEWER = "Demo Reviewer"


def current_reviewer() -> str:
    return os.getenv("P2P_REVIEWER_NAME", DEFAULT_REVIEWER).strip() or DEFAULT_REVIEWER


def initials(name: str) -> str:
    words = name.split()
    if not words:
        return ""
    return (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper()
