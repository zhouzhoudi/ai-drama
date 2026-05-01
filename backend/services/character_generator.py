"""
角色图生成服务

后端模型分工：
  - 角色图生成使用可灵 Kling 图像生成 API
  - 鉴权使用 KLING_AK + KLING_SK 动态生成 JWT（HS256）

注意：这个模块不能静默吞错。角色图生成失败时必须抛出可读错误，
由 main.py 写入任务状态，再由前端轮询展示到对话框。
"""

import os
import json
import time
import requests
from pathlib import Path
from typing import Any, Dict, Optional

from .storage import get_storage
from .kling_auth import kling_auth_headers, _load_kling_credential


class CharacterGenerationError(RuntimeError):
    """角色图生成失败，message 可直接展示给用户。"""


class CharacterGenerator:
    """角色图生成器（可灵 Kling，AK/SK JWT 鉴权）"""

    def __init__(self):
        self.api_base = os.environ.get("KLING_API_BASE", "https://api.klingai.com").rstrip("/")
        # 角色图是纯文生图（无主体/参考图），不能用 v3-omni（v3-omni 必须带 element_list/image_list）。
        # 默认走 v2 系列，查询路径也对应 /v1/images/generations/{id}。
        # 如果想跟分镜共用 KLING_IMAGE_MODEL 也兼容（fallback）。
        self.image_model = (
            os.environ.get("KLING_CHARACTER_IMAGE_MODEL")
            or os.environ.get("KLING_IMAGE_MODEL")
            or "kling-v2-1"
        )
        self.max_wait = int(os.environ.get("KLING_IMAGE_MAX_WAIT", "180") or 180)
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

    # ------------------ 生成可灵图 prompt ------------------
    @staticmethod
    def _as_text(value: Any, default: str = "") -> str:
        """把任意类型字段安全转成可拼 prompt 的字符串。

        本地 LLM 经常把 personality / appearance.face 这类字段输出成 list（关键词
        数组）甚至 dict，直接 .strip() 会炸。这里统一兜底：
          - None -> default
          - str  -> strip
          - list/tuple -> 逗号拼接非空元素
          - dict -> 取 'text'/'value' 字段，否则 JSON 序列化
          - 其他 -> str()
        """
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, tuple)):
            parts = [CharacterGenerator._as_text(v) for v in value]
            return ", ".join(p for p in parts if p)
        if isinstance(value, dict):
            for key in ("text", "value", "description", "name"):
                if value.get(key):
                    return CharacterGenerator._as_text(value.get(key), default)
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value).strip()

    def _gender_phrase(self, gender: Any) -> str:
        g = self._as_text(gender).lower()
        return {
            "female": "woman",
            "male": "man",
            "girl": "young woman",
            "boy": "young man",
            "女": "woman",
            "男": "man",
        }.get(g, "person")

    def _build_full_prompt(self, character: Dict, fallback_style: str = "cinematic realistic drama, photorealistic, 8k") -> str:
        """把 character 各字段拼成一个**信息密度高**的可灵 prompt。

        即使 LLM 只给了较短的 image_prompt，我们也会把 appearance / age_range /
        ethnicity / personality 等中文字段补到 prompt 末尾，让出图更稳定一致。
        前面是英文骨架，后面追加中文细节作为额外指引（可灵 v2-1 对中英文混合理解 OK）。
        """
        if not isinstance(character, dict):
            return ""

        name = self._as_text(character.get("name"))
        gender = self._gender_phrase(character.get("gender"))
        age = self._as_text(character.get("age_range"))
        ethnicity = self._as_text(character.get("ethnicity"), default="East Asian Chinese") or "East Asian Chinese"
        personality = self._as_text(character.get("personality"))
        background = self._as_text(character.get("background"))

        appearance = character.get("appearance") or {}
        if not isinstance(appearance, dict):
            appearance = {}
        face = self._as_text(appearance.get("face"))
        hair = self._as_text(appearance.get("hair"))
        body = self._as_text(appearance.get("body"))
        dress = self._as_text(appearance.get("dress_style"))
        marks = self._as_text(appearance.get("distinguishing_features"))

        base_prompt = self._as_text(character.get("image_prompt"))

        # 英文技术骨架，保证镜头/光线/画质拍摄属性稳定。
        head_parts = [
            "cinematic portrait",
            f"of a {age + ' year old' if age and age[0].isdigit() else age} {ethnicity} {gender}".strip(),
        ]
        head = ", ".join(p for p in head_parts if p and p.strip(", "))

        # 中英混合的角色细节段，**只把模型已经写过的细节往里堆**，避免空洞。
        detail_parts = []
        if face: detail_parts.append(f"face: {face}")
        if hair: detail_parts.append(f"hair: {hair}")
        if body: detail_parts.append(f"body: {body}")
        if dress: detail_parts.append(f"outfit: {dress}")
        if marks: detail_parts.append(f"distinguishing: {marks}")
        if personality: detail_parts.append(f"vibe: {personality}")
        details = ", ".join(detail_parts)

        # 通用拍摄技术参数（保证一致性 / 高质量出图）。
        tech = (
            "medium close-up, eye level, looking at camera, "
            "soft cinematic side lighting, gentle key light, shallow depth of field, "
            "neutral studio background, "
            "photorealistic, high detail skin texture, sharp focus on face, "
            "8k, film grain, "
            f"{fallback_style}"
        )

        # LLM 给的 image_prompt 优先放在前面（如果有），否则用上面拼好的 head。
        prompt_parts = []
        if base_prompt:
            prompt_parts.append(base_prompt)
        if head:
            prompt_parts.append(head)
        if details:
            prompt_parts.append(details)
        prompt_parts.append(tech)

        full = ", ".join(p for p in prompt_parts if p)
        # 去重相邻空段、压缩多余空白。
        full = " ".join(full.split())
        # 可灵单 prompt 上限保守裁到 1500 字符。
        if len(full) > 1500:
            full = full[:1500]
        return full

    def _api_error_text(self, resp: requests.Response) -> str:
        """提取可展示的 API 错误，不输出任何本地密钥。"""
        try:
            body = resp.json()
            if isinstance(body, dict):
                parts = []
                for key in ["code", "message", "msg", "error", "request_id"]:
                    if body.get(key) is not None:
                        parts.append(f"{key}={body.get(key)}")
                data = body.get("data")
                if isinstance(data, dict):
                    for key in ["task_status", "task_status_msg", "error_message"]:
                        if data.get(key) is not None:
                            parts.append(f"{key}={data.get(key)}")
                if parts:
                    return "; ".join(str(p) for p in parts)[:600]
        except Exception:
            pass
        return (resp.text or "").replace("\n", " ")[:600]

    # ------------------ 同步入口（被 main.async_generate_characters 调用） ------------------
    def generate_character(
        self,
        script_id: str,
        character_id: str,
        prompt: str,
        reference_image_url: Optional[str] = None,
    ) -> str:
        """
        同步生成接口：
          - 调可灵任务接口
          - 轮询完成
          - 把 image_url 写入 script.json
          - 返回最终 URL

        Args:
            reference_image_url: 用户上传的角色参考图（公开 URL）。
                提供时走 v2-1 image-to-image，可灵会以这张图为蓝本生成；
                不提供时退化到纯文生图。
        """
        if not self._has_credentials():
            raise CharacterGenerationError("缺少 KLING_AK/KLING_SK，无法调用可灵生成角色图。")
        if not (prompt or "").strip():
            raise CharacterGenerationError(f"角色 {character_id} 缺少 image_prompt，无法生成角色图。")

        task_id = self._create_image_task(prompt, reference_image_url=reference_image_url)
        image_url = self._poll_image_task(task_id, max_wait=self.max_wait)
        if not image_url:
            raise CharacterGenerationError(f"可灵任务 {task_id} 已结束，但没有返回图片 URL。")
        self._save_url_into_script(
            script_id,
            character_id,
            image_url,
            prompt,
            reference_image_url=reference_image_url,
        )
        return image_url

    # ------------------ 异步入口（FastAPI 路由调用） ------------------
    async def generate_character_image(
        self,
        script_id: str,
        character_id: str,
        prompt: Optional[str] = None,
    ) -> Dict:
        script_path = self.data_dir / script_id / "script.json"
        if not script_path.exists():
            raise FileNotFoundError(f"剧本不存在: {script_id}")

        with open(script_path, encoding="utf-8") as f:
            script = json.load(f)

        character = None
        for char in script.get("characters", []):
            if char.get("character_id") == character_id or char.get("name") == character_id:
                character = char
                break
        if not character:
            raise ValueError(f"角色不存在: {character_id}")

        # 用户没显式覆盖 prompt 时，**把所有 character 字段拼成完整 prompt**，
        # 而不是只用 image_prompt 这一个字段。否则面部/发型/穿着/身材等关键
        # 信息会丢，可灵会随机出一张"风格类似但人物不一致"的图。
        style_hint = script.get("style") or "cinematic realistic drama, photorealistic, 8k"
        if not prompt:
            prompt = self._build_full_prompt(character, fallback_style=style_hint)

        # 用户上传的参考图：优先取 reference_image_urls 里第一张（最新一张）。
        ref_urls = [u for u in (character.get("reference_image_urls") or []) if u]
        reference_image_url = ref_urls[0] if ref_urls else None

        image_url = self.generate_character(
            script_id,
            character_id,
            prompt,
            reference_image_url=reference_image_url,
        )

        return {
            "character_id": character_id,
            "image_url": image_url,
            "prompt_used": prompt,
            "reference_image_url": reference_image_url,
            "engine": "kling",
            "model": self.image_model,
        }

    # ------------------ 可灵 API 调用细节 ------------------
    # 角色图走 v2 系列文生图端点：
    #   创建：POST /v1/images/generations
    #   查询：GET  /v1/images/generations/{id}
    # 注意：v3-omni 必须带 element_list/image_list 才能创建任务，不适合纯文生图。
    def _create_image_task(self, prompt: str, reference_image_url: Optional[str] = None) -> str:
        url = f"{self.api_base}/v1/images/generations"
        payload: Dict = {
            "model_name": self.image_model,
            "prompt": prompt,
            "aspect_ratio": "3:4",
            "n": 1,
        }
        # v2-1 文生图与图生图共用同一端点，多塞 image 字段即触发 image-to-image。
        # image_fidelity=0.5 是给"角色参考"留够风格化空间，又能保留主要五官；
        # 用户后续可以在前端再细调（暂不暴露）。
        if reference_image_url:
            payload["image"] = reference_image_url
            payload["image_fidelity"] = 0.5

        print(
            f"[Kling Image] 创建任务 model={self.image_model} "
            f"prompt={prompt[:50]}... ref={'yes' if reference_image_url else 'no'}"
        )
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        except requests.RequestException as e:
            raise CharacterGenerationError(f"可灵图片任务创建请求失败：{e}") from e

        if resp.status_code != 200:
            raise CharacterGenerationError(
                f"可灵图片任务创建失败：HTTP {resp.status_code}，{self._api_error_text(resp)}"
            )

        try:
            data = resp.json()
        except Exception as e:
            raise CharacterGenerationError(f"可灵图片任务创建返回的不是 JSON：{resp.text[:300]}") from e

        code = data.get("code")
        if code not in (None, 0, "0"):
            msg = data.get("message") or data.get("msg") or data.get("error") or str(data)[:300]
            raise CharacterGenerationError(f"可灵图片任务创建失败：code={code}，{msg}")

        task_id = (
            data.get("data", {}).get("task_id")
            or data.get("task_id")
            or ""
        )
        if not task_id:
            raise CharacterGenerationError(f"可灵图片任务创建成功但未返回 task_id：{str(data)[:300]}")
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
                print(f"[Kling Image] {last_error}")
                time.sleep(5)
                continue

            if r.status_code != 200:
                last_error = f"轮询失败 HTTP {r.status_code}，{self._api_error_text(r)}"
                print(f"[Kling Image] {last_error}")
                if r.status_code in (400, 401, 403, 404):
                    raise CharacterGenerationError(f"可灵图片任务 {task_id} {last_error}")
                time.sleep(5)
                continue

            try:
                body = r.json()
            except Exception as e:
                last_error = f"轮询返回不是 JSON：{r.text[:300]}"
                print(f"[Kling Image] {last_error}")
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
                raise CharacterGenerationError(f"可灵图片任务 {task_id} 已成功，但返回结果里没有图片 URL。")

            if status in ("failed", "fail"):
                msg = data.get("task_status_msg") or data.get("error_message") or body.get("message") or str(body)[:300]
                raise CharacterGenerationError(f"可灵图片任务失败：{msg}")

            if status:
                print(f"[Kling Image] 任务 {task_id} 状态：{status}")
            time.sleep(5)

        suffix = f"最后状态：{last_status}" if last_status else last_error or "无状态返回"
        raise CharacterGenerationError(f"可灵图片任务超时：{task_id}，等待 {max_wait} 秒仍未完成。{suffix}")

    # ------------------ 写回剧本 ------------------
    def _save_url_into_script(
        self,
        script_id: str,
        character_id: str,
        image_url: str,
        prompt: str,
        reference_image_url: Optional[str] = None,
    ) -> None:
        script_path = self.data_dir / script_id / "script.json"
        if not script_path.exists():
            return

        try:
            with open(script_path, encoding="utf-8") as f:
                script = json.load(f)

            for char in script.get("characters", []):
                if char.get("character_id") == character_id or char.get("name") == character_id:
                    char.setdefault("appearance", {})
                    char["appearance"]["image_url"] = image_url
                    char["appearance"]["image_prompt_used"] = prompt
                    if reference_image_url:
                        char["appearance"]["reference_image_used"] = reference_image_url
                    break

            with open(script_path, "w", encoding="utf-8") as f:
                json.dump(script, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise CharacterGenerationError(f"角色图已生成，但写回 script.json 失败：{e}") from e

    # ------------------ 兼容旧接口（占位实现） ------------------
    async def register_kling_element(self, script_id: str, character_id: str) -> Dict:
        return {
            "character_id": character_id,
            "kling_element_id": f"elem_{character_id}",
            "message": "需要先在可灵控制台手动创建主体",
        }
