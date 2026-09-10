"""Mock Streamlit for headless unit testing of kit logic.

Only the surface the kit touches: session_state, query_params, and no-op UI
calls. Lets exclude-self, URL round-tripping, and dedup logic be tested without
a browser or a running server.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager


class _QueryParams(dict):
    def get(self, k, default=None):
        v = super().get(k, default)
        return v

    def __delitem__(self, k):
        if k in self:
            super().__delitem__(k)


class _ColumnStub:
    """Stand-in for a st.column_config column. Records its kwargs.

    Named after the real class so tests can assert on type(...).__name__ the
    same way they would against real Streamlit.
    """

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pinned = False

    def __repr__(self):
        return f"{type(self).__name__}({self.kwargs})"


def _make_column_config() -> types.SimpleNamespace:
    names = [
        "Column", "TextColumn", "NumberColumn", "CheckboxColumn", "SelectboxColumn",
        "DatetimeColumn", "DateColumn", "TimeColumn", "ListColumn", "LinkColumn",
        "ImageColumn", "AreaChartColumn", "LineChartColumn", "BarChartColumn",
        "ProgressColumn", "JsonColumn",
    ]
    ns = types.SimpleNamespace()
    for n in names:
        setattr(ns, n, type(n, (_ColumnStub,), {}))
    return ns


class _Col:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Container(_Col):
    def __getattr__(self, name):
        return _noop


def _noop(*a, **k):
    return None


class MockStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = {}
        self.query_params = _QueryParams()
        self.sidebar = _Container()
        self.column_config = _make_column_config()
        self._buttons = {}
        self._multiselects = {}
        self.rerun_count = 0
        # Records (args, kwargs) for dataframe/plotly_chart so tests can inspect
        # what the kit actually asked Streamlit to render.
        self.calls: dict[str, list] = {}

    # state
    def rerun(self):
        self.rerun_count += 1

    # layout
    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Col() for _ in range(n)]

    def container(self, **k):
        return _Container()

    def expander(self, *a, **k):
        return _Container()

    def spinner(self, *a, **k):
        return _Container()

    # widgets: return preset values so tests can drive them
    def button(self, label, **k):
        return self._buttons.get(k.get("key"), False)

    def multiselect(self, label, options, default=None, **k):
        return self._multiselects.get(k.get("key"), default or [])

    def toggle(self, *a, **k):
        return k.get("value", False)

    def radio(self, label, options, **k):
        return options[0] if options else None

    def selectbox(self, label, options, **k):
        return options[0] if options else None

    # Recorded renderers so tests can assert on the kwargs the kit passed.
    def dataframe(self, data=None, **k):
        self.calls.setdefault("dataframe", []).append(k)
        return None

    def plotly_chart(self, fig=None, **k):
        self.calls.setdefault("plotly_chart", []).append(k)
        return None

    def download_button(self, label, data=None, **k):
        self.calls.setdefault("download_button", []).append(
            {"label": label, "size": len(data) if data is not None else 0, **k}
        )
        return False

    # ── decorators ──
    # Must be real passthrough decorators. Returning a no-op would make
    # `@st.cache_data(...)` evaluate to None and raise "NoneType is not callable"
    # at import time, which would break the QA gate's import-time page execution.

    @staticmethod
    def _passthrough_decorator(*d_args, **d_kwargs):
        # Supports both @deco and @deco(...) forms.
        if len(d_args) == 1 and callable(d_args[0]) and not d_kwargs:
            return d_args[0]

        def wrap(fn):
            return fn
        return wrap

    def cache_data(self, *a, **k):
        return self._passthrough_decorator(*a, **k)

    def cache_resource(self, *a, **k):
        return self._passthrough_decorator(*a, **k)

    def fragment(self, *a, **k):
        return self._passthrough_decorator(*a, **k)

    def experimental_fragment(self, *a, **k):
        return self._passthrough_decorator(*a, **k)

    def __getattr__(self, name):
        # markdown, write, error, warning, caption, divider, etc.
        return _noop


def install() -> MockStreamlit:
    m = MockStreamlit()
    sys.modules["streamlit"] = m
    return m


def reset(m: MockStreamlit) -> None:
    m.session_state.clear()
    m.query_params.clear()
    m._buttons.clear()
    m._multiselects.clear()
    m.calls.clear()
    m.rerun_count = 0
