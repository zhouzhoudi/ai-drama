"""
Multi-agent orchestration layer for the AI drama system.

Production pipeline requested by product design:

1. 编剧agent
2. 剧本审阅与复核agent
3. 主体分析设计agent（根据剧本分析角色、场景、道具）
4. 主体生图agent
5. 分镜脚本编写agent
6. 分镜审阅agent
7. 分镜生图agent
8. 分镜生视频agent

Model policy:
- All language-model decisions use deepseek-v4-pro.
- Image generation and video generation are routed to kling-v3-omni by default.

This module only decides the next backend action. Existing execution code still
lives in the legacy AgentRouter and FastAPI background tasks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


try:
    from services.llm_client import llm_chat_json
except Exception:  # keeps imports safe in partial/local environments
    llm_chat_json = None  # type: ignore


DEFAULT_LANGUAGE_MODEL = "deepseek-v4-pro"
DEFAULT_IMAGE_MODEL = "kling-v3-omni"
DEFAULT_VIDEO_MODEL = "kling-v3-omni"

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

PIPELINE_AGENTS: List[Dict[str, str]] = [
    {
        "id": "screenwriter",
        "name": "编剧agent",
        "model": DEFAULT_LANGUAGE_MODEL,
        "responsibility": "根据用户创意生成短剧剧本、角色草案、场景草案和基础镜头结构。",
    },
    {
        "id": "script_reviewer",
        "name": "剧本审阅与复核agent",
        "model": DEFAULT_LANGUAGE_MODEL,
        "responsibility": "检查剧情逻辑、人物动机、节奏、冲突、对白和可拍摄性，并触发剧本修订。",
    },
    {
        "id": "subject_designer",
        "name": "主体分析设计agent",
        "model": DEFAULT_LANGUAGE_MODEL,
        "responsibility": "从剧本里提取并规范化角色、场景、道具，形成后续生图主体清单。",
    },
    {
        "id": "subject_image",
        "name": "主体生图agent",
        "model": DEFAULT_IMAGE_MODEL,
        "responsibility": "调用 Kling 生成角色主体图、场景主体图和可复用视觉参考。",
    },
    {
        "id": "shot_script_writer",
        "name": "分镜脚本编写agent",
        "model": DEFAULT_LANGUAGE_MODEL,
        "responsibility": "把剧本拆成可拍摄镜头，补齐镜头类型、动作、对白、运镜和可灵提示词。",
    },
    {
        "id": "shot_reviewer",
        "name": "分镜审阅agent",
        "model": DEFAULT_LANGUAGE_MODEL,
        "responsibility": "复核镜头连续性、角色一致性、场景一致性、对白与画面匹配度。",
    },
    {
        "id": "storyboard_image",
        "name": "分镜生图agent",
        "model": DEFAULT_IMAGE_MODEL,
        "responsibility": "调用 Kling 为每个镜头生成分镜图，作为视频首帧/参考图。",
    },
    {
        "id": "storyboard_video",
        "name": "分镜生视频agent",
        "model": DEFAULT_VIDEO_MODEL,
        "responsibility": "调用 Kling OmniVideo 根据分镜图、对白和镜头提示生成视频片段。",
    },
]


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
    def lower_text(self) -> str:
        return self.text.lower()

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

    def as_decision(self) -> Dict[str, Any]:
        action = self.action if self.action in ALLOWED_ACTIONS else "CHAT"
        params = dict(self.parameters or {})
        params.setdefault("language_model", DEFAULT_LANGUAGE_MODEL)
        params.setdefault("image_model", DEFAULT_IMAGE_MODEL)
        params.setdefault("video_model", DEFAULT_VIDEO_MODEL)
        return {
            "action": action,
            "reply": self.reply or "收到。",
            "parameters": params,
            "ui_patch": {"active_tab": ACTION_TO_TAB.get(action, "canvas")},
            "collaboration_trace": {
                "selected_agent": self.agent,
                "score": round(self.score, 3),
                "reason": self.reason,
                "language_model": DEFAULT_LANGUAGE_MODEL,
                "image_model": DEFAULT_IMAGE_MODEL,
                "video_model": DEFAULT_VIDEO_MODEL,
            },
        }


class BaseSubAgent:
    name = "BaseAgent"
    action = "CHAT"
    priority = 0
    keywords: Sequence[str] = ()

    def score(self, ctx: AgentContext) -> float:
        text = ctx.lower_text
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
    def is_force_request(text: str) -> bool:
        return any(k in text for k in ["重新", "重做", "重画", "再画", "换一张", "不满意", "不像", "force"])


class ScreenwriterAgent(BaseSubAgent):
    name = "编剧agent"
    action = "CREATE_SCRIPT"
    priority = 50
    keywords = ("写", "剧本", "故事", "短剧", "创作", "构思", "新建", "开一个项目")

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
            "required_agents": ["编剧agent", "剧本审阅与复核agent", "主体分析设计agent", "分镜脚本编写agent"],
        }

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        return "好的，我会先交给编剧agent生成剧本，再由审阅和主体分析流程补齐角色、场景、道具。"


class ScriptReviewAgent(BaseSubAgent):
    name = "剧本审阅与复核agent"
    action = "REWRITE_SCRIPT"
    priority = 88
    keywords = ("审阅剧本", "复核剧本", "检查剧本", "剧本问题", "逻辑问题", "剧情漏洞", "节奏问题", "对白不行", "优化剧本")

    def score(self, ctx: AgentContext) -> float:
        if not ctx.script_exists:
            return 0.0
        return super().score(ctx)

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        return {
            "rewrite_instruction": (
                "由剧本审阅与复核agent审阅当前剧本，检查剧情逻辑、人物动机、冲突、节奏、对白、"
                f"可拍摄性，并根据用户要求修订：{ctx.text}"
            ),
            "review_required": True,
        }

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        return "明白，我会让剧本审阅与复核agent先检查逻辑、节奏和对白，再输出修订版剧本。"


class SubjectAnalysisDesignAgent(BaseSubAgent):
    name = "主体分析设计agent"
    action = "REWRITE_SCRIPT"
    priority = 86
    keywords = ("主体分析", "主体设计", "分析主体", "角色场景道具", "分析角色", "分析场景", "分析道具", "道具清单", "角色清单", "场景清单")

    def score(self, ctx: AgentContext) -> float:
        if not ctx.script_exists:
            return super().score(ctx) + 0.8
        return super().score(ctx)

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        if not ctx.script_exists:
            return ScreenwriterAgent().parameters(ctx)
        return {
            "rewrite_instruction": (
                "由主体分析设计agent基于当前剧本补全并规范化主体资产：角色、场景、道具。"
                "要求每个角色有 character_id/name/appearance/image_prompt；每个场景有 scene_id/location/environment_prompt；"
                "重要道具写入 props 或 asset_refs，便于后续主体生图和分镜生图。"
                f"用户补充要求：{ctx.text}"
            ),
            "subject_analysis_required": True,
            "subject_types": ["characters", "locations", "props"],
        }

    def vote(self, ctx: AgentContext) -> Optional[AgentVote]:
        score = self.score(ctx)
        if score <= 0:
            return None
        params = self.parameters(ctx)
        action = "CREATE_SCRIPT" if not ctx.script_exists else self.action
        return AgentVote(
            agent=self.name,
            action=action,
            score=score,
            reply=self.reply(ctx, params),
            parameters=params,
            reason="subject analysis/design requested",
        )

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        if not ctx.script_exists:
            return "当前还没有剧本，我会先让编剧agent生成剧本，再让主体分析设计agent整理角色、场景和道具。"
        return "好的，我会让主体分析设计agent从剧本里拆出角色、场景、道具，并补齐后续生图需要的主体描述。"


class SubjectImageAgent(BaseSubAgent):
    name = "主体生图agent"
    priority = 84
    keywords = ("主体生图", "主体图", "角色图", "人物图", "角色形象", "参考图", "场景图", "地点图", "环境图", "道具图")

    def vote(self, ctx: AgentContext) -> Optional[AgentVote]:
        score = self.score(ctx)
        if score <= 0:
            return None
        params = self.parameters(ctx)
        action = "GENERATE_LOCATIONS" if self._wants_location_or_props(ctx.text) else "GENERATE_CHARACTERS"
        return AgentVote(
            agent=self.name,
            action=action,
            score=score,
            reply=self.reply_for_action(ctx, params, action),
            parameters=params,
            reason="subject image generation requested",
        )

    @staticmethod
    def _wants_location_or_props(text: str) -> bool:
        location_words = ["场景", "地点", "环境", "背景", "道具"]
        character_words = ["角色", "人物", "主角"]
        return any(w in text for w in location_words) and not any(w in text for w in character_words)

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        params: Dict[str, Any] = {"image_model": DEFAULT_IMAGE_MODEL}
        character_refs = []
        for char in ctx.characters:
            char_id = str(char.get("character_id") or "")
            name = str(char.get("name") or "")
            if char_id and char_id in ctx.text:
                character_refs.append(char_id)
            elif name and name in ctx.text:
                character_refs.append(name)
        if character_refs:
            params["character_ids"] = character_refs

        scene_refs = []
        for scene in ctx.scenes:
            scene_id = str(scene.get("scene_id") or "")
            location = str(scene.get("location") or "")
            if scene_id and scene_id in ctx.text:
                scene_refs.append(scene_id)
            elif location and location in ctx.text:
                scene_refs.append(scene_id or location)
        if scene_refs:
            params["scene_ids"] = scene_refs

        if self.is_force_request(ctx.text):
            params["force"] = True
        return params

    def reply_for_action(self, ctx: AgentContext, params: Dict[str, Any], action: str) -> str:
        verb = "重做" if params.get("force") else "生成"
        if action == "GENERATE_LOCATIONS":
            target = "指定场景/道具" if params.get("scene_ids") else "全部场景主体"
            return f"好的，我会让主体生图agent用 {DEFAULT_IMAGE_MODEL} {verb}{target}图。"
        target = "、".join(params.get("character_ids") or []) or "全部角色主体"
        return f"好的，我会让主体生图agent用 {DEFAULT_IMAGE_MODEL} {verb}「{target}」参考图。"


class ShotScriptWriterAgent(BaseSubAgent):
    name = "分镜脚本编写agent"
    action = "REWRITE_SCRIPT"
    priority = 82
    keywords = ("分镜脚本", "镜头脚本", "拆分镜", "拆镜头", "shot script", "镜头表", "可拍摄镜头")

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        if not ctx.script_exists:
            return ScreenwriterAgent().parameters(ctx)
        return {
            "rewrite_instruction": (
                "由分镜脚本编写agent把当前剧本拆成可拍摄镜头表。每个 shot 必须包含 shot_id、shot_type、"
                "content_description、dialogue、camera_movement、visual_prompt_for_kling、duration、character_ids。"
                f"用户补充要求：{ctx.text}"
            ),
            "shot_script_required": True,
        }

    def vote(self, ctx: AgentContext) -> Optional[AgentVote]:
        score = self.score(ctx)
        if score <= 0:
            return None
        action = "CREATE_SCRIPT" if not ctx.script_exists else self.action
        params = self.parameters(ctx)
        return AgentVote(
            agent=self.name,
            action=action,
            score=score,
            reply=self.reply(ctx, params),
            parameters=params,
            reason="shot script writing requested",
        )

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        if not ctx.script_exists:
            return "还没有剧本，我会先生成剧本，再让分镜脚本编写agent拆成镜头表。"
        return "好的，我会让分镜脚本编写agent把剧本拆成可拍摄的镜头表。"


class ShotReviewAgent(BaseSubAgent):
    name = "分镜审阅agent"
    action = "REWRITE_SCRIPT"
    priority = 80
    keywords = ("分镜审阅", "审阅分镜", "复核分镜", "检查分镜", "镜头不连贯", "分镜问题", "镜头逻辑")

    def score(self, ctx: AgentContext) -> float:
        if not ctx.script_exists:
            return 0.0
        return super().score(ctx)

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        return {
            "rewrite_instruction": (
                "由分镜审阅agent检查当前镜头表：镜头连续性、角色一致性、场景一致性、对白与画面匹配、"
                "可灵提示词是否可执行；发现问题后修订 shots。"
                f"用户补充要求：{ctx.text}"
            ),
            "shot_review_required": True,
        }

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        return "收到，我会让分镜审阅agent复核镜头连续性、角色一致性和可灵提示词，然后修订分镜。"


class StoryboardImageAgent(BaseSubAgent):
    name = "分镜生图agent"
    action = "GENERATE_STORYBOARD"
    priority = 78
    keywords = ("分镜生图", "分镜图", "镜头图", "storyboard", "首帧图")

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        params: Dict[str, Any] = {"image_model": DEFAULT_IMAGE_MODEL}
        shot_ids = extract_shot_refs(ctx.text)
        if shot_ids:
            params["shot_ids"] = shot_ids
        if self.is_force_request(ctx.text):
            params["force"] = True
        return params

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        if params.get("shot_ids"):
            return f"好的，我会让分镜生图agent用 {DEFAULT_IMAGE_MODEL} 生成指定镜头图：{', '.join(params['shot_ids'])}。"
        return f"好的，我会让分镜生图agent用 {DEFAULT_IMAGE_MODEL} 生成分镜图，先把视频首帧确认清楚。"


class StoryboardVideoAgent(BaseSubAgent):
    name = "分镜生视频agent"
    action = "GENERATE_SHOT_VIDEOS"
    priority = 76
    keywords = ("分镜生视频", "生成视频", "出视频", "出片", "可灵跑", "跑视频", "镜头视频", "生视频")

    def parameters(self, ctx: AgentContext) -> Dict[str, Any]:
        params: Dict[str, Any] = {"video_model": DEFAULT_VIDEO_MODEL}
        shot_ids = extract_shot_refs(ctx.text)
        if shot_ids:
            params["shot_ids"] = shot_ids
        return params

    def reply(self, ctx: AgentContext, params: Dict[str, Any]) -> str:
        if params.get("shot_ids"):
            return f"好的，我会让分镜生视频agent用 {DEFAULT_VIDEO_MODEL} 生成指定镜头视频：{', '.join(params['shot_ids'])}。"
        return f"好的，我会让分镜生视频agent用 {DEFAULT_VIDEO_MODEL} 把已确认的分镜生成视频片段。"


class PlannerAgent:
    """Optional LLM planner. Heuristic sub-agents still work if LLM is unavailable."""

    name = "PlannerAgent"

    async def propose(self, ctx: AgentContext) -> Optional[AgentVote]:
        if llm_chat_json is None:
            return None
        summary = summarize_script(ctx.script)
        prompt = f"""
