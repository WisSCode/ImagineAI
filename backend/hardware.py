"""Uso REAL de hardware (GPU vía nvidia-smi, CPU vía psutil) para la barra de
estado del frontend.

No confundir con `llm.gpu_status()`: ese reporta qué FRACCIÓN DE VRAM ocupa el
modelo ya cargado en Ollama (memoria). Este módulo reporta la UTILIZACIÓN DE
CÓMPUTO real del dispositivo (cuánto está trabajando la GPU o la CPU ahora
mismo), que es lo que necesita una barra de "uso de hardware".
"""
import asyncio
import shutil

import psutil

from . import config


async def gpu_utilization() -> int | None:
    """% de utilización de cómputo de la GPU NVIDIA (nvidia-smi).

    Devuelve None si no hay una GPU NVIDIA disponible (binario ausente, timeout,
    salida inválida, o cualquier otro fallo): la ausencia de GPU no es un error,
    es una configuración legítima (equipo sin GPU NVIDIA, o solo CPU).
    """
    exe = shutil.which(config.NVIDIA_SMI_BIN)
    if not exe:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        first_line = stdout.decode("utf-8", "replace").strip().splitlines()[0]
        return max(0, min(100, int(first_line.strip())))
    except Exception:
        return None


def cpu_utilization() -> float:
    """% de utilización de CPU del sistema (todos los núcleos).

    interval=0.15 fuerza una muestra corta y real (en vez del 0.0 que devuelve
    psutil la primera vez que se llama con interval=None); se ejecuta en un
    hilo aparte (ver snapshot) para no bloquear el event loop.
    """
    return psutil.cpu_percent(interval=0.15)


async def snapshot() -> dict:
    """Uso real de GPU y CPU, medido en paralelo."""
    cpu_task = asyncio.to_thread(cpu_utilization)
    gpu_task = asyncio.ensure_future(gpu_utilization())
    cpu, gpu = await asyncio.gather(cpu_task, gpu_task)
    return {
        "gpu": {"available": gpu is not None, "utilization": gpu},
        "cpu": {"available": True, "utilization": round(cpu, 1)},
    }
