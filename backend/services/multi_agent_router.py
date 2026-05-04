"""
Production multi-agent router.

This wrapper keeps the existing AgentRouter execution code available, but upgrades
its decision layer into a standard eight-step production pipeline with explicit
actions instead of overloading everything into REWRITE_SCRIPT.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
    """Drop-in router with explicit eight-agent production actions."""

    PIPELINE_ACTIONS = {
        "CREATE_SCRIPT",
        "REVIEW_SCRIPT",
        "ANALYZE_SUBJECTS",
        "GENERATE_SUBJECT_IMAGES",
        "WRITE_SHOT_SCRIPT",
        "REVIEW_SHOTS",
        "GENERATE_STORYBOARD_IMAGES",
        "GENERATE_STORYBOARD_VIDEOS",
        "QUERY_STATUS",
        "CHAT",
    }

    LEGACY_ACTIONS = set(getattr(LegacyAgentRouter, "ACTIONS", set()))
    ACTIONS = LEGACY_ACTIONS | PIPELINE_ACTIONS

    ACTION_TO_TAB = {
        **getattr(LegacyAgentRouter, "ACTION_TO_TAB", {}),
        "CREATE_SCRIPT": "script",
        "REVIEW_SCRIPT": "script",
        "ANALYZE_SUBJECTS": "characters",
        "GENERATE_SUBJECT_IMAGES": "characters",
        "WRITE_SHOT_SCRIPT": "storyboard",
        "REVIEW_SHOTS": "storyboard",
        "GENERATE_STORYBOARD_IMAGES": "storyboard",
        "GENERATE_STORYBOARD_VIDEOS": "storyboard",
        "QUERY_STATUS": "canvas",
        "CHAT": "canvas",
    }

    LANGUAGE_PIPELINE_ACTIONS = {
        "REVIEW_SCRIPT",
        "ANALYZE_SUBJECTS",
        "WRITE_SHOT_SCRIPT",
        "REVIEW_SHOTS",
    }

    AGENT_TO_ACTION = {
        "编剧agent": "CREATE_SCRIPT",
        "剧本审阅与复核agent": "REVIEW_SCRIPT",
        "主体分析设计agent": "ANALYZE_SUBJECTS",
        "主体生图agent": "GENERATE_SUBJECT_IMAGES",
        "分镜脚本编写agent": "WRITE_SHOT_SCRIPT",
        "分镜审阅agent": "REVIEW_SHOTS",
        "分镜生图agent": "GENERATE_STORYBOARD_IMAGES",
        "分镜生视频agent": "GENERATE_STORYBOARD_VIDEOS",
    }

    LEGACY_TO_PIPELINE = {
        "GENERATE_STORYBOARD": "GENERATE_STORYBOARD_IMAGES",
        "GENERATE_SHOT_VIDEOS": "GENERATE_STORYBOARD_VIDEOS",
        # 当前没有独立 TTS 能力。配音/声音需求统一交给 Kling OmniVideo sound=on。
        "GENERATE_AUDIO": "GENERATE_STORYBOARD_VIDEOS",
    }

    def __init__(self):
        super().__init__()
        self.multi_agent_orchestrator = MultiAgentOrchestrator()

    # ----------------------- decision normalization -----------------------

    def _pipeline_action_from_decision(self, decision: Dict[str, Any]) -> str:
        action = str(decision.get("action") or "CHAT").upper()
        params = decision.get("parameters") if isinstance(decision.get("parameters"), dict) else {}
        trace = decision.get("collaboration_trace") if isinstance(decision.get("collaboration_trace"), dict) else {}
        selected_agent = str(trace.get("selected_agent") or decision.get("agent") or "")

        if action == "REWRITE_SCRIPT":
            if params.get("review_required"):
                return "REVIEW_SCRIPT"
            if params.get("subject_analysis_required"):
                return "ANALYZE_SUBJECTS"
            if params.get("shot_script_required"):
                return "WRITE_SHOT_SCRIPT"
            if params.get("shot_review_required"):
                return "REVIEW_SHOTS"

        if selected_agent in self.AGENT_TO_ACTION:
            return self.AGENT_TO_ACTION[selected_agent]

        if action in {"GENERATE_CHARACTERS", "GENERATE_LOCATIONS"}:
            return "GENERATE_SUBJECT_IMAGES"
        return self.LEGACY_TO_PIPELINE.get(action, action)

    def _sanitize_pipeline_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        action = self._pipeline_action_from_decision(decision)
        if action not in self.ACTIONS:
            action = "CHAT"
        params = decision.get("parameters") if isinstance(decision.get("parameters"), dict) else {}
        params = dict(params)
        legacy_action = str(decision.get("action") or action).upper()
        if action == "GENERATE_SUBJECT_IMAGES":
            targets = params.setdefault("subject_generation_targets", [])
            if legacy_action == "GENERATE_CHARACTERS" and "characters" not in targets:
                targets.append("characters")
            if legacy_action == "GENERATE_LOCATIONS" and "locations" not in targets:
                targets.append("locations")
            if not targets:
                targets.extend(["characters", "locations"])
        if action == "GENERATE_STORYBOARD_IMAGES":
            params.setdefault("image_model", "kling-v3-omni")
        if action == "GENERATE_STORYBOARD_VIDEOS":
            params.setdefault("video_model", "kling-v3-omni")
            params["sound"] = "on"
            params["sound_on"] = True
            if legacy_action == "GENERATE_AUDIO":
                params["audio_strategy"] = "kling_omni_video_sound_on"

        ui_patch = decision.get("ui_patch") if isinstance(decision.get("ui_patch"), dict) else {}
        ui_patch = dict(ui_patch)
        ui_patch.setdefault("active_tab", self.ACTION_TO_TAB.get(action, "canvas"))
        return {
            "action": action,
            "legacy_action": legacy_action,
            "reply": str(decision.get("reply") or "收到。"),
            "parameters": params,
            "ui_patch": ui_patch,
            "collaboration_trace": decision.get("collaboration_trace", {}),
        }

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
            return self._sanitize_pipeline_decision(decision)
        except Exception as e:
            logger.exception("Multi-agent decision failed, falling back to legacy Director Agent: %s", e)
            legacy_decision = await super()._decide(script_id, message, history, project_config, script)
            return self._sanitize_pipeline_decision(legacy_decision)

    # ----------------------- execution helpers -----------------------

    def _needs_project(self, action: str) -> bool:
        return action not in {"CREATE_SCRIPT", "QUERY_STATUS", "CHAT"}

    def _language_pipeline_instruction(self, action: str, params: Dict[str, Any], message: str) -> Dict[str, Any]:
        rewritten = dict(params or {})
        base = rewritten.get("rewrite_instruction") or message
        if action == "REVIEW_SCRIPT":
            rewritten["rewrite_instruction"] = (
                "【剧本审阅与复核agent】请先审阅当前剧本，再输出修订后的完整剧本 JSON。"
                "重点检查：剧情逻辑、人物动机、冲突强度、节奏、对白自然度、可拍摄性。"
                f"用户要求：{base}"
            )
            rewritten["review_required"] = True
        elif action == "ANALYZE_SUBJECTS":
            rewritten["rewrite_instruction"] = (
                "【主体分析设计agent】请基于当前剧本抽取并规范化所有主体资产，输出修订后的完整剧本 JSON。"
                "必须补齐 characters、scenes、props/asset_refs；角色要有 image_prompt，场景要有 environment_prompt，"
                "道具要能被后续主体生图和分镜使用。"
                f"用户要求：{base}"
            )
            rewritten["subject_analysis_required"] = True
        elif action == "WRITE_SHOT_SCRIPT":
            rewritten["rewrite_instruction"] = (
                "【分镜脚本编写agent】请把当前剧本拆成可拍摄镜头表，并输出修订后的完整剧本 JSON。"
                "每个 shot 必须包含 shot_id、shot_type、content_description、dialogue、camera_movement、duration、"
                "character_ids、visual_prompt_for_kling。"
                f"用户要求：{base}"
            )
            rewritten["shot_script_required"] = True
        elif action == "REVIEW_SHOTS":
            rewritten["rewrite_instruction"] = (
                "【分镜审阅agent】请审阅当前分镜并输出修订后的完整剧本 JSON。"
                "重点检查：镜头连续性、角色一致性、场景一致性、对白与画面匹配度、Kling 提示词可执行性。"
                f"用户要求：{base}"
            )
            rewritten["shot_review_required"] = True
        return rewritten

    def _queue_pipeline_background_action(self, background_tasks, action: str, script_id: str, params: Dict[str, Any]) -> Tuple[str, str]:
        if not background_tasks:
            return "skipped", "没有 background_tasks，未能加入后台队列。"
        try:
            import main as app_main
            if action == "GENERATE_SUBJECT_IMAGES":
                targets = set(params.get("subject_generation_targets") or ["characters", "locations"])
                if "characters" in targets:
                    background_tasks.add_task(
                        app_main.async_generate_characters,
                        script_id,
                        params.get("character_ids"),
                        bool(params.get("force")),
                    )
                if "locations" in targets and hasattr(app_main, "async_generate_locations"):
                    background_tasks.add_task(
                        app_main.async_generate_locations,
                        script_id,
                        params.get("scene_ids"),
                        bool(params.get("force")),
                    )
                return "queued", "主体生图任务已加入后台队列。"
            if action == "GENERATE_STORYBOARD_IMAGES":
                background_tasks.add_task(
                    app_main.async_generate_storyboards,
                    script_id,
                    params.get("shot_ids"),
                )
                return "queued", "分镜生图任务已加入后台队列。"
            if action == "GENERATE_STORYBOARD_VIDEOS":
                background_tasks.add_task(
                    app_main.async_generate_video,
                    script_id,
                    params.get("shot_ids"),
                )
                return "queued", "分镜生视频任务已加入后台队列，Kling OmniVideo 会使用 sound=on。"
            if action in self.LEGACY_ACTIONS:
                self._run_background_action(background_tasks, action, script_id, params)
                return "queued", "后台任务已加入队列。"
        except Exception as e:
            logger.exception("Failed to queue pipeline background action: %s", e)
            return "error", f"后台任务加入失败：{e}"
        return "skipped", "该 action 不需要后台任务。"

    async def _execute_pipeline_action(
        self,
        action: str,
        script_id: str,
        params: Dict[str, Any],
        message: str,
        config: Dict[str, Any],
        script: Optional[Dict[str, Any]],
        background_tasks=None,
    ) -> Tuple[str, Dict[str, Any]]:
        if action == "CREATE_SCRIPT":
            new_script_id, script_result = await self._create_script(params, message, config)
            return new_script_id, {"status": "completed", "script_id": new_script_id, "script": script_result}

        if action in self.LANGUAGE_PIPELINE_ACTIONS:
            if not script_id or not script:
                new_script_id, script_result = await self._create_script(params, message, config)
                return new_script_id, {"status": "completed", "script_id": new_script_id, "script": script_result, "bootstrap": True}
            rewrite_params = self._language_pipeline_instruction(action, params, message)
            updated = await self._rewrite_script(script_id, rewrite_params, message, config, script)
            return script_id, {"status": "completed", "script_id": script_id, "script": updated}

        if action in {"GENERATE_SUBJECT_IMAGES", "GENERATE_STORYBOARD_IMAGES", "GENERATE_STORYBOARD_VIDEOS"}:
            queue_status, detail = self._queue_pipeline_background_action(background_tasks, action, script_id, params)
            return script_id, {"status": queue_status, "script_id": script_id, "detail": detail}

        if action in self.LEGACY_ACTIONS and action not in {"CREATE_SCRIPT", "REWRITE_SCRIPT", "GENERATE_AUDIO", "CHAT", "QUERY_STATUS"}:
            queue_status, detail = self._queue_pipeline_background_action(background_tasks, action, script_id, params)
            return script_id, {"status": queue_status, "script_id": script_id, "detail": detail}

        if action == "QUERY_STATUS":
            return script_id, {"status": "completed", "script_id": script_id, "summary": self._summarize_script(script)}

        return script_id, {"status": "planned", "script_id": script_id}

    # ----------------------- public APIs -----------------------

    async def chat(self, script_id: str, message: str, history: list, project_config: dict = None) -> Tuple[str, str, Dict]:
        script = self._load_script(script_id)
        config = self._normalize_project_config(project_config, script)
        decision = await self._decide(script_id, message, history, config, script)
        action = decision["action"]
        params = decision["parameters"]

        if self._needs_project(action) and not script_id:
            action = "CREATE_SCRIPT"

        new_script_id, result = await self._execute_pipeline_action(
            action=action,
            script_id=script_id,
            params=params,
            message=message,
            config=config,
            script=script,
            background_tasks=None,
        )
        result.update({
            "action": action,
            "ui_patch": decision.get("ui_patch", {}),
            "collaboration_trace": decision.get("collaboration_trace", {}),
        })
        return new_script_id, decision["reply"], result

    async def chat_stream(self, script_id: str, message: str, history: list, project_config: dict, background_tasks):
        project_config = project_config or {}
        script = self._load_script(script_id)
        config = self._normalize_project_config(project_config, script)
        llm_display_name = self._llm_display_name()

        yield {
            "type": "status",
            "phase": "thinking",
            "label": "正在进入多 Agent 流程",
            "detail": f"8 个 Agent 正在用 {llm_display_name} 判断下一步。",
            "progress": 10,
        }
        yield {"type": "chat", "content": "🤔 正在判断应该由哪个 Agent 接手..."}

        decision = await self._decide(script_id, message, history, config, script)
        action = decision["action"]
        params = decision["parameters"]
        target_tab = (decision.get("ui_patch") or {}).get("active_tab") or self.ACTION_TO_TAB.get(action, "canvas")

        if self._needs_project(action) and not script_id:
            action = "CREATE_SCRIPT"
            target_tab = "script"
            decision["action"] = action
            decision.setdefault("ui_patch", {})["active_tab"] = target_tab

        yield {"type": "clear_chat"}
        yield {
            "type": "status",
            "phase": "decision",
            "label": f"已识别流程：{action}",
            "detail": f"当前由 {self._selected_agent_name(decision)} 接手，工作区切到 {target_tab}。",
            "progress": 26,
            "action": action,
        }
        yield {
            "type": "decision",
            "action": action,
            "parameters": params,
            "ui_patch": {"active_tab": target_tab},
            "collaboration_trace": decision.get("collaboration_trace", {}),
        }

        async for event in self._type_text(decision["reply"]):
            yield event
        yield {"type": "chat", "content": "\n"}

        try:
            yield {
                "type": "status",
                "phase": "running",
                "label": self._running_label(action),
                "detail": self._running_detail(action),
                "progress": 45,
                "action": action,
            }
            new_script_id, result = await self._execute_pipeline_action(
                action=action,
                script_id=script_id,
                params=params,
                message=message,
                config=config,
                script=script,
                background_tasks=background_tasks,
            )
            script_id = new_script_id or script_id
            yield {"type": "action", "action": action, "status": result.get("status", "completed"), "script_id": script_id, "detail": result.get("detail", "")}
            if script_id:
                yield {"type": "workspace_update", "script_id": script_id, "active_tab": target_tab, "action": action}
            yield {
                "type": "status",
                "phase": "done",
                "label": self._done_label(action, result),
                "detail": self._next_step_hint(action),
                "progress": 100,
                "action": action,
            }
        except Exception as e:
            logger.exception("Pipeline action failed: %s", e)
            yield {"type": "chat", "content": f"\n执行失败：{e}\n"}
            yield {
                "type": "status",
                "phase": "error",
                "label": "执行失败",
                "detail": str(e),
                "progress": 100,
                "action": action,
            }

    def _selected_agent_name(self, decision: Dict[str, Any]) -> str:
        trace = decision.get("collaboration_trace") if isinstance(decision.get("collaboration_trace"), dict) else {}
        return str(trace.get("selected_agent") or "多 Agent 调度器")

    def _running_label(self, action: str) -> str:
        return {
            "CREATE_SCRIPT": "编剧agent 正在生成剧本",
            "REVIEW_SCRIPT": "剧本审阅与复核agent 正在复核剧本",
            "ANALYZE_SUBJECTS": "主体分析设计agent 正在拆解角色、场景、道具",
            "GENERATE_SUBJECT_IMAGES": "主体生图agent 正在加入生图队列",
            "WRITE_SHOT_SCRIPT": "分镜脚本编写agent 正在拆镜头",
            "REVIEW_SHOTS": "分镜审阅agent 正在复核分镜",
            "GENERATE_STORYBOARD_IMAGES": "分镜生图agent 正在加入生图队列",
            "GENERATE_STORYBOARD_VIDEOS": "分镜生视频agent 正在加入视频队列",
            "MERGE_FINAL": "最终成片任务正在加入后台队列",
            "QUERY_STATUS": "正在查询项目状态",
        }.get(action, "正在处理")

    def _running_detail(self, action: str) -> str:
        return {
            "CREATE_SCRIPT": "会输出完整 script.json，包括剧本、角色、场景和基础镜头结构。",
            "REVIEW_SCRIPT": "会输出修订后的完整剧本 JSON。",
            "ANALYZE_SUBJECTS": "会补齐角色、场景、道具等主体资产信息。",
            "GENERATE_SUBJECT_IMAGES": "会调用 Kling 生成角色/场景主体图，后台异步执行。",
            "WRITE_SHOT_SCRIPT": "会把剧本拆成可拍摄镜头表。",
            "REVIEW_SHOTS": "会检查镜头连续性、角色一致性和提示词可执行性。",
            "GENERATE_STORYBOARD_IMAGES": "会调用 Kling 生成每个镜头的分镜图/首帧图。",
            "GENERATE_STORYBOARD_VIDEOS": "会调用 Kling OmniVideo 生成带声音的视频片段，sound=on。",
            "MERGE_FINAL": "会复用旧最终合成后台任务。",
        }.get(action, "正在执行当前流程。")

    def _done_label(self, action: str, result: Dict[str, Any]) -> str:
        if result.get("status") == "queued":
            return "任务已加入后台队列"
        return {
            "CREATE_SCRIPT": "剧本已生成",
            "REVIEW_SCRIPT": "剧本复核完成",
            "ANALYZE_SUBJECTS": "主体分析完成",
            "WRITE_SHOT_SCRIPT": "分镜脚本完成",
            "REVIEW_SHOTS": "分镜复核完成",
            "QUERY_STATUS": "状态查询完成",
        }.get(action, "流程已完成")

    def _next_step_hint(self, action: str) -> str:
        return {
            "CREATE_SCRIPT": "下一步可以输入：审阅剧本，或分析主体角色场景道具。",
            "REVIEW_SCRIPT": "下一步可以输入：分析主体角色场景道具。",
            "ANALYZE_SUBJECTS": "下一步可以输入：生成主体图。",
            "GENERATE_SUBJECT_IMAGES": "下一步可以输入：编写分镜脚本。",
            "WRITE_SHOT_SCRIPT": "下一步可以输入：审阅分镜。",
            "REVIEW_SHOTS": "下一步可以输入：生成分镜图。",
            "GENERATE_STORYBOARD_IMAGES": "下一步可以输入：生成分镜视频。",
            "GENERATE_STORYBOARD_VIDEOS": "已使用 sound=on 生成带声音视频；下一步可以输入：合成最终成片。",
            "MERGE_FINAL": "最终合成已进入队列，可以查看任务状态。",
        }.get(action, "可以继续告诉我下一步要做什么。")
