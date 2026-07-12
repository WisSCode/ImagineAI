"""Cliente asíncrono para el modelo local vía Ollama, con streaming token a token."""
import json
from typing import AsyncIterator

import httpx

from . import config


class OllamaError(RuntimeError):
    pass


async def list_models() -> list[dict]:
    """Modelos locales disponibles capaces de generar texto (excluye embeddings)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{config.OLLAMA_URL}/api/tags")
        resp.raise_for_status()
        models = resp.json().get("models", [])
    usable = []
    for m in models:
        caps = m.get("capabilities") or []
        if caps and "completion" not in caps:
            continue
        family = (m.get("details") or {}).get("family", "")
        if "bert" in family:  # modelos de embedding sin campo capabilities
            continue
        usable.append({
            "name": m["name"],
            "parameter_size": (m.get("details") or {}).get("parameter_size", ""),
            "context_length": (m.get("details") or {}).get("context_length", 0),
        })
    return usable


async def is_alive() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{config.OLLAMA_URL}/api/tags")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def preload(model: str, device: str | None = None) -> None:
    """Precarga el modelo en VRAM/RAM (llamada sin mensajes: Ollama solo lo carga).

    Se dispara al arrancar el servidor para que la primera generación no pague
    los ~30-60 s de carga del modelo. Pasa el MISMO num_ctx que usa el pipeline:
    sin él Ollama carga con su contexto por defecto (más grande → KV cache que
    no cabe en la 4070) y la primera generación fuerza una recarga completa.
    Cualquier fallo se ignora: es una optimización, no un requisito.
    """
    options = {
        "num_ctx": config.CODE_OPTIONS["num_ctx"],
        **config.device_options(device or config.DEFAULT_DEVICE),
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            await client.post(
                f"{config.OLLAMA_URL}/api/chat",
                json={
                    "model": model, "messages": [],
                    "options": options, "keep_alive": config.KEEP_ALIVE,
                },
            )
    except httpx.HTTPError:
        pass


async def gpu_status() -> list[dict]:
    """Modelos cargados y qué fracción está en GPU (para diagnóstico)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.OLLAMA_URL}/api/ps")
            resp.raise_for_status()
            out = []
            for m in resp.json().get("models", []):
                size = m.get("size") or 0
                vram = m.get("size_vram") or 0
                out.append({
                    "name": m.get("name"),
                    "size": size,
                    "size_vram": vram,
                    "gpu_pct": round(100 * vram / size) if size else 0,
                })
            return out
    except httpx.HTTPError:
        return []


async def stream_chat(
    messages: list[dict],
    model: str,
    options: dict,
) -> AsyncIterator[str]:
    """Envía un chat al modelo y produce los tokens según llegan."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": options,
        # Mantiene el modelo en VRAM entre las llamadas del pipeline: sin esto
        # Ollama puede descargarlo y recargar un 14B cuesta ~30-60 s por etapa.
        "keep_alive": config.KEEP_ALIVE,
    }
    timeout = httpx.Timeout(connect=10, read=600, write=30, pool=10)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{config.OLLAMA_URL}/api/chat", json=payload
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise OllamaError(f"Ollama respondió {resp.status_code}: {body[:400]}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise OllamaError(chunk["error"])
                    token = (chunk.get("message") or {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        return
    except httpx.HTTPError as exc:
        # httpx a veces trae mensaje vacío (timeouts, conexión caída al recargar
        # el modelo). Damos siempre un mensaje con el tipo para no dejar "" al usuario.
        detail = str(exc) or exc.__class__.__name__
        raise OllamaError(
            f"Fallo de comunicación con Ollama ({exc.__class__.__name__}): {detail}. "
            "Suele ser transitorio al cargar el modelo; reintenta."
        ) from exc
