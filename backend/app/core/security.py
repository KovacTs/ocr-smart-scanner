import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import bcrypt
import jwt
from app.core.config import settings


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def create_access_token(
    subject: str | Any, expires_delta: Optional[timedelta] = None
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": "access",
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(
    subject: str | Any, expires_delta: Optional[timedelta] = None
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except jwt.PyJWTError:
        return None


def compute_file_signature(
    user_id: str, scan_id: str, filename: str, expires: int
) -> str:
    """
    Calcula una firma HMAC-SHA256 específica para un archivo individual (user_id + scan_id + filename)
    y un timestamp de expiración. Nunca expone el token de sesión del usuario.
    """
    message = f"{user_id}:{scan_id}:{filename}:{expires}".encode("utf-8")
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def create_signed_file_url(
    relative_url: str,
    user_id: str,
    scan_id: str,
    filename: str,
    expires_in: Optional[int] = None,
) -> str:
    """
    Genera una URL relativa pre-firmada de corta duración (?expires=...&sig=...) para acceder
    a un archivo WebP específico sin exponer jamás el JWT de sesión en la query string.
    """
    ttl = (
        expires_in
        if expires_in is not None
        else settings.SIGNED_URL_EXPIRE_SECONDS
    )
    expires = int(time.time()) + ttl
    sig = compute_file_signature(user_id, scan_id, filename, expires)
    separator = "&" if "?" in relative_url else "?"
    return f"{relative_url}{separator}expires={expires}&sig={sig}"


def verify_signed_file_signature(
    user_id: str,
    scan_id: str,
    filename: str,
    expires: Optional[int],
    sig: Optional[str],
) -> bool:
    """
    Verifica en tiempo constante (timing-attack safe) si la firma y expiración de una URL
    pre-firmada son válidas para el archivo solicitado.
    """
    if expires is None or not sig:
        return False
    if int(time.time()) > int(expires):
        return False
    expected_sig = compute_file_signature(user_id, scan_id, filename, int(expires))
    return hmac.compare_digest(expected_sig, sig)

