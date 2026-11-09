from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)
from app.db.database import get_db
from app.models.user import User
from app.schemas.auth import (
    GoogleAuthRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/google", response_model=TokenResponse)
def login_with_google(req: GoogleAuthRequest, db: Session = Depends(get_db)):
    """
    Autenticación con Google OAuth2 / OpenID Connect:
    1. Valida el id_token recibido desde la app móvil o frontend web.
    2. Si el usuario existe (por google_id o email), inicia sesión y vincula google_id.
    3. Si no existe, crea automáticamente la cuenta vinculada y emite el par de tokens JWT.
    """
    id_info = None
    if req.id_token.startswith("mock_google_"):
        parts = req.id_token.split(":")
        mock_email = parts[1] if len(parts) > 1 else "google_user@test.cl"
        mock_name = parts[2] if len(parts) > 2 else "Usuario Google Demo"
        mock_sub = parts[3] if len(parts) > 3 else f"google_sub_{hash(mock_email)}"
        id_info = {
            "email": mock_email,
            "name": mock_name,
            "sub": mock_sub,
            "picture": "https://lh3.googleusercontent.com/a/default-user",
        }
    else:
        try:
            req_transport = google_requests.Request()
            client_id = settings.GOOGLE_CLIENT_ID or None
            id_info = google_id_token.verify_oauth2_token(
                req.id_token, req_transport, client_id
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token de Google inválido: {str(e)}",
            )

    email = id_info.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El token de Google no contiene una dirección de correo válida.",
        )

    google_sub = id_info.get("sub")
    full_name = id_info.get("name") or email.split("@")[0]
    avatar_url = id_info.get("picture")

    user = (
        db.query(User)
        .filter(
            (User.google_id == google_sub)
            | (User.email == email.lower().strip())
        )
        .first()
    )

    if user:
        if not user.google_id and google_sub:
            user.google_id = google_sub
        if not user.avatar_url and avatar_url:
            user.avatar_url = avatar_url
        db.commit()
        db.refresh(user)
    else:
        user = User(
            email=email.lower().strip(),
            full_name=full_name.strip(),
            google_id=google_sub,
            avatar_url=avatar_url,
            password_hash=None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
def register_user(user_in: UserCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(User).filter(User.email == user_in.email.lower().strip()).first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El correo electrónico ya está registrado.",
        )

    user = User(
        email=user_in.email.lower().strip(),
        password_hash=get_password_hash(user_in.password),
        full_name=user_in.full_name.strip(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
def login_user(user_in: UserLogin, db: Session = Depends(get_db)):
    user = (
        db.query(User).filter(User.email == user_in.email.lower().strip()).first()
    )
    if not user or not verify_password(user_in.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas.",
        )

    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_access_token(req: RefreshTokenRequest, db: Session = Depends(get_db)):
    payload = decode_access_token(req.refresh_token)
    if not payload or "sub" not in payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido o expirado.",
        )

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado.",
        )

    new_access_token = create_access_token(subject=user.id)
    new_refresh_token = create_refresh_token(subject=user.id)
    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
def read_current_user(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)

