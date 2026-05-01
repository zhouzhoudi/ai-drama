"""
Shot-level review agent.

The reviewer is intentionally conservative: it first applies deterministic
checks, then optionally asks the configured production LLM for a structured
review. If the model is unavailable, the deterministic result is still useful
and does not block the production workflow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .llm_client import llm_chat_json
from .production_state import find_shot, mark_shot_review, normalize_script, update_production_status


class ShotReviewer:
    def __init__(self):
        self.data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")

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

    def _rule_review(self, shot: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[str] = []
        if not (shot.get("generated_video_url") or shot.get("video_url")):
            issues.append("缺少视频结果")
        if not (shot.get("storyboard_image_url") or shot.get("reference_image_url")):
            issues.append("缺少分镜参考图")
        if not shot.get("character_ids"):
            issues.append("缺少角色引用")
        if not shot.get("location_id"):
            issues.append("缺少场景引用")
        if not shot.get("content_description"):
            issues.append("缺少画面描述")

        status = "approved" if not issues else "needs_regen"
        score = 90 if not issues else max(35, 80 - len(issues) * 15)
        return {
            "status": status,
            "score": score,
            "issues": issues,
            "suggestion": "通过基础检查" if not issues else "；".join(issues),
            "source": "rules",
        }

    async def _model_review(self, script: Dict[str, Any], shot: Dict[str, Any], rule_result: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""
你是短剧 AI 审片员。请根据镜头信息判断是否可以进入最终合成。
只输出 JSON，不要解释。

项目信息：
- 标题：{script.get("title")}
- 风格：{script.get("style")}

镜头信息：
{json.dumps({
    "shot_id": shot.get("shot_id"),
    "content_description": shot.get("content_description"),
    "dialogue": shot.get("dialogue"),
    "asset_refs": shot.get("asset_refs"),
    "storyboard_image_url": shot.get("storyboard_image_url"),
    "video_url": shot.get("generated_video_url") or shot.get("video_url"),
    "rule_review": rule_result,
}, ensure_ascii=False, indent=2)}

输出格式：
{{
  "status": "approved 或 needs_regen",
  "score": 0到100的整数,
  "issues": ["问题1"],
  "suggestion": "如果要重做，给一句具体建议"
}}
"""
        try:
            parsed = await llm_chat_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=800,
                timeout=18,
            )
            if parsed.get("status") not in {"approved", "needs_regen"}:
                parsed["status"] = rule_result["status"]
            parsed["source"] = "llm"
            return parsed
        except Exception as e:
            fallback = dict(rule_result)
            fallback["model_error"] = str(e)
            return fallback

    async def review_shot(self, script_id: str, shot_id: str, use_model: bool = True) -> Dict[str, Any]:
        script = self._load_script(script_id)
        shot = find_shot(script, shot_id)
        if not shot:
            raise ValueError(f"镜头不存在: {shot_id}")

        rule_result = self._rule_review(shot)
        result = await self._model_review(script, shot, rule_result) if use_model else rule_result
        status = result.get("status") or rule_result["status"]
        notes = result.get("suggestion") or "；".join(result.get("issues") or [])
        shot["auto_review"] = result
        mark_shot_review(shot, status, notes)
        self._save_script(script_id, script)
        return result

    async def review_all(self, script_id: str, use_model: bool = True) -> Dict[str, Dict[str, Any]]:
        script = self._load_script(script_id)
        results: Dict[str, Dict[str, Any]] = {}
        for scene in script.get("scenes", []) or []:
            for shot in scene.get("shots", []) or []:
                if not (shot.get("generated_video_url") or shot.get("video_url")):
                    continue
                shot_id = str(shot.get("shot_id"))
                results[shot_id] = await self.review_shot(script_id, shot_id, use_model=use_model)
        return results
