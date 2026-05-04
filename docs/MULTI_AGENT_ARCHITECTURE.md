# Multi-Agent Architecture

This branch introduces a multi-agent decision layer for the AI drama backend.

## Goal

The previous backend used one Director Agent prompt to decide every action. That
works, but it makes the prompt large and mixes several responsibilities:

- script writing
- rewrite intent detection
- character image generation
- location image generation
- storyboard generation
- shot video generation
- audio / TTS
- final merge
- progress query

The new design keeps the same API and frontend protocol, but splits the decision
step into specialist agents.

## Files

### `backend/services/multi_agent_orchestrator.py`

Defines the multi-agent decision engine.

Specialists:

- `PlannerAgent`: optional LLM planner that proposes a route.
- `CreateScriptAgent`: creates a new script project.
- `RewriteScriptAgent`: rewrites an existing project.
- `CharacterAgent`: generates or regenerates character reference images.
- `LocationAgent`: generates or regenerates scene/location images.
- `StoryboardAgent`: generates storyboard images.
- `VideoAgent`: generates shot videos.
- `AudioAgent`: generates audio/TTS.
- `MergeAgent`: final export.
- `StatusAgent`: progress/status query.
- `ChatAgent`: fallback guidance.

The orchestrator collects votes from these agents, optionally adds one LLM
planner vote, applies guardrails, then returns a legacy-compatible decision.

### `backend/services/multi_agent_router.py`

A compatibility wrapper around the existing `AgentRouter`.

The original `agent_router.py` still owns all execution logic:

- SSE streaming
- script creation
- rewrite
- background task scheduling
- workspace updates

The wrapper only overrides `_decide()`.

### `backend/services/__init__.py`

Adds an import hook so existing code can keep using:

```python
from services.agent_router import AgentRouter
```

but actually receive the multi-agent wrapper.

## Rollback

Set this environment variable before starting the backend:

```bash
AI_DRAMA_LEGACY_AGENT_ROUTER=true
```

Then the import hook is disabled and the original single-agent router is used.

## Why this is low-risk

No frontend route changes.
No FastAPI route changes.
No execution code rewrite.
No generator API changes.

Only the intent routing step is replaced.

## Next improvements

Recommended follow-up work:

1. Move hardcoded `/Users/zhoumi/ai-drama-system` paths into env config.
2. Add unit tests for decision routing.
3. Persist `collaboration_trace` into task logs for debugging.
4. Split execution into true worker agents backed by a queue.
5. Add a visual multi-agent timeline in the frontend.
