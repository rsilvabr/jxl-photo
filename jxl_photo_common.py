#!/usr/bin/env python3
"""jxl_photo_common - the helpers the jxl-photo scripts share, one copy each.

This file must sit next to the scripts. Each script imports the copy in ITS
OWN folder (`_import_common` at the top of every script), never another one
found on sys.path, and refuses to start when the versions differ.

Two kinds of functions live here (AGENTS.md, "The common module"):

- Pure helpers read nothing from the calling script. A script binds them by
  name: `reorder_jxl_boxes = _common.reorder_jxl_boxes`.
- The others take the calling script's namespace as their first parameter,
  `g` (a `Script`), and read through it at CALL time: every setting, the
  logger, `__file__`, the run state and every other toolkit helper they call
  (`g.TEMP_DIR`, `g.logger`, `g._run_captured(...)`). So the settings overlay,
  the per-run reset of the run-scoped globals and a test's monkeypatch on the
  script module all reach the code here, and the state stays per script. The
  script keeps a one-line shim with the same name:
  `def _run_captured(*a, **k): return _common._run_captured(_G, *a, **k)`.

Never define a setting here: the settings live at the top of each script.
"""

TOOLKIT_VERSION = "2.28.0"


class Script:
    """A script's globals, read and written at call time (never copied).

    `g.NAME` reads the script's global NAME as it is NOW; `g.NAME = v` sets
    it (what a `global NAME` statement did in the script's own copy)."""

    __slots__ = ("_g",)

    def __init__(self, namespace: dict):
        object.__setattr__(self, "_g", namespace)

    def __getattr__(self, name):
        try:
            return self._g[name]
        except KeyError:
            raise AttributeError(f"the calling script defines no {name!r}") from None

    def __setattr__(self, name, value):
        self._g[name] = value


def _script_get(g, name, default=None):
    """The script's global `name`, or `default` when it defines none."""
    return g._g.get(name, default)
