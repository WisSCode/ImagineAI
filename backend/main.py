"""API del sistema ImagineAI: generación de prototipos web con un modelo local."""
import asyncio
import json
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, config, db, llm, pipeline, prompts
from .jobs import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    # Precarga el modelo por defecto en VRAM/RAM en segundo plano: la primera
    # generación no paga los ~30-60 s de carga del 14B.
    asyncio.create_task(llm.preload(config.DEFAULT_MODEL, config.DEFAULT_DEVICE))
    yield


app = FastAPI(title="ImagineAI", version="2.0.0", lifespan=lifespan)

# Los ids de trabajo son hex de 12 chars (uuid4). Validarlos evita traversal al
# construir rutas de descarga a partir del id.
SAFE_ID_RE = re.compile(r"^[0-9a-f]{12}$")


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=6000)
    model: str | None = None
    stack: str | None = None
    device: str | None = None

    def __init__(self, **data):
        super().__init__(**data)
        # Validar que el prompt no sea solo espacios en blanco
        if not self.prompt.strip():
            raise ValueError("El prompt no puede contener solo espacios en blanco")


class EditRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)
    selector: str = Field(default="", max_length=500)
    element_html: str = Field(default="", max_length=6000)


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)


# ── Autenticación ────────────────────────────────────────────────
@app.post("/api/auth/register")
async def register(req: CredentialsRequest, response: Response):
    auth.register(req.username, req.password)
    return auth.login(req.username, req.password, response)


@app.post("/api/auth/login")
async def login(req: CredentialsRequest, response: Response):
    return auth.login(req.username, req.password, response)


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    auth.logout(request, response)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(request: Request):
    user = auth.current_user(request)
    if not user:
        return {"user": None}
    return {"user": {"id": user["id"], "username": user["username"]}}


# ── Salud, modelos y stacks ──────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"ok": True, "ollama": await llm.is_alive(), "default_model": config.DEFAULT_MODEL}


@app.get("/api/gpu")
async def gpu():
    """Qué modelos están cargados y qué fracción vive en la GPU."""
    return {"loaded": await llm.gpu_status(), "keep_alive": config.KEEP_ALIVE}


@app.get("/api/stacks")
async def stacks():
    return {
        "stacks": [{"id": k, "label": v["label"]} for k, v in prompts.STACKS.items()],
        "default": prompts.DEFAULT_STACK,
    }


@app.get("/api/devices")
async def devices():
    return {
        "devices": [{"id": k, "label": v["label"]} for k, v in config.DEVICES.items()],
        "default": config.DEFAULT_DEVICE,
    }


@app.get("/api/models")
async def models():
    try:
        return {"models": await llm.list_models(), "default": config.DEFAULT_MODEL}
    except Exception as exc:  # Ollama caído
        raise HTTPException(503, f"No se pudo consultar Ollama: {exc}") from exc


# ── Generación y edición ─────────────────────────────────────────
@app.post("/api/generate")
async def generate(req: GenerateRequest, request: Request):
    user = auth.require_user(request)
    if not await llm.is_alive():
        raise HTTPException(
            503,
            "Ollama no está corriendo. Inícialo (`ollama serve`) y verifica "
            f"que responda en {config.OLLAMA_URL}.",
        )
    stack = req.stack if req.stack in prompts.STACKS else prompts.DEFAULT_STACK
    device = req.device if req.device in config.DEVICES else config.DEFAULT_DEVICE
    job = manager.create(
        req.prompt.strip(), req.model or config.DEFAULT_MODEL, stack,
        user_id=user["id"], device=device,
    )
    asyncio.create_task(pipeline.run_generation(job))
    return {"job_id": job.id}


@app.post("/api/jobs/{job_id}/edit")
async def edit_job(job_id: str, req: EditRequest, request: Request):
    """Edición dirigida: el usuario señaló un elemento en la preview y describe
    el cambio. Crea un job hijo que produce una versión nueva del proyecto."""
    user = auth.require_user(request)
    if not SAFE_ID_RE.match(job_id):
        raise HTTPException(404, "Identificador de trabajo inválido")
    row = db.get_job(job_id)
    if not row or row["user_id"] != user["id"]:
        raise HTTPException(404, "Trabajo no encontrado")
    if row["status"] != "done":
        raise HTTPException(409, "El trabajo aún no está listo para editarse")
    if not (config.GENERATED_DIR / job_id).is_dir():
        raise HTTPException(404, "El proyecto original ya no existe en disco")
    if not await llm.is_alive():
        raise HTTPException(503, "Ollama no está corriendo.")
    job = manager.create(
        req.instruction.strip(), row["model"], row["stack"],
        user_id=user["id"], kind="edit", parent_id=job_id,
        device=row["device"] if row["device"] in config.DEVICES else config.DEFAULT_DEVICE,
    )
    asyncio.create_task(
        pipeline.run_edit(job, req.selector, req.element_html, req.instruction)
    )
    return {"job_id": job.id}


