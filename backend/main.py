"""
AI 短剧自动生成系统 - 后端调度服务
基于 FastAPI 构建，提供 REST API 接口
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid
import json
import os
import asyncio
from pathlib import Path
from urllib.parse import quote

# ============== 环境变量加载 ==============

def _load_dotenv_file(dotenv_path: Path) -> None:
    """
    轻量加载 .env（避免引入 python-dotenv 依赖）。
    只处理 KEY=VALUE，忽略空行与 # 注释；若环境变量已存在则不覆盖。
    """
    if not dotenv_path.exists():
        return
    try:
        for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as e:
        # 不阻塞服务启动
        print(f"Warning: failed to load dotenv {dotenv_path}: {e}")


# 优先加载项目 backend/.env，其次加载 ~/.hermes/.env
_load_dotenv_file(Path(__file__).parent / ".env")
_load_dotenv_file(Path.home() / ".hermes" / ".env")

# 导入服务模块
from services.script_generator import ScriptGenerator
from services.storage import MinIOStorage
from services.character_generator import CharacterGenerator, CharacterGenerationError
from services.location_generator import LocationGenerator, LocationGenerationError
from services.video_generator import VideoGenerator
from services.audio_generator import AudioGenerator
from services.merger import MediaMerger
from services.storyboard_generator import StoryboardGenerator, StoryboardGenerationError
from services.shot_reviewer import ShotReviewer
from services.production_state import (
    find_shot,
    iter_shots,
    mark_shot_confirmed,
    mark_shot_review,
    mark_shot_video,
    normalize_script,
    update_production_status,
)
from schemas import AgentChatRequest
from services.agent_router import AgentRouter

agent_router = AgentRouter()

# ============== Agent API ==============


async def async_generate_characters(
    script_id: str,
    character_ids: Optional[List[str]] = None,
    force: bool = False,
):
    """后台运行的角色生成任务：逐个生成角色参考图，并持续写回 script.json。

    Args:
        script_id: 剧本 ID。
        character_ids: 仅生成指定角色（character_id 或 name 任一匹配）。None 表示全量。
        force: 即便已有 image_url 也强制重新生成（"重做"语义）。
    """
    import json
    char_gen = CharacterGenerator()
    data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")
    script_file = data_dir / script_id / "script.json"

    target_ids = {str(x) for x in (character_ids or []) if x}

    task = tasks_store.get(script_id)
    if not task:
        task = TaskStatus(
            task_id=script_id,
            status="processing",
            progress=35,
            current_step="正在生成角色图...",
            action="GENERATE_CHARACTERS",
        )
        tasks_store[script_id] = task
    else:
        task.status = "processing"
        task.progress = max(task.progress, 35)
        task.current_step = "正在生成角色图..."
        task.error = None
        task.action = "GENERATE_CHARACTERS"

    try:
        if not script_file.exists():
            raise FileNotFoundError(f"script.json 不存在: {script_id}")

        with open(script_file, "r", encoding="utf-8") as f:
            script_data = normalize_script(json.load(f))

        all_characters = script_data.get("characters", []) or []
        if not all_characters:
            raise ValueError("当前剧本没有角色数据，无法生成角色图。")

        # 若指定了 character_ids，只挑出命中的角色（name 或 character_id 任一匹配）
        if target_ids:
            characters = [
                c for c in all_characters
                if str(c.get("character_id") or "") in target_ids
                or str(c.get("name") or "") in target_ids
            ]
            if not characters:
                raise ValueError(f"指定的角色未找到：{', '.join(sorted(target_ids))}")
        else:
            characters = all_characters

        total = len(characters)
        generated_count = 0
        skipped_count = 0
        failures = []
        # 把所有 character 字段（gender / age / ethnicity / appearance / personality 等）
        # 拼成完整 prompt，避免只用 image_prompt 一个字段，否则面部/发型/穿着这些关键
        # 信息会丢，导致出图与设定不一致。
        style_hint = script_data.get("style") or "cinematic realistic drama, photorealistic, 8k"
        for idx, char in enumerate(characters, start=1):
            character_id = char.get("character_id") or char.get("name") or f"char_{idx}"
            character_name = char.get("name", character_id)
            prompt = char_gen._build_full_prompt(char, fallback_style=style_hint)
            if not prompt:
                prompt = (
                    char.get("image_prompt")
                    or (char.get("appearance") or {}).get("face")
                    or f"cinematic portrait of {character_name}, high quality"
                )
            # 用户在角色卡上传的参考图：取最新一张走 v2-1 image-to-image。
            # 没传就退化成纯文生图（保持原行为）。
            ref_urls = [u for u in (char.get("reference_image_urls") or []) if u]
            ref_url_for_kling = ref_urls[0] if ref_urls else None
            # force=False 时已有图就跳过；force=True 时无视已有图重做。
            if not force and (char.get("appearance") or {}).get("image_url"):
                skipped_count += 1
                task.progress = 35 + int(idx / total * 30)
                continue

            ref_hint = "（已使用上传参考图）" if ref_url_for_kling else ""
            task.current_step = f"正在生成角色图：{character_name}（{idx}/{total}）{ref_hint}"
            try:
                image_url = char_gen.generate_character(
                    script_id,
                    character_id,
                    prompt,
                    reference_image_url=ref_url_for_kling,
                )
                if not image_url:
                    raise CharacterGenerationError("生成器没有返回图片 URL")
                generated_count += 1
                task.progress = 35 + int(idx / total * 30)
                task.current_step = f"角色图已生成：{character_name}（{idx}/{total}）"
            except Exception as item_error:
                msg = f"{character_name}: {item_error}"
                failures.append(msg)
                print(f"[Character Task] {msg}")
                task.error = msg
                task.current_step = f"角色图生成失败：{character_name}"
                break

        if failures:
            raise CharacterGenerationError("；".join(failures))

        with open(script_file, "r", encoding="utf-8") as f:
            script_data = normalize_script(json.load(f))
        update_production_status(script_data)
        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)

        # 关键修复：若调用方明确要求 force（重做），但因故没有任何生成发生，
        # 也算失败而不是 "completed"，否则前端会看到 100% 却没有新图。
        if generated_count == 0:
            if force:
                raise CharacterGenerationError(
                    f"未能重新生成任何角色图（已选 {total} 个），请检查上游 LLM 返回或可灵 API 是否报错。"
                )
            if skipped_count == 0:
                raise CharacterGenerationError("没有生成任何角色图，请检查角色列表和图片提示词。")

        task.status = "completed"
        task.progress = max(task.progress, 65)
        if force:
            task.current_step = f"角色图重做完成：重新生成 {generated_count} 个"
        else:
            task.current_step = f"角色图生成完成：新生成 {generated_count} 个，已存在 {skipped_count} 个"
        task.result = {"script_id": script_id, "generated": generated_count, "skipped": skipped_count, "force": force}
    except Exception as e:
        task.status = "failed"
        task.progress = 100
        task.error = str(e)
        task.current_step = "角色图生成失败"
        print(f"Background character generation failed: {e}")

BASE_DIR = Path("/Users/zhoumi/ai-drama-system")
DATA_DIR = BASE_DIR / "data"
SCRIPTS_DIR = DATA_DIR / "scripts"
OUTPUTS_DIR = DATA_DIR / "outputs"

app = FastAPI(
    title="AI Short Drama API",
    description="AI短剧自动生成系统接口",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============== 数据模型 ==============

class CharacterInput(BaseModel):
    name: str
    gender: str = Field(..., pattern="^(male|female|other)$")
    age_range: Optional[str] = None
    appearance: Optional[Dict[str, str]] = None
    personality: Optional[str] = None

class DramaRequest(BaseModel):
    title: str = Field(..., description="短剧标题")
    theme: str = Field(..., description="题材类型")
    style: str = Field(..., description="风格定位")
    duration: int = Field(default=180, description="预估时长(秒)", ge=60, le=600)
    characters: List[CharacterInput] = Field(default=[], description="角色列表，为空时由AI自动生成")
    description: str = Field(..., description="剧情描述")
    gen_mode: Optional[str] = None
    visual_style: Optional[str] = None
    genre: Optional[str] = None

class TaskStatus(BaseModel):
    task_id: str
    status: str
    progress: int
    current_step: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    # 让前端知道当前后台任务是哪个动作（GENERATE_CHARACTERS / GENERATE_LOCATIONS / ... ）
    # 这样前端可以按 action 弹出对应的"下一步引导"提示。
    action: Optional[str] = None

# ============== 内存存储 (生产环境用数据库) ==============

tasks_store: Dict[str, TaskStatus] = {}
scripts_store: Dict[str, Dict] = {}

# ============== 工具函数 ==============

def get_script_dir(script_id: str) -> Path:
    """获取剧本目录"""
    return SCRIPTS_DIR / script_id

def ensure_dirs():
    """确保目录存在"""
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    (SCRIPTS_DIR / "schemas").mkdir(parents=True, exist_ok=True)


def _safe_media_file(root: Path, relative_path: str) -> Path:
    """Resolve a user-requested media path while blocking ../ traversal."""
    root = root.resolve()
    target = (root / relative_path).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=403, detail="非法文件路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


def public_media_url(script_id: str, value: str) -> str:
    """Convert local generated files to browser-readable API URLs.

    Generators/merger often return Mac local paths like /Users/.../video.mp4.
    Browsers cannot load those directly, so script.json should store a relative
    HTTP endpoint while backend merge logic can still infer the local file path.
    """
    raw = str(value or "").strip()
    if not raw or raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/api/files/") or raw.startswith("/api/outputs/"):
        return raw
    try:
        path = Path(raw).expanduser().resolve()
    except Exception:
        return raw

    script_root = get_script_dir(script_id).resolve()
    try:
        rel = path.relative_to(script_root)
        return f"/api/files/{script_id}/{quote(rel.as_posix())}"
    except ValueError:
        pass

    output_root = (OUTPUTS_DIR / script_id).resolve()
    try:
        rel = path.relative_to(output_root)
        return f"/api/outputs/{script_id}/{quote(rel.as_posix())}"
    except ValueError:
        return raw

# ============== API 路由 ==============

@app.get("/")
async def root():
    return {"message": "AI Short Drama API", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/api/system/info")
async def system_info():
    """前端系统设置页展示用。只返回模型/引擎名称，不返回任何密钥。"""
    llm_model = os.environ.get("LLM_MODEL") or "未配置"
    llm_base = os.environ.get("LLM_API_BASE") or ""
    llm_label = f"本地模型 {llm_model}" if ("127.0.0.1" in llm_base or "localhost" in llm_base) else llm_model
    kling_model = os.environ.get("KLING_IMAGE_MODEL") or "kling"
    video_engine = os.environ.get("VIDEO_ENGINE", "kling")
    return {
        "name": "AI 短剧自动生成系统",
        "version": "1.0.0",
        "capabilities": [
            {"id": "agent_brain", "name": "导演大脑 / 调度", "model": llm_label, "status": "ready", "icon": "🧠"},
            {"id": "script_generation", "name": "剧本生成", "model": llm_label, "status": "ready", "icon": "📝"},
            {"id": "character_image", "name": "角色图生成", "model": f"Kling 可灵 ({kling_model})", "status": "ready", "icon": "🎨"},
            {"id": "video_generation", "name": "分镜视频生成", "model": f"{video_engine} 视频生成", "status": "ready", "icon": "🎬"},
            {"id": "tts_audio", "name": "配音生成 (TTS)", "model": "MiniMax TTS", "status": "pending", "icon": "🎙️"},
            {"id": "video_merge", "name": "最终合成", "model": "FFmpeg (本地)", "status": "ready", "icon": "🎞️"},
        ],
    }

# ---------- 剧本相关 ----------

@app.get("/api/scripts")
async def list_scripts():
    """获取所有剧本列表"""
    ensure_dirs()
    results = []
    data_dir = SCRIPTS_DIR
    if data_dir.exists():
        for script_dir in sorted(data_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            script_file = script_dir / "script.json"
            if script_file.exists():
                try:
                    with open(script_file, encoding="utf-8") as f:
                                data = normalize_script(json.load(f))
                    results.append({
                        "script_id": data.get("script_id", script_dir.name),
                        "title": data.get("title", "未命名"),
                        "theme": data.get("theme", ""),
                        "style": data.get("style", ""),
                        "status": data.get("status", "draft"),
                                "production_status": data.get("production_status", "script_ready"),
                        "created_at": data.get("created_at", ""),
                                "updated_at": data.get("updated_at", ""),
                        "synopsis": data.get("synopsis", "")[:100],
                        "characters_count": len(data.get("characters", [])),
                        "scenes_count": len(data.get("scenes", [])),
                    })
                except Exception:
                    pass
    return results


@app.post("/api/scripts")
async def create_script(request: DramaRequest, background_tasks: BackgroundTasks):
    """立即创建项目，后台生成完整剧本和分镜。"""
    ensure_dirs()
    
    script_id = str(uuid.uuid4())
    
    # 初始化任务状态
    task = TaskStatus(
        task_id=script_id,
        status="processing",
        progress=0,
        current_step="正在生成剧本..."
    )
    tasks_store[script_id] = task

    try:
        now = datetime.now().isoformat()
        script_data = normalize_script({
            "script_id": script_id,
            "title": request.title,
            "theme": request.theme,
            "genre": request.genre or request.theme,
            "style": request.style,
            "total_duration": request.duration,
            "description": request.description,
            "synopsis": "剧本正在生成中，稍后会自动刷新。",
            "characters": [],
            "scenes": [],
            "status": "processing",
            "created_at": now,
            "updated_at": now,
            "project_config": {
                "gen_mode": request.gen_mode or "parallel",
                "visual_style": request.visual_style or request.style,
                "style": request.style,
                "aspect_ratio": "9:16",
                "image_engine": os.environ.get("IMAGE_ENGINE", "kling"),
                "video_engine": os.environ.get("VIDEO_ENGINE", "kling"),
            },
        })

        script_dir = get_script_dir(script_id)
        script_dir.mkdir(parents=True, exist_ok=True)
        with open(script_dir / "script.json", "w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)
        scripts_store[script_id] = script_data
        background_tasks.add_task(async_generate_initial_script, script_id, request.dict())
        return {
            "script_id": script_id,
            "status": "processing",
            "message": "项目已创建，剧本正在后台生成"
        }
        
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        raise HTTPException(status_code=500, detail=str(e))


async def async_generate_initial_script(script_id: str, request_data: Dict[str, Any]):
    """后台生成新项目的完整剧本，避免创建项目时阻塞前端。"""
    task = tasks_store.get(script_id)
    if not task:
        task = TaskStatus(task_id=script_id, status="processing", progress=0, current_step="正在生成剧本...")
        tasks_store[script_id] = task
    else:
        task.status = "processing"
        task.progress = max(task.progress, 5)
        task.current_step = "正在生成剧本..."

    script_path = get_script_dir(script_id) / "script.json"
    try:
        generator = ScriptGenerator()
        script_data = await generator.generate(
            title=request_data.get("title") or "新短剧",
            theme=request_data.get("theme") or request_data.get("genre") or "自选",
            style=request_data.get("style") or request_data.get("visual_style") or "写实电影风",
            duration=int(request_data.get("duration") or 180),
            characters=request_data.get("characters") or [],
            description=request_data.get("description") or request_data.get("title") or "短剧创意",
        )

        existing = {}
        if script_path.exists():
            with open(script_path, encoding="utf-8") as f:
                existing = json.load(f)
        script_data["script_id"] = script_id
        script_data["created_at"] = existing.get("created_at") or datetime.now().isoformat()
        script_data["status"] = "draft"
        script_data["project_config"] = existing.get("project_config", {})
        script_data = normalize_script(script_data)

        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)
        scripts_store[script_id] = script_data

        task.status = "completed"
        task.progress = 30
        task.current_step = "剧本生成完成"
        task.result = {"script_id": script_id}
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.current_step = "剧本生成失败"
        if script_path.exists():
            try:
                with open(script_path, encoding="utf-8") as f:
                    data = json.load(f)
                data["status"] = "failed"
                data["generation_warning"] = str(e)
                data["updated_at"] = datetime.now().isoformat()
                with open(script_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

@app.get("/api/scripts/{script_id}")
async def get_script(script_id: str):
    """获取剧本详情。兼容老项目：如果缺少完整剧本文本，自动从 scenes/shots 拼出来。

    **重要：以磁盘 script.json 为唯一真相。**
    以前会优先命中 `scripts_store` 内存缓存，但角色图 / 场景图 / 分镜图都是
    在后台任务里直接写磁盘的，并不会同步刷新内存缓存。这会导致前端拿到没有
    image_url 的旧版本，看到"100% 完成"却没有图片，刷新也没用。
    现在直接读磁盘，缓存只作为不存在时的兜底。
    """
    def normalize_loaded(data: Dict[str, Any]) -> Dict[str, Any]:
        generator = ScriptGenerator()
        return normalize_script(generator._normalize_script(
            data,
            title=data.get("title", "新短剧"),
            theme=data.get("theme") or data.get("genre") or "自选",
            style=data.get("style", "写实电影风"),
            duration=int(data.get("total_duration") or 180),
        ))

    script_path = get_script_dir(script_id) / "script.json"
    if script_path.exists():
        with open(script_path, encoding="utf-8") as f:
            data = json.load(f)
        data = normalize_loaded(data)
        # 写回 normalize 后的数据（保持磁盘和返回值一致），同步刷新内存缓存。
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        scripts_store[script_id] = data
        return data

    # 磁盘没有，但内存里有（极少见的迁移过渡场景），用缓存兜底一次。
    if script_id in scripts_store:
        data = normalize_loaded(scripts_store[script_id])
        scripts_store[script_id] = data
        return data

    raise HTTPException(status_code=404, detail="剧本不存在")


@app.get("/api/scripts/{script_id}/status")
async def get_script_status(script_id: str):
    """获取剧本生成状态"""
    if script_id not in tasks_store:
        return TaskStatus(
            task_id=script_id,
            status="unknown",
            progress=0,
            current_step="未找到任务"
        )
    return tasks_store[script_id]

# ---------- 角色相关 ----------


def _resolve_character_in_script(script: Dict[str, Any], character_id: str) -> Optional[Dict[str, Any]]:
    """同时支持按 character_id 或按 name 定位角色，返回 dict 引用（后续可直接修改）。"""
    for char in script.get("characters", []) or []:
        if char.get("character_id") == character_id or char.get("name") == character_id:
            return char
    return None


@app.post("/api/scripts/{script_id}/characters/{character_id}/reference-image")
async def upload_character_reference_image(
    script_id: str,
    character_id: str,
    file: UploadFile = File(...),
):
    """用户上传角色参考图。
    上传到 MinIO 后，把公开 URL 追加到 character.reference_image_urls。
    后续生成/重做角色图会**走可灵 v2-1 图生图**，以这张图为蓝本。
    """
    script_path = Path("/Users/zhoumi/ai-drama-system/data/scripts") / script_id / "script.json"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail=f"剧本不存在：{script_id}")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空。")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="参考图最大支持 10MB。")

    # 推断扩展名 + content-type，可灵需要拉远程 URL，必须能正确响应 image/*。
    ext = (Path(file.filename or "").suffix or "").lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"
    content_type = file.content_type or {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")
    object_name = f"refs_{character_id}_{uuid.uuid4().hex[:10]}{ext}"

    storage = MinIOStorage()
    if not storage.enabled or storage._client is None:
        # MinIO 没启时也能用：落到 data/scripts/{id}/character_refs，再让前端用本地静态路径。
        # 注意：可灵需要远程 URL，这种情况下图生图实际上用不了，我们提示用户启 MinIO。
        raise HTTPException(
            status_code=503,
            detail="MinIO 未启用，无法对外暴露参考图给可灵远程拉取。请在 backend/.env 启用 MINIO_ENABLED=true。",
        )

    public_url = storage.upload_bytes(
        script_id=script_id,
        data=content,
        file_name=object_name,
        folder="character_refs",
        content_type=content_type,
    )
    if not public_url:
        raise HTTPException(status_code=500, detail="参考图上传到 MinIO 失败。")

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    char = _resolve_character_in_script(script, character_id)
    if not char:
        raise HTTPException(status_code=404, detail=f"角色不存在：{character_id}")
    refs = char.get("reference_image_urls")
    if not isinstance(refs, list):
        refs = []
    refs.insert(0, public_url)  # 最新上传的放最前面，生成时优先取
    # 限 5 张，避免无限堆积。
    char["reference_image_urls"] = refs[:5]

    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    return {
        "character_id": character_id,
        "reference_image_urls": char["reference_image_urls"],
        "uploaded_url": public_url,
    }


@app.delete("/api/scripts/{script_id}/characters/{character_id}/reference-image")
async def delete_character_reference_image(
    script_id: str,
    character_id: str,
    url: Optional[str] = Query(None, description="只删指定 URL；不传则清空全部参考图。"),
):
    """删除角色的某张参考图（或清空全部）。"""
    script_path = Path("/Users/zhoumi/ai-drama-system/data/scripts") / script_id / "script.json"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail=f"剧本不存在：{script_id}")

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    char = _resolve_character_in_script(script, character_id)
    if not char:
        raise HTTPException(status_code=404, detail=f"角色不存在：{character_id}")

    refs = char.get("reference_image_urls") or []
    if url:
        refs = [u for u in refs if u != url]
    else:
        refs = []
    char["reference_image_urls"] = refs

    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    return {"character_id": character_id, "reference_image_urls": refs}


@app.post("/api/scripts/{script_id}/characters/{character_id}/generate-image")
async def generate_character_image(script_id: str, character_id: str):
    """为角色生成参考图"""
    task = tasks_store.get(script_id)
    if task:
        task.status = "processing"
        task.progress = 40
        task.current_step = f"正在生成角色 {character_id} 的图片..."
        task.error = None
    
    try:
        generator = CharacterGenerator()
        result = await generator.generate_character_image(
            script_id=script_id,
            character_id=character_id
        )
        if task:
            task.status = "completed"
            task.progress = max(task.progress, 65)
            task.current_step = f"角色 {character_id} 图片生成完成"
            task.result = result
        return result
    except Exception as e:
        if task:
            task.status = "failed"
            task.progress = 100
            task.current_step = f"角色 {character_id} 图片生成失败"
            task.error = str(e)
        raise HTTPException(status_code=500, detail=str(e))

# ---------- 场景相关 ----------


def _resolve_scene_in_script(script: Dict[str, Any], scene_ref: str) -> Optional[Dict[str, Any]]:
    """既支持 scene_id 也支持 location_id，前端两种都可能传过来。"""
    for scene in script.get("scenes", []) or []:
        if scene.get("scene_id") == scene_ref or scene.get("location_id") == scene_ref:
            return scene
    return None


@app.post("/api/scripts/{script_id}/locations/{scene_ref}/reference-image")
async def upload_location_reference_image(
    script_id: str,
    scene_ref: str,
    file: UploadFile = File(...),
):
    """用户上传场景参考图。后续生成会以这张图为蓝本（v2-1 image-to-image）。"""
    script_path = Path("/Users/zhoumi/ai-drama-system/data/scripts") / script_id / "script.json"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail=f"剧本不存在：{script_id}")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空。")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="参考图最大支持 10MB。")

    ext = (Path(file.filename or "").suffix or "").lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"
    content_type = file.content_type or {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")
    object_name = f"loc_refs_{scene_ref}_{uuid.uuid4().hex[:10]}{ext}"

    storage = MinIOStorage()
    if not storage.enabled or storage._client is None:
        raise HTTPException(
            status_code=503,
            detail="MinIO 未启用，无法对外暴露参考图给可灵远程拉取。请在 backend/.env 启用 MINIO_ENABLED=true。",
        )

    public_url = storage.upload_bytes(
        script_id=script_id,
        data=content,
        file_name=object_name,
        folder="location_refs",
        content_type=content_type,
    )
    if not public_url:
        raise HTTPException(status_code=500, detail="参考图上传到 MinIO 失败。")

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    scene = _resolve_scene_in_script(script, scene_ref)
    if not scene:
        raise HTTPException(status_code=404, detail=f"场景不存在：{scene_ref}")
    refs = scene.get("reference_image_urls")
    if not isinstance(refs, list):
        refs = []
    refs.insert(0, public_url)
    scene["reference_image_urls"] = refs[:5]

    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    return {
        "scene_id": scene.get("scene_id"),
        "location_id": scene.get("location_id"),
        "reference_image_urls": scene["reference_image_urls"],
        "uploaded_url": public_url,
    }


@app.delete("/api/scripts/{script_id}/locations/{scene_ref}/reference-image")
async def delete_location_reference_image(
    script_id: str,
    scene_ref: str,
    url: Optional[str] = Query(None, description="只删指定 URL；不传则清空全部参考图。"),
):
    script_path = Path("/Users/zhoumi/ai-drama-system/data/scripts") / script_id / "script.json"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail=f"剧本不存在：{script_id}")

    with open(script_path, "r", encoding="utf-8") as f:
        script = json.load(f)
    scene = _resolve_scene_in_script(script, scene_ref)
    if not scene:
        raise HTTPException(status_code=404, detail=f"场景不存在：{scene_ref}")

    refs = scene.get("reference_image_urls") or []
    if url:
        refs = [u for u in refs if u != url]
    else:
        refs = []
    scene["reference_image_urls"] = refs

    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    return {
        "scene_id": scene.get("scene_id"),
        "location_id": scene.get("location_id"),
        "reference_image_urls": refs,
    }


@app.post("/api/scripts/{script_id}/locations/{scene_ref}/generate-image")
async def generate_location_image(script_id: str, scene_ref: str):
    """为单个场景生成场景图（同步）。前端用「重做」按钮直接驱动。"""
    try:
        gen = LocationGenerator()
        result = await gen.generate_scene_image(script_id=script_id, scene_id=scene_ref)
        return result
    except LocationGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/scripts/{script_id}/locations/batch-generate")
async def batch_generate_locations(
    script_id: str,
    background_tasks: BackgroundTasks,
    payload: Optional[Dict[str, Any]] = None,
):
    """批量生成所有场景图（后台任务，前端轮询 /status）。"""
    scene_refs = (payload or {}).get("scene_ids") if payload else None
    force = bool((payload or {}).get("force")) if payload else False

    def do_generate():
        asyncio.run(async_generate_locations(script_id, scene_refs=scene_refs, force=force))

    background_tasks.add_task(do_generate)
    return {"message": "场景图批量生成任务已启动", "script_id": script_id}


async def async_generate_locations(
    script_id: str,
    scene_refs: Optional[List[str]] = None,
    force: bool = False,
):
    """后台运行的场景图批量任务，逐个生成并写回 script.json。"""
    loc_gen = LocationGenerator()
    data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")
    script_file = data_dir / script_id / "script.json"

    target_refs = {str(x) for x in (scene_refs or []) if x}

    task = tasks_store.get(script_id)
    if not task:
        task = TaskStatus(
            task_id=script_id,
            status="processing",
            progress=40,
            current_step="正在生成场景图...",
            action="GENERATE_LOCATIONS",
        )
        tasks_store[script_id] = task
    else:
        task.status = "processing"
        task.progress = max(task.progress, 40)
        task.current_step = "正在生成场景图..."
        task.error = None
        task.action = "GENERATE_LOCATIONS"

    try:
        if not script_file.exists():
            raise FileNotFoundError(f"script.json 不存在: {script_id}")

        with open(script_file, "r", encoding="utf-8") as f:
            script_data = normalize_script(json.load(f))

        all_scenes = script_data.get("scenes", []) or []
        if not all_scenes:
            raise ValueError("当前剧本没有场景数据，无法生成场景图。")

        if target_refs:
            scenes = [
                s for s in all_scenes
                if str(s.get("scene_id") or "") in target_refs
                or str(s.get("location_id") or "") in target_refs
            ]
            if not scenes:
                raise ValueError(f"指定的场景未找到：{', '.join(sorted(target_refs))}")
        else:
            scenes = all_scenes

        total = len(scenes)
        generated = 0
        skipped = 0
        failures: List[str] = []

        style_hint = script_data.get("style") or "cinematic realistic drama"
        for idx, scene in enumerate(scenes, start=1):
            scene_id = scene.get("scene_id") or scene.get("location_id") or f"scene_{idx}"
            location_name = scene.get("location") or scene_id

            if not force and scene.get("location_image_url"):
                skipped += 1
                task.progress = 40 + int(idx / total * 30)
                continue

            prompt = loc_gen._build_full_prompt(scene, style=style_hint)
            ref_urls = [u for u in (scene.get("reference_image_urls") or []) if u]
            ref = ref_urls[0] if ref_urls else None

            ref_hint = "（已使用上传参考图）" if ref else ""
            task.current_step = f"正在生成场景图：{location_name}（{idx}/{total}）{ref_hint}"
            try:
                image_url = loc_gen.generate_location(
                    script_id, scene_id, prompt, reference_image_url=ref
                )
                if not image_url:
                    raise LocationGenerationError("生成器没有返回图片 URL")
                generated += 1
                task.progress = 40 + int(idx / total * 30)
                task.current_step = f"场景图已生成：{location_name}（{idx}/{total}）"
            except Exception as item_err:
                msg = f"{location_name}: {item_err}"
                failures.append(msg)
                print(f"[Location Task] {msg}")
                task.error = msg
                task.current_step = f"场景图生成失败：{location_name}"
                # 不 break，继续下一个场景，最大化产出

        # 重新加载（有可能在生成过程中已经写过）并触发 normalize
        with open(script_file, "r", encoding="utf-8") as f:
            script_data = normalize_script(json.load(f))
        update_production_status(script_data)
        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)

        if force and generated == 0 and not skipped:
            raise LocationGenerationError("场景图生成全部失败，未产出任何图片。")

        if failures and generated == 0:
            raise LocationGenerationError("；".join(failures))

        task.status = "completed"
        task.progress = max(task.progress, 70)
        if failures:
            task.current_step = f"场景图：{generated} 成功 / {len(failures)} 失败"
            task.error = "；".join(failures)
        else:
            task.current_step = f"场景图全部生成完成（{generated} 个，跳过 {skipped} 个已有图）"
    except Exception as e:
        task.status = "failed"
        task.progress = 100
        task.current_step = "场景图生成失败"
        task.error = str(e)
        print(f"[Location Task] 失败: {e}")


# ---------- 分镜相关 ----------

@app.post("/api/scripts/{script_id}/storyboard/batch-generate")
async def batch_generate_storyboards(script_id: str, background_tasks: BackgroundTasks, payload: Optional[Dict[str, Any]] = None):
    """批量生成分镜图。先出图让用户确认，再进入视频生成。"""
    shot_ids = (payload or {}).get("shot_ids") if payload else None
    force = bool((payload or {}).get("force")) if payload else False

    def do_storyboard():
        asyncio.run(async_generate_storyboards(script_id, shot_ids=shot_ids, force=force))

    background_tasks.add_task(do_storyboard)
    return {"message": "分镜图生成任务已启动", "script_id": script_id}


@app.post("/api/scripts/{script_id}/shots/{shot_id}/storyboard")
async def generate_single_storyboard(script_id: str, shot_id: str, payload: Optional[Dict[str, Any]] = None):
    """生成或重生成单个镜头分镜图。"""
    force = bool((payload or {}).get("force")) if payload else False
    gen = StoryboardGenerator()
    try:
        image_url = gen.generate_shot_storyboard(script_id, shot_id, force=force)
    except StoryboardGenerationError as e:
        # 业务可读错误：直接展给前端
        raise HTTPException(status_code=502, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"script_id": script_id, "shot_id": shot_id, "storyboard_image_url": image_url}


@app.put("/api/scripts/{script_id}/shots/{shot_id}/confirm")
async def confirm_shot_storyboard(script_id: str, shot_id: str, payload: Optional[Dict[str, Any]] = None):
    """确认某个分镜图，确认后该 shot 才进入批量视频生成队列。"""
    script_file = get_script_dir(script_id) / "script.json"
    if not script_file.exists():
        raise HTTPException(status_code=404, detail="剧本不存在")
    with open(script_file, encoding="utf-8") as f:
        script_data = normalize_script(json.load(f))
    shot = find_shot(script_data, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")
    confirmed = bool((payload or {}).get("confirmed", True))
    mark_shot_confirmed(shot, confirmed=confirmed)
    update_production_status(script_data)
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script_data, f, ensure_ascii=False, indent=2)
    return {"script_id": script_id, "shot_id": shot_id, "confirmed": confirmed, "status": shot.get("status")}


@app.put("/api/scripts/{script_id}/shots/{shot_id}/review")
async def review_shot(script_id: str, shot_id: str, payload: Dict[str, Any]):
    """保存单镜头审片结论。status: approved / needs_regen / rejected / unchecked。"""
    script_file = get_script_dir(script_id) / "script.json"
    if not script_file.exists():
        raise HTTPException(status_code=404, detail="剧本不存在")
    with open(script_file, encoding="utf-8") as f:
        script_data = normalize_script(json.load(f))
    shot = find_shot(script_data, shot_id)
    if not shot:
        raise HTTPException(status_code=404, detail="镜头不存在")
    status = payload.get("status") or "unchecked"
    notes = payload.get("notes") or ""
    mark_shot_review(shot, status, notes)
    update_production_status(script_data)
    with open(script_file, "w", encoding="utf-8") as f:
        json.dump(script_data, f, ensure_ascii=False, indent=2)
    return {"script_id": script_id, "shot_id": shot_id, "review_status": shot.get("review_status"), "status": shot.get("status")}


@app.post("/api/scripts/{script_id}/shots/{shot_id}/auto-review")
async def auto_review_shot(script_id: str, shot_id: str, payload: Optional[Dict[str, Any]] = None):
    """让 AI 审片员检查单个镜头。Hermes 不可用时会返回规则审片结果。"""
    use_model = bool((payload or {}).get("use_model", True))
    reviewer = ShotReviewer()
    result = await reviewer.review_shot(script_id, shot_id, use_model=use_model)
    return {"script_id": script_id, "shot_id": shot_id, "review": result}


@app.post("/api/scripts/{script_id}/shots/auto-review")
async def auto_review_all_shots(script_id: str, payload: Optional[Dict[str, Any]] = None):
    """批量审片所有已有视频的镜头。"""
    use_model = bool((payload or {}).get("use_model", True))
    reviewer = ShotReviewer()
    results = await reviewer.review_all(script_id, use_model=use_model)
    return {"script_id": script_id, "reviews": results}


@app.post("/api/scripts/{script_id}/shots/{shot_id}/regenerate-video")
async def regenerate_shot_video(script_id: str, shot_id: str, background_tasks: BackgroundTasks):
    """只重生成一个镜头视频。"""
    def do_regen():
        asyncio.run(async_generate_video(script_id, shot_ids=[shot_id], force=True))

    background_tasks.add_task(do_regen)
    return {"message": "单镜头视频重生成任务已启动", "script_id": script_id, "shot_id": shot_id}


async def async_generate_storyboards(script_id: str, shot_ids: Optional[List[str]] = None, force: bool = False):
    """后台生成分镜图并写回 shot.storyboard_image_url。

    失败处理：每个分镜单独 try，失败的累积到 failures。最后：
      - 全部失败 → task.status=failed，错误带头条原因，前端会看到红色失败状态。
      - 部分失败 → task.status=completed 但 task.error 列出失败镜头，current_step 标 X 成功 Y 失败。
      - 全部成功 → 现在的行为不变。
    避免之前那种 "100% 完成但所有分镜图都没生成出来" 的假成功。
    """
    script_file = get_script_dir(script_id) / "script.json"
    task = tasks_store.get(script_id)
    if not task:
        task = TaskStatus(
            task_id=script_id,
            status="processing",
            progress=45,
            current_step="正在生成分镜图...",
            action="GENERATE_STORYBOARD",
        )
        tasks_store[script_id] = task
    else:
        task.status = "processing"
        task.progress = max(task.progress, 45)
        task.current_step = "正在生成分镜图..."
        task.error = None
        task.action = "GENERATE_STORYBOARD"

    try:
        if not script_file.exists():
            raise FileNotFoundError(f"script.json 不存在: {script_id}")
        with open(script_file, encoding="utf-8") as f:
            script_data = normalize_script(json.load(f))
        selected = {str(s) for s in (shot_ids or []) if s}
        target_shots = [
            shot for _scene, shot in iter_shots(script_data)
            if (not selected or str(shot.get("shot_id")) in selected or str(shot.get("shot_number")) in selected)
            and (force or not shot.get("storyboard_image_url"))
        ]
        if not target_shots:
            task.status = "completed"
            task.progress = max(task.progress, 65)
            task.current_step = "没有需要生成的分镜图（已存在或不在选定范围内）。"
            task.result = {"script_id": script_id, "generated": 0, "skipped": 0, "failed": 0}
            return

        total = len(target_shots)
        gen = StoryboardGenerator()
        success_count = 0
        failures: List[str] = []
        for idx, shot in enumerate(target_shots, start=1):
            shot_id = str(shot.get("shot_id"))
            task.current_step = f"正在生成分镜图：{shot_id}（{idx}/{total}）"
            try:
                gen.generate_shot_storyboard(script_id, shot_id, force=force)
                success_count += 1
                task.current_step = f"分镜图已生成：{shot_id}（{idx}/{total}）"
            except Exception as item_err:
                msg = f"{shot_id}: {item_err}"
                failures.append(msg)
                print(f"[Storyboard Task] {msg}")
                # 临时记录最近一次错误，前端轮询能看到，但不要中断后续镜头
                task.error = msg
                task.current_step = f"分镜图生成失败：{shot_id}（{idx}/{total}）"
            task.progress = 45 + int(idx / total * 20)

        with open(script_file, encoding="utf-8") as f:
            script_data = normalize_script(json.load(f))
        update_production_status(script_data)
        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)

        # 全失败 → 真失败
        if success_count == 0:
            raise RuntimeError(
                f"全部 {total} 个分镜图生成失败。首条失败：{failures[0] if failures else '未知错误'}"
            )

        # 部分失败 → 完成但保留错误明细
        task.status = "completed"
        task.progress = max(task.progress, 65)
        task.result = {
            "script_id": script_id,
            "generated": success_count,
            "failed": len(failures),
            "failures": failures[:10],
        }
        if failures:
            task.error = "；".join(failures[:5])
            task.current_step = f"分镜图部分失败：成功 {success_count}/{total}，失败 {len(failures)}（详见错误信息）"
        else:
            task.error = None
            task.current_step = f"分镜图生成完成：{success_count}/{total}"
    except Exception as e:
        task.status = "failed"
        task.progress = 100
        task.error = str(e)
        task.current_step = "分镜图生成失败"
        print(f"Background storyboard generation failed: {e}")


@app.post("/api/scripts/{script_id}/shots/batch-generate")
async def batch_generate_shots(script_id: str, background_tasks: BackgroundTasks):
    """批量生成分镜视频"""
    task = tasks_store.get(script_id)
    if task:
        task.progress = 50
        task.current_step = "正在批量生成分镜视频..."
    
    def generate_all_shots():
        asyncio.run(async_generate_video(script_id))
    
    background_tasks.add_task(generate_all_shots)
    return {"message": "分镜生成任务已启动", "script_id": script_id}

async def _generate_shots_async(script_id: str):
    """异步生成分镜"""
    task = tasks_store.get(script_id)
    
    try:
        # 加载剧本
        script_path = get_script_dir(script_id) / "script.json"
        with open(script_path, encoding="utf-8") as f:
            script_data = json.load(f)
        
        total_shots = sum(len(scene["shots"]) for scene in script_data["scenes"])
        completed = 0
        
        # 逐个生成分镜
        video_gen = VideoGenerator()
        audio_gen = AudioGenerator()
        
        for scene in script_data["scenes"]:
            for shot in scene["shots"]:
                shot_id = shot["shot_id"]
                
                # 生成配音
                if shot.get("dialogue") and shot["dialogue"].get("text"):
                    audio_url = await audio_gen.generate(
                        text=shot["dialogue"]["text"],
                        voice_id=shot["dialogue"].get("voice_id", "female-tianmei")
                    )
                    shot["dialogue"]["tts_audio_url"] = audio_url
                
                # 生成视频
                video_url = await video_gen.generate_shot_video(
                    script_id=script_id,
                    shot=shot,
                    characters=script_data["characters"]
                )
                shot["generated_video_url"] = video_url
                shot["status"] = "completed"
                
                completed += 1
                if task:
                    task.progress = 50 + int(completed / total_shots * 40)
        
        # 保存更新后的剧本
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)
        
        if task:
            task.progress = 95
            task.current_step = "正在合并音视频..."
            task.status = "completed"
        
    except Exception as e:
        if task:
            task.status = "failed"
            task.error = str(e)

# ---------- 最终合成 ----------

@app.post("/api/scripts/{script_id}/merge")
async def merge_final_video(script_id: str, background_tasks: BackgroundTasks):
    """合并所有分镜生成最终成片"""
    task = tasks_store.get(script_id)
    if task:
        task.progress = 95
        task.current_step = "正在合并音视频..."
    
    def do_merge():
        asyncio.run(_merge_video_async(script_id))
    
    background_tasks.add_task(do_merge)
    return {"message": "视频合并任务已启动", "script_id": script_id}

async def _merge_video_async(script_id: str):
    """异步合并视频"""
    task = tasks_store.get(script_id)
    
    try:
        merger = MediaMerger()
        final_path = await merger.merge_script(script_id)
        script_file = get_script_dir(script_id) / "script.json"
        if script_file.exists():
            with open(script_file, encoding="utf-8") as f:
                script_data = normalize_script(json.load(f))
            final_url = public_media_url(script_id, str(final_path))
            script_data["final_video_url"] = final_url
            script_data.setdefault("timeline", {})["final_video_url"] = final_url
            update_production_status(script_data)
            with open(script_file, "w", encoding="utf-8") as f:
                json.dump(script_data, f, ensure_ascii=False, indent=2)
        
        if task:
            task.progress = 100
            task.current_step = "完成"
            task.status = "completed"
            task.result = {"final_video_url": str(final_path)}
        
    except Exception as e:
        if task:
            task.status = "failed"
            task.error = str(e)

# ---------- 文件下载 ----------

@app.get("/api/files/{script_id}/{file_path:path}")
async def get_file(script_id: str, file_path: str):
    """返回项目脚本目录下生成的图片/视频/音频文件。"""
    full_path = _safe_media_file(get_script_dir(script_id), file_path)
    return FileResponse(str(full_path))


@app.get("/api/outputs/{script_id}/{file_path:path}")
async def get_output_file(script_id: str, file_path: str):
    """返回最终合成输出目录下的成片文件。"""
    full_path = _safe_media_file(OUTPUTS_DIR / script_id, file_path)
    return FileResponse(str(full_path))

# ============== Agent API ==============

from fastapi.responses import StreamingResponse, FileResponse
import json

@app.post("/v1/agent/chat")
async def agent_chat(request: AgentChatRequest, background_tasks: BackgroundTasks):
    """前端左侧边栏聊天的入口，SSE流式返回"""
    async def event_generator():
        try:
            async for event in agent_router.chat_stream(
                script_id=request.script_id,
                message=request.message,
                history=request.history,
                project_config=request.project_config,
                background_tasks=background_tasks,
            ):
                chunk = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                # 部分 dev proxy / 浏览器会缓冲很小的 SSE chunk；状态事件后补一个 comment padding 强制尽快 flush。
                if event.get("type") == "status":
                    chunk += ": " + (" " * 2048) + "\n\n"
                yield chunk
        except Exception as e:
            err_event = {"type": "chat", "content": f"\n\n[系统错误]: {str(e)}"}
            yield "data: " + json.dumps(err_event, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        background=background_tasks,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

async def async_generate_video(script_id: str, shot_ids: Optional[List[str]] = None, force: bool = False):
    """后台运行的分镜视频生成任务 (根据 project_config 路由引擎)。shot_ids 为空则生成所有未完成镜头。"""
    import json
    from pathlib import Path

    script_file = Path("/Users/zhoumi/ai-drama-system/data/scripts") / script_id / "script.json"
    task = tasks_store.get(script_id)
    if not task:
        task = TaskStatus(
            task_id=script_id,
            status="processing",
            progress=65,
            current_step="正在生成分镜视频...",
            action="GENERATE_SHOT_VIDEOS",
        )
        tasks_store[script_id] = task
    else:
        task.status = "processing"
        task.progress = max(task.progress, 65)
        task.current_step = "正在生成分镜视频..."
        task.action = "GENERATE_SHOT_VIDEOS"

    if not script_file.exists():
        task.status = "failed"
        task.error = f"script {script_id} not found"
        print(f"Video Gen Error: script {script_id} not found")
        return

    selected = {str(s) for s in (shot_ids or []) if s}

    try:
        with open(script_file, "r", encoding="utf-8") as f:
            script_data = normalize_script(json.load(f))

        config = script_data.get("project_config", {}) or {}
        engine = config.get("video_engine", "kling")
        aspect = config.get("aspect_ratio", "9:16")
        all_shots = [shot for scene in script_data.get("scenes", []) for shot in scene.get("shots", [])]
        todo_shots = [
            shot for shot in all_shots
            if (not selected or str(shot.get("shot_id")) in selected or str(shot.get("shot_number")) in selected)
            and (force or not (shot.get("generated_video_url") or shot.get("video_url")))
            and (
                force
                or shot.get("storyboard_confirmed")
                or shot.get("status") in {"storyboard_approved", "video_failed", "needs_regen"}
            )
        ]
        total = max(len(todo_shots), 1)

        print(f"Starting background video generation for script {script_id} using {engine}, shots={len(todo_shots)}...")

        if not todo_shots:
            selected_count = len([
                shot for shot in all_shots
                if not selected or str(shot.get("shot_id")) in selected or str(shot.get("shot_number")) in selected
            ])
            confirmed_count = len([
                shot for shot in all_shots
                if shot.get("storyboard_confirmed") or shot.get("status") in {"storyboard_approved", "video_failed", "needs_regen"}
            ])
            already_has_video_count = len([
                shot for shot in all_shots
                if shot.get("generated_video_url") or shot.get("video_url")
            ])
            message = "没有可生成的视频镜头：请先生成并确认分镜图，然后再生成视频。"
            if selected and selected_count == 0:
                message = "没有找到所选镜头，请刷新项目后再试。"
            elif all_shots and already_has_video_count == len(all_shots) and not force:
                message = "所有镜头都已经有视频了；如果要重新生成，请使用单镜头重生成或 force=true。"
            task.status = "failed"
            task.progress = 65
            task.current_step = message
            task.error = message
            task.result = {
                "total_shots": len(all_shots),
                "selected_shots": selected_count,
                "confirmed_shots": confirmed_count,
                "already_has_video_shots": already_has_video_count,
                "generated_shots": 0,
            }
            update_production_status(script_data)
            with open(script_file, "w", encoding="utf-8") as fw:
                json.dump(script_data, fw, ensure_ascii=False, indent=2)
            print(f"Video generation skipped for {script_id}: {message}")
            return

        if engine == "kling":
            from services.video_generator import VideoGenerator
            gen = VideoGenerator()
            reviewer = ShotReviewer()
            for idx, shot in enumerate(todo_shots, start=1):
                shot_id = shot.get("shot_id") or f"shot_{idx}"
                print(f"[{script_id}] 开始为 Shot {shot_id} 生成视频 (Kling)")
                task.current_step = f"正在生成分镜视频：{shot_id}"
                try:
                    url = await gen.generate_shot_video(
                        script_id=script_id,
                        shot=shot,
                        characters=script_data.get("characters", []),
                        aspect_ratio=aspect,
                        duration=int(shot.get("duration") or 5),
                    )
                    public_url = public_media_url(script_id, url)
                    mark_shot_video(shot, public_url)
                    task.progress = 65 + int(idx / total * 25)
                    update_production_status(script_data)
                    with open(script_file, "w", encoding="utf-8") as fw:
                        json.dump(script_data, fw, ensure_ascii=False, indent=2)
                    if url:
                        await reviewer.review_shot(script_id, str(shot_id), use_model=False)
                        with open(script_file, "r", encoding="utf-8") as f:
                            script_data = normalize_script(json.load(f))
                except Exception as e:
                    shot["status"] = "video_failed"
                    shot["video_status"] = "failed"
                    shot["error"] = str(e)
                    with open(script_file, "w", encoding="utf-8") as fw:
                        json.dump(script_data, fw, ensure_ascii=False, indent=2)
                    print(f"Error generating shot {shot_id}: {e}")
        else:
            from services.minimax_video_generator import MinimaxVideoGenerator
            gen = MinimaxVideoGenerator()
            reviewer = ShotReviewer()
            for idx, shot in enumerate(todo_shots, start=1):
                shot_id = shot.get("shot_id") or f"shot_{idx}"
                print(f"[{script_id}] 开始为 Shot {shot_id} 生成视频 ({engine})")
                task.current_step = f"正在生成分镜视频：{shot_id}"
                try:
                    url = gen.generate_shot_video(
                        script_id=script_id,
                        shot=shot,
                        characters=script_data.get("characters", []),
                        aspect_ratio=aspect,
                    )
                    public_url = public_media_url(script_id, url)
                    mark_shot_video(shot, public_url)
                    task.progress = 65 + int(idx / total * 25)
                    update_production_status(script_data)
                    with open(script_file, "w", encoding="utf-8") as fw:
                        json.dump(script_data, fw, ensure_ascii=False, indent=2)
                    if url:
                        await reviewer.review_shot(script_id, str(shot_id), use_model=False)
                        with open(script_file, "r", encoding="utf-8") as f:
                            script_data = normalize_script(json.load(f))
                except Exception as e:
                    shot["status"] = "video_failed"
                    shot["video_status"] = "failed"
                    shot["error"] = str(e)
                    with open(script_file, "w", encoding="utf-8") as fw:
                        json.dump(script_data, fw, ensure_ascii=False, indent=2)
                    print(f"Error generating shot {shot_id}: {e}")

        task.status = "completed"
        task.progress = max(task.progress, 90)
        task.current_step = "分镜视频生成完成"
        update_production_status(script_data)
        with open(script_file, "w", encoding="utf-8") as fw:
            json.dump(script_data, fw, ensure_ascii=False, indent=2)
        print(f"Video generation loop complete for {script_id} (Engine: {engine})")

    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.current_step = "分镜视频生成失败"
        print(f"Background video generation failed: {e}")

async def async_generate_audio(script_id: str):
    """后台生成所有有对白分镜的配音，并写回 tts_audio_url。"""
    script_file = DATA_DIR / "scripts" / script_id / "script.json"
    task = tasks_store.get(script_id)
    if not task:
        task = TaskStatus(
            task_id=script_id,
            status="processing",
            progress=70,
            current_step="正在生成配音...",
        )
        tasks_store[script_id] = task
    else:
        task.status = "processing"
        task.progress = max(task.progress, 70)
        task.current_step = "正在生成配音..."

    try:
        if not script_file.exists():
            raise FileNotFoundError(f"script.json 不存在: {script_id}")
        with open(script_file, "r", encoding="utf-8") as f:
            script_data = normalize_script(json.load(f))

        voice_map = {c.get("character_id") or c.get("name"): c.get("voice_id", "female-tianmei") for c in script_data.get("characters", [])}
        shots = [shot for scene in script_data.get("scenes", []) for shot in scene.get("shots", [])]
        dialogue_shots = [shot for shot in shots if (shot.get("dialogue") or {}).get("text")]
        total = max(len(dialogue_shots), 1)
        audio_gen = AudioGenerator()

        for idx, shot in enumerate(dialogue_shots, start=1):
            dialogue = shot.get("dialogue") or {}
            if dialogue.get("tts_audio_url"):
                continue
            shot_id = str(shot.get("shot_id") or f"shot_{idx}")
            text = dialogue.get("tts_text") or dialogue.get("text")
            voice_id = dialogue.get("voice_id") or voice_map.get(dialogue.get("character_id"), "female-tianmei")
            task.current_step = f"正在生成配音：{shot_id}"
            audio_path = await audio_gen.generate_for_shot(script_id, shot_id, text, voice_id)
            dialogue["tts_audio_url"] = audio_path
            dialogue["voice_id"] = voice_id
            shot["dialogue"] = dialogue
            task.progress = 70 + int(idx / total * 15)
            with open(script_file, "w", encoding="utf-8") as fw:
                json.dump(script_data, fw, ensure_ascii=False, indent=2)

        task.status = "completed"
        task.progress = max(task.progress, 85)
        task.current_step = "配音生成完成"
        update_production_status(script_data)
        with open(script_file, "w", encoding="utf-8") as fw:
            json.dump(script_data, fw, ensure_ascii=False, indent=2)
    except Exception as e:
        task.status = "failed"
        task.error = str(e)
        task.current_step = "配音生成失败"
        print(f"Background audio generation failed: {e}")

@app.put("/api/scripts/{script_id}/characters/{character_id}/voice")
async def update_character_voice(script_id: str, character_id: str, payload: dict):
    """前端角色卡片选择声音后，更新保存到剧本 json 中"""
    voice_id = payload.get("voice_id")
    if not voice_id:
        raise HTTPException(status_code=400, detail="缺少 voice_id")
        
    script_file = DATA_DIR / "scripts" / script_id / "script.json"
    if not script_file.exists():
        raise HTTPException(status_code=404, detail="找不到剧本文件")
        
    try:
        with open(script_file, "r") as f:
            script_data = json.load(f)
            
        updated = False
        for char in script_data.get("characters", []):
            # 兼容：有时候大模型可能没生成 character_id，就用 name 匹配
            if char.get("character_id") == character_id or char.get("name") == character_id:
                char["voice_id"] = voice_id
                updated = True
                break
                
        if updated:
            with open(script_file, "w") as f:
                json.dump(script_data, f, ensure_ascii=False, indent=2)
            return {"status": "success", "message": "声音设置已保存", "voice_id": voice_id}
        else:
            raise HTTPException(status_code=404, detail="未找到对应的角色")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============== 启动 ==============

if __name__ == "__main__":
    import uvicorn
    ensure_dirs()
    uvicorn.run(app, host="0.0.0.0", port=8000)
