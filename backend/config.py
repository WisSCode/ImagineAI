"""Configuración central del sistema (todo sobreescribible por variables de entorno)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Conexión con el modelo local (Ollama)
OLLAMA_URL = os.getenv("SIX_OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("SIX_MODEL", "qwen2.5:14b")

# Directorios y base de datos
GENERATED_DIR = Path(os.getenv("SIX_GENERATED_DIR", BASE_DIR / "generated"))
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = Path(os.getenv("SIX_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "softwareix.db"

# Sesiones de usuario (cookie HttpOnly)
SESSION_TTL_SECONDS = float(os.getenv("SIX_SESSION_TTL", str(30 * 24 * 3600)))

# ── GPU / CPU / memoria (RTX 4070, 12 GB) ───────────────────────
# keep_alive mantiene el modelo cargado en VRAM entre llamadas: el pipeline hace
# 4+ llamadas seguidas y sin esto Ollama puede descargar el modelo entre etapas
# (recargar un 14B cuesta ~30-60 s).
KEEP_ALIVE = os.getenv("SIX_KEEP_ALIVE", "30m")
# num_gpu=-1 deja que Ollama offloadee el máximo de capas que quepan en la GPU
# (comportamiento por defecto cuando se elige procesar en GPU); un entero fuerza
# ese número exacto de capas.
NUM_GPU = int(os.getenv("SIX_NUM_GPU", "-1"))

# Binario de la CLI de NVIDIA para leer el % de utilización REAL de la GPU
# (compute, no memoria). En Windows/Linux con drivers NVIDIA suele estar en el
# PATH; se puede apuntar a una ruta absoluta si no lo está.
NVIDIA_SMI_BIN = os.getenv("SIX_NVIDIA_SMI_BIN", "nvidia-smi")

# Dispositivo de cómputo: seleccionable por generación, igual que el stack.
DEVICES = {
    "gpu": {"label": "GPU (rápido, usa VRAM)"},
    "cpu": {"label": "CPU (sin VRAM, más lento)"},
}
DEFAULT_DEVICE = os.getenv("SIX_DEVICE", "gpu")
if DEFAULT_DEVICE not in DEVICES:
    DEFAULT_DEVICE = "gpu"


def device_options(device: str) -> dict:
    """Opciones de Ollama para forzar el procesamiento en GPU o en CPU.

    - "cpu": num_gpu=0 saca TODAS las capas de la GPU, y num_thread se fija al
      número de núcleos lógicos disponibles para aprovechar toda la CPU (Ollama
      por defecto no siempre usa todos los hilos).
    - "gpu" (o cualquier otro valor): comportamiento normal — NUM_GPU<0 deja que
      Ollama decida cuántas capas caben en la GPU; un entero fuerza ese número.
    """
    if device == "cpu":
        return {"num_gpu": 0, "num_thread": os.cpu_count() or 4}
    return {} if NUM_GPU < 0 else {"num_gpu": NUM_GPU}


# Parámetros de muestreo por etapa del pipeline (sin las opciones de
# dispositivo: esas se mezclan por job vía device_options() en el pipeline,
# porque el dispositivo se elige por generación, no globalmente).
# La etapa creativa usa temperatura alta para forzar divergencia entre corridas;
# la etapa de código baja un poco para mantener sintaxis correcta sin matar la voz.
BRIEF_OPTIONS = {
    "temperature": float(os.getenv("SIX_BRIEF_TEMP", "1.1")),
    "top_p": 0.97,
    "num_ctx": int(os.getenv("SIX_NUM_CTX", "16384")),
    "num_predict": 2200,
}
CODE_OPTIONS = {
    "temperature": float(os.getenv("SIX_CODE_TEMP", "0.75")),
    "top_p": 0.92,
    "num_ctx": int(os.getenv("SIX_NUM_CTX", "16384")),
    "num_predict": int(os.getenv("SIX_CODE_MAX_TOKENS", "9000")),
}
# La edición parte de código existente: menos temperatura, cambios quirúrgicos.
EDIT_OPTIONS = {
    "temperature": float(os.getenv("SIX_EDIT_TEMP", "0.5")),
    "top_p": 0.9,
    "num_ctx": int(os.getenv("SIX_NUM_CTX", "16384")),
    "num_predict": int(os.getenv("SIX_CODE_MAX_TOKENS", "9000")),
}

GENERATED_DIR.mkdir(parents=True, exist_ok=True)
