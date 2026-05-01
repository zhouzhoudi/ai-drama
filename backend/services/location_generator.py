"""
场景图生成服务

跟 CharacterGenerator 一一对应：
  - 一个 scene = 一个 location（剧本里的实例化场景）
  - 走可灵 v2-1 文生图，端点 /v1/images/generations，查询同路径
  - 用户上传参考图时自动切换为 image-to-image
  - 生成结果写到：
      script.scenes[i].location_image_url
      script.assets.locations[i].image_url
    后续分镜图、视频生成都能用 location_image_url 作参考
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .kling_auth import kling_auth_headers, _load_kling_credential


class LocationGenerationError(RuntimeError):
    """场景图生成失败，message 可直接展示给用户。"""


class LocationGenerator:
    def __init__(self):
        self.api_base = os.environ.get("KLING_API_BASE", "https://api.klingai.com").rstrip("/")
        # 复用角色图模型变量（同样是 v2 系列文生图最稳）；也可以单独用 KLING_LOCATION_IMAGE_MODEL 覆盖。
        self.image_model = (
            os.environ.get("KLING_LOCATION_IMAGE_MODEL")
            or os.environ.get("KLING_CHARACTER_IMAGE_MODEL")
            or os.environ.get("KLING_IMAGE_MODEL")
            or "kling-v2-1"
        )
        self.max_wait = int(os.environ.get("KLING_IMAGE_MAX_WAIT", "180") or 180)
        self.data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")

    # ---------- 工具 ----------
    @staticmethod
    def _as_text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, tuple)):
            return ", ".join(LocationGenerator._as_text(v) for v in value if v not in (None, ""))
        if isinstance(value, dict):
            for key in ("text", "value", "description", "name"):
                if value.get(key):
                    return LocationGenerator._as_text(value.get(key), default)
            return str(value)
        return str(value).strip()

    def _has_credentials(self) -> bool:
        return bool(_load_kling_credential("KLING_AK") and _load_kling_credential("KLING_SK"))

    def _headers(self) -> Dict[str, str]:
        return kling_auth_headers()

    def _api_error_text(self, resp: requests.Response) -> str:
        try:
            body = resp.json()
            if isinstance(body, dict):
                parts = []
                for key in ["code", "message", "msg", "error", "request_id"]:
                    if body.get(key) is not None:
                        parts.append(f"{key}={body.get(key)}")
                if parts:
                    return "; ".join(str(p) for p in parts)[:600]
        except Exception:
            pass
        return (resp.text or "").replace("\n", " ")[:600]

    def _build_full_prompt(self, scene: Dict, style: str = "cinematic realistic drama") -> str:
        """把一个 scene 字段拼成可灵能稳定出图的场景 prompt。

        最终 prompt 包含：地点、时间、本场氛围、典型陈设/光线、电影质感参数。
        刻意不放角色，避免"场景图里随便冒出一个人"。
        """
        location = self._as_text(scene.get("location"), default="未指定地点") or "未指定地点"
        time_of_day = self._as_text(scene.get("time_of_day"), default="白天") or "白天"
        summary = self._as_text(scene.get("scene_summary"))
        env_prompt = self._as_text(scene.get("environment_prompt"))
        existing_prompt = self._as_text(scene.get("location_prompt"))

        head = f"cinematic establishing shot of {location}, {time_of_day}"
        # 中文细节作为补强（v2-1 中英混排理解 OK）
        details = []
        if summary:
            details.append(f"氛围：{summary}")
        if env_prompt:
            details.append(env_prompt)
        if existing_prompt:
            details.append(existing_prompt)

        tech = (
            "wide angle, eye-level, no people, empty environment, "
            "natural realistic lighting, cinematic composition, depth of field, "
            "photorealistic, high detail, 8k, film grain, vertical 9:16, "
            f"{style}"
        )

        parts = [head]
        if details:
            parts.append("。".join(details))
        parts.append(tech)
        prompt = ", ".join(p for p in parts if p)
        prompt = " ".join(prompt.split())
        if len(prompt) > 1500:
            prompt = prompt[:1500]
        return prompt

    # ---------- 同步入口 ----------
    def generate_location(
        self,
        script_id: str,
        scene_id: str,
        prompt: str,
        reference_image_url: Optional[str] = None,
    ) -> str:
        if not self._has_credentials():
            raise LocationGenerationError("缺少 KLING_AK/KLING_SK，无法生成场景图。")
        if not (prompt or "").strip():
            raise LocationGenerationError(f"场景 {scene_id} 缺少 prompt，无法生成场景图。")

        task_id = self._create_image_task(prompt, reference_image_url=reference_image_url)
        image_url = self._poll_image_task(task_id, max_wait=self.max_wait)
        if not image_url:
            raise LocationGenerationError(f"可灵任务 {task_id} 已结束，但没有返回图片 URL。")
        self._save_url_into_script(
            script_id, scene_id, image_url, prompt, reference_image_url=reference_image_url
        )
        return image_url

    # ---------- 异步入口（走单 scene 同步生成）----------
    async def generate_scene_image(self, script_id: str, scene_id: str) -> Dict:
        path = self.data_dir / script_id / "script.json"
        if not path.exists():
            raise FileNotFoundError(f"剧本不存在: {script_id}")
        with open(path, encoding="utf-8") as f:
            script = json.load(f)

        scene = self._resolve_scene(script, scene_id)
        if not scene:
            raise ValueError(f"场景不存在: {scene_id}")

        style_hint = script.get("style") or "cinematic realistic drama"
        prompt = self._build_full_prompt(scene, style=style_hint)

        ref_urls = [u for u in (scene.get("reference_image_urls") or []) if u]
        ref = ref_urls[0] if ref_urls else None

        image_url = self.generate_location(
            script_id, scene_id, prompt, reference_image_url=ref
        )
        return {
            "scene_id": scene_id,
            "image_url": image_url,
            "prompt_used": prompt,
            "reference_image_url": ref,
            "engine": "kling",
            "model": self.image_model,
        }

    # ---------- 可灵 API ----------
    def _create_image_task(self, prompt: str, reference_image_url: Optional[str] = None) -> str:
        url = f"{self.api_base}/v1/images/generations"
        payload: Dict[str, Any] = {
            "model_name": self.image_model,
            "prompt": prompt,
            "aspect_ratio": "16:9",  # 场景图横屏，给后续分镜留构图余量
            "n": 1,
        }
        if reference_image_url:
            payload["image"] = reference_image_url
            payload["image_fidelity"] = 0.5

        print(
            f"[Kling Location] 创建任务 model={self.image_model} "
            f"prompt={prompt[:50]}... ref={'yes' if reference_image_url else 'no'}"
        )
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        except requests.RequestException as e:
            raise LocationGenerationError(f"可灵场景图任务创建请求失败：{e}") from e

        if resp.status_code != 200:
            raise LocationGenerationError(
                f"可灵场景图任务创建失败：HTTP {resp.status_code}，{self._api_error_text(resp)}"
            )

        try:
            data = resp.json()
        except Exception as e:
            raise LocationGenerationError(f"可灵场景图任务创建返回的不是 JSON：{resp.text[:300]}") from e

        code = data.get("code")
        if code not in (None, 0, "0"):
            msg = data.get("message") or data.get("msg") or data.get("error") or str(data)[:300]
            raise LocationGenerationError(f"可灵场景图任务创建失败：code={code}，{msg}")

        task_id = data.get("data", {}).get("task_id") or data.get("task_id") or ""
        if not task_id:
            raise LocationGenerationError(f"可灵场景图任务创建成功但未返回 task_id：{str(data)[:300]}")
        return task_id

    def _poll_image_task(self, task_id: str, max_wait: int = 180) -> str:
        url = f"{self.api_base}/v1/images/generations/{task_id}"
        start = time.time()
        last_error = ""
        last_status = ""
        while time.time() - start < max_wait:
            try:
                r = requests.get(url, headers=self._headers(), timeout=15)
            except requests.RequestException as e:
                last_error = f"轮询请求异常：{e}"
                time.sleep(5)
                continue
            if r.status_code != 200:
                last_error = f"轮询失败 HTTP {r.status_code}，{self._api_error_text(r)}"
                if r.status_code in (400, 401, 403, 404):
                    raise LocationGenerationError(f"可灵场景图任务 {task_id} {last_error}")
                time.sleep(5)
                continue
            try:
                body = r.json()
            except Exception:
                last_error = f"轮询返回不是 JSON：{r.text[:300]}"
                time.sleep(5)
                continue

            data = body.get("data", body)
            status = (data.get("task_status") or data.get("status") or "").lower()
            last_status = status or last_status

            images = (
                data.get("task_result", {}).get("images")
                or data.get("images")
                or []
            )
            if status in ("succeed", "success", "completed"):
                if images:
                    first = images[0]
                    return first.get("url") or first.get("image_url") or ""
                raise LocationGenerationError(f"可灵场景图任务 {task_id} 已成功，但返回结果里没有图片 URL。")
            if status in ("failed", "fail"):
                msg = data.get("task_status_msg") or data.get("error_message") or body.get("message") or str(body)[:300]
                raise LocationGenerationError(f"可灵场景图任务失败：{msg}")
            time.sleep(5)

        suffix = f"最后状态：{last_status}" if last_status else last_error or "无状态返回"
        raise LocationGenerationError(f"可灵场景图任务超时：{task_id}，等待 {max_wait} 秒仍未完成。{suffix}")

    # ---------- 数据写回 ----------
    @staticmethod
    def _resolve_scene(script: Dict[str, Any], scene_ref: str) -> Optional[Dict[str, Any]]:
        """既能按 scene_id 也能按 location_id 找到 scene，统一交给前端用同一个标识符。"""
        for scene in script.get("scenes", []) or []:
            if scene.get("scene_id") == scene_ref or scene.get("location_id") == scene_ref:
                return scene
        return None

    def _save_url_into_script(
        self,
        script_id: str,
        scene_id: str,
        image_url: str,
        prompt: str,
        reference_image_url: Optional[str] = None,
    ) -> None:
        path = self.data_dir / script_id / "script.json"
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                script = json.load(f)
            scene = self._resolve_scene(script, scene_id)
            if scene:
                scene["location_image_url"] = image_url
                scene["location_prompt_used"] = prompt
                if reference_image_url:
                    scene["location_reference_image_used"] = reference_image_url

            # 同步更新 assets.locations[i].image_url，方便前端只看 assets 的渲染入口
            assets = script.setdefault("assets", {})
            location_id = scene.get("location_id") if scene else None
            for loc_asset in assets.get("locations", []) or []:
                if loc_asset.get("asset_id") == location_id:
                    loc_asset["image_url"] = image_url
                    loc_asset["status"] = "ready"
                    break

            with open(path, "w", encoding="utf-8") as f:
                json.dump(script, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise LocationGenerationError(f"场景图已生成但写回 script.json 失败：{e}") from e
