"""
剧本生成服务
使用 LLM 生成完整剧本和分镜
"""

import asyncio
import json
import os
import re
import uuid
from typing import Dict, List, Any
from pathlib import Path

from .llm_client import llm_chat, llm_chat_json
from .production_state import normalize_script


SCRIPT_TEMPLATE = """只输出一个合法 JSON，不要 Markdown，不要解释。

请生成一部可直接拍摄的**短剧完整剧本**。要求像真实短剧剧本，不能是简介。

基本信息：
- 标题：{title}
- 题材：{theme}
- 风格：{style}
- 时长：{duration}秒（参考分镜数 = 时长 ÷ 6 左右）
- 剧情要求：{description}

必须输出这些字段：
{{
  "title": "短剧标题",
  "genre": "题材",
  "style": "风格",
  "synopsis": "**至少 200 字、不少于 4 句**的完整故事梗概，必须包含：1) 故事背景与世界观；2) 主角处境与目标；3) 关键冲突；4) 高潮和结局走向。",
  "logline": "一句话 25-40 字的钩子，必须有具体冲突",
  "screenplay_text": "完整剧本正文，至少 800 字。**严格按【第1场 地点 / 时间】格式分场**，每场必须包含：环境/时间描述、人物上场动作、推进冲突的对白、转场。语言要有现场感，能直接当拍摄脚本用。",
  "characters": [
    {{
      "character_id":"char_1",
      "name":"中文角色名",
      "gender":"female|male|other",
      "age_range":"具体年龄段，例如 28-32 / 高中生 / 中年",
      "ethnicity":"族裔/外貌人种，例如 East Asian Chinese / 中国南方人",
      "personality":"3-5 个关键词刻画性格",
      "background":"至少 40 字的角色背景，包含动机和转折",
      "appearance":{{
        "face":"**至少 25 字**的面部细节：脸型、眉眼、鼻唇、肤色、神情，例：'瓜子脸，杏眼凌厉，鼻梁挺直，唇形清冷，肤色冷白，眼神带着克制的疲惫'",
        "hair":"**发型+发色+长度+造型**，例：'黑色及肩中长发，微卷，平时低马尾'",
        "body":"**至少 15 字**身形 + 气质，例：'身高约168cm，纤瘦挺拔，肩颈线条利落，整体气场冷静'",
        "dress_style":"**至少 20 字**的标志性穿着 + 配色 + 配饰，例：'黑色合身西装套装，白色衬衫，银色细链项链，黑色皮带尖头高跟'",
        "distinguishing_features":"标志性细节，例：'左眼下有一颗小痣 / 习惯戴一只素圈戒指'"
      }},
      "image_prompt":"**English** cinematic portrait prompt, **at least 50 words**, must include: age range, gender, ethnicity (East Asian Chinese), face shape, eye/lip/skin details, hair style and color, body figure, full outfit and color palette, distinguishing features, mood, pose, lighting (studio key light / cinematic side lighting), camera shot type (medium close-up portrait), background hint, style keywords (photorealistic, cinematic, 8k, soft film grain). 不要写中文，不要写形容词性副词以外的多余文本。"
    }}
  ],
  "scenes": [
    {{
      "scene_id":"scene_1",
      "scene_number":"第1场",
      "location":"具体地点（例如：Time-Life大堂 / 雨夜街头）",
      "time_of_day":"白天|黄昏|夜晚",
      "scene_summary":"50 字以内的本场冲突点",
      "script_text":"本场**完整剧本文本**，至少 200 字，含动作行 + 对白行（格式：林晚：xxxxx）",
      "shots":[
        {{
          "shot_id":"shot_1",
          "shot_number":1,
          "shot_type":"远景|中景|中近景|近景|特写|蒙太奇",
          "camera_movement":"固定|缓慢推进|跟拍|拉远|平移|环绕",
          "duration":5,
          "character_ids":["char_1"],
          "location_id":"loc_1",
          "content_description":"**至少 80 字、不超过 200 字**的镜头画面描述，必须包含：1) 角色用 [@角色名] 引用；2) 场景用 [@场景名] 引用；3) 镜头从哪里推/拉/移；4) 角色具体动作和情绪；5) 对白用 {{[@角色名]说：\\"对白内容\\"}} 格式包裹放在描述末尾。例：\\"中景，[@Time-Life大堂] 内，镜头继续无缝推进，[@双喜] 抱着文件向前走，[@Roy] 从旁边经过并看向他。{{[@Roy]说：\\"双喜。\\"}}\\"",
          "shot_text":"按 '0-3s 景别，[@场景] 内/外，画面描述。{{[@角色]说：\\"对白\\"}}' 格式输出，时间段由后端按 duration 累加，这里只输出从 '景别' 开始的部分，例如 '中景，[@Time-Life大堂] 内，镜头跟随 [@双喜] 推进，他抱着文件低头快走。{{[@双喜]说：\\"嘿。\\"}}'",
          "dialogue":{{"character_id":"char_1","text":"对白原文","emotion":"情绪关键词","tts_text":"配音文本（可以与 text 不同，更口语）"}},
          "visual_prompt_for_kling":"English video prompt for image-to-video generation, 25+ words, includes scene/character/lighting/mood",
          "negative_prompt_for_kling":"blurry, low quality, deformed face, bad hands"
        }}
      ]
    }}
  ]
}}

数量要求：{num_scenes} 场，每场 {shots_per_scene} 个镜头，{num_characters} 个主要角色。
**重要**：
- 每个镜头必须有 character_ids 和 location_id，方便后续保持角色与场景一致。
- shot_text 字段是给前端展示的"分镜剧本格式"，**必须使用 [@场景名] 和 [@角色名] 引用语法**，对白必须用 {{[@角色名]说："对白"}} 包裹。
- 不要出现"占位/示例/TODO"之类的字眼，所有内容都按真实剧情写。"""


