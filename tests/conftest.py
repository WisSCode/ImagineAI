"""Config de tests: aísla directorios ANTES de importar el backend (config.py
resuelve rutas en el import) y provee un cliente y un LLM falso."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="softwareix-tests-")
os.environ["SIX_GENERATED_DIR"] = os.path.join(_tmp, "generated")
os.environ["SIX_DATA_DIR"] = os.path.join(_tmp, "data")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import llm  # noqa: E402
from backend.main import app  # noqa: E402


class FakeLLM:
    """Sustituye llm.stream_chat: entrega respuestas guionadas en orden.
    
    Soporta:
    - push(): agregar respuestas normales
    - push_empty(): simular respuestas vacías (fallo del modelo)
    - fail_on_count(n): lanzar excepción después de n llamadas
    """

    def __init__(self):
        self.responses: list[str | None] = []  # None = respuesta vacía simulada
        self.calls: list[list[dict]] = []  # mensajes recibidos, para aserciones
        self.options_calls: list[dict] = []  # opciones recibidas, para aserciones
        self.call_count: int = 0
        self.fail_after_count: int | None = None
        self._fail_exception: Exception | None = None

    def push(self, *texts: str) -> None:
        """Agrega respuestas normales al queue."""
        self.responses.extend(texts)

    def push_empty(self) -> None:
        """Simula una respuesta vacía (fallo del modelo)."""
        self.responses.append(None)

    def fail_after_count(self, n: int, exc: Exception | None = None) -> None:
        """Lanza excepción después de n llamadas exitosas."""
        self.fail_after_count = n
        self._fail_exception = exc or Exception("Fallo simulado en stream_chat")

    async def stream_chat(self, messages, model, options):
        self.calls.append(messages)
        self.options_calls.append(options)
        self.call_count += 1

        # Verificar si debe fallar
        if self.fail_after_count is not None and self.call_count > self.fail_after_count:
            raise self._fail_exception

        if not self.responses:
            raise AssertionError("FakeLLM sin respuestas guionadas para esta llamada")
        
        response = self.responses.pop(0)
        
        # Si es None, devolver cadena vacía (simula respuesta vacía del modelo)
        if response is None:
            text = ""
        else:
            text = response
        
        # Emitir en trozos para ejercitar el buffering del SSE.
        for i in range(0, len(text), 40):
            yield text[i : i + 40]


@pytest.fixture()
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm, "stream_chat", fake.stream_chat)

    async def alive():
        return True

    monkeypatch.setattr(llm, "is_alive", alive)
    return fake


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


_counter = {"n": 0}


@pytest.fixture()
def user(client):
    """Registra (y deja logueado) un usuario único por test."""
    _counter["n"] += 1
    creds = {"username": f"tester{_counter['n']}", "password": "secreto123"}
    resp = client.post("/api/auth/register", json=creds)
    assert resp.status_code == 200, resp.text
    return creds
