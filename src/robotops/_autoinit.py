"""Env-default auto-init startup hook (ROB-421).

This module is imported at interpreter startup by the installed
``robotops_autoinit.pth`` file (a ``.pth`` line beginning with ``import`` is
executed by the ``site`` module when the interpreter boots). Importing it checks
``ROBOTOPS_TRACE_AUTOINIT`` and, when truthy, calls :func:`robotops.init` — the
Datadog ``-javaagent`` model: set one env var once in the launch environment and
every Python process auto-initializes tracing with zero per-process code.

The ``.pth`` only gates on *presence* of the env var; this module is the
authority on what counts as truthy, so ``ROBOTOPS_TRACE_AUTOINIT=0`` imports the
hook but does nothing. Processes launched without the env var are unaffected and
keep calling :func:`robotops.init` explicitly (the override path), which is
idempotent with the auto-init so there is never a double-initialize.

Importing this module never raises: a failure to auto-init must not break the
host process at startup.
"""

from __future__ import annotations

import os

# Accepted truthy spellings for ROBOTOPS_TRACE_AUTOINIT. Anything else (unset,
# empty, "0", "false", ...) leaves auto-init off.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _autoinit_enabled() -> bool:
    """Return True iff ROBOTOPS_TRACE_AUTOINIT is set to a truthy value."""
    return os.environ.get("ROBOTOPS_TRACE_AUTOINIT", "").strip().lower() in _TRUTHY


def _maybe_autoinit() -> None:
    """Initialize tracing at startup when opted in; never raises."""
    if not _autoinit_enabled():
        return
    try:
        import robotops

        robotops.init()
    except Exception:  # noqa: BLE001 - startup hook must never crash the host.
        # Best-effort: a broken auto-init must not take down the process.
        pass


_maybe_autoinit()
