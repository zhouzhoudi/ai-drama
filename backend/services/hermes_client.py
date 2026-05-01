"""
Hermes Gateway LLM 客户端

后端统一通过本地 Hermes Gateway 的 OpenAI 兼容接口调用模型：
  POST http://127.0.0.1:8642/v1/chat/completions

模型选择原则：
- 默认读取当前 Hermes 配置里的 model.default / model.name；
- 也就是用户在 Hermes 里切到什么模型，AI 短剧系统就跟着用什么模型；
- 不在业务后端写死 Copilot、MiniMax、Claude 等具体模型。

注意：这里只调用已经运行的 Hermes Gateway，不负责启动/重启/修改 Hermes 配置。
"""

import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx


HERMES_BASE_URL = os.environ.get("HERMES_GATEWAY_BASE_URL", "http://127.0.0.1:8642/v1").rstrip("/")
HERMES_ENDPOINT = os.environ.get("HERMES_GATEWAY_ENDPOINT", f"{HERMES_BASE_URL}/chat/completions")
# 只使用显式配置的 key。不要用 *** 这种占位值当 Authorization 发出去。
HERMES_API_KEY = (os.environ.get("HERMES_GATEWAY_API_KEY") or os.environ.get("HERMES_API_KEY") or "").strip()


def _hermes_config_path() -> Path:
    explicit = os.environ.get("HERMES_CONFIG_PATH")
    if explicit:
        return Path(explicit).expanduser()
    hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    return hermes_home / "config.yaml"


def _read_current_hermes_model() -> Optional[str]:
    """
    每次请求时读取当前 Hermes 模型配置，而不是模块加载时固定。
    这样用户在 Hermes 里切换模型后，AI 短剧后端重启即可跟随当前 Hermes 模型。
    """
    env_model = os.environ.get("HERMES_GATEWAY_MODEL") or os.environ.get("HERMES_MODEL")
    if env_model:
        return env_model.strip()

    path = _hermes_config_path()
    if not path.exists():
        return None

    try:
        import yaml  # type: ignore
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        model_cfg = data.get("model") or {}
        if isinstance(model_cfg, str):
            return model_cfg.strip() or None
        if isinstance(model_cfg, dict):
            for key in ("default", "name", "model"):
                value = model_cfg.get(key)
                if value:
                    return str(value).strip()
    except Exception:
        # PyYAML 不可用或配置格式异常时，做一个保守文本解析。
        try:
            in_model = False
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.rstrip()
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if line.startswith("model:"):
                    in_model = True
                    continue
                if in_model and line and not line.startswith(" ") and not line.startswith("\t"):
                    break
                if in_model and ":" in stripped:
                    k, v = stripped.split(":", 1)
                    if k.strip() in {"default", "name", "model"}:
                        v = v.strip().strip('"').strip("'")
                        if v:
                            return v
        except Exception:
            return None
    return None


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if HERMES_API_KEY and HERMES_API_KEY != "***":
        headers["Authorization"] = f"Bearer {HERMES_API_KEY}"
    return headers


def _build_payload(
    messages: List[Dict[str, str]],
    model: Optional[str],
    temperature: float,
    max_tokens: int,
    response_format: Optional[Dict[str, Any]],
    stream: bool,
) -> Dict[str, Any]:
    selected_model = model or _read_current_hermes_model()
    payload: Dict[str, Any] = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if selected_model:
        # Hermes Gateway 实际会使用自身当前 runtime model；这里传入当前配置值，方便响应和日志保持一致。
        payload["model"] = selected_model
    if response_format:
        payload["response_format"] = response_format
    return payload


def _timeout(total: float) -> httpx.Timeout:
    # read timeout 给足一点；stream 模式下只要有 chunk/keepalive 就不会触发。
    return httpx.Timeout(connect=10.0, read=total, write=30.0, pool=10.0)


async def hermes_chat_stream(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4500,
    response_format: Optional[Dict[str, Any]] = None,
    timeout: float = 150.0,
) -> AsyncIterator[str]:
    """调用 Hermes Gateway 的 stream 模式，逐段返回 assistant 文本。"""
    payload = _build_payload(messages, model, temperature, max_tokens, response_format, stream=True)
    started_at = time.monotonic()

    async with httpx.AsyncClient(timeout=_timeout(timeout), trust_env=False) as client:
        async with client.stream("POST", HERMES_ENDPOINT, headers=_headers(), json=payload) as resp:
            if resp.status_code >= 400:
                text = await resp.aread()
                raise RuntimeError(f"Hermes Gateway HTTP {resp.status_code}: {text.decode('utf-8', errors='ignore')[:1000]}")

            async for raw_line in resp.aiter_lines():
                if time.monotonic() - started_at > timeout:
                    raise TimeoutError(f"Hermes Gateway stream 超过 {int(timeout)} 秒仍未返回有效内容，已主动中断。")
                line = raw_line.strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content


async def hermes_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4500,
    response_format: Optional[Dict[str, Any]] = None,
    timeout: float = 150.0,
    stream: bool = True,
) -> str:
    """调用本地 Hermes Gateway，返回 assistant 回复文本。默认使用 stream 避免长响应整段超时。"""
    if stream:
        chunks: List[str] = []
        async for chunk in hermes_chat_stream(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            timeout=timeout,
        ):
            chunks.append(chunk)
        content = "".join(chunks).strip()
        if not content:
            raise RuntimeError("Hermes Gateway 返回空内容：当前 Hermes 模型/API Server 调用可能超时或被上游中断。")
        return content

    payload = _build_payload(messages, model, temperature, max_tokens, response_format, stream=False)
    async with httpx.AsyncClient(timeout=_timeout(timeout), trust_env=False) as client:
        resp = await client.post(HERMES_ENDPOINT, headers=_headers(), json=payload)
        resp.raise_for_status()
        data = resp.json()

    try:
        return data["choices"][0]["message"]["content"] or ""
    except Exception as e:
        raise RuntimeError(f"Hermes Gateway 返回格式异常: {data}") from e