你是短剧多 Agent 系统里的 PlannerAgent。你只负责选择下一步 action，不直接执行任务。

固定模型规则：
- 所有语言模型：{DEFAULT_LANGUAGE_MODEL}
- 所有生图：{DEFAULT_IMAGE_MODEL}
- 所有生视频：{DEFAULT_VIDEO_MODEL}

系统一共包含 8 个生产 Agent：
{json.dumps(PIPELINE_AGENTS, ensure_ascii=False, indent=2)}

可选 action：{', '.join(sorted(ALLOWED_ACTIONS))}

当前项目摘要：
{json.dumps(summary, ensure_ascii=False, indent=2)}

用户消息：{ctx.text}

只输出 JSON：
{{"agent":"8个生产Agent之一","action":"...","confidence":0.0-1.0,"reply":"一句中文回复","parameters":{{}},"reason":"选择原因"}}

规则：
1. 用户没明确说生成视频，不要选 GENERATE_SHOT_VIDEOS。
2. 用户说分镜图/首帧图，选 GENERATE_STORYBOARD；说出片/生成视频/跑可灵才选 GENERATE_SHOT_VIDEOS。
3. 用户说主体分析/角色场景道具，已有剧本选 REWRITE_SCRIPT，没有剧本先 CREATE_SCRIPT。
4. 用户说主体生图，按角色图选 GENERATE_CHARACTERS，按场景/道具图选 GENERATE_LOCATIONS。
5. 已有项目且用户说审阅/复核/修改/不满意，优先 REWRITE_SCRIPT。
"""
        try:
            data = await llm_chat_json(
                messages=[
                    {"role": "system", "content": f"你是短剧多 Agent 编排器，只输出 JSON。模型固定为 {DEFAULT_LANGUAGE_MODEL}。"},
                    {"role": "user", "content": prompt},
                ],
                model=DEFAULT_LANGUAGE_MODEL,
                temperature=0.1,
                max_tokens=900,
                timeout=8,
            )
        except Exception:
            return None

        action = str(data.get("action") or "CHAT").upper()
        if action not in ALLOWED_ACTIONS:
            return None
        confidence = float(data.get("confidence") or 0.0)
        params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
        params.setdefault("language_model", DEFAULT_LANGUAGE_MODEL)
        params.setdefault("image_model", DEFAULT_IMAGE_MODEL)
        params.setdefault("video_model", DEFAULT_VIDEO_MODEL)
        return AgentVote(
            agent=str(data.get("agent") or self.name),
            action=action,
            score=confidence + 0.25,
            reply=str(data.get("reply") or "收到，我来安排。"),
            parameters=params,
            reason=str(data.get("reason") or "LLM planner proposal"),
        )


class MultiAgentOrchestrator:
    """Coordinates the 8 production agents and returns a legacy-compatible decision."""

    def __init__(self) -> None:
        self.planner = PlannerAgent()
        self.agents: List[BaseSubAgent] = [
            ScreenwriterAgent(),
            ScriptReviewAgent(),
            SubjectAnalysisDesignAgent(),
            SubjectImageAgent(),
            ShotScriptWriterAgent(),
            ShotReviewAgent(),
            StoryboardImageAgent(),
            StoryboardVideoAgent(),
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
            project_config=normalize_project_config(project_config or {}),
            script=script,
        )

        status_vote = self.maybe_status_vote(ctx)
        votes: List[AgentVote] = [status_vote] if status_vote else []

        for agent in self.agents:
            vote = agent.vote(ctx)
            if vote:
                votes.append(vote)

        planner_vote = await self.planner.propose(ctx)
        if planner_vote:
            votes.append(self.complete_planner_vote(planner_vote, ctx))

        votes = [self.guard_no_project(v, ctx) for v in votes]
        votes.sort(key=lambda v: v.score, reverse=True)

        selected = votes[0] if votes else AgentVote(
            agent="系统兜底",
            action="CHAT",
            score=0.1,
            reply="收到。你可以继续告诉我要写剧本、审阅剧本、分析主体、生成主体图、写分镜、审阅分镜、生成分镜图或生成视频。",
            parameters={},
            reason="fallback",
        )
        decision = selected.as_decision()
        decision["collaboration_trace"].update({
            "pipeline_agents": PIPELINE_AGENTS,
            "votes": [
                {"agent": v.agent, "action": v.action, "score": round(v.score, 3), "reason": v.reason}
                for v in votes[:8]
            ],
        })
        return sanitize_decision(decision)

    def maybe_status_vote(self, ctx: AgentContext) -> Optional[AgentVote]:
        if any(k in ctx.text for k in ["状态", "进度", "完成了吗", "到哪了", "现在怎么样", "status"]):
            return AgentVote(
                agent="系统状态路由",
                action="QUERY_STATUS",
                score=1.95,
                reply="我来查看当前项目进度和后台任务状态。",
                parameters={},
                reason="status query",
            )
        return None

    def complete_planner_vote(self, vote: AgentVote, ctx: AgentContext) -> AgentVote:
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

    def guard_no_project(self, vote: AgentVote, ctx: AgentContext) -> AgentVote:
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
                agent="编剧agent",
                action="CREATE_SCRIPT",
                score=vote.score + 0.5,
                reply="当前还没有可操作的剧本项目，我先让编剧agent创建剧本，再进入后续主体、分镜或视频流程。",
                parameters=ScreenwriterAgent().parameters(ctx),
                reason=f"converted {vote.action} to CREATE_SCRIPT because no project exists",
            )
        return vote


def normalize_project_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(config or {})
    normalized.setdefault("language_model", DEFAULT_LANGUAGE_MODEL)
    normalized.setdefault("llm_model", DEFAULT_LANGUAGE_MODEL)
    normalized.setdefault("image_engine", "kling")
    normalized.setdefault("video_engine", "kling")
    normalized.setdefault("image_model", DEFAULT_IMAGE_MODEL)
    normalized.setdefault("video_model", DEFAULT_VIDEO_MODEL)
    normalized.setdefault("kling_image_model", DEFAULT_IMAGE_MODEL)
    normalized.setdefault("kling_video_model", DEFAULT_VIDEO_MODEL)
    return normalized


def sanitize_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    action = str(decision.get("action") or "CHAT").upper()
    if action not in ALLOWED_ACTIONS:
        action = "CHAT"
    params = decision.get("parameters") if isinstance(decision.get("parameters"), dict) else {}
    params.setdefault("language_model", DEFAULT_LANGUAGE_MODEL)
    params.setdefault("image_model", DEFAULT_IMAGE_MODEL)
    params.setdefault("video_model", DEFAULT_VIDEO_MODEL)
    ui_patch = decision.get("ui_patch") if isinstance(decision.get("ui_patch"), dict) else {}
    ui_patch.setdefault("active_tab", ACTION_TO_TAB.get(action, "canvas"))
    return {
        "action": action,
        "reply": str(decision.get("reply") or "收到。"),
        "parameters": params,
        "ui_patch": ui_patch,
        "collaboration_trace": decision.get("collaboration_trace", {}),
    }


def extract_shot_refs(text: str) -> List[str]:
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


def summarize_script(script: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not script:
        return {"exists": False}
    scenes = script.get("scenes") or []
    characters = script.get("characters") or []
    shots_count = sum(len(scene.get("shots") or []) for scene in scenes if isinstance(scene, dict))
    props = script.get("props") or script.get("assets") or []
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
        "props_count": len(props) if isinstance(props, list) else 0,
        "shots_count": shots_count,
        "production_status": script.get("production_status"),
    }
