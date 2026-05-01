"""
分镜视频生成服务（可灵 Kling，OmniVideo 统一端点）

- v3-omni / video-o1 等新模型统一走 /v1/videos/omni-video，
  无需再区分文生视频和图生视频；参考图通过 image_list 传入。
- 轮询任务状态，拿到下载 URL 后保存到本地。
- 鉴权使用 KLING_AK + KLING_SK 动态生成 JWT（HS256），每次请求前刷新，有效期 30 分钟。
- 模型 / 基础地址来自 backend/.env：
    KLING_API_BASE     默认 https://api.klingai.com
    KLING_VIDEO_MODEL  默认 kling-v3-omni
    KLING_VIDEO_MODE   默认 std（可选 pro）
"""

import os
import json
import time
import requests
from pathlib import Path
from typing import Dict, List, Any, Optional

from .storage import get_storage
from .kling_auth import kling_auth_headers, _load_kling_credential


class VideoGenerator:
    """视频生成器（可灵 Kling，AK/SK JWT 鉴权）"""

    def __init__(self):
        base = os.environ.get("KLING_API_BASE", "https://api.klingai.com").rstrip("/")
        self.api_root = base
        self.video_model = os.environ.get("KLING_VIDEO_MODEL", "kling-v3-omni")
        self.video_mode = os.environ.get("KLING_VIDEO_MODE", "std")
        self.data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")
        self.storage = get_storage()

    def _has_credentials(self) -> bool:
        return bool(
            _load_kling_credential("KLING_AK")
            and _load_kling_credential("KLING_SK")
        )

    def _headers(self) -> Dict[str, str]:
        """每次调用动态生成 JWT，避免 token 过期。"""
        return kling_auth_headers()

    # -------------------- 主入口 --------------------
    async def generate_shot_video(
        self,
        script_id: str,
        shot: Dict,
        characters: List[Dict],
        reference_image_url: Optional[str] = None,
        aspect_ratio: str = "9:16",
        duration: int = 5,
    ) -> str:
        """生成单个分镜视频，返回本地路径或下载 URL。"""
        if not self._has_credentials():
            print("[Kling Video] 缺少 KLING_AK/SK，跳过实际请求。")
            return ""

        # 视频 prompt 一律用 _build_video_prompt 重新拼，把 shot_text、对白、视觉
        # 描述全部融合，让 v3-omni（OmniVideo）能同时生成画面 + 同步口型 + 配音。
        # visual_prompt_for_kling 单独使用会丢中文对白和场景标签信息。
        prompt = self._build_video_prompt(shot, characters)
        negative_prompt = shot.get("negative_prompt_for_kling", "blurry, low quality, deformed")

        if not reference_image_url:
            reference_image_url = (
                shot.get("storyboard_image_url")
                or shot.get("reference_image_url")
                or (shot.get("reference_image_urls") or [""])[0]
            )

        if not reference_image_url:
            char_id = (shot.get("dialogue") or {}).get("character_id")
            if char_id:
                for char in characters:
                    if char.get("character_id") == char_id:
                        reference_image_url = (char.get("appearance") or {}).get("image_url", "")
                        break

        if not reference_image_url:
            char_ids = set(shot.get("character_ids") or [])
            for char in characters:
                if char.get("character_id") in char_ids:
                    reference_image_url = (char.get("appearance") or {}).get("image_url", "")
                    if reference_image_url:
                        break

        task_id = self._create_omni_video_task(
            prompt=prompt,
            negative_prompt=negative_prompt,
            image_url=reference_image_url or "",
            aspect_ratio=aspect_ratio,
            duration=duration,
        )

        if not task_id:
            return ""

        video_url = self._poll_video_task(task_id)
        if not video_url:
            return ""

        try:
            local_path = self._save_video(script_id, shot.get("shot_id", task_id), video_url)
            return str(local_path)
        except Exception as e:
            print(f"[Kling Video] 保存到本地失败: {e}, 返回原始 URL")
            return video_url

    # -------------------- 可灵 API --------------------
    # OmniVideo 统一端点（v3-omni / video-o1 都走这里）：
    #   POST /v1/videos/omni-video
    #   GET  /v1/videos/omni-video/{task_id}
    # 参考图通过 image_list 传，type=first_frame；不再区分 text2video / image2video。
    def _create_omni_video_task(
        self,
        prompt: str,
        negative_prompt: str,
        image_url: str,
        aspect_ratio: str,
        duration: int,
    ) -> str:
        url = f"{self.api_root}/v1/videos/omni-video"
        payload: Dict[str, Any] = {
            "model_name": self.video_model,
            "prompt": prompt,
            "mode": self.video_mode,
            "aspect_ratio": aspect_ratio,
            "duration": str(duration),
        }
        # 关键：OmniVideo 的音频默认通常是关闭的（sound=off / generate_audio=false）。
        # 仅靠 prompt 指令可能会出现“有口型但无音轨”的结果，所以这里显式开启。
        # 约定：KLING_VIDEO_SOUND=on/off，默认 on。
        sound = (os.environ.get("KLING_VIDEO_SOUND", "on") or "on").strip().lower()
        if sound in ("on", "off"):
            payload["sound"] = sound
        else:
            payload["sound"] = "on"
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if image_url:
            payload["image_list"] = [{"image_url": image_url, "type": "first_frame"}]

        kind = "image2video" if image_url else "text2video"
        print(f"[Kling Video] 创建 omni-video({kind}) model={self.video_model} mode={self.video_mode} prompt={prompt[:60]}...")
        return self._post_create_task(url, payload)

    def _post_create_task(self, url: str, payload: Dict[str, Any]) -> str:
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        except Exception as e:
            print(f"[Kling Video] 请求异常: {e}")
            return ""

        if resp.status_code != 200:
            print(f"[Kling Video] 创建任务失败 {resp.status_code}: {resp.text[:300]}")
            return ""

        data = resp.json()
        code = data.get("code")
        if code not in (None, 0, "0"):
            print(f"[Kling Video] 创建任务返回错误: code={code} message={data.get('message')} request_id={data.get('request_id')}")
            return ""
        return (
            data.get("data", {}).get("task_id")
            or data.get("task_id")
            or ""
        )

    def _poll_video_task(self, task_id: str, max_wait: int = 600) -> str:
        url = f"{self.api_root}/v1/videos/omni-video/{task_id}"

        start = time.time()
        while time.time() - start < max_wait:
            try:
                r = requests.get(url, headers=self._headers(), timeout=15)
                if r.status_code != 200:
                    print(f"[Kling Video] 轮询失败 {r.status_code}: {r.text[:200]}")
                    time.sleep(8)
                    continue
                body = r.json()
                data = body.get("data", body)
                status = (data.get("task_status") or data.get("status") or "").lower()

                videos = (
                    data.get("task_result", {}).get("videos")
                    or data.get("videos")
                    or []
                )

                if status in ("succeed", "success", "completed") and videos:
                    first = videos[0]
                    video_url = first.get("url") or first.get("video_url") or ""
                    if video_url:
                        return video_url

                if status in ("failed", "fail"):
                    print(f"[Kling Video] 任务失败: {body}")
                    return ""

                if status:
                    print(f"[Kling Video] 任务 {task_id} 状态：{status}")
            except Exception as e:
                print(f"[Kling Video] 轮询异常: {e}")
            time.sleep(8)

        print(f"[Kling Video] 任务超时: {task_id}")
        return ""

    # -------------------- 工具函数 --------------------
    @staticmethod
    def _safe_text(value: Any, default: str = "") -> str:
        """把任意类型字段安全转字符串，避免 LLM 偶发输出 list/dict 时 .strip() 报错。"""
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, tuple)):
            return ", ".join(VideoGenerator._safe_text(v) for v in value if v not in (None, ""))
        if isinstance(value, dict):
            for key in ("text", "value", "description", "name"):
                if value.get(key):
                    return VideoGenerator._safe_text(value.get(key), default)
            return str(value)
        return str(value).strip()

    def _is_omni_model(self) -> bool:
        return "omni" in (self.video_model or "").lower()

    def _build_video_prompt(self, shot: Dict, characters: List[Dict]) -> str:
        """构造可灵视频 prompt：把视觉描述 + 分镜剧本格式 + 对白 + 配音指令融合。

        对 v3-omni（OmniVideo）来说，prompt 里出现 `角色名说："对白"` 时模型会
        生成同步口型与配音；所以这里**必须把对白显式写进 prompt**，并加一句
        "同步生成口型与配音"指令。
        """
        char_name_by_id: Dict[str, str] = {
            c.get("character_id"): self._safe_text(c.get("name")) or c.get("character_id")
            for c in characters
            if c.get("character_id")
        }

        parts: List[str] = []

        # 1) 英文视觉骨架（LLM 给的 visual_prompt_for_kling），保证摄影/光线/质感稳定。
        visual = self._safe_text(shot.get("visual_prompt_for_kling"))
        if visual:
            parts.append(visual)

        # 2) 中文分镜剧本（含 [@场景] / [@角色] / 对白格式），让 v3-omni 知道
        #    谁说话、在哪里、画面动作怎么演。优先用 shot_text，其次 content_description。
        shot_text = self._safe_text(shot.get("shot_text"))
        if shot_text:
            parts.append(shot_text)
        else:
            desc = self._safe_text(shot.get("content_description"))
            if desc:
                shot_type = self._safe_text(shot.get("shot_type"), default="中景") or "中景"
                camera = self._safe_text(shot.get("camera_movement"))
                head = ", ".join(p for p in [shot_type, camera] if p)
                parts.append(f"{head}，{desc}" if head else desc)

        # 3) 显式对白行——这是触发 v3-omni 生成同步口型/配音的关键。
        dialogue = shot.get("dialogue") or {}
        if isinstance(dialogue, dict):
            text = self._safe_text(dialogue.get("text")) or self._safe_text(dialogue.get("tts_text"))
            speaker_id = self._safe_text(dialogue.get("character_id"))
            speaker_name = char_name_by_id.get(speaker_id) or speaker_id
            emotion = self._safe_text(dialogue.get("emotion"))
            if text:
                if speaker_name:
                    line = f'对白：{speaker_name} 说："{text}"。'
                else:
                    line = f'对白："{text}"。'
                if emotion:
                    line += f"语气：{emotion}。"
                # 明确告诉 OmniVideo 必须生成同步说话画面 + 配音
                line += "请让说话角色在画面中嘴型与对白同步，并生成自然的中文配音音轨。"
                parts.append(line)

        # 4) 角色一致性提示（让模型继续保持参考图里的人物外观）
        char_ids = shot.get("character_ids") or []
        if char_ids:
            names = [
                char_name_by_id.get(cid)
                for cid in char_ids
                if char_name_by_id.get(cid)
            ]
            if names:
                parts.append(
                    f"画面中的角色：{', '.join(names)}，需与参考图保持外貌一致（发型/穿着/五官）。"
                )

        # 5) 画质和电影感收尾
        parts.append("写实电影质感，cinematic lighting，4K，自然运镜。")

        prompt = " ".join(p for p in parts if p)
        # 去除多余空白
        prompt = " ".join(prompt.split())
        # 可灵 prompt 长度上限保守裁到 2000 字符
        if len(prompt) > 2000:
            prompt = prompt[:2000]
        return prompt

    # 保留旧名称做兼容（其他地方可能引用到）
    def _generate_prompt_from_shot(self, shot: Dict, characters: List[Dict]) -> str:
        return self._build_video_prompt(shot, characters)

    def _save_video(self, script_id: str, shot_id: str, video_url: str) -> Path:
        shot_dir = self.data_dir / script_id / "shots" / str(shot_id)
        shot_dir.mkdir(parents=True, exist_ok=True)
        video_path = shot_dir / "video.mp4"

        try:
            r = requests.get(video_url, timeout=300)
            if r.status_code == 200:
                video_path.write_bytes(r.content)
        except Exception as e:
            print(f"[Kling Video] 下载视频失败: {e}")

        return video_path

    async def batch_generate(
        self,
        script_id: str,
        shots: List[Dict],
        characters: List[Dict],
    ) -> List[str]:
        results = []
        for shot in shots:
            url = await self.generate_shot_video(
                script_id=script_id,
                shot=shot,
                characters=characters,
            )
            results.append(url)
        return results
