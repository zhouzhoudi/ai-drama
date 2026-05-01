"""
Storyboard image generation for shot-level production.

This stage creates a still frame for every shot before video generation. The
still frame becomes both the user's preview and the reference image for Kling
image-to-video, which is the key control point for continuity.

错误处理原则：本模块不再静默吞错。任何来自可灵的错误（HTTP 非 200、code != 0、
任务 failed、超时、缺凭证）都必须抛出 StoryboardGenerationError，由上层
async_generate_storyboards 收集并写到 task 状态里，最终传给前端展示。
否则前端会出现 "100% 完成 但分镜图为空" 的假成功。
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .kling_auth import kling_auth_headers, _load_kling_credential
from .production_state import (
    find_shot,
    mark_shot_storyboard,
    normalize_script,
    update_production_status,
)


class StoryboardGenerationError(RuntimeError):
    """分镜图生成失败，message 可直接展示给用户。"""


class StoryboardGenerator:
    """Generate storyboard still images via Kling image generation."""

    def __init__(self):
        self.api_base = os.environ.get("KLING_API_BASE", "https://api.klingai.com").rstrip("/")
        # 分镜图独立模型变量；fallback 到 KLING_IMAGE_MODEL；兜底 kling-v2-1。
        # v3-omni 必须有控制台 element 作主体引导，纯远程 URL 经常被判 1201；
        # v2-1 走标准 generations，可带单图 image 做参考，最稳。
        self.image_model = (
            os.environ.get("KLING_STORYBOARD_IMAGE_MODEL")
            or os.environ.get("KLING_IMAGE_MODEL")
            or "kling-v2-1"
        )
        self.data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")

    def _has_credentials(self) -> bool:
        return bool(_load_kling_credential("KLING_AK") and _load_kling_credential("KLING_SK"))

    def _headers(self) -> Dict[str, str]:
        return kling_auth_headers()

    def _script_path(self, script_id: str) -> Path:
        return self.data_dir / script_id / "script.json"

    def _load_script(self, script_id: str) -> Dict[str, Any]:
        path = self._script_path(script_id)
        if not path.exists():
            raise FileNotFoundError(f"剧本不存在: {script_id}")
        with open(path, encoding="utf-8") as f:
            return normalize_script(json.load(f))

    def _save_script(self, script_id: str, script: Dict[str, Any]) -> None:
        update_production_status(script)
        with open(self._script_path(script_id), "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)

    def _api_error_text(self, resp: requests.Response) -> str:
        """提取可展示的可灵 API 错误，不输出本地密钥。"""
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

    def _character_context(self, shot: Dict[str, Any], characters: List[Dict[str, Any]]) -> str:
        char_map = {c.get("character_id"): c for c in characters}
        names = []
        refs = []
        for char_id in shot.get("character_ids", []) or []:
            char = char_map.get(char_id)
            if not char:
                continue
            names.append(char.get("name", char_id))
            appearance = char.get("appearance") or {}
            if appearance.get("face"):
                refs.append(str(appearance.get("face")))
            if appearance.get("dress_style"):
                refs.append(str(appearance.get("dress_style")))
            image_url = appearance.get("image_url")
            if image_url:
                shot.setdefault("reference_image_urls", [])
                if image_url not in shot["reference_image_urls"]:
                    shot["reference_image_urls"].append(image_url)
        joined_names = ", ".join(names)
        joined_refs = ", ".join(refs)
        return ", ".join(part for part in [joined_names, joined_refs] if part)

    def _location_context(self, shot: Dict[str, Any], scene: Dict[str, Any]) -> str:
        """把对应 scene 的场景图也注入 shot.reference_image_urls，
        让分镜图的"地点"维度有具体视觉锚点。

        v2-1 的 image 字段只支持单图，所以策略是：
          - 角色图比场景图更影响"人像保真"，先 append 角色图
          - 再 append 场景图（在 _create_image_task 取 [0] 时还是角色优先）
          - 后面如果切到 omni / 多图模型，image_list 会都用上
        """
        location_image_url = scene.get("location_image_url") or ""
        if location_image_url:
            shot.setdefault("reference_image_urls", [])
            if location_image_url not in shot["reference_image_urls"]:
                shot["reference_image_urls"].append(location_image_url)
        location_name = scene.get("location") or ""
        time_of_day = scene.get("time_of_day") or ""
        return ", ".join(p for p in [location_name, time_of_day] if p)

    def _build_prompt(self, script: Dict[str, Any], shot: Dict[str, Any]) -> str:
        style = script.get("style") or (script.get("project_config") or {}).get("style") or "cinematic realistic"
        scene = next((s for s in script.get("scenes", []) if s.get("scene_id") == shot.get("scene_id")), {})
        location = scene.get("location") or shot.get("location_id") or "cinematic location"
        environment = scene.get("environment_prompt") or location
        char_context = self._character_context(shot, script.get("characters", []))
        # 注入场景图作为参考图（v2-1 单图模式时角色图优先，场景图作为补充）
        loc_context = self._location_context(shot, scene)
        asset_refs = shot.get("asset_refs") or {}
        base = shot.get("visual_prompt_for_kling") or shot.get("content_description") or ""
        return (
            f"{base}. Storyboard still frame, {style}, {environment}, "
            f"location: {loc_context}, characters: {char_context}, "
            f"asset refs characters={asset_refs.get('characters', [])}, location={asset_refs.get('location')}, "
            "cinematic composition, coherent character design, vertical 9:16, high detail"
        )

    def _is_omni_model(self) -> bool:
        return "omni" in (self.image_model or "").lower()

    def _create_image_task(
        self,
        prompt: str,
        aspect_ratio: str,
        reference_image_urls: Optional[List[str]] = None,
    ) -> str:
        url = f"{self.api_base}/v1/images/generations"
        payload: Dict[str, Any] = {
            "model_name": self.image_model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "n": 1,
        }
        clean_refs = [u for u in (reference_image_urls or []) if u]
        if self._is_omni_model():
            # v3-omni / image-o1：参考图通过 image_list 字段；强烈建议同时配 element_list（控制台主体），
            # 这里只能传 image_list，v3-omni 在某些账号下仍会回 1201。
            if clean_refs:
                payload["image_list"] = [{"image": u} for u in clean_refs[:5]]
                payload.setdefault("aspect_ratio", "auto")
        else:
            # v2 系列（kling-v1/v1-5/v2/v2-new/v2-1）：参考图用单张 image 字段；
            # 多张参考时只取第一张（v2 不支持数组）。
            if clean_refs:
                payload["image"] = clean_refs[0]
                payload["image_fidelity"] = 0.55

        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        except requests.RequestException as e:
            raise StoryboardGenerationError(f"分镜图任务创建请求失败：{e}") from e

        if resp.status_code != 200:
            raise StoryboardGenerationError(
                f"分镜图任务创建失败：HTTP {resp.status_code}，{self._api_error_text(resp)}"
            )

        try:
            data = resp.json()
        except Exception as e:
            raise StoryboardGenerationError(f"分镜图任务创建返回非 JSON：{resp.text[:300]}") from e

        code = data.get("code")
        if code not in (None, 0, "0"):
            msg = data.get("message") or data.get("msg") or data.get("error") or str(data)[:300]
            raise StoryboardGenerationError(
                f"分镜图任务创建失败：code={code}; message={msg}; request_id={data.get('request_id')}"
            )

        task_id = data.get("data", {}).get("task_id") or data.get("task_id") or ""
        if not task_id:
            raise StoryboardGenerationError(f"分镜图任务创建成功但未返回 task_id：{str(data)[:300]}")
        return task_id

    def _poll_image_task(self, task_id: str, max_wait: int = 180) -> str:
        # 查询路径跟模型走：omni 系列 → /v1/images/omni-image/{id}；v2.x → /v1/images/generations/{id}
        if self._is_omni_model():
            url = f"{self.api_base}/v1/images/omni-image/{task_id}"
        else:
            url = f"{self.api_base}/v1/images/generations/{task_id}"
        start = time.time()
        last_error = ""
        last_status = ""
        while time.time() - start < max_wait:
            try:
                resp = requests.get(url, headers=self._headers(), timeout=15)
            except requests.RequestException as e:
                last_error = f"轮询请求异常：{e}"
                print(f"[Storyboard] {last_error}")
                time.sleep(5)
                continue

            if resp.status_code != 200:
                last_error = f"轮询失败 HTTP {resp.status_code}，{self._api_error_text(resp)}"
                print(f"[Storyboard] {last_error}")
                # 4xx 一般是终态错误（鉴权/参数/任务不存在），直接抛
                if resp.status_code in (400, 401, 403, 404):
                    raise StoryboardGenerationError(f"分镜图任务 {task_id} {last_error}")
                time.sleep(5)
                continue

            try:
                body = resp.json()
            except Exception:
                last_error = f"轮询返回非 JSON：{resp.text[:300]}"
                time.sleep(5)
                continue

            data = body.get("data", body)
            status = (data.get("task_status") or data.get("status") or "").lower()
            last_status = status or last_status
            images = data.get("task_result", {}).get("images") or data.get("images") or []
            if status in {"succeed", "success", "completed"}:
                if images:
                    first = images[0]
                    return first.get("url") or first.get("image_url") or ""
                raise StoryboardGenerationError(f"分镜图任务 {task_id} 已成功，但返回结果里没有图片 URL。")
            if status in {"failed", "fail"}:
                msg = data.get("task_status_msg") or data.get("error_message") or body.get("message") or str(body)[:300]
                raise StoryboardGenerationError(f"分镜图任务失败：{msg}")
            if status:
                print(f"[Storyboard] 任务 {task_id} 状态：{status}")
            time.sleep(5)

        suffix = f"最后状态：{last_status}" if last_status else last_error or "无状态返回"
        raise StoryboardGenerationError(
            f"分镜图任务超时：{task_id}，等待 {max_wait} 秒仍未完成。{suffix}"
        )

    def generate_shot_storyboard(self, script_id: str, shot_id: str, force: bool = False) -> str:
        script = self._load_script(script_id)
        shot = find_shot(script, shot_id)
        if not shot:
            raise ValueError(f"镜头不存在: {shot_id}")
        if shot.get("storyboard_image_url") and not force:
            return shot["storyboard_image_url"]
        if not self._has_credentials():
            raise StoryboardGenerationError("缺少 KLING_AK/KLING_SK，无法调用可灵生成分镜图。")

        aspect = (script.get("project_config") or {}).get("aspect_ratio", "9:16")
        prompt = self._build_prompt(script, shot)
        # _build_prompt 内部已经把角色 image_url 收集进 shot.reference_image_urls
        ref_urls = list(shot.get("reference_image_urls") or [])
        task_id = self._create_image_task(prompt, aspect, reference_image_urls=ref_urls)
        image_url = self._poll_image_task(task_id)
        if not image_url:
            raise StoryboardGenerationError(f"分镜图任务 {task_id} 已结束，但没有返回图片 URL。")

        if force:
            shot["retry_count"] = int(shot.get("retry_count") or 0) + 1
        mark_shot_storyboard(shot, image_url)
        shot["storyboard_prompt"] = prompt
        self._save_script(script_id, script)
        return image_url

    def generate_all(self, script_id: str, shot_ids: Optional[List[str]] = None, force: bool = False) -> Dict[str, str]:
        script = self._load_script(script_id)
        selected = {str(s) for s in (shot_ids or []) if s}
        results: Dict[str, str] = {}
        for _scene, shot in list(self._iter_target_shots(script, selected, force)):
            shot_id = str(shot.get("shot_id"))
            results[shot_id] = self.generate_shot_storyboard(script_id, shot_id, force=force)
        return results

    def _iter_target_shots(self, script: Dict[str, Any], selected: set, force: bool):
        for scene, shot in ((s, sh) for s in script.get("scenes", []) for sh in s.get("shots", [])):
            shot_id = str(shot.get("shot_id") or "")
            shot_number = str(shot.get("shot_number") or "")
            if selected and shot_id not in selected and shot_number not in selected:
                continue
            if shot.get("storyboard_image_url") and not force:
                continue
            yield scene, shot