COMPACT_SCRIPT_TEMPLATE = """请只输出一个可解析 JSON 对象，不要输出思考过程、Markdown 或解释。
生成一部**完整可拍摄的短剧**：《{title}》
题材：{theme}
风格：{style}
剧情：{description}

必须包含：
- title, genre, style
- synopsis（**至少 200 字**：背景+主角目标+关键冲突+高潮走向）
- logline（一句话钩子）
- screenplay_text（**至少 800 字、按【第N场 地点 / 时间】分场的真实剧本正文**）
- characters：3 个角色，每个包含 character_id, name, gender, age_range, ethnicity, personality, background(≥40字),
  appearance{{face(≥25字),hair,body(≥15字),dress_style(≥20字),distinguishing_features}},
  image_prompt(英文,≥50词，必须含 age/gender/ethnicity/face/hair/body/outfit/lighting/shot type/style)
- scenes：3 场，每场 3 个 shots
- 每个 shot 必须包含：shot_id, shot_number, shot_type, camera_movement, duration, character_ids, location_id,
  content_description（**至少 80 字**，必须用 [@角色名] 和 [@场景名] 引用语法，对白用 {{[@角色名]说："..."}} 格式放在描述末尾），
  shot_text（同样的引用格式，例：'中景，[@Time-Life大堂] 内，镜头跟随 [@双喜] 推进，他抱着文件低头快走。{{[@双喜]说："嘿。"}}'），
  dialogue{{character_id,text,emotion,tts_text}}, visual_prompt_for_kling(英文), negative_prompt_for_kling

输出示例结构：
{{"title":"...","genre":"...","style":"...","synopsis":"...","logline":"...","screenplay_text":"...","characters":[],"scenes":[]}}"""


class ScriptGenerator:
    """剧本生成器（通过系统自带 LLM 客户端调用生产语言模型）"""

    def __init__(self):
        self.data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")

    async def _call_llm(self, prompt: str, system_prompt: str = "") -> str:
        """调用 backend/.env 配置的 OpenAI 兼容语言模型。"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        return await llm_chat(
            messages=messages,
            temperature=0.75,
            max_tokens=12000,
            timeout=360,
        )

    async def _call_compact_llm(self, title: str, theme: str, style: str, description: str) -> str:
        """本地模型长提示词失败时，用更短的 JSON 提示词重试。"""
        prompt = COMPACT_SCRIPT_TEMPLATE.format(
            title=title,
            theme=theme,
            style=style,
            description=description,
        )
        return await llm_chat(
            messages=[
                {"role": "system", "content": "你是短剧编剧。只输出 JSON 对象，不要输出思考过程。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=10000,
            timeout=360,
        )

    def _is_usable_script(self, script: Dict[str, Any]) -> bool:
        if not isinstance(script, dict):
            return False
        if not script.get("title") and not script.get("screenplay_text"):
            return False
        characters = script.get("characters") or []
        scenes = script.get("scenes") or []
        shots_count = sum(
            len(scene.get("shots", []) or [])
            for scene in scenes
            if isinstance(scene, dict)
        )
        return len(characters) >= 2 and len(scenes) >= 1 and shots_count >= 2

    async def _generate_stepwise_script(
        self,
        title: str,
        theme: str,
        style: str,
        duration: int,
        description: str,
    ) -> Dict[str, Any]:
        """把长剧本拆成多个小 JSON 任务，适配本地 thinking 模型。"""
        base = f"标题:{title}\n题材:{theme}\n风格:{style}\n剧情:{description}"
        characters_data = await llm_chat_json(
            messages=[
                {"role": "system", "content": "你是短剧角色设计师。只输出 JSON，不要解释。"},
                {
                    "role": "user",
                    "content": base + """

