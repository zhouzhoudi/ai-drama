"""AI Drama Backend Services.

Model policy:
- All language-model agents default to deepseek-v4-pro.
- All Kling image/video generators default to kling-v3-omni.

Import compatibility hook:
- Existing backend code imports `services.agent_router.AgentRouter`.
- The actual multi-agent version lives in `services.multi_agent_router`.
- We alias `services.agent_router` to the wrapper module before main.py imports it,
  so the rest of the application can keep the old import path.

Rollback:
- Set AI_DRAMA_LEGACY_AGENT_ROUTER=true to use the original single Director Agent router.
- Set AI_DRAMA_SKIP_MODEL_POLICY=true only if you intentionally want to manage all
  model env vars yourself.
"""

from __future__ import annotations

import importlib
import os
import sys


def _apply_default_model_policy() -> None:
    if os.environ.get("AI_DRAMA_SKIP_MODEL_POLICY", "false").lower() == "true":
        return
    # Set runtime defaults before submodules read backend/.env. This means an old
    # .env value such as LLM_MODEL=deepseek-chat will not silently override the
    # product-level model policy unless AI_DRAMA_SKIP_MODEL_POLICY=true is set.
    os.environ.setdefault("LLM_MODEL", "deepseek-v4-pro")
    os.environ.setdefault("KLING_IMAGE_MODEL", "kling-v3-omni")
    os.environ.setdefault("KLING_CHARACTER_IMAGE_MODEL", "kling-v3-omni")
    os.environ.setdefault("KLING_STORYBOARD_IMAGE_MODEL", "kling-v3-omni")
    os.environ.setdefault("KLING_VIDEO_MODEL", "kling-v3-omni")


_apply_default_model_policy()

if os.environ.get("AI_DRAMA_LEGACY_AGENT_ROUTER", "false").lower() != "true":
    sys.modules.setdefault(
        __name__ + ".agent_router",
        importlib.import_module(__name__ + ".multi_agent_router"),
    )
