"""
bim_ui.states — deliberate empty, error, loading, and permission states.

A dashboard that renders a blank panel when a filter excludes everything looks
broken. A dashboard that says "No bookings match Team = PX Sales in Jan" looks
correct. That difference is most of what separates a BI tool from a script.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import streamlit as st

from . import compat


def empty(
    message: str = "No data matches the current filters.",
    *,
    hint: str | None = None,
    height: int = 220,
    on_clear=None,
    container=None,
) -> None:
    """Empty state sized to hold the layout so the page does not jump.

    on_clear, when given, renders a button that clears the offending filters.
    Always offer a way out of an empty state the user filtered themselves into.

    `container` must be a Streamlit container/column, never the `st` module --
    the module does not implement the context-manager protocol, so `with st:`
    raises TypeError. Callers pass containers explicitly rather than relying on
    `with`.
    """
    with compat.container(border=True, height=height, parent=container):
        st.markdown(
            f"<div style='text-align:center;padding-top:{max(height // 4, 20)}px'>"
            f"<div style='font-size:13.5px;font-weight:600;opacity:.75'>{message}</div>"
            + (f"<div style='font-size:12px;opacity:.55;margin-top:5px'>{hint}</div>"
               if hint else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        if on_clear is not None:
            cols = st.columns([1, 1, 1])
            with cols[1]:
                if st.button("Clear filters", use_container_width=True,
                             key=f"bim_empty_clear_{abs(hash((message, height)))}"):
                    on_clear()
                    compat.rerun()


def error(
    message: str,
    *,
    detail: str | None = None,
    height: int = 220,
    container=None,
) -> None:
    """Error state that surfaces the detail without dumping a raw traceback.

    Detail goes in an expander: visible to whoever needs it, not shouting at
    an executive viewing the dashboard.
    """
    with compat.container(border=True, height=height, parent=container):
        compat.error(message, icon=":material/error:")
        if detail:
            with st.expander("Technical detail"):
                st.code(detail, language="text")


def no_permission(
    obj: str,
    *,
    role: str | None = None,
    height: int = 220,
    container=None,
) -> None:
    """Insufficient-privilege state.

    Names the object and the role so the viewer can file an accurate access
    request instead of reporting "the dashboard is broken".
    """
    with compat.container(border=True, height=height, parent=container):
        compat.warning(
            f"You do not have access to **{obj}**."
            + (f" Current role: `{role}`." if role else "")
            + " Ask your Snowflake administrator for read access to view this panel.",
            icon=":material/lock:",
        )


@contextmanager
def loading(message: str = "Loading…") -> Iterator[None]:
    """Spinner context for a panel-scoped load."""
    with st.spinner(message):
        yield


def guard(
    df,
    *,
    message: str = "No data matches the current filters.",
    hint: str | None = None,
    height: int = 220,
    on_clear=None,
    container=None,
) -> bool:
    """Render an empty state and return False when df has no rows.

    Standard panel opening:

        if not states.guard(df, on_clear=filters.clear):
            return
    """
    if df is None or len(df) == 0:
        empty(message, hint=hint, height=height, on_clear=on_clear,
              container=container)
        return False
    return True
