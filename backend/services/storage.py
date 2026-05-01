"""
MinIO 存储服务
统一管理 AI 生成内容的存储
"""

import os
import uuid
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import timedelta

try:
    from minio import Minio
    from minio.error import S3Error
except ImportError:
    Minio = None
    S3Error = Exception


class MinIOStorage:
    """MinIO 对象存储服务"""
    
    _instance = None
    _client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        self.enabled = self._get_config('MINIO_ENABLED', 'false').lower() == 'true'
        self.endpoint = self._get_config('MINIO_ENDPOINT', 'localhost:9000')
        self.access_key = self._get_config('MINIO_ACCESS_KEY', '')
        self.secret_key = self._get_config('MINIO_SECRET_KEY', '')
        self.bucket = self._get_config('MINIO_BUCKET', 'ai-drama')
        self.base_path = self._get_config('MINIO_BASE_PATH', 'projects')
        self.public_base_url = self._get_config('MINIO_PUBLIC_BASE_URL', '')
        self.use_ssl = self._get_config('MINIO_USE_SSL', 'false').lower() == 'true'
        
        if self.enabled and Minio and self.access_key and self.secret_key:
            try:
                self._client = Minio(
                    self.endpoint,
                    access_key=self.access_key,
                    secret_key=self.secret_key,
                    secure=self.use_ssl
                )
                # 确保 bucket 存在
                if not self._client.bucket_exists(self.bucket):
                    self._client.make_bucket(self.bucket)
                    # 设置 bucket 为公开读取
                    self._client.set_bucket_policy(
                        self.bucket,
                        '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":["*"]},"Action":["s3:GetObject"],"Resource":["arn:aws:s3:::' + self.bucket + '/*"]}]}'
                    )
                print(f"MinIO connected: {self.endpoint}/{self.bucket}")
            except Exception as e:
                print(f"MinIO connection failed: {e}")
                self._client = None
        
        self._initialized = True
    
    def _get_config(self, key: str, default: str) -> str:
        """从环境变量或 .env 文件加载配置"""
        # 先从环境变量
        value = os.environ.get(key, '')
        if value:
            return value
        
        # 从 .env 文件
        env_file = Path(__file__).parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"\'')
        
        return default
    
    def _get_project_path(self, script_id: str, *parts) -> str:
        """生成 MinIO 对象路径"""
        components = [self.base_path, script_id] + list(parts)
        return "/".join(components)
    
    def upload_file(
        self, 
        script_id: str, 
        file_path: str, 
        folder: str = "",
        content_type: str = "application/octet-stream"
    ) -> Optional[str]:
        """
        上传文件到 MinIO
        
        Args:
            script_id: 剧本ID
            file_path: 本地文件路径
            folder: 子文件夹 (如 'characters', 'audio', 'video')
            content_type: 文件 MIME 类型
        
        Returns:
            MinIO 公开访问 URL 或 None
        """
        if not self.enabled or not self._client:
            print("MinIO not enabled or not connected, returning local path")
            return file_path
        
        try:
            file_name = Path(file_path).name
            object_name = self._get_project_path(script_id, folder, file_name) if folder else self._get_project_path(script_id, file_name)
            
            self._client.fput_object(
                self.bucket,
                object_name,
                file_path,
                content_type=content_type
            )
            
            # 生成公开访问 URL
            url = f"{self.public_base_url}/{object_name}"
            print(f"Uploaded to MinIO: {url}")
            return url
            
        except S3Error as e:
            print(f"MinIO upload error: {e}")
            return file_path
        except Exception as e:
            print(f"Upload error: {e}")
            return file_path
    
    def upload_bytes(
        self,
        script_id: str,
        data: bytes,
        file_name: str,
        folder: str = "",
        content_type: str = "application/octet-stream"
    ) -> Optional[str]:
        """
        上传字节数据到 MinIO
        """
        if not self.enabled or not self._client:
            return None
        
        try:
            object_name = self._get_project_path(script_id, folder, file_name) if folder else self._get_project_path(script_id, file_name)
            
            self._client.put_object(
                self.bucket,
                object_name,
                data,
                length=len(data),
                content_type=content_type
            )
            
            url = f"{self.public_base_url}/{object_name}"
            print(f"Uploaded bytes to MinIO: {url}")
            return url
            
        except Exception as e:
            print(f"MinIO bytes upload error: {e}")
            return None
    
    def download_file(self, object_name: str, dest_path: str) -> bool:
        """从 MinIO 下载文件"""
        if not self.enabled or not self._client:
            return False
        
        try:
            self._client.fget_object(self.bucket, object_name, dest_path)
            return True
        except Exception as e:
            print(f"MinIO download error: {e}")
            return False
    
    def get_presigned_url(self, object_name: str, expires: int = 3600) -> Optional[str]:
        """获取预签名 URL (用于私有文件访问)"""
        if not self.enabled or not self._client:
            return None
        
        try:
            url = self._client.presigned_get_object(self.bucket, object_name, expires=timedelta(seconds=expires))
            return url
        except Exception as e:
            print(f"Presigned URL error: {e}")
            return None
    
    def delete_file(self, object_name: str) -> bool:
        """删除 MinIO 中的文件"""
        if not self.enabled or not self._client:
            return False
        
        try:
            self._client.remove_object(self.bucket, object_name)
            return True
        except Exception as e:
            print(f"MinIO delete error: {e}")
            return False
    
    def list_files(self, script_id: str, folder: str = "") -> list:
        """列出指定剧本文件夹中的文件"""
        if not self.enabled or not self._client:
            return []
        
        try:
            prefix = self._get_project_path(script_id, folder) if folder else self._get_project_path(script_id)
            objects = self._client.list_objects(self.bucket, prefix=prefix, recursive=True)
            return [obj.object_name for obj in objects]
        except Exception as e:
            print(f"MinIO list error: {e}")
            return []
    
    def get_status(self) -> Dict[str, Any]:
        """获取 MinIO 连接状态"""
        return {
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            "connected": self._client is not None,
            "public_base_url": self.public_base_url
        }


# 全局单例
_storage = None

def get_storage() -> MinIOStorage:
    """获取存储服务单例"""
    global _storage
    if _storage is None:
        _storage = MinIOStorage()
    return _storage
