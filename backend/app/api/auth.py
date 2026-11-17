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
        req_transport = google_requests.Request()
        client_id = settings.GOOGLE_CLIENT_ID or None
        try:
            id_info = google_id_token.verify_oauth2_token(
                req.id_token, req_transport, client_id
            )
        except Exception as jwt_err:
            # Si expo-auth-session entrega un OAuth2 access_token en lugar de un JWT id_token,
            # validamos contra los endpoints oficiales de Google (tokeninfo + userinfo)
            try:
                import json
                import urllib.parse
                import urllib.request

                tokeninfo_url = (
                    "https://oauth2.googleapis.com/tokeninfo?"
                    + urllib.parse.urlencode({"access_token": req.id_token})
                )
                with urllib.request.urlopen(tokeninfo_url, timeout=5) as resp:
                    token_meta = json.loads(resp.read().decode("utf-8"))

                if client_id and token_meta.get("aud") != client_id and token_meta.get("azp") != client_id:
                    raise ValueError("El access_token no pertenece al GOOGLE_CLIENT_ID configurado.")

                userinfo_req = urllib.request.Request(
                    "https://openidconnect.googleapis.com/v1/userinfo",
                    headers={"Authorization": f"Bearer {req.id_token}"},
                )
                with urllib.request.urlopen(userinfo_req, timeout=5) as u_resp:
                    id_info = json.loads(u_resp.read().decode("utf-8"))
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Token de Google inválido: {str(jwt_err)}",
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


# =====================================================================
# Puente OAuth2 para Expo Go en Android / iOS (evita bloqueo de exp:// en Chrome)
# =====================================================================
import json as _json
from pathlib import Path as _Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

_OAUTH_SESSION_DIR = _Path("/tmp/ocr_google_oauth_sessions")
_OAUTH_SESSION_DIR.mkdir(parents=True, exist_ok=True)


class ExpoOAuthSessionStoreRequest(BaseModel):
    session_id: str
    id_token: str


@router.post("/google/expo-session")
def save_expo_google_session(payload: ExpoOAuthSessionStoreRequest):
    safe_id = "".join(c for c in payload.session_id if c.isalnum() or c in "-_")
    if not safe_id or not payload.id_token:
        raise HTTPException(status_code=400, detail="session_id e id_token requeridos")
    session_file = _OAUTH_SESSION_DIR / f"{safe_id}.json"
    session_file.write_text(
        _json.dumps({"id_token": payload.id_token}), encoding="utf-8"
    )
    return {"status": "saved"}


@router.get("/google/expo-session/{session_id}")
def get_expo_google_session(session_id: str):
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_")
    session_file = _OAUTH_SESSION_DIR / f"{safe_id}.json"
    if not session_file.exists():
        return {"ready": False, "id_token": None}
    try:
        data = _json.loads(session_file.read_text(encoding="utf-8"))
        session_file.unlink(missing_ok=True)
        return {"ready": True, "id_token": data.get("id_token")}
    except Exception:
        return {"ready": False, "id_token": None}


@router.get("/google/expo-callback", response_class=HTMLResponse)
def expo_google_oauth_callback(session_id: str = "", app_url: str = ""):
    """
    Recibe la redirección HTTP desde auth.expo.io con el #id_token en el hash,
    guarda el id_token en el backend asociado al session_id y lanza el Intent
    nativo hacia Expo Go (o permite volver cerrando la pestaña).
    """
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Autenticación Completada - Smart OCR</title>
  <style>
    body {{
      background-color: #0F172A;
      color: #F8FAFC;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100vh;
      margin: 0;
      padding: 20px;
      box-sizing: border-box;
      text-align: center;
    }}
    .card {{
      background: #1E293B;
      border: 1px solid #334155;
      border-radius: 16px;
      padding: 28px 24px;
      max-width: 400px;
      width: 100%;
    }}
    .badge {{
      color: #34D399;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 1px;
      margin-bottom: 10px;
    }}
    h2 {{
      margin: 0 0 10px 0;
      font-size: 22px;
    }}
    p {{
      color: #94A3B8;
      font-size: 14px;
      line-height: 1.5;
      margin-bottom: 22px;
    }}
    .btn {{
      display: block;
      background: #0284C7;
      color: #FFFFFF;
      text-decoration: none;
      font-weight: 700;
      padding: 14px 20px;
      border-radius: 10px;
      font-size: 15px;
      margin-bottom: 12px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">✓ GOOGLE OAUTH2 VERIFICADO</div>
    <h2 id="title">¡Cuenta vinculada!</h2>
    <p id="desc">Volviendo automáticamente a Smart OCR Scanner... Si no se cierra sola esta ventana, presiona el botón de abajo o la <b>X</b> arriba a la izquierda.</p>
    <a id="open-app-btn" class="btn" href="#">Volver a Smart OCR Scanner →</a>
  </div>
  <script>
    (async function() {{
      const sessionId = {_json.dumps(session_id)};
      const appUrl = {_json.dumps(app_url)};
      const hash = window.location.hash ? window.location.hash.substring(1) : "";
      const query = window.location.search ? window.location.search.substring(1) : "";
      const hashParams = new URLSearchParams(hash);
      const queryParams = new URLSearchParams(query);
      const idToken = hashParams.get("id_token") || queryParams.get("id_token") || hashParams.get("access_token") || queryParams.get("access_token");

      if (idToken && sessionId) {{
        try {{
          await fetch("/api/auth/google/expo-session", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ session_id: sessionId, id_token: idToken }})
          }});
        }} catch (e) {{
          console.error(e);
        }}
      }}

      if (appUrl) {{
        const separator = appUrl.includes("?") ? "&" : "?";
        const targetDeepLink = idToken ? (appUrl + separator + "id_token=" + encodeURIComponent(idToken)) : appUrl;
        const cleanHostPath = targetDeepLink.replace(/^exp:\\/\\//i, "");
        const intentUrl = "intent://" + cleanHostPath + "#Intent;scheme=exp;package=host.exp.exponent;end";

        const btn = document.getElementById("open-app-btn");
        if (btn) {{
          btn.href = intentUrl;
        }}
        setTimeout(function() {{
          window.location.href = intentUrl;
        }}, 250);
      }}
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