生成 3 个主要角色，要求每个角色信息足以让模型稳定出图（年龄/族裔/面部/发型/身形/穿着/标志特征都要写明）。只输出：
{"characters":[{
  "character_id":"char_1",
  "name":"角色名",
  "gender":"female|male",
  "age_range":"28-32 / 高中生 / ...",
  "ethnicity":"East Asian Chinese / 中国南方人",
  "personality":"3-5 个关键词",
  "background":"至少 40 字的角色背景与动机",
  "appearance":{
    "face":"≥25字面部细节：脸型/眉眼/鼻唇/肤色/神情",
    "hair":"发型+发色+长度",
    "body":"≥15字身形与气质",
    "dress_style":"≥20字标志性穿着+配色+配饰",
    "distinguishing_features":"标志性细节如痣/戒指/伤疤"
  },
  "image_prompt":"English cinematic portrait prompt, ≥50 words, must include age/gender/ethnicity/face/hair/body/outfit/lighting/shot type/style"
}]}
""",
                },
            ],
            temperature=0.5,
            max_tokens=1800,
            timeout=140,
        )
        characters = characters_data.get("characters") or []
        if len(characters) < 2:
            raise ValueError("分步生成角色失败")

        outline = await llm_chat_json(
            messages=[
                {"role": "system", "content": "你是短剧编剧。只输出 JSON，不要解释。"},
                {
                    "role": "user",
                    "content": base + """

生成3场短剧大纲。只输出：
{"synopsis":"120字以内故事梗概","logline":"一句话钩子","scenes":[{"scene_id":"scene_1","scene_number":"第1场","location":"地点","time_of_day":"白天/夜晚","scene_summary":"本场冲突和剧情推进"}]}
""",
                },
            ],
            temperature=0.55,
            max_tokens=1800,
            timeout=140,
        )
        scenes = outline.get("scenes") or []
        if len(scenes) < 1:
            raise ValueError("分步生成场景失败")
        scenes = scenes[:3]

        screenplay_parts = []
        shot_number = 1
        character_hint = json.dumps(
            [{"character_id": c.get("character_id"), "name": c.get("name")} for c in characters],
            ensure_ascii=False,
        )
        for scene_idx, scene in enumerate(scenes, start=1):
            scene_id = scene.get("scene_id") or f"scene_{scene_idx}"
            scene["scene_id"] = scene_id
            scene["scene_number"] = scene.get("scene_number") or f"第{scene_idx}场"
            scene["location_id"] = scene.get("location_id") or f"loc_{scene_idx}"
            shot_data = await llm_chat_json(
                messages=[
                    {"role": "system", "content": "你是短剧分镜导演。只输出 JSON，不要解释。"},
                    {
                        "role": "user",
                        "content": f"""
项目：{base}
角色：{character_hint}
当前场景：{json.dumps(scene, ensure_ascii=False)}