# ── Historial y eventos ──────────────────────────────────────────
def _row_summary(row) -> dict:
    files = [f for f in (row["files"] or "").split("\n") if f]
    done = row["status"] == "done"
    return {
        "id": row["id"],
        "prompt": row["prompt"],
        "model": row["model"],
        "stack": row["stack"],
        "device": row["device"],
        "kind": row["kind"],
        "parent_id": row["parent_id"],
        "status": row["status"],
        "error": row["error"],
        "files": files,
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "preview_url": f"/preview/{row['id']}/index.html" if done else None,
        "download_url": f"/api/jobs/{row['id']}/download" if done else None,
    }


@app.get("/api/jobs")
async def list_jobs(request: Request):
    user = auth.require_user(request)
    return {"jobs": [_row_summary(r) for r in db.jobs_for_user(user["id"])]}


@app.get("/api/jobs/{job_id}")
async def job_detail(job_id: str, request: Request):
    user = auth.require_user(request)
    job = manager.get(job_id)
    if job and job.user_id == user["id"]:
        return job.summary()
    row = db.get_job(job_id)
    if not row or row["user_id"] != user["id"]:
        raise HTTPException(404, "Trabajo no encontrado")
    return _row_summary(row)


@app.get("/api/jobs/{job_id}/brief")
async def job_brief(job_id: str, request: Request):
    """Manifiesto de diseño del trabajo (extraído del README.md del proyecto), para
    recuperar ese contexto al reabrir un trabajo terminado desde el historial."""
    user = auth.require_user(request)
    if not SAFE_ID_RE.match(job_id):
        raise HTTPException(404, "Identificador de trabajo inválido")
    row = db.get_job(job_id)
    if not row or row["user_id"] != user["id"]:
        raise HTTPException(404, "Trabajo no encontrado")
    return {"brief": pipeline.read_manifesto(job_id)}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    """SSE: replay del historial + eventos en vivo hasta que el trabajo termine."""
    user = auth.require_user(request)
    job = manager.get(job_id)
    if not job or job.user_id != user["id"]:
        raise HTTPException(404, "Trabajo no encontrado")

    async def stream():
        queue: asyncio.Queue = asyncio.Queue()
        # Congelamos el replay ANTES de suscribirnos para no duplicar eventos.
        replay = list(job.events)
        job.subscribers.add(queue)
        try:
            for event in replay:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if job.status in ("done", "error"):
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in ("done", "error"):
                    return
        finally:
            job.subscribers.discard(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str, request: Request):
    # Los proyectos quedan en disco aunque el server se reinicie. La propiedad
    # se valida contra la base; los proyectos antiguos sin dueño (previos al
    # sistema de usuarios) siguen siendo descargables por cualquier sesión.
    user = auth.require_user(request)
    if not SAFE_ID_RE.match(job_id):
        raise HTTPException(404, "Identificador de trabajo inválido")
    row = db.get_job(job_id)
    if row and row["user_id"] != user["id"]:
        raise HTTPException(404, "Trabajo no encontrado")
    if row and row["status"] != "done":
        raise HTTPException(404, "El trabajo aún no termina")
    project_dir = config.GENERATED_DIR / job_id
    if not project_dir.is_dir():
        raise HTTPException(404, "El trabajo no existe")
    zip_path = config.GENERATED_DIR / f"{job_id}.zip"
    if not zip_path.exists():
        pipeline.make_zip(job_id)
    return FileResponse(
        zip_path, media_type="application/zip", filename=f"prototipo-{job_id}.zip"
    )


# Los prototipos generados se sirven tal cual para la preview embebida.
app.mount("/preview", StaticFiles(directory=config.GENERATED_DIR, html=True), name="preview")
# El frontend del producto se sirve en la raíz.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
