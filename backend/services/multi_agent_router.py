"""
Compatibility wrapper that turns the existing AgentRouter into a multi-agent router.

Why this file exists:
- The current backend imports `from services.agent_router import AgentRouter`.
- `agent_router.py` is large and already contains all executor/SSE logic.
- To reduce risk, this wrapper reuses the legacy router and only replaces the
  decision step (`_decide`) with MultiAgentOrchestrator.

`services/__init__.py` aliases `services.agent_router` to this module at import
startup, so main.py does not need to change.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from services.multi_agent_orchestrator import MultiAgentOrchestrator

logger = logging.getLogger(__name__)


def _load_legacy_agent_router_class():
    """Load the original agent_router.py under a private module name.

    We cannot `import services.agent_router` here because services/__init__.py
    redirects that name to this wrapper. Loading by file path avoids recursion.
    """
    legacy_path = Path(__file__).with_name("agent_router.py")
    spec = importlib.util.spec_from_file_location("services._legacy_agent_router", legacy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load legacy AgentRouter from {legacy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AgentRouter


LegacyAgentRouter = _load_legacy_agent_router_class()


class AgentRouter(LegacyAgentRouter):
    """Drop-in replacement for the original AgentRouter.

    It keeps all legacy execution methods unchanged:
    - script creation / rewrite
    - background task scheduling
    - SSE streaming
    - workspace update events

    Only `_decide` is replaced by a multi-agent collaboration layer.
    """

    def __init__(self):
        super().__init__()
        self.multi_agent_orchestrator = MultiAgentOrchestrator()

    async def _decide(
        self,
        script_id: Optional[str],
        message: str,
        history: list,
        project_config: Dict[str, Any],
        script: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            decision = await self.multi_agent_orchestrator.decide(
                script_id=script_id,
                message=message,
                history=history,
                project_config=project_config,
                script=script,
            )
            # Preserve the legacy public shape. collaboration_trace is kept for
            # logs/debugging but ignored by the frontend if unused.
            action = str(decision.get("action") or "CHAT").upper()
            params = decision.get("parameters") if isinstance(decision.get("parameters"), dict) else {}
            ui_patch = decision.get("ui_patch") if isinstance(decision.get("ui_patch"), dict) else {}
            if action not in self.ACTIONS:
                action = "CHAT"
            ui_patch.setdefault("active_tab", self.ACTION_TO_TAB.get(action, "canvas"))
            return {
                "action": action,
                "reply": str(decision.get("reply") or "收到。"),
                "parameters": params,
                "ui_patch": ui_patch,
                "collaboration_trace": decision.get("collaboration_trace", {}),
            }
        except Exception as e:
            logger.exception("Multi-agent decision failed, falling back to legacy Director Agent: %s", e)
            return await super()._decide(script_id, message, history, project_config, script)
