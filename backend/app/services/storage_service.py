import io
import shutil
from pathlib import Path
from typing import Optional
from app.core.config import settings

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None
    BotoConfig = None


class BaseStorageService:
    def save_bytes(
        self, file_bytes: bytes, relative_path: str, content_type: str = "image/webp"
    ) -> str:
        raise NotImplementedError

    def get_bytes(self, relative_path: str) -> Optional[bytes]:
        raise NotImplementedError

    def delete_prefix(self, prefix: str) -> None:
        raise NotImplementedError

    def get_presigned_url(
        self, relative_path: str, expiration_seconds: int = 3600
    ) -> Optional[str]:
        return None

    def file_exists(self, relative_path: str) -> bool:
        raise NotImplementedError


class LocalStorageService(BaseStorageService):
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        return self.base_dir / relative_path

    def save_bytes(
        self, file_bytes: bytes, relative_path: str, content_type: str = "image/webp"
    ) -> str:
        target_path = self._resolve(relative_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(file_bytes)
        return str(target_path)

    def get_bytes(self, relative_path: str) -> Optional[bytes]:
        target_path = self._resolve(relative_path)
        if not target_path.exists():
            return None
        return target_path.read_bytes()

    def delete_prefix(self, prefix: str) -> None:
        target_path = self._resolve(prefix)
        if target_path.exists():
            if target_path.is_dir():
                shutil.rmtree(target_path, ignore_errors=True)
            else:
                target_path.unlink(missing_ok=True)

    def file_exists(self, relative_path: str) -> bool:
        return self._resolve(relative_path).exists()


class S3StorageService(BaseStorageService):
    """
    Cliente de almacenamiento en la nube compatible con S3 estándar:
    - AWS S3
    - Oracle Cloud Infrastructure (OCI) Object Storage con API compatible S3
    - MinIO, Wasabi, Cloudflare R2
    """

    def __init__(
        self,
        bucket_name: str,
        endpoint_url: Optional[str] = None,
        region_name: str = "us-east-1",
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        use_ssl: bool = True,
    ):
        if not boto3:
            raise RuntimeError(
                "La biblioteca 'boto3' es requerida para el almacenamiento S3. Instálela con 'pip install boto3'."
            )
        self.bucket_name = bucket_name
        self.endpoint_url = endpoint_url
        self.region_name = region_name

        boto_config = BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        )

        session = boto3.session.Session()
        self.client = session.client(
            service_name="s3",
            region_name=self.region_name,
            endpoint_url=self.endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            use_ssl=use_ssl,
            config=boto_config,
        )

    def _normalize_key(self, relative_path: str) -> str:
        return relative_path.lstrip("/").replace("\\", "/")

    def save_bytes(
        self, file_bytes: bytes, relative_path: str, content_type: str = "image/webp"
    ) -> str:
        key = self._normalize_key(relative_path)
        self.client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
        )
        return key

    def get_bytes(self, relative_path: str) -> Optional[bytes]:
        key = self._normalize_key(relative_path)
        try:
            resp = self.client.get_object(Bucket=self.bucket_name, Key=key)
            return resp["Body"].read()
        except Exception:
            return None

    def delete_prefix(self, prefix: str) -> None:
        prefix_key = self._normalize_key(prefix)
        if not prefix_key.endswith("/"):
            prefix_key += "/"
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix_key):
                if "Contents" in page:
                    objects_to_delete = [{"Key": obj["Key"]} for obj in page["Contents"]]
                    if objects_to_delete:
                        self.client.delete_objects(
                            Bucket=self.bucket_name,
                            Delete={"Objects": objects_to_delete},
                        )
        except Exception:
            pass

    def get_presigned_url(
        self, relative_path: str, expiration_seconds: int = 3600
    ) -> Optional[str]:
        key = self._normalize_key(relative_path)
        try:
            url = self.client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expiration_seconds,
            )
            return url
        except Exception:
            return None

    def file_exists(self, relative_path: str) -> bool:
        key = self._normalize_key(relative_path)
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception:
            return False


def get_storage_service() -> BaseStorageService:
    """
    Fábrica del servicio de almacenamiento según configuración del entorno.
    """
    if settings.STORAGE_BACKEND.upper() == "S3" and settings.S3_BUCKET_NAME:
        return S3StorageService(
            bucket_name=settings.S3_BUCKET_NAME,
            endpoint_url=settings.S3_ENDPOINT_URL,
            region_name=settings.S3_REGION_NAME,
            access_key_id=settings.S3_ACCESS_KEY_ID,
            secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            use_ssl=settings.S3_USE_SSL,
        )
    return LocalStorageService(base_dir=settings.UPLOADS_DIR)


storage_service = get_storage_service()
