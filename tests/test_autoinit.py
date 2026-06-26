"""Tests for the env-default auto-init startup hook (ROB-421).

Two layers:

1. The ``robotops._autoinit`` hook module logic — importing it initializes the
   SDK iff ``ROBOTOPS_TRACE_AUTOINIT`` is truthy. Runs in-process.
2. The shipped ``robotops_autoinit.pth`` one-liner, executed in a *fresh*
   interpreter subprocess, to prove the gating logic works at real interpreter
   startup.

Note: a ``.pth`` file is only auto-executed by the ``site`` module when it lives
in a site-packages directory of an *installed* distribution. These tests exercise
the hook module and the exact ``.pth`` source line directly; the genuine
site-processing path (real ``pip install`` into a fresh venv) is verified in the
PR's container run and documented there.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import robotops

REPO_ROOT = Path(__file__).resolve().parent.parent
PTH_FILE = REPO_ROOT / "robotops_autoinit.pth"


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Reset the SDK + drop the cached hook module so each test re-runs it."""
    robotops._initialized = False
    sys.modules.pop("robotops._autoinit", None)


def _import_hook() -> None:
    importlib.import_module("robotops._autoinit")


def test_hook_initializes_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOTOPS_TRACE_AUTOINIT", "1")
    _import_hook()
    assert robotops._initialized is True


@pytest.mark.parametrize("value", ["true", "TRUE", "yes", "on"])
def test_hook_accepts_truthy_spellings(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ROBOTOPS_TRACE_AUTOINIT", value)
    _import_hook()
    assert robotops._initialized is True


def test_hook_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOTOPS_TRACE_AUTOINIT", "0")
    _import_hook()
    assert robotops._initialized is False


def test_hook_noop_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOTOPS_TRACE_AUTOINIT", raising=False)
    _import_hook()
    assert robotops._initialized is False


def test_pth_file_shipped_and_executes() -> None:
    """The shipped .pth one-liner gates auto-init in a fresh interpreter."""
    assert PTH_FILE.is_file()
    line = PTH_FILE.read_text().strip()
    # `site` only executes .pth lines that start with `import`.
    assert line.startswith("import ")

    probe = line + "\nimport robotops; print('AUTOINIT', robotops._initialized)"

    enabled = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "ROBOTOPS_TRACE_AUTOINIT": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "AUTOINIT True" in enabled.stdout, enabled.stderr

    disabled_env = {k: v for k, v in os.environ.items() if k != "ROBOTOPS_TRACE_AUTOINIT"}
    disabled = subprocess.run(
        [sys.executable, "-c", probe],
        env=disabled_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "AUTOINIT False" in disabled.stdout, disabled.stderr