为当前场景生成完整剧本文本和 3 个镜头。要求 script_text ≥ 200 字，每个 shot 的 content_description ≥ 80 字，必须用 [@角色名] 和 [@场景名] 引用语法，对白用 {{[@角色名]说："..."}} 包在描述末尾。只输出：
{{"script_text":"本场完整剧本文本，包含动作和对白","shots":[{{"shot_type":"近景","camera_movement":"固定","duration":5,"character_ids":["char_1"],"content_description":"中景，[@场景名] 内，镜头跟随 [@角色名] 推进...{{[@角色名]说：\\"对白\\"}}","shot_text":"中景，[@场景名] 内，镜头跟随 [@角色名] 推进，描述...{{[@角色名]说：\\"对白\\"}}","dialogue":{{"character_id":"char_1","text":"对白","emotion":"情绪","tts_text":"配音文本"}},"visual_prompt_for_kling":"English video prompt","negative_prompt_for_kling":"blurry, low quality"}}]}}
""",
                    },
                ],
                temperature=0.6,
                max_tokens=2200,
                timeout=160,
            )
            scene["script_text"] = shot_data.get("script_text") or scene.get("scene_summary", "")
            screenplay_parts.append(scene["script_text"])
            normalized_shots = []
            for shot in (shot_data.get("shots") or [])[:3]:
                shot["shot_id"] = f"shot_{shot_number}"
                shot["shot_number"] = shot_number
                shot["location_id"] = scene["location_id"]
                shot.setdefault("duration", 5)
                shot.setdefault("character_ids", [characters[0].get("character_id", "char_1")])
                shot.setdefault("negative_prompt_for_kling", "blurry, low quality, deformed face, bad hands")
                normalized_shots.append(shot)
                shot_number += 1
            if len(normalized_shots) < 1:
                raise ValueError(f"分步生成镜头失败: {scene_id}")
            scene["shots"] = normalized_shots

        return {
            "title": title,
            "theme": theme,
            "genre": theme,
            "style": style,
            "total_duration": duration,
            "synopsis": outline.get("synopsis") or description,
            "logline": outline.get("logline") or description[:60],
            "screenplay_text": "\n\n".join(screenplay_parts),
            "characters": characters,
            "scenes": scenes,
            "generation_mode": "stepwise_llm",
        }
    
    async def generate(
        self,
        title: str,
        theme: str,
        style: str,
        duration: int,
        characters: List[Dict],
        description: str
    ) -> Dict[str, Any]:
        """
        生成完整剧本
        """
        # 控制生成规模：保证生成"真剧本"，镜头/字数都拉到能讲完一个完整故事
        num_scenes = 3
        shots_per_scene = 3
        num_characters = 3 if len(description) < 80 else 4

        # 构建提示词
        prompt = SCRIPT_TEMPLATE.format(
            title=title,
            theme=theme,
            style=style,
            duration=duration,
            description=description,
            num_scenes=num_scenes,
            shots_per_scene=shots_per_scene,
            num_characters=num_characters,
        )
        
        system_prompt = ""
        
        # 调用 LLM 生成剧本
        llm_output = ""
        try:
            llm_output = await asyncio.wait_for(
                self._call_llm(prompt, system_prompt=system_prompt),
                timeout=250,
            )
            script = await self.parse_generated_script(llm_output)
            if not self._is_usable_script(script):
                raise ValueError("模型返回 JSON 结构不完整，缺少角色或分镜。")
        except Exception as e:
            print(f"剧本生成/解析失败，尝试紧凑提示词重试。原因: {e}")
            try:
                llm_output = await asyncio.wait_for(
                    self._call_compact_llm(title, theme, style, description),
                    timeout=250,
                )
                script = await self.parse_generated_script(llm_output)
                if not self._is_usable_script(script):
                    raise ValueError("紧凑提示词返回结构仍不完整。")
            except Exception as retry_error:
                print(f"紧凑剧本生成仍失败，尝试分步生成。原因: {retry_error}")
                try:
                    script = await self._generate_stepwise_script(
                        title=title,
                        theme=theme,
                        style=style,
                        duration=duration,
                        description=description,
                    )
                    if not self._is_usable_script(script):
                        raise ValueError("分步生成结构仍不完整。")
                except Exception as step_error:
                    print(f"分步剧本生成仍失败，启用完整本地剧本兜底。原因: {step_error}")
                    script = self._build_stub_from_partial(
                        title=title,
                        theme=theme,
                        style=style,
                        duration=duration,
                        description=description,
                        raw_llm_output=llm_output,
                    )

        # 兜底填充关键字段，避免前端工作台空数据
        script = self._normalize_script(script, title, theme, style, duration)
        script["script_id"] = str(uuid.uuid4())
        return script

    def _dialogue_text(self, dialogue: Any) -> str:
        if isinstance(dialogue, dict):
            speaker = dialogue.get("character_id") or dialogue.get("character") or dialogue.get("name") or ""
            text = dialogue.get("text") or dialogue.get("tts_text") or ""
            return f"{speaker}：{text}" if speaker and text else text
        return str(dialogue or "")

    def _build_screenplay_text(self, script: Dict[str, Any]) -> str:
        """根据 scenes/shots 自动拼完整剧本文本，作为模型漏字段时的兜底。"""
        parts = []
        for idx, scene in enumerate(script.get("scenes", []) or [], start=1):
            scene_no = scene.get("scene_number") or f"第{idx}场"
            location = scene.get("location") or "未指定地点"
            time_of_day = scene.get("time_of_day") or "时间未定"
            parts.append(f"【{scene_no} {location} / {time_of_day}】")
            if scene.get("scene_summary"):
                parts.append(str(scene.get("scene_summary")))
            if scene.get("script_text"):
                parts.append(str(scene.get("script_text")))
            else:
                for shot in scene.get("shots", []) or []:
                    desc = shot.get("content_description") or ""
                    dialogue = self._dialogue_text(shot.get("dialogue"))
                    if desc:
                        parts.append(f"动作：{desc}")
                    if dialogue:
                        parts.append(f"对白：{dialogue}")
            parts.append("")
        return "\n".join(parts).strip()

    def _normalize_script(self, script: Dict[str, Any], title: str, theme: str, style: str, duration: int) -> Dict[str, Any]:
        script.setdefault("title", title)
        script.setdefault("theme", theme)
        script.setdefault("genre", theme)
        script.setdefault("style", style)
        script.setdefault("total_duration", duration)
        script.setdefault("characters", [])
        script.setdefault("scenes", [])

        for idx, char in enumerate(script.get("characters", []) or [], start=1):
            char.setdefault("character_id", f"char_{idx}")
            char.setdefault("gender", "other")
            char.setdefault("appearance", {})
            char.setdefault("image_prompt", f"cinematic portrait of {char.get('name', f'character {idx}')}, {style}, high quality")

        shot_counter = 1
        for s_idx, scene in enumerate(script.get("scenes", []) or [], start=1):
            scene.setdefault("scene_id", f"scene_{s_idx}")
            scene.setdefault("scene_number", f"第{s_idx}场")
            scene.setdefault("shots", [])
            for shot in scene.get("shots", []) or []:
                shot.setdefault("shot_id", f"shot_{shot_counter}")
                shot.setdefault("shot_number", shot_counter)
                shot.setdefault("duration", 5)
                dialogue = shot.get("dialogue")
                if isinstance(dialogue, str):
                    shot["dialogue"] = {"character_id": "", "text": dialogue, "emotion": "", "tts_text": dialogue} if dialogue else {}
                elif dialogue is None:
                    shot["dialogue"] = {}
                elif isinstance(dialogue, dict):
                    text = dialogue.get("text") or dialogue.get("tts_text") or ""
                    dialogue.setdefault("tts_text", text)
                    shot["dialogue"] = dialogue
                shot_counter += 1

        if not script.get("screenplay_text") or len(str(script.get("screenplay_text") or "")) < 200:
            script["screenplay_text"] = self._build_screenplay_text(script)
        return normalize_script(script)

    @staticmethod
    def _extract_chinese_names(text: str) -> List[str]:
        """从用户描述里粗略抽取潜在中文人名（2-4 个汉字，前后是非汉字字符）。

        只用于 stub 兜底场景，避免硬塞"林晚/苏曼/顾承"这种跟剧本无关的角色。
        识别不出就返回空列表，让兜底使用通用占位名。
        """
        if not text:
            return []
        # 中文 2-4 字 token；过滤掉常见非人名词汇（地点 / 场景 / 题材关键字）
        tokens = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
        blacklist = {
            "电梯", "公司", "办公", "大堂", "会议", "宴会", "发布会", "酒店", "学校", "教室",
            "故事", "剧情", "题材", "风格", "白天", "夜晚", "都市", "校园", "古风", "悬疑",
            "现代", "短剧", "幻想", "穿越", "甜宠", "言情", "复仇", "重生", "逆袭", "豪门",
            "晚上", "早上", "中午", "傍晚", "凌晨", "周末", "城市", "雨夜", "街头",
            "标题", "时长", "角色", "主角", "配角", "反派", "演员", "导演", "本剧", "本片",
        }
        seen = []
        for tok in tokens:
            if tok in blacklist or tok in seen:
                continue
            # 不太可能是人名的：以"的"/"了"/"和"/"与"开头结尾
            if tok[0] in "的了和与又或而但是从就也都但又再" or tok[-1] in "的了和与又或":
                continue
            seen.append(tok)
            if len(seen) >= 3:
                break
        return seen

    def _build_stub_script(
        self,
        title: str,
        theme: str,
        style: str,
        duration: int,
        description: str,
        raw_llm_output: str = "",
    ) -> Dict[str, Any]:
        """模型不可用时的完整本地兜底剧本。

        不能再返回"占位分镜"，否则用户会以为系统生成了假内容。
        这里至少生成一版可阅读、可继续下游拆分镜/角色图/视频的短剧结构。

        **重要修复**：以前会硬塞"林晚/苏曼/顾承"三个跟剧本无关的固定角色，
        导致用户在角色 Tab 里看到莫名其妙的人物。现在改为：
          1) 优先从用户 description 里抽取潜在中文人名
          2) 抽不到就用"主角 / 关键角色 A / 关键角色 B"通用占位
          3) 在每个角色的 background 上明确标注「本角色由本地兜底生成，请重写剧本」
          4) script.generation_warning 显式提示用户兜底来源
        """
        premise = (description or raw_llm_output or "主角遭遇关键事件并完成转折").strip()
        if len(premise) > 180:
            premise = premise[:180] + "…"

        # 1) 试图从描述里抠中文人名
        extracted_names = self._extract_chinese_names(description)
        # 2) 不够 3 个就用通用占位补齐
        roles = ["主角", "关键角色甲", "关键角色乙"]
        names = []
        for i in range(3):
            if i < len(extracted_names):
                names.append(extracted_names[i])
            else:
                names.append(roles[i])

        warning_note = "（本角色由本地兜底生成，建议你回复『重写剧本』让系统按真实剧本重新设计）"

        characters = [
            {
                "character_id": f"char_{i+1}",
                "name": names[i],
                "gender": "other",
                "age_range": "",
                "ethnicity": "East Asian Chinese",
                "personality": "性格信息缺失",
                "background": f"基于剧情简介自动占位：{premise[:80]}…{warning_note}",
                "appearance": {
                    "face": "面部细节缺失",
                    "hair": "",
                    "body": "身形与气质待补充",
                    "dress_style": "穿着待补充",
                    "distinguishing_features": "",
                },
                "image_prompt": (
                    f"cinematic portrait of an East Asian Chinese person named {names[i]}, "
                    f"neutral background, soft cinematic lighting, photorealistic, {style}, high quality, "
                    "placeholder character (please regenerate)"
                ),
            }
            for i in range(3)
        ]

        scenes = [
            {
                "scene_id": "scene_1",
                "scene_number": "第1场",
                "location": "公司会议室",
                "time_of_day": "白天",
                "scene_summary": "女主被闺蜜当众背叛，事业和信任同时崩塌。",
                "script_text": "【第1场 公司会议室 / 白天】\n会议室里，投影幕上显示着林晚亲手做了三个月的项目方案，可署名却变成了苏曼。林晚站在长桌尽头，脸色发白。苏曼坐在主位旁，温柔地笑着，却没有半点愧疚。\n林晚压着声音问：\"苏曼，这是我的方案。\"\n苏曼轻轻合上文件：\"晚晚，职场不是讲感情的地方。你太天真了。\"\n全场沉默。林晚看着一张张回避的脸，终于明白自己被彻底推出局。",
                "shots": [
                    {
                        "shot_id": "shot_1",
                        "shot_number": 1,
                        "shot_type": "中景",
                        "camera_movement": "固定",
                        "duration": 5,
                        "content_description": "林晚站在会议室长桌尽头，投影幕上是被改名的项目方案，她震惊地看向苏曼。",
                        "dialogue": {"character_id": "char_1", "text": "苏曼，这是我的方案。", "emotion": "震惊克制", "tts_text": "苏曼，这是我的方案。"},
                        "visual_prompt_for_kling": "Chinese office meeting room, young woman shocked, projector screen, cinematic realistic drama, vertical 9:16",
                        "negative_prompt_for_kling": "blurry, low quality, deformed face",
                    },
                    {
                        "shot_id": "shot_2",
                        "shot_number": 2,
                        "shot_type": "近景",
                        "camera_movement": "缓慢推近",
                        "duration": 5,
                        "content_description": "苏曼微笑着把离职协议推到林晚面前，周围人沉默回避。",
                        "dialogue": {"character_id": "char_2", "text": "晚晚，职场不是讲感情的地方。你太天真了。", "emotion": "虚伪冷漠", "tts_text": "晚晚，职场不是讲感情的地方。你太天真了。"},
                        "visual_prompt_for_kling": "stylish Chinese woman antagonist smiling coldly in office meeting, pushing document, cinematic close-up",
                        "negative_prompt_for_kling": "blurry, low quality, bad hands",
                    },
                ],
            },
            {
                "scene_id": "scene_2",
                "scene_number": "第2场",
                "location": "雨夜街头 / 简陋出租屋",
                "time_of_day": "夜晚",
                "scene_summary": "女主跌入低谷，但决定隐忍两年重新回来。",
                "script_text": "【第2场 雨夜街头 / 夜晚】\n雨水打湿林晚的头发，她抱着纸箱站在公司楼下。身后的大楼灯火通明，像一座不属于她的城。她没有哭，只是慢慢攥紧手里的旧工牌。\n林晚低声说：\"今天你们拿走的，我会一样一样拿回来。\"\n画面转到出租屋，墙上的日历一页页翻过。林晚熬夜学习、谈客户、做方案。两年后，她换上黑色西装，合上电脑，眼神平静而锋利。",
                "shots": [
                    {
                        "shot_id": "shot_3",
                        "shot_number": 3,
                        "shot_type": "远景",
                        "camera_movement": "缓慢拉远",
                        "duration": 5,
                        "content_description": "雨夜里，林晚抱着纸箱站在公司楼下，霓虹灯映在积水中。",
                        "dialogue": {"character_id": "char_1", "text": "今天你们拿走的，我会一样一样拿回来。", "emotion": "压抑坚定", "tts_text": "今天你们拿走的，我会一样一样拿回来。"},
                        "visual_prompt_for_kling": "rainy night city street, Chinese woman holding cardboard box, neon reflection, cinematic revenge drama",
                        "negative_prompt_for_kling": "blurry, low quality",
                    },
                    {
                        "shot_id": "shot_4",
                        "shot_number": 4,
                        "shot_type": "蒙太奇",
                        "camera_movement": "快速切换",
                        "duration": 6,
                        "content_description": "出租屋内，林晚熬夜学习、整理资料、打电话谈客户，日历快速翻过两年。",
                        "dialogue": {"character_id": "char_1", "text": "两年了，该回去了。", "emotion": "冷静", "tts_text": "两年了，该回去了。"},
                        "visual_prompt_for_kling": "small apartment montage, woman working late at laptop, calendar pages flipping, cinematic realistic",
                        "negative_prompt_for_kling": "blurry, low quality",
                    },
                ],
            },
            {
                "scene_id": "scene_3",
                "scene_number": "第3场",
                "location": "新品发布会宴会厅",
                "time_of_day": "夜晚",
                "scene_summary": "女主以投资人身份归来，当众揭开闺蜜背叛真相。",
                "script_text": "【第3场 新品发布会宴会厅 / 夜晚】\n苏曼站在台上，正接受掌声。大门打开，林晚穿着黑色西装走进来，身后跟着投资团队。全场安静。顾承起身，向林晚点头。\n林晚把一份证据投到大屏上：聊天记录、转账记录、原始方案时间戳，一页页出现。\n苏曼的笑容僵住：\"你想干什么？\"\n林晚平静地看着她：\"不是复仇，是把真相还给所有人。\"\n掌声消失，镜头定格在苏曼惨白的脸上。",
                "shots": [
                    {
                        "shot_id": "shot_5",
                        "shot_number": 5,
                        "shot_type": "全景",
                        "camera_movement": "跟拍",
                        "duration": 6,
                        "content_description": "宴会厅大门打开，林晚带着投资团队走进发布会，全场回头。",
                        "dialogue": {"character_id": "char_3", "text": "林总，资料已经准备好了。", "emotion": "沉稳", "tts_text": "林总，资料已经准备好了。"},
                        "visual_prompt_for_kling": "luxury product launch banquet hall, elegant woman entering with investment team, cinematic wide shot",
                        "negative_prompt_for_kling": "blurry, low quality",
                    },
                    {
                        "shot_id": "shot_6",
                        "shot_number": 6,
                        "shot_type": "特写",
                        "camera_movement": "推近",
                        "duration": 6,
                        "content_description": "大屏幕显示证据，苏曼脸色惨白，林晚平静地看着她。",
                        "dialogue": {"character_id": "char_1", "text": "不是复仇，是把真相还给所有人。", "emotion": "冷静有力", "tts_text": "不是复仇，是把真相还给所有人。"},
                        "visual_prompt_for_kling": "dramatic close-up, evidence on big screen, antagonist shocked, heroine calm, cinematic revenge climax",
                        "negative_prompt_for_kling": "blurry, low quality, deformed face",
                    },
                ],
            },
        ]

        screenplay = "\n\n".join(scene["script_text"] for scene in scenes)
        return {
            "title": title,
            "theme": theme,
            "genre": theme,
            "style": style,
            "total_duration": duration,
            "logline": "被闺蜜背叛的女人，两年后以投资人身份归来，当众夺回属于自己的一切。",
            "synopsis": premise,
            "screenplay_text": screenplay,
            "characters": characters,
            "scenes": scenes,
            "generation_warning": (
                "⚠️ 当前剧本由【本地兜底模板】生成，并非按你输入的剧情写成。原因：LLM 调用超时/返回空内容/JSON 解析失败。\n"
                "请按以下任一方式重试：\n"
                "  1) 在对话区直接说『重写剧本』，系统会再次调用 LLM；\n"
                "  2) 在 .env 里换成更稳定的 LLM_MODEL 后再试；\n"
                "角色 Tab 里如果出现你剧本里没有的人物，说明命中了本兜底，请重写剧本。"
            ),
        }
    
    def _extract_json_candidates(self, text: str) -> List[str]:
        """从模型输出里提取可能的 JSON 对象，支持 markdown、前后解释、嵌套大括号。"""
        if not text:
            return []
        cleaned = text.strip().lstrip("\ufeff")
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        # 常见中文智能引号修正；不要动普通英文引号。
        cleaned = cleaned.replace("“", '"').replace("”", '"')
        cleaned = cleaned.replace("‘", "'").replace("’", "'")

        candidates = [cleaned]
        for block in re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE):
            candidates.append(block.strip())

        # 用 JSONDecoder.raw_decode 从每一个 { 开始尝试，避免贪婪正则吞掉多余文本。
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(cleaned):
            if ch != "{":
                continue
            snippet = cleaned[idx:]
            try:
                obj, end = decoder.raw_decode(snippet)
                if isinstance(obj, dict):
                    candidates.append(snippet[:end])
            except Exception:
                pass

        # 最后兜底：平衡大括号提取第一个完整对象。
        start = cleaned.find("{")
        if start >= 0:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(cleaned)):
                c = cleaned[i]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                    continue
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(cleaned[start:i + 1])
                        break

        # 去重并做常见尾随逗号修复。
        normalized = []
        seen = set()
        for candidate in candidates:
            candidate = re.sub(r",(\s*[}\]])", r"\1", candidate.strip())
            if candidate and candidate not in seen:
                normalized.append(candidate)
                seen.add(candidate)
        return normalized

    async def parse_generated_script(self, llm_output: str) -> Dict[str, Any]:
        """解析 LLM 输出的剧本 JSON。Hermes Agent 可能在 JSON 前后包一层说明，所以这里要更宽容。"""
        errors = []
        parsed_candidates = []
        for candidate in self._extract_json_candidates(llm_output):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    # 有些模型会包一层 {"script": {...}} / {"data": {...}}
                    for key in ("script", "data", "result"):
                        if isinstance(parsed.get(key), dict) and (parsed[key].get("scenes") or parsed[key].get("screenplay_text")):
                            parsed_candidates.append(parsed[key])
                            break
                    else:
                        parsed_candidates.append(parsed)
            except Exception as e:
                errors.append(str(e))
                continue

        for parsed in parsed_candidates:
            if self._is_usable_script(parsed):
                return parsed
        if parsed_candidates:
            return parsed_candidates[-1]

        preview = (llm_output or "")[:800].replace("\n", " ")
        print("【严重错误】完全无法解析 LLM 返回的 JSON 格式。")
        print(f"LLM 原始输出预览: {preview}")
        if errors:
            print(f"JSON 解析错误预览: {errors[:3]}")
        raise ValueError("无法从 LLM 输出中解析剧本 JSON，可能模型返回了非 JSON 格式文本或输出被截断。")

    def _recover_partial_script_fields(self, raw: str) -> Dict[str, str]:
        """本地模型截断 JSON 时，尽量回收已经生成出的文本字段。"""
        if not raw:
            return {}
        fields = {}
        for key in ("title", "genre", "style", "synopsis", "logline", "screenplay_text"):
            match = re.search(rf'"{key}"\s*:\s*"([\s\S]*?)(?<!\\)"\s*,?\s*(?:"[a-zA-Z_]+\"|[\}}\]])', raw)
            if match:
                value = match.group(1)
                value = value.replace("\\n", "\n").replace('\\"', '"').strip()
                if value:
                    fields[key] = value
        return fields

    def _build_stub_from_partial(
        self,
        title: str,
        theme: str,
        style: str,
        duration: int,
        description: str,
        raw_llm_output: str,
    ) -> Dict[str, Any]:
        script = self._build_stub_script(title, theme, style, duration, description, raw_llm_output="")
        recovered = self._recover_partial_script_fields(raw_llm_output)
        if recovered.get("title"):
            script["title"] = recovered["title"]
        if recovered.get("genre"):
            script["genre"] = recovered["genre"]
            script["theme"] = recovered["genre"]
        if recovered.get("style"):
            script["style"] = recovered["style"]
        if recovered.get("synopsis"):
            script["synopsis"] = recovered["synopsis"]
        if recovered.get("logline"):
            script["logline"] = recovered["logline"]
        if recovered.get("screenplay_text") and len(recovered["screenplay_text"]) > 80:
            script["screenplay_text"] = recovered["screenplay_text"]
        script["generation_warning"] = "当前本地 LLM 输出了不完整 JSON，系统已回收可用文本并补齐生产结构。建议换非 thinking/instruct 模型以获得完整剧本结构。"
        return script
