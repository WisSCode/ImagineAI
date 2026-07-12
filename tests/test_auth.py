"""Registro, login, sesiones y sus reglas."""


def test_register_and_me(client):
    resp = client.post(
        "/api/auth/register", json={"username": "ana.lopez", "password": "clave-segura"}
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "ana.lopez"
    me = client.get("/api/auth/me").json()
    assert me["user"]["username"] == "ana.lopez"


def test_register_duplicate_username(client):
    creds = {"username": "duplicado", "password": "clave-segura"}
    assert client.post("/api/auth/register", json=creds).status_code == 200
    assert client.post("/api/auth/register", json=creds).status_code == 409
    # Insensible a mayúsculas: el mismo nombre no se puede reusar
    creds["username"] = "DUPLICADO"
    assert client.post("/api/auth/register", json=creds).status_code == 409


def test_register_validation(client):
    r = client.post("/api/auth/register", json={"username": "ab", "password": "clave-segura"})
    assert r.status_code == 422  # muy corto (pydantic)
    r = client.post(
        "/api/auth/register", json={"username": "nombre con espacios", "password": "clave-segura"}
    )
    assert r.status_code == 400


def test_login_wrong_password(client, user):
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": user["username"], "password": "incorrecta1"}
    )
    assert r.status_code == 401


def test_logout_clears_session(client, user):
    assert client.get("/api/auth/me").json()["user"] is not None
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["user"] is None


def test_protected_endpoints_require_login(client):
    client.post("/api/auth/logout")
    assert client.get("/api/jobs").status_code == 401
    assert (
        client.post("/api/generate", json={"prompt": "una página de prueba"}).status_code == 401
    )


def test_password_hashing_roundtrip():
    from backend import auth

    stored = auth.hash_password("mi-clave")
    assert auth.verify_password("mi-clave", stored)
    assert not auth.verify_password("otra-clave", stored)
    assert not auth.verify_password("mi-clave", "basura-sin-formato")
