"""
Production LLM client for the AI drama system.

This client is intentionally independent from Hermes. The product backend owns
its model configuration through backend/.env:

  LLM_API_BASE=https://api.deepseek.com/v1
  LLM_API_KEY=...
  LLM_MODEL=deepseek-chat

Any OpenAI-compatible chat completion provider can be used.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


DEFAULT_LLM_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_LLM_MODEL = "deepseek-chat"


def _read_env_file_value(key: str) -> str:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return ""
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, value = line.split("=", 1)
            if k.strip() == key:
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _get_config(name: str, default: str = "") -> str:
    return (os.environ.get(name) or _read_env_file_value(name) or default).strip()


def _is_local_url(url: str) -> bool:
    return (
        url.startswith("http://127.0.0.1")
        or url.startswith("http://localhost")
        or url.startswith("http://0.0.0.0")
        or url.startswith("http://[::1]")
    )


def _get_external_proxy(target_url: str = "") -> Optional[str]:
    if target_url and _is_local_url(target_url):
        return None
    proxy = (
        os.environ.get("EXTERNAL_PROXY")
        or _read_env_file_value("EXTERNAL_PROXY")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or ""
    ).strip()
    return proxy or None


def _extract_json_text(content: str) -> str:
    text = (content or "").strip().lstrip("\ufeff")
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    if text.startswith("{") and text.endswith("}"):
        return text

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            _obj, end = decoder.raw_decode(text[idx:])
            return text[idx:idx + end]
        except Exception:
            continue

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0)
    return text


def _prepare_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    prepared = [dict(message) for message in messages]
    if _get_config("LLM_DISABLE_THINKING", "false").lower() != "true":
        return prepared
    for message in reversed(prepared):
        if message.get("role") == "user":
            content = message.get("content") or ""
            if "/no_think" not in content:
                message["content"] = f"{content}\n\n/no_think"
            break
    return prepared


def _strip_thinking(content: str) -> str:
    text = content or ""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    # Qwen/LM Studio 有时把思考段作为普通文本输出；如果后面有 JSON，优先保留 JSON 起点后的内容。
    first_json = text.find("{")
    if first_json > 0:
        return text[first_json:].strip()
    return text


async def llm_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4000,
    timeout: float = 90.0,
    response_format: Optional[Dict[str, Any]] = None,
) -> str:
    """Call the configured OpenAI-compatible chat endpoint."""
    api_key = _get_config("LLM_API_KEY")
    if not api_key or api_key == "***":
        raise RuntimeError("未配置 LLM_API_KEY，请在 backend/.env 配置生产语言模型。")

    base_url = _get_config("LLM_API_BASE", DEFAULT_LLM_API_BASE).rstrip("/")
    endpoint = _get_config("LLM_CHAT_ENDPOINT") or f"{base_url}/chat/completions"
    selected_model = model or _get_config("LLM_MODEL", DEFAULT_LLM_MODEL)

    payload: Dict[str, Any] = {
        "model": selected_model,
        "messages": _prepare_messages(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    reasoning_effort = _get_config("LLM_REASONING_EFFORT")
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if _get_config("LLM_DISABLE_THINKING", "false").lower() == "true":
        # Different OpenAI-compatible local runtimes use different names. Unsupported
        # keys are ignored by LM Studio, but they help on runtimes that honor them.
        payload["thinking"] = False
        payload["enable_thinking"] = False
    if response_format:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    proxy = _get_external_proxy(endpoint)

    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=timeout, trust_env=False) as client:
                resp = await client.post(endpoint, headers=headers, json=payload)
                if resp.status_code == 400 and payload.get("response_format"):
                    # LM Studio / 部分本地 OpenAI-compatible 服务不支持 response_format。
                    fallback_payload = dict(payload)
                    fallback_payload.pop("response_format", None)
                    resp = await client.post(endpoint, headers=headers, json=fallback_payload)
                if resp.status_code == 429 and attempt < 2:
                    retry_after = int(resp.headers.get("retry-after", 0) or 0)
                    await asyncio.sleep(retry_after or (attempt + 1) * 2)
                    continue
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                message = choice.get("message") or {}
                content = message.get("content") or choice.get("text") or ""
                if not content and message.get("reasoning_content"):
                    raise RuntimeError("模型只返回了 reasoning_content，未生成最终回答；请提高 max_tokens 或换非 thinking 模型。")
                return _strip_thinking(content)
        except Exception as e:
            last_error = e
            if attempt < 2:
                await asyncio.sleep((attempt + 1) * 1.5)
                continue
    raise RuntimeError(f"LLM 调用失败: {last_error}")


async def llm_chat_json(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 4000,
    timeout: float = 90.0,
) -> Dict[str, Any]:
    """Call LLM and parse a JSON object from the answer."""
    content = _strip_thinking(await llm_chat(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        response_format={"type": "json_object"} if _get_config("LLM_RESPONSE_FORMAT_JSON", "true").lower() == "true" else None,
    ))
    json_text = _extract_json_text(content)
    return json.loads(json_text)
