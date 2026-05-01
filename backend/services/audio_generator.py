"""
音频生成服务
使用 MiniMax TTS v2 API 生成配音
"""

import requests
import base64
from pathlib import Path
from typing import Dict, Optional
import json
import hashlib

from .storage import get_storage


class AudioGenerator:
    """音频生成器"""
    
    def __init__(self):
        self.api_key = self._load_api_key()
        self.api_base = "https://api.minimaxi.com/v1/t2a_v2"
        self.data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")
        self.storage = get_storage()
    
    def _load_api_key(self) -> str:
        """从环境变量 / backend/.env / ~/.hermes/.env 加载 MiniMax CN API Key。"""
        import os
        from pathlib import Path

        def clean(value: str) -> str:
            return (value or "").strip().strip('"').strip("'")

        api_key = clean(os.environ.get("MINIMAX_CN_API_KEY", ""))
        if api_key and api_key != "***":
            return api_key

        for env_file in [Path(__file__).parent.parent / ".env", Path.home() / ".hermes" / ".env"]:
            if not env_file.exists():
                continue
            try:
                for raw in env_file.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() == "MINIMAX_CN_API_KEY":
                        value = clean(value)
                        if value and value != "***":
                            return value
            except Exception as e:
                print(f"读取 MiniMax .env 失败 {env_file}: {e}")

        return ""
    
    async def generate(
        self,
        text: str,
        voice_id: str = "female-tianmei",
        speed: float = 1.0,
        pitch: float = 0,
        output_path: Optional[str] = None
    ) -> str:
        """
        生成配音
        
        Args:
            text: 配音文本
            voice_id: 音色ID
            speed: 语速 (0.5-2.0)
            pitch: 音调 (-10到10)
            output_path: 输出路径
        
        Returns:
            生成的音频文件路径
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "speech-2.8-hd",
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": speed,
                "vol": 1.0,
                "pitch": pitch
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1
            }
        }
        
        response = requests.post(
            self.api_base,
            headers=headers,
            json=payload,
            timeout=60
        )
        
        if response.status_code != 200:
            raise Exception(f"MiniMax TTS API 错误: {response.status_code} - {response.text}")
        
        result = response.json()
        
        # 解析 hex 编码的音频
        audio_hex = result["data"]["audio"]
        audio_data = bytes.fromhex(audio_hex)
        
        # 生成唯一文件名
        text_hash = hashlib.md5(text.encode()).hexdigest()[:8]
        
        # 保存音频到本地临时目录
        if output_path:
            audio_path = Path(output_path)
        else:
            audio_dir = self.data_dir / "temp_audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio_path = audio_dir / f"audio_{text_hash}.mp3"
        
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(audio_data)
        
        return str(audio_path)
    
    async def upload_to_minio(
        self,
        script_id: str,
        local_path: str,
        folder: str = "audio"
    ) -> str:
        """
        将本地音频文件上传到 MinIO
        
        Args:
            script_id: 剧本ID
            local_path: 本地文件路径
            folder: MinIO 文件夹
        
        Returns:
            MinIO 公开访问 URL
        """
        return self.storage.upload_file(
            script_id=script_id,
            file_path=local_path,
            folder=folder,
            content_type="audio/mpeg"
        )
    
    async def generate_for_shot(
        self,
        script_id: str,
        shot_id: str,
        text: str,
        voice_id: str = "female-tianmei"
    ) -> str:
        """
        为分镜生成配音
        
        Args:
            script_id: 剧本ID
            shot_id: 分镜ID
            text: 配音文本
            voice_id: 音色ID
        
        Returns:
            音频文件路径
        """
        # 创建分镜目录
        shot_dir = self.data_dir / script_id / "shots" / shot_id
        shot_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = shot_dir / "audio.mp3"
        
        return await self.generate(
            text=text,
            voice_id=voice_id,
            output_path=str(output_path)
        )
    
    async def batch_generate(
        self,
        script_id: str,
        dialogues: list  # [(shot_id, text, voice_id), ...]
    ) -> Dict[str, str]:
        """
        批量生成配音
        
        Args:
            script_id: 剧本ID
            dialogues: 对话列表
        
        Returns:
            {shot_id: audio_path, ...}
        """
        results = {}
        
        for shot_id, text, voice_id in dialogues:
            try:
                audio_path = await self.generate_for_shot(
                    script_id=script_id,
                    shot_id=shot_id,
                    text=text,
                    voice_id=voice_id
                )
                results[shot_id] = audio_path
            except Exception as e:
                print(f"生成配音失败 {shot_id}: {e}")
                results[shot_id] = ""
        
        return results


# 音色映射
VOICE_OPTIONS = {
    # 女声
    "female-tianmei": "甜美女声",
    "female-yujing": "御姐音",
    "female-xiaowanzi": "活泼女声",
    "female-shanshan": "温柔女声",
    "female-xiaohei": "低沉女声",
    
    # 男声
    "male-qn-qingse": "清亮男声",
    "male-yunyang": "成熟男声",
    "male-xiaohei": "低沉男声",
    "male-xiaowanzi": "活泼男声",
    "male-jiuxu": "磁性男声",
}


def get_voice_options() -> Dict[str, str]:
    """获取可选音色列表"""
    return VOICE_OPTIONS


def suggest_voice_for_character(character: Dict) -> str:
    """
    根据角色特征建议音色
    
    Args:
        character: 角色数据
    
    Returns:
        推荐的 voice_id
    """
    gender = character.get("gender", "female")
    personality = character.get("personality", "")
    age_range = character.get("age_range", "25-30")
    
    # 简单规则匹配
    if gender == "female":
        if "活泼" in personality or "可爱" in personality:
            return "female-xiaowanzi"
        elif "温柔" in personality or "文静" in personality:
            return "female-shanshan"
        elif "成熟" in personality or "强势" in personality:
            return "female-yujing"
        else:
            return "female-tianmei"
    else:
        if "活泼" in personality or "阳光" in personality:
            return "male-xiaowanzi"
        elif "成熟" in personality or "稳重" in personality:
            return "male-yunyang"
        elif "磁性" in personality:
            return "male-jiuxu"
        else:
            return "male-qn-qingse"
