"""
Compatibility wrapper that turns the existing AgentRouter into a multi-agent router.

The legacy agent_router.py already owns all execution logic and SSE streaming.
This wrapper only replaces the decision step with MultiAgentOrchestrator.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from services.multi_agent_orchestrator import MultiAgentOrchestrator

logger = logging.getLogger(__name__)


def load_legacy_agent_router_class():
    legacy_path = Path(__file__).with_name("agent_router.py")
    spec = importlib.util.spec_from_file_location("services._legacy_agent_router", legacy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load legacy AgentRouter from {legacy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AgentRouter


LegacyAgentRouter = load_legacy_agent_router_class()


class AgentRouter(LegacyAgentRouter):
    """Drop-in replacement for the original AgentRouter."""

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
            action = str(decision.get("action") or "CHAT").upper()
            if action not in self.ACTIONS:
                action = "CHAT"
            params = decision.get("parameters") if isinstance(decision.get("parameters"), dict) else {}
            ui_patch = decision.get("ui_patch") if isinstance(decision.get("ui_patch"), dict) else {}
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
