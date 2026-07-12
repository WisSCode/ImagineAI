"""Gestión de trabajos de generación: estado, eventos y suscriptores SSE.

Los eventos (tokens, estados) viven en memoria: son efímeros y solo sirven para
el streaming en vivo / replay de la sesión actual. Los METADATOS del trabajo se
persisten en SQLite (vía db.py) para que el historial por usuario sobreviva
reinicios del servidor.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field

from . import db


@dataclass
class Job:
    id: str
    prompt: str
    model: str
    stack: str = "react-tailwind"
    device: str = "gpu"           # gpu | cpu — dispositivo de cómputo de Ollama
    user_id: int | None = None
    kind: str = "generate"        # generate | edit
    parent_id: str | None = None  # job del que parte una edición
    status: str = "queued"        # queued | briefing | coding | editing | packaging | done | error
    error: str | None = None
    files: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # Historial completo de eventos para que un cliente que se conecta tarde
    # (o recarga la página) reciba el replay íntegro antes de los eventos en vivo.
    events: list[dict] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)

    def publish(self, event: dict) -> None:
        event.setdefault("t", round(time.time() - self.created_at, 2))
        self.events.append(event)
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    def set_status(self, status: str, detail: str = "") -> None:
        self.status = status
        self.publish({"type": "status", "status": status, "detail": detail})
        self.persist()

    def persist(self) -> None:
        """Sincroniza los metadatos del trabajo a SQLite."""
        db.update_job(
            self.id, self.status, error=self.error,
            files=self.files, finished_at=self.finished_at,
        )

    def summary(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "model": self.model,
            "stack": self.stack,
            "device": self.device,
            "kind": self.kind,
            "parent_id": self.parent_id,
            "status": self.status,
            "error": self.error,
            "files": self.files,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, prompt: str, model: str, stack: str = "react-tailwind",
               user_id: int | None = None, kind: str = "generate",
               parent_id: str | None = None, device: str = "gpu") -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12], prompt=prompt, model=model, stack=stack,
            device=device, user_id=user_id, kind=kind, parent_id=parent_id,
        )
        self._jobs[job.id] = job
        if user_id is not None:
            db.insert_job(job.id, user_id, prompt, model, stack, kind, parent_id, device)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)


manager = JobManager()
