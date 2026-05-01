"""
Production state helpers for the shot-level director workflow.

The project still stores data in script.json, but all services normalize the
same shape before reading or writing so assets, shot status and review state
stay consistent across script, storyboard, video and merge stages.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


SCRIPT_READY = "script_ready"
ASSETS_READY = "assets_ready"
STORYBOARD_READY = "storyboard_ready"
VIDEO_READY = "video_ready"
FINAL_READY = "final_ready"


def now_iso() -> str:
    return datetime.now().isoformat()


def iter_shots(script: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for scene in script.get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            yield scene, shot


def find_shot(script: Dict[str, Any], shot_id: str) -> Optional[Dict[str, Any]]:
    target = str(shot_id)
    for _scene, shot in iter_shots(script):
        if str(shot.get("shot_id")) == target or str(shot.get("shot_number")) == target:
            return shot
    return None


def _dialogue_character_id(shot: Dict[str, Any]) -> str:
    dialogue = shot.get("dialogue") or {}
    if isinstance(dialogue, dict):
        return str(dialogue.get("character_id") or dialogue.get("character") or "")
    return ""


def _slug(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", str(value or "")).strip("_")
    return text or fallback


def _normalize_character(char: Dict[str, Any], idx: int, style: str) -> Dict[str, Any]:
    char.setdefault("character_id", f"char_{idx}")
    char.setdefault("asset_id", char.get("character_id"))
    char.setdefault("name", f"角色{idx}")
    char.setdefault("gender", "other")
    char.setdefault("age_range", "")
    char.setdefault("ethnicity", "East Asian Chinese")
    char.setdefault("personality", "")
    char.setdefault("background", "")
    # 用户上传的角色参考图 URL 列表（用于驱动 image-to-image 模式）。
    # 历史项目可能没这个字段，统一补空数组，避免下游 .get(...) 出 None。
    refs = char.get("reference_image_urls")
    if not isinstance(refs, list):
        char["reference_image_urls"] = []

    appearance = char.setdefault("appearance", {})
    appearance.setdefault("face", "")
    appearance.setdefault("hair", "")
    appearance.setdefault("body", "")
    appearance.setdefault("dress_style", "")
    appearance.setdefault("distinguishing_features", "")

    if not char.get("image_prompt"):
        char["image_prompt"] = f"cinematic portrait of {char.get('name')}, {style}, high quality"
    char["asset_status"] = "ready" if appearance.get("image_url") else char.get("asset_status", "pending_image")
    char.setdefault("consistency_tags", [
        str(v)
        for v in [
            char.get("name"),
            char.get("age_range"),
            char.get("ethnicity"),
            appearance.get("face"),
            appearance.get("hair"),
            appearance.get("body"),
            appearance.get("dress_style"),
            appearance.get("distinguishing_features"),
        ]
        if v
    ])
    return char


def _safe_text(value: Any, default: str = "") -> str:
    """把任意类型字段安全转成字符串（list/dict/None 等都能兜住）。

    LLM 在中文输出里偶尔会把 personality / shot_type / dialogue.text 等字段写成
    数组或字典，下游再 .strip() 就会抛 'list/dict has no attribute strip'。
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return ", ".join(_safe_text(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        for key in ("text", "value", "description", "name"):
            if value.get(key):
                return _safe_text(value.get(key), default)
        return str(value)
    return str(value).strip()


def _build_shot_text(
    shot: Dict[str, Any],
    scene_location_name: str,
    character_name_by_id: Dict[str, str],
) -> str:
    """LLM 没生成 shot_text 时，用 shot_type / 场景 / 描述 / 对白拼一个简易的剧本格式文本。

    最终展示形如：
    "中景，[@Time-Life大堂] 内，<content_description>。{[@双喜]说：\"嘿。\"}"
    """
    shot_type = _safe_text(shot.get("shot_type"), default="中景") or "中景"
    location_tag = f"[@{scene_location_name}]" if scene_location_name else ""
    description = _safe_text(shot.get("content_description"))

    # 把描述里的角色名前补 [@xxx]（如果还没标注）。
    for char_id, char_name in character_name_by_id.items():
        if not char_name:
            continue
        marker = f"[@{char_name}]"
        if marker in description:
            continue
        # 简单替换：仅替换"独立"出现的角色名（前后是中文/标点），避免误伤更长的姓名。
        if char_name in description:
            description = description.replace(char_name, marker, 1)

    head = ", ".join(p for p in [shot_type, location_tag] if p)
    body = description or (shot.get("scene_summary") or "")

    dialogue = shot.get("dialogue") or {}
    if isinstance(dialogue, dict):
        dialogue_text = _safe_text(dialogue.get("text")) or _safe_text(dialogue.get("tts_text"))
        speaker_id = _safe_text(dialogue.get("character_id"))
        speaker = character_name_by_id.get(speaker_id) or speaker_id or ""
    else:
        dialogue_text = _safe_text(dialogue)
        speaker = ""

    parts = []
    if head and body:
        parts.append(f"{head}，{body}")
    elif head:
        parts.append(head)
    elif body:
        parts.append(body)

    if dialogue_text:
        if speaker:
            parts.append(f'{{[@{speaker}]说："{dialogue_text}"}}')
        else:
            parts.append(f'{{说："{dialogue_text}"}}')

    return " ".join(parts).strip()


def _normalize_shot(
    shot: Dict[str, Any],
    shot_counter: int,
    scene_id: str,
    character_ids: List[str],
    location_id: str,
    style: str,
) -> Dict[str, Any]:
    shot.setdefault("shot_id", f"shot_{shot_counter}")
    shot.setdefault("shot_number", shot_counter)
    shot.setdefault("duration", 5)
    shot.setdefault("scene_id", scene_id)

    if isinstance(shot.get("dialogue"), str):
        text = shot.get("dialogue") or ""
        shot["dialogue"] = {"character_id": "", "text": text, "emotion": "", "tts_text": text} if text else {}
    elif shot.get("dialogue") is None:
        shot["dialogue"] = {}

    if not shot.get("character_ids"):
        char_id = _dialogue_character_id(shot)
        shot["character_ids"] = [char_id] if char_id else character_ids[:1]
    if not shot.get("location_id"):
        shot["location_id"] = location_id

    shot.setdefault("prop_ids", [])
    shot["asset_refs"] = {
        "characters": list(dict.fromkeys(shot.get("character_ids") or [])),
        "location": shot.get("location_id"),
        "props": list(dict.fromkeys(shot.get("prop_ids") or [])),
    }

    shot.setdefault("reference_image_urls", [])
    if shot.get("reference_image_url") and shot["reference_image_url"] not in shot["reference_image_urls"]:
        shot["reference_image_urls"].append(shot["reference_image_url"])

    if not shot.get("visual_prompt_for_kling"):
        content = shot.get("content_description") or ""
        shot["visual_prompt_for_kling"] = f"{content}, {style}, cinematic, vertical 9:16"
    shot.setdefault("negative_prompt_for_kling", "blurry, low quality, deformed face, bad hands")

    has_video = bool(shot.get("generated_video_url") or shot.get("video_url"))
    has_storyboard = bool(shot.get("storyboard_image_url") or shot.get("reference_image_url"))
    if has_video:
        if shot.get("status") not in {"review_approved", "needs_regen"}:
            shot["status"] = "video_ready"
    elif has_storyboard:
        if shot.get("status") not in {"storyboard_approved", "storyboard_failed"}:
            shot["status"] = "storyboard_ready"
    else:
        shot.setdefault("status", "pending_storyboard")
    shot["storyboard_status"] = "ready" if has_storyboard else shot.get("storyboard_status", "pending")
    shot["video_status"] = "ready" if has_video else shot.get("video_status", "pending")
    shot.setdefault("review_status", "unchecked")
    shot.setdefault("review_notes", "")
    shot.setdefault("auto_review", {})
    shot.setdefault("retry_count", 0)
    shot.setdefault("updated_at", now_iso())
    return shot


def normalize_script(script: Dict[str, Any]) -> Dict[str, Any]:
    style = script.get("style") or (script.get("project_config") or {}).get("style") or "写实电影风"
    script.setdefault("production_status", SCRIPT_READY)
    script.setdefault("assets", {})
    script.setdefault("characters", [])
    script.setdefault("scenes", [])
    script.setdefault("timeline", {"shot_order": [], "final_video_url": ""})
    script.setdefault("updated_at", now_iso())

    characters = [
        _normalize_character(char, idx, style)
        for idx, char in enumerate(script.get("characters", []) or [], start=1)
    ]
    script["characters"] = characters
    character_ids = [c.get("character_id") for c in characters if c.get("character_id")]
    # 角色 id->name 映射，给 shot_text 兜底渲染用 [@角色名] 标签。
    character_name_by_id = {
        c.get("character_id"): c.get("name") or c.get("character_id")
        for c in characters
        if c.get("character_id")
    }
    character_assets = []
    for char in characters:
        appearance = char.get("appearance") or {}
        character_assets.append({
            "asset_id": char.get("asset_id") or char.get("character_id"),
            "type": "character",
            "character_id": char.get("character_id"),
            "name": char.get("name"),
            "description": ", ".join(char.get("consistency_tags") or []),
            "image_url": appearance.get("image_url", ""),
            "prompt": char.get("image_prompt", ""),
            "status": "ready" if appearance.get("image_url") else "pending_image",
        })

    location_assets = []
    prop_assets = []
    seen_locations = set()
    seen_props = set()

    # LLM 输出和旧项目数据里，常见问题是每个 scene 都从 shot_1/shot_2
    # 重新编号。后续确认、重生成、状态写回都按 shot_id 定位；重复 ID
    # 会导致只命中第一个镜头。所以这里统一在 normalize 阶段修复：
    # - 已经唯一的自定义 shot_id 尽量保持不变，避免打断已有素材引用；
    # - 缺失/重复的 shot_id 改成全局顺序 shot_1..shot_N；
    # - 被改名的旧 ID 记录到 legacy_shot_ids，方便排查和兼容展示。
    existing_shot_ids = [
        str(shot.get("shot_id") or "").strip()
        for scene in script.get("scenes", []) or []
        for shot in scene.get("shots", []) or []
        if isinstance(shot, dict)
    ]
    shot_id_counts = Counter(sid for sid in existing_shot_ids if sid)
    assigned_shot_ids = set()

    def next_available_shot_id(start_at: int, allow_existing: str = "") -> str:
        idx = max(start_at, 1)
        while True:
            candidate = f"shot_{idx}"
            if candidate not in assigned_shot_ids and (candidate == allow_existing or shot_id_counts.get(candidate, 0) == 0):
                return candidate
            idx += 1

    shot_counter = 1
    shot_order = []
    cumulative_seconds = 0  # 用于给每个 shot 自动生成 0-3s / 3-6s 这种时间码
    for scene_idx, scene in enumerate(script.get("scenes", []) or [], start=1):
        scene.setdefault("scene_id", f"scene_{scene_idx}")
        scene.setdefault("scene_number", f"第{scene_idx}场")
        scene.setdefault("location", "未指定地点")
        location_id = scene.setdefault("location_id", f"loc_{scene_idx}")
        # 场景图：location_image_url 是当前生成结果；reference_image_urls 是用户上传的参考图。
        scene.setdefault("location_image_url", "")
        if not isinstance(scene.get("reference_image_urls"), list):
            scene["reference_image_urls"] = []
        scene_location_name = scene.get("location") or "未指定地点"
        scene.setdefault("scene_asset_id", location_id)
        scene.setdefault("environment_prompt", f"{scene.get('location')}, {style}, cinematic environment, vertical 9:16")
        if location_id not in seen_locations:
            location_assets.append({
                "asset_id": location_id,
                "type": "location",
                "name": scene.get("location"),
                "description": scene.get("scene_summary", ""),
                "prompt": scene.get("environment_prompt", ""),
                "image_url": scene.get("location_image_url") or "",
                "status": "ready" if scene.get("location_image_url") else "pending_image",
            })
            seen_locations.add(location_id)

        scene.setdefault("shots", [])
        for shot in scene.get("shots", []) or []:
            if not shot.get("prop_ids"):
                prop_ids = []
                for prop in shot.get("props", []) or []:
                    prop_name = prop.get("name") if isinstance(prop, dict) else str(prop)
                    prop_id = f"prop_{_slug(prop_name, str(len(seen_props) + 1))}"
                    prop_ids.append(prop_id)
                    if prop_id not in seen_props:
                        prop_assets.append({
                            "asset_id": prop_id,
                            "type": "prop",
                            "name": prop_name,
                            "description": prop.get("description", "") if isinstance(prop, dict) else "",
                            "status": "pending_image",
                        })
                        seen_props.add(prop_id)
                if prop_ids:
                    shot["prop_ids"] = prop_ids
            original_shot_id = str(shot.get("shot_id") or "").strip()
            is_duplicate_or_missing = (
                not original_shot_id
                or original_shot_id in assigned_shot_ids
                or shot_id_counts.get(original_shot_id, 0) > 1
            )
            if is_duplicate_or_missing:
                # Preserve the first occurrence of shot_N when possible; later
                # duplicates are promoted to the next globally free shot_N.
                allow_existing = original_shot_id if original_shot_id.startswith("shot_") and original_shot_id not in assigned_shot_ids else ""
                new_shot_id = next_available_shot_id(shot_counter, allow_existing=allow_existing)
                if original_shot_id and original_shot_id != new_shot_id:
                    legacy_ids = shot.setdefault("legacy_shot_ids", [])
                    if original_shot_id not in legacy_ids:
                        legacy_ids.append(original_shot_id)
                    shot.setdefault("legacy_shot_id", original_shot_id)
                shot["shot_id"] = new_shot_id
            else:
                shot["shot_id"] = original_shot_id
            # shot_number 是展示/排序字段，也强制全局连续，避免每场都 1/2。
            shot["shot_number"] = shot_counter

            _normalize_shot(shot, shot_counter, scene["scene_id"], character_ids, location_id, style)

            # 时间码 0-3s / 3-6s 这种，按 shot.duration 累加。
            shot_duration = int(shot.get("duration") or 5)
            shot["time_code"] = f"{cumulative_seconds}-{cumulative_seconds + shot_duration}s"
            cumulative_seconds += shot_duration

            # shot_text 兜底：LLM 没给，就用 shot 字段拼一个用户喜欢的剧本格式。
            existing_shot_text = (shot.get("shot_text") or "").strip()
            if not existing_shot_text:
                shot["shot_text"] = _build_shot_text(
                    shot,
                    scene_location_name,
                    character_name_by_id,
                )
            assigned_shot_ids.add(str(shot.get("shot_id")))
            shot_order.append(shot.get("shot_id"))
            shot_counter += 1

    assets = script.setdefault("assets", {})
    assets["characters"] = character_assets
    assets["locations"] = location_assets
    existing_props = assets.get("props") or []
    existing_by_id = {p.get("asset_id"): p for p in existing_props if isinstance(p, dict)}
    for prop in prop_assets:
        existing_by_id.setdefault(prop.get("asset_id"), prop)
    assets["props"] = list(existing_by_id.values())
    assets.setdefault("style", {"name": style, "status": "ready"})

    timeline = script.setdefault("timeline", {})
    timeline["shot_order"] = shot_order
    timeline.setdefault("final_video_url", script.get("final_video_url", ""))

    update_production_status(script)
    return script


def update_production_status(script: Dict[str, Any]) -> str:
    shots = [shot for _scene, shot in iter_shots(script)]
    if script.get("final_video_url") or (script.get("timeline") or {}).get("final_video_url"):
        status = FINAL_READY
    elif shots and all(shot.get("generated_video_url") or shot.get("video_url") for shot in shots):
        status = VIDEO_READY
    elif shots and all(shot.get("storyboard_image_url") or shot.get("reference_image_url") for shot in shots):
        status = STORYBOARD_READY
    elif script.get("characters") and all((char.get("appearance") or {}).get("image_url") for char in script.get("characters", [])):
        status = ASSETS_READY
    else:
        status = SCRIPT_READY
    script["production_status"] = status
    script["updated_at"] = now_iso()
    return status


def mark_shot_storyboard(shot: Dict[str, Any], image_url: str) -> None:
    shot["storyboard_image_url"] = image_url
    shot["reference_image_url"] = image_url
    refs = shot.setdefault("reference_image_urls", [])
    if image_url and image_url not in refs:
        refs.insert(0, image_url)
    shot["storyboard_status"] = "ready" if image_url else "failed"
    shot["status"] = "storyboard_ready" if image_url else "storyboard_failed"
    shot["updated_at"] = now_iso()


def mark_shot_confirmed(shot: Dict[str, Any], confirmed: bool = True) -> None:
    shot["storyboard_confirmed"] = confirmed
    if confirmed and (shot.get("storyboard_image_url") or shot.get("reference_image_url")):
        shot["status"] = "storyboard_approved"
    elif not confirmed:
        shot["status"] = "storyboard_ready"
    shot["updated_at"] = now_iso()


def mark_shot_video(shot: Dict[str, Any], video_url: str) -> None:
    if video_url:
        shot["generated_video_url"] = video_url
        shot["video_url"] = video_url
        shot["video_status"] = "ready"
        shot["status"] = "video_ready"
    else:
        shot["video_status"] = "failed"
        shot["status"] = "video_failed"
    shot["updated_at"] = now_iso()


def mark_shot_review(shot: Dict[str, Any], status: str, notes: str = "") -> None:
    shot["review_status"] = status or "unchecked"
    shot["review_notes"] = notes or ""
    if status == "approved" and (shot.get("generated_video_url") or shot.get("video_url")):
        shot["status"] = "review_approved"
    elif status in {"needs_regen", "rejected"}:
        shot["status"] = "needs_regen"
    shot["updated_at"] = now_iso()
