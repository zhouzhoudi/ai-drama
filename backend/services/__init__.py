"""AI Drama Backend Services.

Import compatibility hook:
- Existing backend code imports `services.agent_router.AgentRouter`.
- The actual multi-agent version lives in `services.multi_agent_router`.
- We alias `services.agent_router` to the wrapper module before main.py imports it,
  so the rest of the application can keep the old import path.

Set AI_DRAMA_LEGACY_AGENT_ROUTER=true to disable the hook and use the original
single Director Agent router.
"""

from __future__ import annotations

import importlib
import os
import sys

if os.environ.get("AI_DRAMA_LEGACY_AGENT_ROUTER", "false").lower() != "true":
    sys.modules.setdefault(
        __name__ + ".agent_router",
        importlib.import_module(__name__ + ".multi_agent_router"),
    )
