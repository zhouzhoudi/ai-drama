"""
可灵 Kling AK/SK → JWT 鉴权工具

可灵开发者平台不直接颁发 API Key，而是颁发 AK（Access Key）+ SK（Secret Key）。
调用时需用 HS256 算法以 SK 签名生成 JWT，放入 Authorization: Bearer <token>。

官方 JWT Payload 格式：
  {
      "iss": "<AK>",
      "exp": <当前时间 + 有效期秒数>,
      "nbf": <当前时间 - 5秒（防时钟偏差）>
  }

每次调用前重新生成 token（有效期 30 分钟），避免过期。
"""

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

try:
    import jwt as _jwt
    _HAS_JWT = True
except ImportError:
    _HAS_JWT = False


def _read_env_value(env_path: Path, key: str) -> str:
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _load_kling_credential(key: str) -> str:
    """
    按优先级加载 KLING_AK 或 KLING_SK：
      1. 已注入到 os.environ（由 main.py 的 _load_dotenv_file 处理）
      2. backend/.env
      3. ~/.hermes/.env
    """
    v = os.environ.get(key, "")
    if v and v not in ("your_kling_ak_here", "your_kling_sk_here", "***", ""):
        return v

    for path in [
        Path(__file__).parent.parent / ".env",
        Path.home() / ".hermes" / ".env",
    ]:
        v = _read_env_value(path, key)
        if v and v not in ("your_kling_ak_here", "your_kling_sk_here", "***", ""):
            return v
    return ""


def generate_kling_token(expire_seconds: int = 1800) -> str:
    """
    使用 KLING_AK / KLING_SK 生成可灵 JWT。
    - 需要 PyJWT：pip install "PyJWT>=2.8"
    - 未配置 AK/SK 时返回空字符串（后续接口调用会因 401 失败，但不会崩溃）
    """
    ak = _load_kling_credential("KLING_AK")
    sk = _load_kling_credential("KLING_SK")

    if not ak or not sk:
        return ""

    now = int(time.time())
    payload = {
        "iss": ak,
        "exp": now + expire_seconds,
        "nbf": now - 5,
        "iat": now,
    }
    # 优先使用 PyJWT；如果本机没装 PyJWT，则用标准库手写 HS256 JWT，
    # 避免角色图生成因为缺一个依赖直接静默失败。
    if _HAS_JWT:
        token = _jwt.encode(
            payload,
            sk,
            algorithm="HS256",
            headers={"alg": "HS256", "typ": "JWT"},
        )
        return token

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join([
        b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ])
    signature = hmac.new(
        sk.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{b64url(signature)}"


def kling_auth_headers() -> dict:
    """返回带 JWT 的鉴权 Header，可直接传给 requests/httpx。"""
    token = generate_kling_token()
    if not token:
        return {"Content-Type": "application/json"}
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
