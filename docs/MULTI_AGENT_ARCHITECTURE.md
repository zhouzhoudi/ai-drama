# Multi-Agent Architecture

This branch converts the Director Agent decision layer from a single monolithic router into a multi-agent collaboration model while keeping the existing FastAPI routes and React SSE protocol intact.

## Goals

- Keep the current user experience stable.
- Avoid rewriting the large legacy `AgentRouter` file in one risky change.
- Split intent handling into focused agents that can evolve independently.
- Preserve the current action contract used by the frontend:

```json
{
  "action": "GENERATE_STORYBOARD",
  "reply": "...",
  "parameters": {},
  "ui_patch": { "active_tab": "storyboard" }
}
```

## Runtime Flow

```text
User message
  ↓
/v1/agent/chat
  ↓
Legacy AgentRouter API surface
  ↓
MultiAgentOrchestrator.decide(...)
  ↓
Focused sub-agent votes + optional LLM PlannerAgent proposal
  ↓
Best valid action selected
  ↓
Existing backend executors run unchanged
  ↓
Existing frontend SSE events continue unchanged
```

## Agents

| Agent | Responsibility | Output Action |
|---|---|---|
| `PlannerAgent` | Optional LLM planner that proposes the next action | Any allowed action |
| `CreateScriptAgent` | Starts a new project/script when no project exists or user asks to write | `CREATE_SCRIPT` |
| `RewriteScriptAgent` | Handles script edits and rewrites | `REWRITE_SCRIPT` |
| `CharacterAgent` | Generates or regenerates character reference images | `GENERATE_CHARACTERS` |
| `LocationAgent` | Generates or regenerates scene/location images | `GENERATE_LOCATIONS` |
| `StoryboardAgent` | Generates storyboard images and shot visuals | `GENERATE_STORYBOARD` |
| `VideoAgent` | Sends confirmed shots to video generation | `GENERATE_SHOT_VIDEOS` |
| `AudioAgent` | Handles TTS / voice / audio generation | `GENERATE_AUDIO` |
| `MergeAgent` | Exports the final film | `MERGE_FINAL` |
| `StatusAgent` | Checks current task or project status | `QUERY_STATUS` |
| `ChatAgent` | Safe fallback for normal guidance | `CHAT` |

## Integration Strategy

The integration is intentionally conservative:

- `backend/services/multi_agent_orchestrator.py` contains the new agent system.
- `backend/sitecustomize.py` patches `services.agent_router.AgentRouter` at Python startup.
- The existing `AgentRouter` class remains available as `LegacyAgentRouter` after patching.
- Set `AI_DRAMA_DISABLE_MULTI_AGENT=true` to disable the patch and return to the legacy router.

## Why `sitecustomize.py`?

The existing `backend/services/agent_router.py` is large and tightly connected to streaming, execution, status messages, and frontend behavior. Directly replacing it in one commit would be high risk.

`sitecustomize.py` lets us override only the decision step (`_decide`) while keeping every existing executor and SSE behavior unchanged. This makes the migration reversible and easier to review.

## Safety Rules Preserved

The new orchestrator preserves the current safety rules:

- Do not generate video unless the user explicitly asks for video / 出片 / 可灵跑.
- `分镜` means storyboard first, not video generation.
- If there is no project yet, project-dependent actions are converted to `CREATE_SCRIPT`.
- Rework requests like `重做`, `重新生成`, `不满意`, `不像` set `force=true` where appropriate.
- Matching character names, scene names, and shot refs are extracted into action parameters when possible.

## Rollback

Temporary rollback:

```bash
AI_DRAMA_DISABLE_MULTI_AGENT=true python main.py
```

Permanent rollback:

- Delete `backend/sitecustomize.py`.
- Keep or remove `backend/services/multi_agent_orchestrator.py` depending on whether the new code is still needed.

## Next Step

After this branch is verified locally, the next engineering step is to move execution itself into separate worker agents. This branch only changes the decision layer. Existing execution functions remain unchanged on purpose.
