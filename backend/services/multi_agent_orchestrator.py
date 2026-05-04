"""
Multi-agent orchestration layer for the AI drama system.

This module keeps the existing Director Agent API contract, but splits the
reasoning into focused sub-agents:

- PlannerAgent: understands user intent and optionally asks the configured LLM
  for an action proposal.
- ScriptAgent: create / rewrite script decisions.
- CharacterAgent: character reference image decisions.
- LocationAgent: scene / location image decisions.
- StoryboardAgent: storyboard image decisions.
- VideoAgent: shot video generation decisions.
- AudioAgent: voice / TTS decisions.
- MergeAgent: final film export decisions.
- StatusAgent: progress checks.

The orchestrator returns the same shape as the legacy AgentRouter._decide():
{
    "action": "...",
    "reply": "...",
    "parameters": {...},
    "ui_patch": {"active_tab": "..."}
}

So it can be used as a drop-in decision engine without rewriting the current
FastAPI routes or frontend SSE protocol.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

try:
    from services.llm_client import llm_chat_json
except Exception:  # pragma: no cover - keeps import safe in partial environments
    llm_chat_json = None  # type: ignore


ALLOWED_ACTIONS = {
    "CREATE_SCRIPT",
    "REWRITE_SCRIPT",
    "GENERATE_CHARACTERS",
    "GENERATE_LOCATIONS",
    "GENERATE_STORYBOARD",
    "GENERATE_SHOT_VIDEOS",
    "GENERATE_AUDIO",
    "MERGE_FINAL",
    "QUERY_STATUS",
    "CHAT",
}

ACTION_TO_TAB = {
    "CREATE_SCRIPT": "script",
    "REWRITE_SCRIPT": "script",
    "GENERATE_CHARACTERS": "characters",
    "GENERATE_LOCATIONS": "characters",
    "GENERATE_STORYBOARD": "storyboard",
    "GENERATE_SHOT_VIDEOS": "storyboard",
    "GENERATE_AUDIO": "storyboard",
    "MERGE_FINAL": "storyboard",
    "QUERY_STATUS": "canvas",
    "CHAT": "canvas",
}


@dataclass
class AgentContext:
    script_id: Optional[str]
    message: str
    history: List[Any] = field(default_factory=list)
    project_config: Dict[str, Any] = field(default_factory=dict)
    script: Optional[Dict[str, Any]] = None

    @property
    def text(self) -> str:
        return (self.message or "").strip()

    @property
    def script_exists(self) -> bool:
        return bool(self.script_id and self.script)

    @property
    def characters(self) -> List[Dict[str, Any]]:
        if not self.script:
            return []
        return self.script.get("characters") or []

    @property
    def scenes(self) -> List[Dict[str, Any]]:
        if not self.script:
            return []
        return self.script.get("scenes") or []


@dataclass
class AgentVote:
    agent: str
    action: str
    score: float
    reply: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_decision(self) -> Dict[str, Any]:
        action = self.action if self.action in ALLOWED_ACTIONS else "CHAT"
        return {
            "action": action,
            "reply": self.reply or "收到。",
            "parameters": self.parameters or {},
            "ui_patch": {"active_tab": ACTION_TO_TAB.get(action, "canvas")},
            "collaboration_trace": {
                "selected_agent": self.agent,
                "score": self.score,
                "reason": self.reason,
            },
        }


class BaseSubAgent:
    name = "BaseAgent"
    action = "CHAT"
    priority = 0
    keywords: Sequence[str] = ()

    def score(self, ctx: AgentContext) -> float:
        text = ctx.text.lower()
        score = 0.0
        for keyword in self.keywords:
            if keyword.lower() in text:
                score += 1.0
        return score + self.priority / 100.0 if score > 0 else 0.0

    def vote(self, ctx: AgentContext) -> Optional[AgentVote]:
        score = self.score(ctx)
        if score <= 0:
            return None
        params = self.parameters(ctx)
        return AgentVote(
            agent=self.name,
            action=self.action,
            score=score,
            reply=self.reply(ctx, params),
            parameters=params,
            reason=f"matched keywords: {', '.join(self.keywords)}",
        )

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        return {}

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        return "收到，我来处理。"

    @staticmethod
    def _is_force_request(text: str) -> bool:
        return any(k in text for k in ["重新", "重做", "重画", "再画", "换一张", "不满意", "不像", "force"])


class StatusAgent(BaseSubAgent):
    name = "StatusAgent"
    action = "QUERY_STATUS"
    priority = 95
    keywords = ("状态", "进度", "完成了吗", "到哪了", "现在怎么样", "status")

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        return "我来查看当前项目进度和后台任务状态。"


class MergeAgent(BaseSubAgent):
    name = "MergeAgent"
    action = "MERGE_FINAL"
    priority = 90
    keywords = ("合成", "成片", "导出", "最终视频", "最终成片", "merge")

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        return "好的，我来进入最终成片合成流程。"


class AudioAgent(BaseSubAgent):
    name = "AudioAgent"
    action = "GENERATE_AUDIO"
    priority = 80
    keywords = ("配音", "声音", "音频", "tts", "TTS", "旁白")

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        return "好的，我来处理角色配音和音频生成。"


class VideoAgent(BaseSubAgent):
    name = "VideoAgent"
    action = "GENERATE_SHOT_VIDEOS"
    priority = 75
    keywords = ("生成视频", "出视频", "出片", "可灵跑", "跑视频", "镜头视频", "分镜视频")

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        shot_ids = _extract_shot_refs(ctx.text)
        return {"shot_ids": shot_ids} if shot_ids else {}

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        if params.get("shot_ids"):
            return f"好的，我来生成指定镜头的视频：{', '.join(params['shot_ids'])}。"
        return "好的，我来把已确认的分镜交给视频生成 Agent 处理。"


class StoryboardAgent(BaseSubAgent):
    name = "StoryboardAgent"
    action = "GENERATE_STORYBOARD"
    priority = 70
    keywords = ("分镜图", "分镜", "镜头图", "storyboard")

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        shot_ids = _extract_shot_refs(ctx.text)
        params: Dict[str, Any] = {}
        if shot_ids:
            params["shot_ids"] = shot_ids
        if self._is_force_request(ctx.text):
            params["force"] = True
        return params

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        if params.get("shot_ids"):
            return f"好的，我来重做指定分镜图：{', '.join(params['shot_ids'])}。"
        return "好的，我来生成分镜图。生成视频之前，先用分镜图把画面确认清楚。"


class LocationAgent(BaseSubAgent):
    name = "LocationAgent"
    action = "GENERATE_LOCATIONS"
    priority = 85
    keywords = ("场景图", "地点图", "环境图", "背景图", "场景", "地点")

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        scene_ids = []
        for scene in ctx.scenes:
            scene_id = str(scene.get("scene_id") or "")
            location = str(scene.get("location") or "")
            if scene_id and scene_id in ctx.text:
                scene_ids.append(scene_id)
            elif location and location in ctx.text:
                scene_ids.append(scene_id or location)
        if scene_ids:
            params["scene_ids"] = scene_ids
        if self._is_force_request(ctx.text):
            params["force"] = True
        return params

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        target = "指定场景" if params.get("scene_ids") else "全部场景"
        verb = "重做" if params.get("force") else "生成"
        return f"好的，我来{verb}{target}的场景图。"


class CharacterAgent(BaseSubAgent):
    name = "CharacterAgent"
    action = "GENERATE_CHARACTERS"
    priority = 65
    keywords = ("角色图", "人物图", "角色", "人物", "形象", "主角", "参考图")

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        names = []
        for char in ctx.characters:
            char_id = str(char.get("character_id") or "")
            name = str(char.get("name") or "")
            if char_id and char_id in ctx.text:
                names.append(char_id)
            elif name and name in ctx.text:
                names.append(name)
        if names:
            params["character_ids"] = names
        if self._is_force_request(ctx.text):
            params["force"] = True
        return params

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        target = "、".join(params.get("character_ids") or []) or "全部角色"
        verb = "重做" if params.get("force") else "生成"
        return f"好的，我来{verb}「{target}」的角色参考图。"


class RewriteScriptAgent(BaseSubAgent):
    name = "RewriteScriptAgent"
    action = "REWRITE_SCRIPT"
    priority = 60
    keywords = ("修改", "改一下", "重写", "调整", "换成", "不够", "不满意", "优化剧本", "改剧本")

    def score(self, ctx: AgentContext) -> float:
        if not ctx.script_exists:
            return 0.0
        return super().score(ctx)

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        return {"rewrite_instruction": ctx.text}

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        return "明白，我会交给剧本 Agent 按你的反馈重写，并尽量保留已有项目结构。"


class CreateScriptAgent(BaseSubAgent):
    name = "CreateScriptAgent"
    action = "CREATE_SCRIPT"
    priority = 50
    keywords = ("写", "剧本", "故事", "短剧", "创作", "构思", "新建")

    def score(self, ctx: AgentContext) -> float:
        base = super().score(ctx)
        if not ctx.script_exists and ctx.text:
            base += 2.0
        return base

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        config = ctx.project_config or {}
        return {
            "title": config.get("title") or "新短剧",
            "theme": config.get("theme") or config.get("genre") or "自选",
            "style": config.get("style") or config.get("visual_style") or "写实电影风",
            "duration": int(config.get("duration") or 180),
            "description": ctx.text,
        }

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        return "好的，我先让剧本 Agent 生成剧本、角色和基础分镜。"


class ChatAgent(BaseSubAgent):
    name = "ChatAgent"
    action = "CHAT"
    priority = 1

    def vote(self, ctx: AgentContext) -> Optional[AgentVote]:
        return AgentVote(
            agent=self.name,
            action="CHAT",
            score=0.1,
            reply="收到。你可以继续告诉我要写什么题材，或者让我生成角色图、场景图、分镜图、视频。",
            parameters={},
            reason="fallback chat agent",
        )


class PlannerAgent:
    """Optional LLM planner that proposes an action, then the orchestrator validates it."""

    name = "PlannerAgent"

    async def propose(self, ctx: AgentContext) -> Optional[AgentVote]:
        if llm_chat_json is None:
            return None
        summary = _summarize_script(ctx.script)
        prompt = f"""
