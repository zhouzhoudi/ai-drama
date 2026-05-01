import json
import logging
import os
import re
from typing import Dict, Any, Tuple, List, Optional
from pathlib import Path
from datetime import datetime
import uuid
import asyncio

from services.script_generator import ScriptGenerator
from services.character_generator import CharacterGenerator
from services.llm_client import llm_chat_json

logger = logging.getLogger(__name__)


class AgentRouter:
    """
    Director Agent 调度器。

    设计原则：
    - LLM 只负责理解用户意图、产出结构化 action。
    - 后端只执行白名单 action，避免模型直接控制系统。
    - 每次决策都带项目上下文，让它像 Zopia 右侧 Director Agent 一样知道当前进度。
    """

    ACTIONS = {
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
        "GENERATE_LOCATIONS": "characters",  # 场景在角色 Tab 下半部
        "GENERATE_STORYBOARD": "storyboard",
        "GENERATE_SHOT_VIDEOS": "storyboard",
        "GENERATE_AUDIO": "storyboard",
        "MERGE_FINAL": "storyboard",
        "QUERY_STATUS": "canvas",
        "CHAT": "canvas",
    }

    def __init__(self):
        self.script_gen = ScriptGenerator()
        self.char_gen = CharacterGenerator()
        self.data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")

    def _llm_display_name(self) -> str:
        model = os.environ.get("LLM_MODEL") or "LLM"
        base = os.environ.get("LLM_API_BASE") or ""
        if "127.0.0.1" in base or "localhost" in base:
            return f"本地模型 {model}"
        return model

    # ----------------------- file/state helpers -----------------------

    def _script_file(self, script_id: str) -> Path:
        return self.data_dir / script_id / "script.json"

    def _load_script(self, script_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not script_id:
            return None
        path = self._script_file(script_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取 script.json 失败: {e}")
            return None

    def _save_script(self, script_id: str, data: Dict[str, Any]) -> None:
        script_dir = self.data_dir / script_id
        script_dir.mkdir(parents=True, exist_ok=True)
        with open(script_dir / "script.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _normalize_project_config(self, project_config: Optional[Dict[str, Any]], existing_script: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        saved = (existing_script or {}).get("project_config") or {}
        incoming = project_config or {}
        merged = {**saved, **incoming}
        return {
            "aspect_ratio": merged.get("aspect_ratio", "9:16"),
            "style": merged.get("style", merged.get("visual_style", "写实电影风")),
            "visual_style": merged.get("visual_style", merged.get("style", "写实电影风")),
            "image_engine": merged.get("image_engine", os.environ.get("IMAGE_ENGINE", "kling")),
            "video_engine": merged.get("video_engine", os.environ.get("VIDEO_ENGINE", "kling")),
        }

    def _ensure_script_shape(self, script: Dict[str, Any], script_id: str, project_config: Dict[str, Any]) -> Dict[str, Any]:
        script["script_id"] = script_id
        script.setdefault("created_at", datetime.now().isoformat())
        script["updated_at"] = datetime.now().isoformat()
        script.setdefault("status", "draft")
        script.setdefault("title", "新短剧")
        script.setdefault("theme", script.get("genre", "自选"))
        script.setdefault("genre", script.get("theme", "自选"))
        script.setdefault("style", project_config.get("style", "写实电影风"))
        script.setdefault("characters", [])
        script.setdefault("scenes", [])
        script["project_config"] = project_config

        # 兼容模型偶尔漏 character_id / dialogue 结构的问题
        for idx, char in enumerate(script.get("characters", []), start=1):
            char.setdefault("character_id", f"char_{idx}")
            char.setdefault("name", f"角色{idx}")
            char.setdefault("gender", "other")
            char.setdefault("personality", "")
            if not char.get("image_prompt"):
                appearance = char.get("appearance") or {}
                char["image_prompt"] = ", ".join(str(v) for v in appearance.values() if v) or f"cinematic portrait of {char.get('name')}, {project_config.get('style')}, high quality"

        shot_counter = 1
        for scene_idx, scene in enumerate(script.get("scenes", []), start=1):
            scene.setdefault("scene_id", f"scene_{scene_idx}")
            scene.setdefault("scene_number", f"场景 {scene_idx}")
            scene.setdefault("shots", [])
            for shot in scene.get("shots", []):
                shot.setdefault("shot_id", f"shot_{shot_counter}")
                shot.setdefault("shot_number", shot_counter)
                shot.setdefault("duration", 5)
                if isinstance(shot.get("dialogue"), str):
                    text = shot.get("dialogue") or ""
                    shot["dialogue"] = {"text": text} if text else {}
                elif shot.get("dialogue") is None:
                    shot["dialogue"] = {}
                shot_counter += 1
        return script

    def _summarize_script(self, script: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not script:
            return {"exists": False}
        scenes = script.get("scenes", []) or []
        shots_count = sum(len(scene.get("shots", []) or []) for scene in scenes)
        chars = script.get("characters", []) or []
        generated_images = sum(1 for c in chars if (c.get("appearance") or {}).get("image_url"))
        generated_videos = 0
        for scene in scenes:
            for shot in scene.get("shots", []) or []:
                if shot.get("generated_video_url") or shot.get("video_url"):
                    generated_videos += 1
        return {
            "exists": True,
            "script_id": script.get("script_id"),
            "title": script.get("title"),
            "theme": script.get("theme") or script.get("genre"),
            "style": script.get("style"),
            "characters_count": len(chars),
            "character_names": [c.get("name") for c in chars],
            "generated_character_images": generated_images,
            "scenes_count": len(scenes),
            "shots_count": shots_count,
            "generated_videos": generated_videos,
            "project_config": script.get("project_config", {}),
        }

    def _history_to_text(self, history: list, max_items: int = 6) -> str:
        rows = []
        for item in (history or [])[-max_items:]:
            role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else "")
            text = getattr(item, "text", None) or (item.get("text") if isinstance(item, dict) else "")
            if text:
                rows.append(f"{role}: {text[:300]}")
        return "\n".join(rows)

    # ----------------------- LLM decision -----------------------

    def _fallback_route(self, message: str, script_exists: bool = False, script: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """模型不可用时的兜底路由。"""
        msg = (message or "").strip()
        if any(k in msg for k in ["合成", "成片", "导出", "最终视频"]):
            return {"action": "MERGE_FINAL", "reply": "好的，我来准备最终成片合成。", "parameters": {}}
        if any(k in msg for k in ["配音", "声音", "音频", "tts", "TTS"]):
            return {"action": "GENERATE_AUDIO", "reply": "好的，我来处理分镜配音。", "parameters": {}}
        if any(k in msg for k in ["视频", "可灵", "生成镜头", "出片"]):
            return {"action": "GENERATE_SHOT_VIDEOS", "reply": "好的，我会把分镜视频任务放入后台队列。", "parameters": {}}
        if any(k in msg for k in ["分镜", "镜头", "storyboard"]):
            return {"action": "GENERATE_STORYBOARD", "reply": "好的，我来整理分镜看板。", "parameters": {}}
        # 场景图：放在角色匹配前面，因为"场景"两字优先级高于"画"
        if any(k in msg for k in ["场景图", "场景", "地点图", "环境图", "背景图"]):
            is_redo = any(k in msg for k in ["重新生成", "重做", "重画", "再画", "重新画", "不满意"])
            scene_refs: List[str] = []
            if script:
                for s in script.get("scenes", []) or []:
                    loc = s.get("location") or ""
                    if loc and str(loc) in msg:
                        scene_refs.append(str(s.get("scene_id")))
            params: Dict[str, Any] = {}
            if scene_refs:
                params["scene_ids"] = scene_refs
            if is_redo:
                params["force"] = True
            reply_target = "指定场景" if scene_refs else "所有场景"
            reply = f"好的，我来{'重做' if is_redo else '生成'}「{reply_target}」的场景图。"
            return {"action": "GENERATE_LOCATIONS", "reply": reply, "parameters": params}
        if any(k in msg for k in ["角色", "画", "形象", "人物", "主角"]):
            # 是否是"重做/重新生成"语义
            is_redo = any(k in msg for k in ["重新生成", "重做", "重画", "再画", "重新画", "换一张", "不像", "不满意"])
            # 试着从消息里抠出角色名（命中 character_names 列表里的某一个）
            char_names = []
            if script:
                for c in script.get("characters", []) or []:
                    name = c.get("name") or c.get("character_id")
                    if name and str(name) in msg:
                        char_names.append(str(name))
            params: Dict[str, Any] = {}
            if char_names:
                params["character_ids"] = char_names
            if is_redo:
                params["force"] = True
            reply_target = "、".join(char_names) if char_names else "全部角色"
            reply = f"好的，我来{'重做' if is_redo else '生成'}「{reply_target}」的参考图。"
            return {"action": "GENERATE_CHARACTERS", "reply": reply, "parameters": params}
        if script_exists and any(k in msg for k in ["改", "重写", "调整", "不够", "换成", "修改"]):
            return {"action": "REWRITE_SCRIPT", "reply": "明白，我会按你的反馈调整剧本结构。", "parameters": {"rewrite_instruction": msg}}
        if any(k in msg for k in ["写", "剧本", "故事", "短剧", "构思", "创作"]):
            return {"action": "CREATE_SCRIPT", "reply": "好的，我先帮你构思剧本和基础分镜。", "parameters": {"theme": "自选", "description": msg}}
        return {"action": "CHAT", "reply": "收到。你可以继续告诉我想写什么题材，或者让我生成角色、分镜、视频。", "parameters": {}}

    def _parse_llm_json(self, content: str) -> Dict[str, Any]:
        if not content:
            raise ValueError("empty model content")
        text = content.strip()
        if text.startswith("```json"):
            text = text.replace("```json", "", 1).strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        elif text.startswith("```"):
            text = text.replace("```", "", 1).strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", text)
            if not m:
                raise
            return json.loads(m.group(0))

    def _sanitize_decision(self, decision: Dict[str, Any], message: str, script_exists: bool) -> Dict[str, Any]:
        if not isinstance(decision, dict):
            return self._fallback_route(message, script_exists)
        action = str(decision.get("action") or "CHAT").upper()
        if action not in self.ACTIONS:
            action = "CHAT"
        params = decision.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        reply = str(decision.get("reply") or "好的，收到。")
        return {
            "action": action,
            "reply": reply,
            "parameters": params,
            "ui_patch": decision.get("ui_patch") if isinstance(decision.get("ui_patch"), dict) else {},
        }

    async def _decide(self, script_id: Optional[str], message: str, history: list, project_config: Dict[str, Any], script: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        summary = self._summarize_script(script)
        fallback = self._fallback_route(message, script_exists=bool(script), script=script)

        system_prompt = f"""
你是 AI 短剧系统的 Director Agent / 总导演调度大脑，体验类似 Zopia。
你不直接调用外部 API，只输出一个后端可执行的白名单 action JSON。

白名单 action：
- CREATE_SCRIPT：没有项目或用户要新写剧本时使用
- REWRITE_SCRIPT：已有剧本，用户要求修改/重写/换风格/调整角色或剧情时使用
- GENERATE_CHARACTERS：为角色生成参考图/角色形象
- GENERATE_LOCATIONS：为剧本里的"场景/地点"生成场景图（地点环境图，不含人物），分镜会以此为视觉锚点
- GENERATE_STORYBOARD：整理或查看分镜，不直接烧视频额度
- GENERATE_SHOT_VIDEOS：用户明确要生成视频/出镜头/用可灵生成
- GENERATE_AUDIO：生成配音/声音/TTS
- MERGE_FINAL：合成最终成片/导出
- QUERY_STATUS：查询进度
- CHAT：普通解释或引导

当前项目摘要：
{json.dumps(summary, ensure_ascii=False, indent=2)}

前端项目配置：
{json.dumps(project_config, ensure_ascii=False, indent=2)}

最近对话：
{self._history_to_text(history)}

你必须只输出 JSON，格式：
{{
  "action": "CREATE_SCRIPT | REWRITE_SCRIPT | GENERATE_CHARACTERS | GENERATE_LOCATIONS | GENERATE_STORYBOARD | GENERATE_SHOT_VIDEOS | GENERATE_AUDIO | MERGE_FINAL | QUERY_STATUS | CHAT",
  "reply": "自然、口语化回复用户，告诉他你准备做什么。不要太正式。",
  "parameters": {{
    "title": "可选，短剧标题",
    "theme": "可选，题材",
    "style": "可选，风格",
    "duration": 180,
    "description": "CREATE_SCRIPT 时必须包含完整需求",
    "rewrite_instruction": "REWRITE_SCRIPT 时包含用户修改要求",
    "shot_ids": ["可选，只生成指定分镜视频"],
    "character_ids": ["可选，GENERATE_CHARACTERS 时只重做指定角色（用角色名或 character_id）"],
    "scene_ids": ["可选，GENERATE_LOCATIONS 时只重做指定场景（用 scene_id 或场景地名）"],
    "force": false
  }},
  "ui_patch": {{"active_tab": "canvas|script|characters|storyboard"}}
}}

重要规则：
1. 用户没明确说要生成视频时，不要选择 GENERATE_SHOT_VIDEOS，避免浪费额度。
2. 用户说"分镜"一般选 GENERATE_STORYBOARD；说"生成视频/出片/可灵跑一下"才选 GENERATE_SHOT_VIDEOS。
3. 已有项目时，用户说"改一下/不够/换成"优先 REWRITE_SCRIPT，不要新建项目。
4. 如果没有项目，角色/分镜/视频类请求都应该先引导或 CREATE_SCRIPT。
5. 重要：用户说"重新生成 X 角色的图 / 重做 X 的角色图 / X 的形象重画 / 这个角色不像"等措辞时，
   action 必须是 GENERATE_CHARACTERS，并把 parameters.character_ids 设为命中的角色名数组（参考"当前项目摘要".character_names），
   parameters.force 设为 true。这样后端才会真正重做那个角色，否则会跳过已有图。
6. **重要：用户体验是逐步确认的，绝对不要自动接力。**
   - CREATE_SCRIPT 完成后，**绝对不要自动 GENERATE_CHARACTERS**，必须等用户明确说"生成角色"才继续。
   - GENERATE_CHARACTERS 完成后，**绝对不要自动 GENERATE_LOCATIONS**，必须等用户明确说"生成场景图"才继续。
   - GENERATE_LOCATIONS 完成后，**绝对不要自动 GENERATE_STORYBOARD**，必须等用户明确说"生成分镜图"才继续。
   - GENERATE_STORYBOARD 完成后，**绝对不要自动 GENERATE_SHOT_VIDEOS**，必须等用户明确说"生成视频"才继续。
   - 每次 reply 末尾，请用一句话告诉用户"下一步该回复什么才能继续"，让用户掌控节奏。
"""
        try:
            decision = await llm_chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                temperature=0.2,
                max_tokens=1200,
                timeout=12,
            )
            return self._sanitize_decision(decision, message, bool(script))
        except Exception as e:
            logger.error(f"Director Agent decision failed: {e}")
            return self._sanitize_decision(fallback, message, bool(script))

    # ----------------------- executors -----------------------

    async def _create_script(self, params: Dict[str, Any], message: str, project_config: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        title = params.get("title") or "新短剧"
        theme = params.get("theme") or "自选"
        style = params.get("style") or project_config.get("style") or "写实电影风"
        duration = int(params.get("duration") or 180)
        description = params.get("description") or message

        result = await self.script_gen.generate(
            title=title,
            theme=theme,
            style=style,
            duration=duration,
            characters=[],
            description=description,
        )
        new_script_id = result.get("script_id") or str(uuid.uuid4())
        result = self._ensure_script_shape(result, new_script_id, project_config)
        self._save_script(new_script_id, result)
        return new_script_id, result

    async def _rewrite_script(self, script_id: str, params: Dict[str, Any], message: str, project_config: Dict[str, Any], old_script: Dict[str, Any]) -> Dict[str, Any]:
        instruction = params.get("rewrite_instruction") or message
        old_summary = json.dumps(self._summarize_script(old_script), ensure_ascii=False)
        description = f"基于已有短剧进行修改。已有项目摘要：{old_summary}\n用户修改要求：{instruction}\n请保留合理的角色和世界观，只重写需要调整的部分，并输出完整剧本 JSON。"
        result = await self.script_gen.generate(
            title=old_script.get("title") or params.get("title") or "新短剧",
            theme=params.get("theme") or old_script.get("theme") or old_script.get("genre") or "自选",
            style=params.get("style") or project_config.get("style") or old_script.get("style") or "写实电影风",
            duration=int(params.get("duration") or old_script.get("total_duration") or 180),
            characters=old_script.get("characters", []),
            description=description,
        )
        # 保持同一个 script_id，避免前端项目跳来跳去
        result = self._ensure_script_shape(result, script_id, project_config)
        result["revision_note"] = instruction
        self._save_script(script_id, result)
        return result

    def _run_background_action(self, background_tasks, action: str, script_id: str, params: Dict[str, Any]) -> None:
        """把耗时动作交给 main.py 里的后台函数，避免循环 import 放在模块加载阶段。"""
        if not background_tasks:
            return
        try:
            import main as app_main
            if action == "GENERATE_CHARACTERS":
                background_tasks.add_task(
                    app_main.async_generate_characters,
                    script_id,
                    params.get("character_ids"),
                    bool(params.get("force")),
                )
            elif action == "GENERATE_LOCATIONS":
                background_tasks.add_task(
                    app_main.async_generate_locations,
                    script_id,
                    params.get("scene_ids"),
                    bool(params.get("force")),
                )
            elif action == "GENERATE_SHOT_VIDEOS":
                background_tasks.add_task(app_main.async_generate_video, script_id, params.get("shot_ids"))
            elif action == "GENERATE_STORYBOARD":
                background_tasks.add_task(app_main.async_generate_storyboards, script_id, params.get("shot_ids"))
            elif action == "GENERATE_AUDIO":
                if hasattr(app_main, "async_generate_audio"):
                    background_tasks.add_task(app_main.async_generate_audio, script_id)
            elif action == "MERGE_FINAL":
                if hasattr(app_main, "_merge_video_async"):
                    background_tasks.add_task(lambda: asyncio.run(app_main._merge_video_async(script_id)))
        except Exception as e:
            logger.error(f"添加后台任务失败: {e}")

    async def chat(self, script_id: str, message: str, history: list, project_config: dict = None) -> Tuple[str, str, Dict]:
        """非流式兼容入口。"""
        script = self._load_script(script_id)
        config = self._normalize_project_config(project_config, script)
        decision = await self._decide(script_id, message, history, config, script)
        action = decision["action"]
        reply = decision["reply"]
        params = decision["parameters"]
        action_result = {"action": action, "status": "planned", "ui_patch": decision.get("ui_patch", {})}

        if action == "CREATE_SCRIPT" or (action != "CHAT" and not script_id):
            script_id, _ = await self._create_script(params, message, config)
            action_result.update({"status": "executed", "script_id": script_id})
        elif action == "REWRITE_SCRIPT" and script_id and script:
            await self._rewrite_script(script_id, params, message, config, script)
            action_result.update({"status": "executed", "script_id": script_id})

        return script_id, reply, action_result

    # ----------------------- SSE stream -----------------------

    async def _type_text(self, text: str, delay: float = 0.01):
        for char in text:
            await asyncio.sleep(delay)
            yield {"type": "chat", "content": char}

    async def chat_stream(self, script_id: str, message: str, history: list, project_config: dict, background_tasks):
        project_config = project_config or {}
        script = self._load_script(script_id)
        config = self._normalize_project_config(project_config, script)

        llm_display_name = self._llm_display_name()

        yield {"type": "status", "phase": "thinking", "label": "正在理解你的需求", "detail": f"Director Agent 正在用 {llm_display_name} 判断你是要写剧本、改剧本、生成角色还是生成视频。", "progress": 12}
        yield {"type": "chat", "content": "🤔 正在判断下一步..."}

        decision_task = asyncio.create_task(self._decide(script_id, message, history, config, script))
        decision_ticks = 0
        while not decision_task.done():
            await asyncio.sleep(2)
            decision_ticks += 1
            yield {
                "type": "status",
                "phase": "thinking",
                "label": "正在分析意图",
                "detail": f"{llm_display_name} 正在读取你的需求和当前项目上下文，已等待 {decision_ticks * 2} 秒。",
                "progress": min(26, 12 + decision_ticks * 3),
            }
        decision = await decision_task
        action = decision["action"]
        reply = decision["reply"]
        params = decision["parameters"]
        ui_patch = decision.get("ui_patch") or {}
        target_tab = ui_patch.get("active_tab") or self.ACTION_TO_TAB.get(action, "canvas")

        yield {"type": "clear_chat"}
        yield {"type": "status", "phase": "decision", "label": f"已识别意图：{action}", "detail": f"接下来会切到 {target_tab} 工作区并执行对应流程。", "progress": 28, "action": action}
        yield {"type": "decision", "action": action, "parameters": params, "ui_patch": {"active_tab": target_tab}}

        async for event in self._type_text(reply):
            yield event
        yield {"type": "chat", "content": "\n"}

        # 没有项目时，除了普通闲聊，都先创建剧本骨架
        if not script_id and action in {"GENERATE_CHARACTERS", "GENERATE_LOCATIONS", "GENERATE_STORYBOARD", "GENERATE_SHOT_VIDEOS", "GENERATE_AUDIO", "MERGE_FINAL"}:
            yield {"type": "chat", "content": "我先帮你建一个项目和基础剧本，不然角色/分镜没地方挂载。\n"}
            action = "CREATE_SCRIPT"
            target_tab = "script"

        if action == "CREATE_SCRIPT":
            yield {"type": "status", "phase": "running", "label": "正在生成完整剧本", "detail": "正在调用剧本生成器，完成后会写入左侧剧本工作区。若模型超时，会自动启用本地完整剧本兜底。", "progress": 42, "action": "CREATE_SCRIPT"}
            yield {"type": "chat", "content": "⏳ 正在写剧本和基础分镜...\n"}
            try:
                create_task = asyncio.create_task(self._create_script(params, message, config))
                script_ticks = 0
                status_steps = [
                    (f"正在让 {llm_display_name} 生成剧本正文", "模型正在输出完整 JSON 剧本，这一步可能需要几十秒。", 48),
                    ("正在整理角色和分场", "等待模型返回角色、分场剧本和镜头对白。", 56),
                    ("正在检查 JSON 结构", "如果模型返回前后带说明，后端会自动提取里面的剧本 JSON。", 64),
                    ("正在准备工作台数据", "马上会把剧本、角色和分镜写入左侧工作区。", 72),
                ]
                while not create_task.done():
                    await asyncio.sleep(5)
                    label, detail, base_progress = status_steps[min(script_ticks, len(status_steps) - 1)]
                    script_ticks += 1
                    yield {
                        "type": "status",
                        "phase": "running",
                        "label": label,
                        "detail": f"{detail} 已等待 {script_ticks * 5} 秒。",
                        "progress": min(78, base_progress + min(script_ticks, 6)),
                        "action": "CREATE_SCRIPT",
                    }
                new_script_id, script_result = await create_task
                warning = script_result.get("generation_warning") if isinstance(script_result, dict) else ""
                # 关键修复：先吐"done 100%"再吐 workspace_update。
                # 以前是 workspace_update -> action completed -> status done。
                # 前端在 workspace_update 上 await fetchWorkspaceData，如果这次请求慢
                # （磁盘读+normalize+写回），后续 status done 事件就会被堵在前端的事件队列里，
                # 用户看到模型其实已经跑完了、磁盘 script.json 也写好了，但进度条卡在 78~82%。
                yield {"type": "status", "phase": "writing", "label": "正在写入工作台", "detail": "剧本、角色和分镜数据已经生成，正在刷新左侧工作区。", "progress": 82, "action": "CREATE_SCRIPT"}
                yield {"type": "action", "action": "CREATE_SCRIPT", "status": "completed", "script_id": new_script_id}
                yield {"type": "status", "phase": "done", "label": "剧本已生成", "detail": "左侧剧本 Tab 已刷新，可以继续生成角色图或分镜视频。", "progress": 100, "action": "CREATE_SCRIPT"}
                # 让前端去拉最新工作台数据。即使这次拉取慢也不影响进度显示。
                yield {"type": "workspace_update", "script_id": new_script_id, "tab": "script"}
                if warning:
                    yield {"type": "chat", "content": f"⚠️ {warning}\n"}
                yield {"type": "chat", "content": (
                    "✅ 剧本已生成好，左侧「剧本」Tab 已刷新。\n\n"
                    "📋 **请先确认剧本内容**：\n"
                    "  • 看「故事梗概 / 剧本正文 / 角色 / 分场」是否符合你的设想\n"
                    "  • 不满意可以直接告诉我「**改成 …**」「**重写第 N 场**」「**加一个角色**」之类，我会重写\n\n"
                    "👉 **确认无误后，回复「生成角色图」我才会进入下一步**。我不会自动接力，由你掌控节奏。"
                )}
            except Exception as e:
                logger.exception("CREATE_SCRIPT failed")
                yield {"type": "status", "phase": "error", "label": "剧本生成失败", "detail": str(e), "progress": 100, "action": "CREATE_SCRIPT"}
                yield {"type": "chat", "content": f"❌ 剧本生成失败：{e}"}
            return

        if action == "REWRITE_SCRIPT":
            if not script_id or not script:
                yield {"type": "chat", "content": "⚠️ 现在还没有可修改的剧本，我可以先帮你新建一个。"}
                return
            yield {"type": "chat", "content": "⏳ 正在按你的反馈重写剧本...\n"}
            try:
                await self._rewrite_script(script_id, params, message, config, script)
                # 顺序同 CREATE_SCRIPT：先吐 done/completed 让进度到 100%，再让前端去拉数据。
                yield {"type": "action", "action": "REWRITE_SCRIPT", "status": "completed", "script_id": script_id}
                yield {"type": "status", "phase": "done", "label": "剧本已重写", "detail": "左侧剧本 Tab 已刷新。", "progress": 100, "action": "REWRITE_SCRIPT"}
                yield {"type": "workspace_update", "script_id": script_id, "tab": "script"}
                yield {"type": "chat", "content": "✅ 已按你的要求改好了，右侧剧本已刷新。"}
            except Exception as e:
                logger.exception("REWRITE_SCRIPT failed")
                yield {"type": "chat", "content": f"❌ 修改失败：{e}"}
            return

        if action in {"GENERATE_CHARACTERS", "GENERATE_LOCATIONS", "GENERATE_STORYBOARD", "GENERATE_SHOT_VIDEOS", "GENERATE_AUDIO", "MERGE_FINAL"}:
            if not script_id:
                yield {"type": "chat", "content": "⚠️ 请先创建剧本。"}
                return
            self._run_background_action(background_tasks, action, script_id, params)
            yield {"type": "status", "phase": "queued", "label": f"已加入后台任务：{action}", "detail": "耗时任务已进入后台队列，前端会自动轮询刷新。", "progress": 55, "action": action}
            yield {"type": "workspace_update", "script_id": script_id, "tab": target_tab}
            yield {"type": "action", "action": action, "status": "pending_background", "script_id": script_id, "poll": True}
            if action == "GENERATE_CHARACTERS":
                yield {"type": "chat", "content": (
                    "✅ 角色图任务已加入后台队列，「角色」Tab 会自动刷新。\n\n"
                    "🎨 **等所有角色出图后请确认**：\n"
                    "  • 不满意可以告诉我「**重做 [角色名] 的图**」或点角色卡片上的「重做」按钮\n"
                    "  • 角色形象会作为后续分镜图的参考，请仔细把关\n\n"
                    "👉 **全部满意后，回复「生成场景图」我再进入下一步**。"
                )}
            elif action == "GENERATE_LOCATIONS":
                yield {"type": "chat", "content": (
                    "✅ 场景图任务已加入后台队列，「角色」Tab 下方的「场景」区会自动刷新。\n\n"
                    "🏞 **等所有场景出图后请确认**：\n"
                    "  • 不满意可以告诉我「**重做 [场景名] 的图**」或点场景卡上的「重做」按钮\n"
                    "  • 场景图会作为分镜图的视觉锚点（@场景的实际样貌）\n\n"
                    "👉 **全部满意后，回复「生成分镜图」我再进入下一步**。"
                )}
            elif action == "GENERATE_STORYBOARD":
                yield {"type": "chat", "content": (
                    "✅ 分镜图任务已加入后台队列，「分镜」Tab 会自动刷新。\n\n"
                    "🎬 **等所有分镜出图后请逐镜确认**：\n"
                    "  • 不满意单镜可以点「重做」按钮，或告诉我「**重做第 N 镜**」\n"
                    "  • 分镜图同时是视频生成的首帧参考，请把关\n\n"
                    "👉 **全部满意后，回复「生成视频」我再进入下一步**（视频生成耗时和额度都比较多）。"
                )}
            elif action == "GENERATE_SHOT_VIDEOS":
                yield {"type": "chat", "content": (
                    "✅ 分镜视频任务已加入后台队列，「分镜」Tab 会自动刷新。\n\n"
                    "🎥 这一步耗时较长。完成后：\n"
                    "  • 可以用「**自动审片**」让模型逐镜检查\n"
                    "  • 也可以人工逐镜审核，不合格可单镜重做\n\n"
                    "👉 全部确认通过后，回复「**合成成片**」我再合成最终视频。"
                )}
            elif action == "GENERATE_AUDIO":
                yield {"type": "chat", "content": "✅ 配音任务已加入后台队列。完成后可以试听并调整。"}
            elif action == "MERGE_FINAL":
                yield {"type": "chat", "content": "✅ 最终合成任务已加入后台队列。完成后可以下载查看。"}
            return

        if action == "QUERY_STATUS":
            summary = self._summarize_script(script)
            yield {"type": "chat", "content": f"当前项目：{json.dumps(summary, ensure_ascii=False)}"}
            if script_id:
                yield {"type": "workspace_update", "script_id": script_id, "tab": target_tab}
            return

        # CHAT
        if script_id:
            yield {"type": "workspace_update", "script_id": script_id, "tab": target_tab}
