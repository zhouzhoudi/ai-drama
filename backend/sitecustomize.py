"""
Enable the multi-agent Director Agent without rewriting the large legacy router.

Python automatically imports `sitecustomize` on startup when this file is on
sys.path. The project README starts the backend with:

    cd backend
    python main.py

In that startup mode, `backend/` is on sys.path, so this hook runs before
main.py imports `services.agent_router.AgentRouter`.

Set AI_DRAMA_DISABLE_MULTI_AGENT=true to fall back to the legacy single-router
behavior.
"""

from __future__ import annotations

import builtins
import os
import sys
from types import ModuleType
from typing import Any


_ORIGINAL_IMPORT = builtins.__import__
_PATCH_FLAG = "_AI_DRAMA_MULTI_AGENT_PATCHED"


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _patch_agent_router(module: ModuleType) -> None:
    if getattr(module, _PATCH_FLAG, False):
        return
    if _truthy(os.environ.get("AI_DRAMA_DISABLE_MULTI_AGENT", "false")):
        return

    try:
        from services.multi_agent_orchestrator import MultiAgentOrchestrator
    except Exception as exc:  # pragma: no cover - startup fallback
        print(f"[MultiAgent] disabled: failed to import orchestrator: {exc}")
        return

    legacy_router = getattr(module, "AgentRouter", None)
    if legacy_router is None:
        return

    class MultiAgentRouter(legacy_router):  # type: ignore[misc, valid-type]
        """Legacy-compatible router whose decision step is multi-agent based."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.multi_agent_orchestrator = MultiAgentOrchestrator()

        async def _decide(self, script_id, message, history, project_config, script):
            return await self.multi_agent_orchestrator.decide(
                script_id=script_id,
                message=message,
                history=history,
                project_config=project_config,
                script=script,
            )

    MultiAgentRouter.__name__ = "AgentRouter"
    MultiAgentRouter.__qualname__ = "AgentRouter"
    MultiAgentRouter.__module__ = module.__name__

    setattr(module, "LegacyAgentRouter", legacy_router)
    setattr(module, "AgentRouter", MultiAgentRouter)
    setattr(module, _PATCH_FLAG, True)
    print("[MultiAgent] AgentRouter patched with MultiAgentOrchestrator")


def _import_hook(name, globals=None, locals=None, fromlist=(), level=0):
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    # `from services.agent_router import AgentRouter` calls __import__ with
    # name='services.agent_router' and fromlist=('AgentRouter',). Patch before
    # the import machinery reads AgentRouter from the returned module.
    target = sys.modules.get("services.agent_router")
    if target is not None:
        _patch_agent_router(target)

    return module


if not _truthy(os.environ.get("AI_DRAMA_DISABLE_MULTI_AGENT", "false")):
    builtins.__import__ = _import_hook
