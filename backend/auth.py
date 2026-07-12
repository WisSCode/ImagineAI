"""Autenticación: registro/login con PBKDF2 y sesiones por cookie HttpOnly."""
import hashlib
import hmac
import re
import secrets

from fastapi import HTTPException, Request, Response

from . import config, db

_PBKDF2_ITERS = 200_000
USERNAME_RE = re.compile(r"^[\w.\-]{3,32}$")
COOKIE_NAME = "six_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), _PBKDF2_ITERS
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def register(username: str, password: str) -> int:
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(
            400, "El usuario debe tener 3-32 caracteres (letras, números, ., -, _)."
        )
    if len(password) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres.")
    if db.get_user_by_name(username):
        raise HTTPException(409, "Ese nombre de usuario ya existe.")
    return db.create_user(username, hash_password(password))


def login(username: str, password: str, response: Response) -> dict:
    user = db.get_user_by_name(username.strip())
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Usuario o contraseña incorrectos.")
    token = secrets.token_hex(32)
    db.create_session(token, user["id"], config.SESSION_TTL_SECONDS)
    db.purge_expired_sessions()
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=int(config.SESSION_TTL_SECONDS),
        httponly=True, samesite="lax",
    )
    return {"id": user["id"], "username": user["username"]}


def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.delete_session(token)
    response.delete_cookie(COOKIE_NAME)


def current_user(request: Request):
    """Usuario de la sesión o None (fila de sqlite con id/username)."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return db.get_session_user(token)


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Inicia sesión para continuar.")
    return user
