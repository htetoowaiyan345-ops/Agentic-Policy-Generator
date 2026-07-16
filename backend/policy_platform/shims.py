"""shims.py — compatibility shims applied at backend startup.

This module is imported FIRST by every entry-point (api server,
test runner, scripts).  Add Python-3.14 / pyproject shims here
so the rest of the codebase doesn't need to care.

Current shims:

* Python 3.14 removed `inspect.getargspec` (use `getfullargspec`
  instead).  However, the lxml binary wheel distributed via PyPI
  for Python 3.14 was still compiled with the old API and crashes
  on first import in some Python distributions.  We alias
  `getargspec` to `getfullargspec` so the compiled-in fallback path
  in lxml works.

This file MUST stay side-effect-free outside the shim block so
unittest discovery still works.
"""
from __future__ import annotations


def _apply_inspect_shim() -> None:
    """Add `inspect.getargspec = inspect.getfullargspec` if missing.

    Python 3.14 removed `inspect.getargspec` (it was deprecated since
    Python 3.0).  lxml's precompiled Windows wheels still reference
    the old name in their `.pxi` source (now compiled into the
    `.pyd` binary).  When CPython invokes the cython-level
    `inspect_getargspec` selector at module init time, the lookup
    fails.  Aliasing the function name bridges the gap without
    requiring a rebuild of lxml against 3.14.
    """
    import inspect

    if not hasattr(inspect, "getargspec"):
        inspect.getargspec = inspect.getfullargspec  # type: ignore[attr-defined]


_apply_inspect_shim()
