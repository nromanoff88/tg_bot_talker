from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth import AUTH_COOKIE_NAME, create_access_token, require_admin, verify_credentials
from app.config import get_settings
from app.schemas import AuthStatusResponse, LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.post("/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    if not verify_credentials(payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )
    token = create_access_token(payload.username)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.jwt_expire_hours * 3600,
    )
    return {"authenticated": True, "username": payload.username, "token": token}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(key=AUTH_COOKIE_NAME)
    return {"authenticated": False}


@router.get("/me", response_model=AuthStatusResponse)
async def me(username: str = Depends(require_admin)) -> AuthStatusResponse:
    return AuthStatusResponse(authenticated=True, username=username)

