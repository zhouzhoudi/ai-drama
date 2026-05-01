import requests
import time
import json
from pathlib import Path
from typing import Dict, List, Optional
import os

class MinimaxVideoGenerator:
    """MiniMax 视频生成器"""
    
    def __init__(self):
        self.api_key = self._load_api_key()
        self.api_base = "https://api.minimax.chat/v1/video_generation"
        self.data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")
        
    def _load_api_key(self) -> str:
        return "sk-cp-M90YUmz8d1nfNrCA3N0DXjNdAHI-Hh-7-JgF6MMi_xIG_WG9RYZj8VF77H7yn9Ac2uHv6xBcGDy7Pcs_--Ruk5I4xVwkNlnl8006_HKp9K7tsV7T9_orN8Q"

    def generate_shot_video(self, script_id: str, shot: Dict, characters: List[Dict], aspect_ratio: str = "9:16") -> str:
        prompt = f"{shot.get('shot_type', '')}, {shot.get('content_description', '')}, cinematic, masterpiece"
        print(f"[MiniMax Video] 开始生成视频任务: {prompt[:50]}...")
        
        headers = {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        
        # 兼容 MiniMax 格式处理
        if aspect_ratio == "9:16": aspect = "9:16"
        elif aspect_ratio == "16:9": aspect = "16:9"
        else: aspect = "1:1"
            
        payload = {
            "model": "video-01",
            "prompt": prompt,
            "aspect_ratio": aspect
        }
        
        try:
            # 发起任务
            resp = requests.post(self.api_base, headers=headers, json=payload, timeout=30)
            if resp.status_code != 200:
                print(f"[MiniMax Video] 请求失败: {resp.text}")
                return ""
                
            task_id = resp.json().get("task_id")
            if not task_id: 
                print(f"[MiniMax Video] 没拿到 task_id: {resp.json()}")
                return ""
            
            print(f"[MiniMax Video] 任务提交成功，task_id: {task_id}，开始轮询...")
            
            # 轮询
            max_wait = 600
            start_time = time.time()
            while time.time() - start_time < max_wait:
                status_resp = requests.get(f"{self.api_base}?task_id={task_id}", headers=headers, timeout=10)
                if status_resp.status_code == 200:
                    res = status_resp.json()
                    status = res.get("status")
                    print(f"[MiniMax Video] 任务 {task_id} 状态: {status}")
                    
                    if status == "Success":
                        file_id = res.get("file_id")
                        print(f"[MiniMax Video] 生成成功！FileID: {file_id}")
                        
                        # 通过 file_id 获取下载链接 (文件检索接口)
                        file_url_resp = requests.get(f"https://api.minimax.chat/v1/files/retrieve?file_id={file_id}", headers={"authorization": f"Bearer {self.api_key}"})
                        if file_url_resp.status_code == 200:
                            download_url = file_url_resp.json().get("file", {}).get("download_url")
                            if download_url:
                                print(f"[MiniMax Video] 获取到真实下载地址: {download_url[:50]}...")
                                return download_url
                                
                        return f"minimax_video_{file_id}.mp4"
                    elif status == "Fail":
                        print("[MiniMax Video] 生成失败！")
                        return ""
                time.sleep(10)
        except Exception as e:
            print(f"[MiniMax Video] 发生异常: {e}")
            
        return ""
