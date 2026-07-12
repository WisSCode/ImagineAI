"""Unidades del cliente LLM: la precarga debe cargar el modelo con las MISMAS
opciones de contexto que usa el pipeline, o Ollama lo recarga en la primera
generación (y con el contexto por defecto el KV cache no cabe en la GPU)."""
import asyncio

import httpx

from backend import config, llm


def test_preload_sends_pipeline_num_ctx(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    asyncio.run(llm.preload("modelo-test"))

    payload = captured["json"]
    assert payload["model"] == "modelo-test"
    assert payload["keep_alive"] == config.KEEP_ALIVE
    assert payload["options"]["num_ctx"] == config.CODE_OPTIONS["num_ctx"]


def test_preload_forces_cpu_when_device_is_cpu(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            captured["json"] = json

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    asyncio.run(llm.preload("modelo-test", device="cpu"))

    options = captured["json"]["options"]
    assert options["num_gpu"] == 0
    assert options["num_thread"] > 0


def test_preload_swallows_http_errors(monkeypatch):
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            raise httpx.ConnectError("Ollama caído")

    monkeypatch.setattr(httpx, "AsyncClient", FailingClient)
    asyncio.run(llm.preload("modelo-test"))  # no debe lanzar
