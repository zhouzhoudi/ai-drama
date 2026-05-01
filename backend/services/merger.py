"""
音视频合并服务
使用 FFmpeg 合并分镜视频和音频
"""

import subprocess
from pathlib import Path
from typing import Dict, List, Optional
import json
import shutil

from .production_state import normalize_script


class MediaMerger:
    """音视频合并器"""
    
    def __init__(self):
        self.data_dir = Path("/Users/zhoumi/ai-drama-system/data/scripts")
        self.output_dir = Path("/Users/zhoumi/ai-drama-system/data/outputs")
        self.ffmpeg = self._find_ffmpeg()
    
    def _find_ffmpeg(self) -> str:
        """查找 ffmpeg 路径"""
        # 优先使用本地安装的 ffmpeg
        local_ffmpeg = Path("/usr/local/bin/ffmpeg")
        if local_ffmpeg.exists():
            return str(local_ffmpeg)
        
        # 使用系统 ffmpeg
        return "ffmpeg"
    
    async def merge_script(self, script_id: str) -> Path:
        """
        合并整个剧本的所有分镜
        
        Args:
            script_id: 剧本ID
        
        Returns:
            最终成片路径
        """
        # 加载剧本
        script_path = self.data_dir / script_id / "script.json"
        if not script_path.exists():
            raise FileNotFoundError(f"剧本不存在: {script_id}")
        
        with open(script_path, encoding="utf-8") as f:
            script = normalize_script(json.load(f))
        
        # 创建输出目录
        output_dir = self.output_dir / script_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 收集所有分镜视频和音频
        segments = []
        
        shot_map = {
            str(shot.get("shot_id")): shot
            for scene in script.get("scenes", [])
            for shot in scene.get("shots", [])
        }
        ordered_ids = (script.get("timeline") or {}).get("shot_order") or list(shot_map.keys())

        for shot_id in ordered_ids:
            shot = shot_map.get(str(shot_id))
            if not shot:
                continue
            video_value = shot.get("generated_video_url") or shot.get("video_url") or ""
            video_path = Path(video_value) if video_value and not str(video_value).startswith("http") else self.data_dir / script_id / "shots" / str(shot_id) / "video.mp4"
            audio_path = self.data_dir / script_id / "shots" / str(shot_id) / "audio.mp3"

            if video_path.exists():
                segments.append({
                    "shot_id": shot_id,
                    "video": str(video_path),
                    "audio": str(audio_path) if audio_path.exists() else None,
                    "duration": shot.get("duration", 5)
                })
        
        if not segments:
            raise ValueError("没有可用的分镜视频")
        
        # 合并视频
        final_video = await self._merge_videos([s["video"] for s in segments], output_dir)
        
        # 添加背景音乐（可选）
        # await self._add_background_music(final_video, script, output_dir)
        
        return final_video
    
    async def _merge_videos(self, video_paths: List[str], output_dir: Path) -> Path:
        """合并多个视频文件"""
        if not video_paths:
            raise ValueError("没有视频文件")
        
        if len(video_paths) == 1:
            # 只有一个视频，直接复制
            output_path = output_dir / "final.mp4"
            shutil.copy(video_paths[0], output_path)
            return output_path
        
        # 创建文件列表
        concat_file = output_dir / "concat.txt"
        with open(concat_file, "w") as f:
            for video_path in video_paths:
                f.write(f"file '{video_path}'\n")
        
        # 使用 ffmpeg 合并
        output_path = output_dir / "final.mp4"
        
        cmd = [
            self.ffmpeg,
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-y",
            str(output_path)
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                print(f"FFmpeg 错误: {result.stderr}")
                # 尝试重新编码合并
                output_path = await self._merge_videos_reencode(video_paths, output_dir)
            
        except subprocess.TimeoutExpired:
            raise Exception("视频合并超时")
        
        # 清理临时文件
        concat_file.unlink(missing_ok=True)
        
        return output_path
    
    async def _merge_videos_reencode(self, video_paths: List[str], output_dir: Path) -> Path:
        """重新编码合并视频（当直接复制失败时）"""
        output_path = output_dir / "final.mp4"

        def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        # 先尝试带音轨 concat（OmniVideo 开启 sound 后通常会输出音轨）。
        cmd_with_audio = [self.ffmpeg, "-y"]
        for video_path in video_paths:
            cmd_with_audio.extend(["-i", video_path])

        # concat 需要把每段的 v+a 都喂进去；输出 [vout][aout]
        # 若某些输入不含音轨，这一步可能失败；我们会回退到纯视频合并。
        av_inputs = "".join([f"[{i}:v][{i}:a]" for i in range(len(video_paths))])
        filter_complex_av = f"{av_inputs}concat=n={len(video_paths)}:v=1:a=1[vout][aout]"
        cmd_with_audio.extend([
            "-filter_complex", filter_complex_av,
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-preset", "fast",
            "-crf", "23",
            "-shortest",
            str(output_path),
        ])

        result = run_cmd(cmd_with_audio)
        if result.returncode == 0:
            return output_path

        # 回退：纯视频 concat（旧逻辑），避免因为某段无音轨而导致整个合并失败。
        cmd_video_only = [self.ffmpeg, "-y"]
        for video_path in video_paths:
            cmd_video_only.extend(["-i", video_path])
        v_inputs = "".join([f"[{i}:v]" for i in range(len(video_paths))])
        filter_complex_v = f"{v_inputs}concat=n={len(video_paths)}:v=1:a=0[vout]"
        cmd_video_only.extend([
            "-filter_complex", filter_complex_v,
            "-map", "[vout]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            str(output_path),
        ])

        result2 = run_cmd(cmd_video_only)
        if result2.returncode != 0:
            raise Exception(f"视频合并失败: {result2.stderr}\n(带音轨尝试错误: {result.stderr})")

        return output_path
    
    async def add_audio_to_video(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        audio_offset: float = 0
    ) -> Path:
        """
        为视频添加音频轨道
        
        Args:
            video_path: 视频路径
            audio_path: 音频路径
            output_path: 输出路径
            audio_offset: 音频偏移（秒）
        
        Returns:
            输出文件路径
        """
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-i", audio_path,
            "-itsoffset", str(audio_offset),
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-y",
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        if result.returncode != 0:
            raise Exception(f"添加音频失败: {result.stderr}")
        
        return Path(output_path)
    
    async def add_background_music(
        self,
        video_path: str,
        music_path: str,
        output_path: str,
        music_volume: float = 0.3
    ) -> Path:
        """
        为视频添加背景音乐
        
        Args:
            video_path: 视频路径
            music_path: 音乐路径
            output_path: 输出路径
            music_volume: 背景音乐音量 (0.0-1.0)
        
        Returns:
            输出文件路径
        """
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-i", music_path,
            "-filter_complex",
            f"[1:a]volume={music_volume}[music];[0:a][music]amix=inputs=2:duration=first[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-y",
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        
        if result.returncode != 0:
            raise Exception(f"添加背景音乐失败: {result.stderr}")
        
        return Path(output_path)
    
    def get_video_info(self, video_path: str) -> Dict:
        """获取视频信息"""
        cmd = [
            self.ffmpeg,
            "-i", video_path,
            "-hide_banner"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        
        # 解析输出获取信息
        info = {}
        output = result.stderr
        
        # 简单解析（实际可用 ffprobe）
        if "Video:" in output:
            info["has_video"] = True
        if "Audio:" in output:
            info["has_audio"] = True
        
        return info