你是多 Agent 短剧系统里的 PlannerAgent。你只负责选择下一步 action，不直接执行任务。

可选 action：{', '.join(sorted(ALLOWED_ACTIONS))}

当前项目摘要：
{json.dumps(summary, ensure_ascii=False, indent=2)}

用户消息：{ctx.text}

只输出 JSON：
{{"action":"...","confidence":0.0-1.0,"reply":"一句中文回复","parameters":{{}},"reason":"选择原因"}}

规则：
1. 用户没明确说生成视频，不要选 GENERATE_SHOT_VIDEOS。
2. 用户说分镜，优先 GENERATE_STORYBOARD；说出片/生成视频/跑可灵才选 GENERATE_SHOT_VIDEOS。
3. 已有项目且用户说修改/重写/不满意，优先 REWRITE_SCRIPT。
4. 没有项目但用户要角色/分镜/视频，先 CREATE_SCRIPT。
"""
        try:
            data = await llm_chat_json(
                messages=[
                    {"role": "system", "content": "你是短剧多 Agent 编排器，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=800,
                timeout=8,
            )
        except Exception:
            return None

        action = str(data.get("action") or "CHAT").upper()
        if action not in ALLOWED_ACTIONS:
            return None
        confidence = float(data.get("confidence") or 0.0)
        params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
        return AgentVote(
            agent=self.name,
            action=action,
            score=confidence + 0.25,
            reply=str(data.get("reply") or "收到，我来安排。"),
            parameters=params,
            reason=str(data.get("reason") or "LLM planner proposal"),
        )


class MultiAgentOrchestrator:
    """Coordinates focused sub-agents and returns a legacy-compatible decision."""

    def __init__(self) -> None:
        self.planner = PlannerAgent()
        self.agents: List[BaseSubAgent] = [
            StatusAgent(),
            MergeAgent(),
            AudioAgent(),
            VideoAgent(),
            LocationAgent(),
            StoryboardAgent(),
            CharacterAgent(),
            RewriteScriptAgent(),
            CreateScriptAgent(),
            ChatAgent(),
        ]

    async def decide(
        self,
        script_id: Optional[str],
        message: str,
        history: Optional[list] = None,
        project_config: Optional[Dict[str, Any]] = None,
        script: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = AgentContext(
            script_id=script_id,
            message=message,
            history=history or [],
            project_config=project_config or {},
            script=script,
        )

        votes: List[AgentVote] = []
        for agent in self.agents:
            vote = agent.vote(ctx)
            if vote:
                votes.append(vote)

        planner_vote = await self.planner.propose(ctx)
        if planner_vote:
            votes.append(self._complete_planner_vote(planner_vote, ctx))

        votes = [self._guard_no_project(v, ctx) for v in votes]
        votes.sort(key=lambda v: v.score, reverse=True)
        selected = votes[0] if votes else ChatAgent().vote(ctx)  # type: ignore
        decision = selected.to_decision()
        decision["collaboration_trace"].update({
            "votes": [
                {"agent": v.agent, "action": v.action, "score": round(v.score, 3), "reason": v.reason}
                for v in votes[:6]
            ]
        })
        return self._sanitize_decision(decision)

    def _complete_planner_vote(self, vote: AgentVote, ctx: AgentContext) -> AgentVote:
        if vote.parameters and vote.reply:
            return vote
        for agent in self.agents:
            if agent.action == vote.action:
                if not vote.parameters:
                    vote.parameters = agent.parameters(ctx)
                if not vote.reply:
                    vote.reply = agent.reply(ctx, vote.parameters)
                vote.reason = f"planner + {agent.name}: {vote.reason}"
                break
        return vote

    def _guard_no_project(self, vote: AgentVote, ctx: AgentContext) -> AgentVote:
        needs_project = {
            "REWRITE_SCRIPT",
            "GENERATE_CHARACTERS",
            "GENERATE_LOCATIONS",
            "GENERATE_STORYBOARD",
            "GENERATE_SHOT_VIDEOS",
            "GENERATE_AUDIO",
            "MERGE_FINAL",
        }
        if not ctx.script_exists and vote.action in needs_project:
            return AgentVote(
                agent="ProjectBootstrapAgent",
                action="CREATE_SCRIPT",
                score=vote.score + 0.5,
                reply="当前还没有可操作的项目，我先帮你创建剧本和基础分镜，再继续后面的角色、场景或视频流程。",
                parameters=CreateScriptAgent().parameters(ctx),
                reason=f"converted {vote.action} to CREATE_SCRIPT because no project exists",
            )
        return vote

    def _sanitize_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        action = str(decision.get("action") or "CHAT").upper()
        if action not in ALLOWED_ACTIONS:
            action = "CHAT"
        params = decision.get("parameters") if isinstance(decision.get("parameters"), dict) else {}
        ui_patch = decision.get("ui_patch") if isinstance(decision.get("ui_patch"), dict) else {}
        ui_patch.setdefault("active_tab", ACTION_TO_TAB.get(action, "canvas"))
        return {
            "action": action,
            "reply": str(decision.get("reply") or "收到。"),
            "parameters": params,
            "ui_patch": ui_patch,
            "collaboration_trace": decision.get("collaboration_trace", {}),
        }


def _extract_shot_refs(text: str) -> List[str]:
    refs: List[str] = []
    for match in re.findall(r"shot[_-]?\d+", text or "", flags=re.IGNORECASE):
        normalized = match.lower().replace("-", "_")
        if normalized not in refs:
            refs.append(normalized)
    for number in re.findall(r"第\s*(\d+)\s*(?:个)?镜", text or ""):
        ref = f"shot_{number}"
        if ref not in refs:
            refs.append(ref)
    return refs


def _summarize_script(script: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not script:
        return {"exists": False}
    scenes = script.get("scenes") or []
    characters = script.get("characters") or []
    shots_count = sum(len(scene.get("shots") or []) for scene in scenes if isinstance(scene, dict))
    return {
        "exists": True,
        "script_id": script.get("script_id"),
        "title": script.get("title"),
        "theme": script.get("theme") or script.get("genre"),
        "style": script.get("style"),
        "characters_count": len(characters),
        "character_names": [c.get("name") for c in characters if isinstance(c, dict)],
        "scenes_count": len(scenes),
        "scene_locations": [s.get("location") for s in scenes if isinstance(s, dict)],
        "shots_count": shots_count,
        "production_status": script.get("production_status"),
    }
