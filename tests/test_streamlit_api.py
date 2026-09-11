"""Every `st.*` keyword these pages pass must still exist in the installed Streamlit.

WHY
Streamlit Cloud updates itself. The apps here are read on a phone, usually
minutes before a lock, and a removed keyword is not a warning there -- it is a
TypeError that takes the whole page down at the line it happens on.

This repo had a live example. Every run printed:

    Please replace `use_container_width` with `width`.
    `use_container_width` will be removed after 2025-12-31.

and on 2026-09-11 -- more than eight months past that date -- all fifteen call
sites were still on the old spelling. It only kept working because the pinned
Streamlit had not yet dropped it. Nothing would have caught the day it did.

The check is deliberately generic rather than a grep for one name: it asks the
installed Streamlit whether it would accept each keyword, so the next
deprecation is caught by the same test without anyone remembering to add it.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
import streamlit as st

#: Everything a browser actually loads.
ENTRYPOINTS = [ROOT / "app.py", *sorted((ROOT / "pages").glob("*.py"))]


def _st_calls(path: Path):
    """(line, attribute, kwarg names) for each literal `st.<name>(...)` call.

    Only `st.<name>` and `st.sidebar.<name>`: anything reached through a
    variable cannot be resolved statically, and guessing is how a test like
    this starts failing for reasons that have nothing to do with the code.
    """
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        chain = ast.unparse(node.func)
        if not (chain.startswith("st.") or chain.startswith("st.sidebar.")):
            continue
        name = node.func.attr
        kwargs = [kw.arg for kw in node.keywords if kw.arg]
        if kwargs:
            yield node.lineno, name, kwargs


@pytest.mark.parametrize("path", ENTRYPOINTS, ids=lambda p: p.name)
def test_no_streamlit_keyword_the_installed_version_would_reject(path):
    offenders = []
    for lineno, name, kwargs in _st_calls(path):
        fn = getattr(st, name, None)
        if fn is None or not callable(fn):
            continue
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):       # C-implemented or wrapped
            continue
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            continue                          # **kwargs accepts anything
        for kw in kwargs:
            if kw not in params:
                offenders.append(f"{path.name}:{lineno} st.{name}(... {kw}=...)")

    assert not offenders, (
        "these keywords no longer exist in streamlit "
        f"{st.__version__} and will raise TypeError in the browser:\n  "
        + "\n  ".join(offenders))


@pytest.mark.parametrize("path", ENTRYPOINTS, ids=lambda p: p.name)
def test_use_container_width_stays_gone(path):
    """The specific one that was already eight months over its removal date.

    Kept as its own assertion because the generic check above only fires once
    the installed Streamlit actually drops it -- which, on Cloud, is the same
    moment the page breaks for a user. This one fails while there is still
    time to act.
    """
    assert "use_container_width" not in path.read_text(), (
        f"{path.name} still uses use_container_width; Streamlit's own message "
        "says it is removed after 2025-12-31. Use width='stretch' (or "
        "'content') instead.")
