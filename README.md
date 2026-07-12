# ImagineAI

Sistema completo para generar **prototipos web reales y creativos a partir de un prompt**, usando un **modelo de lenguaje local** (vía Ollama) — sin plantillas, sin catálogos de estilos, sin HTML pre-escrito. Escribes qué quieres construir, ves en vivo cómo el modelo diseña y programa, obtienes una preview navegable, editas cualquier elemento con un click y descargas el proyecto en `.zip`.

> Nota interna: el proyecto se llamó anteriormente "SoftwareIX". El prefijo de variables de entorno (`SIX_*`) y el nombre del archivo de base de datos (`data/softwareix.db`) vienen de ese nombre original y se mantienen tal cual para no romper configuraciones ni datos existentes.

## ¿De qué trata el proyecto?

ImagineAI no es un generador basado en plantillas: en lugar de rellenar un layout fijo con el contenido del usuario, el modelo primero **inventa una dirección de diseño propia** (concepto, paleta, tipografía, sistema de espaciado, arquitectura de secciones y animaciones) derivada del significado del encargo, y **después la implementa como código real**. El resultado es un prototipo distinto cada vez, con datos y textos reales (no *lorem ipsum*), que corre en el navegador sin necesidad de build.

Además del generador, el producto resuelve tres problemas típicos de este tipo de herramientas:

- **Cuentas de usuario** — cada quien guarda su propio historial de generaciones, persistente entre sesiones.
- **Edición conversacional sobre la preview** — en vez de reescribir el prompt completo, haces click en cualquier elemento del prototipo ya generado, describes el cambio en lenguaje natural, y el modelo produce una nueva versión quirúrgica (sin tocar el resto del diseño).
- **Uso eficiente del hardware local** — el modelo se mantiene caliente en VRAM entre las llamadas del pipeline, y se puede elegir explícitamente si una generación corre en **GPU** o en **CPU**, según qué recursos estén disponibles en el momento.

## Tecnologías

| Capa | Tecnología |
|---|---|
| Backend | Python 3.11+, [FastAPI](https://fastapi.tiangolo.com/), [httpx](https://www.python-httpx.org/) (cliente async), Uvicorn |
| Persistencia | SQLite (usuarios, sesiones, historial de trabajos) |
| Modelo de lenguaje | [Ollama](https://ollama.com) corriendo local — cualquier modelo de texto que tengas descargado (por defecto `qwen2.5:14b`) |
| Frontend | HTML/CSS/JS **vanilla**, sin frameworks ni build |
| Prototipos generados | A elección por proyecto: **React 18 + Tailwind** (cargados por CDN, JSX transpilado en el navegador con Babel standalone) o **HTML/CSS/JS puro** |
| Validación de código | Node.js + `@babel/standalone` (vendorizado) para detectar y auto-reparar errores de sintaxis JSX |
| Autenticación | Sesiones por cookie `HttpOnly`, contraseñas con PBKDF2-HMAC-SHA256 |
| Streaming | Server-Sent Events (SSE) para ver tokens del modelo en vivo |

No hay dependencias de frontend que instalar (ni `npm`, ni bundler): todo el cliente es JS plano servido como archivos estáticos.

## Arquitectura

```
┌────────────┐  POST /api/generate   ┌──────────────────┐  /api/chat (stream)  ┌────────────┐
│  Frontend  │ ────────────────────► │  Backend FastAPI  │ ───────────────────► │   Ollama   │
│(SPA vanilla)│ ◄──────────────────── │    (pipeline)     │ ◄─────────────────── │ modelo local│
└────────────┘   SSE /events         └──────────────────┘   tokens             └────────────┘
      │                                      │        │
      │  GET /preview/{id}/index.html        │        └─► SQLite (usuarios, sesiones, jobs)
      └──────────────────────────►  generated/{job_id}/  ──► {job_id}.zip
```

| Componente | Rol |
|---|---|
| `backend/main.py` | Rutas FastAPI: auth, generación, edición, historial, SSE, descarga, y sirve `frontend/` y `generated/` |
| `backend/pipeline.py` | Orquesta las etapas del pipeline, parsea la salida del modelo, ensambla y empaqueta el proyecto |
| `backend/prompts.py` | Los prompts anti-plantilla (dirección creativa + implementación por stack) y la semilla creativa por corrida |
| `backend/llm.py` | Cliente streaming de Ollama (chat, listado de modelos, estado de GPU, precarga) |
| `backend/auth.py` / `backend/db.py` | Registro/login, sesiones, y persistencia de usuarios/trabajos en SQLite |
| `backend/jobs.py` | Trabajos en memoria: estado, eventos SSE y suscriptores |
| `backend/config.py` | Toda la configuración, sobreescribible por variables de entorno |
| `frontend/` | SPA: prompt, selección de stack/modelo/dispositivo, progreso en vivo, preview embebida, editor por click |

### Pipeline creativo (por qué no hay plantillas)

La generación ocurre en **dos etapas con roles y temperaturas distintas**:

1. **Dirección creativa** (`temperature ≈ 1.1`): el modelo actúa como directora de arte y produce un *manifiesto de diseño* (concepto, paleta, tipografía, sistema de espaciado/contenedor de 8px, arquitectura de secciones, lenguaje de animación y contenido real). El prompt del sistema **prohíbe los clichés** (hero genérico, tarjetas con sombra, 3 columnas de features, paletas por defecto) y exige **principios de oficio concretos**: emparejar una tipografía display con una de texto, una metodología de color con contraste AA, un sistema de espaciado consistente, y una coreografía de animación (gesto firma + revelados con stagger + micro-interacciones). Se inyecta una **semilla aleatoria por corrida** y el mandato de descartar la primera idea (la obvia).
2. **Ingeniería frontend** (`temperature ≈ 0.75`): el modelo implementa el manifiesto con **una llamada dedicada por archivo** (cada una ve los archivos ya generados como contexto), lo que concentra el presupuesto de tokens y evita que "resuma" el código. Si un archivo no llega en el formato esperado hay un reintento de reparación dirigido, con un parser tolerante a marcadores deformados, *fences* y salidas crudas.

No existe ningún catálogo de estilos ni HTML pre-escrito: todo el diseño sale del modelo condicionado por el prompt y la semilla de la corrida.

### Stacks de generación

Se elige por proyecto (selector en el frontend, campo `stack` en `/api/generate`):

- **`react-tailwind` (default)** — genera `index.html` (cascarón con React 18 + Tailwind por CDN y los *design tokens* del manifiesto en `tailwind.config`), `styles.css` (animaciones firma con `@keyframes`) y `app.jsx` (la app React completa). No hay paso de build: el JSX se transpila en el navegador con Babel standalone. Al empaquetar, el sistema ensambla un `index.html` **autocontenido** (CSS y JSX embebidos) para que el `.zip` funcione con doble clic. Un error de sintaxis en el JSX se detecta con Babel (vía Node) y se auto-repara pasándole al modelo el error exacto del compilador.
- **`vanilla`** — `index.html` + `styles.css` + `app.js` sin dependencias, self-contained y offline.

### Modelo y dispositivo de cómputo

`GET /api/models` consulta Ollama directamente (`/api/tags`) y expone **cualquier modelo de texto que tengas descargado** — no hay lista fija en el código. El **dispositivo de cómputo** (GPU o CPU) se elige por generación con el mismo patrón que el stack (selector en el frontend, campo `device`, catálogo en `GET /api/devices`):

- **`gpu` (default)** — Ollama offloadea el máximo de capas que quepan en la GPU (o el número exacto fijado por `SIX_NUM_GPU`). Modo rápido; requiere VRAM suficiente para el modelo elegido.
- **`cpu`** — fuerza `num_gpu=0` (todo el modelo en RAM) y fija `num_thread` al número de núcleos lógicos disponibles. Útil si la GPU está ocupada o el modelo no cabe en VRAM; bastante más lento.

Las ediciones dirigidas heredan el dispositivo del proyecto original. `GET /api/gpu` muestra qué modelo está cargado y qué fracción vive en VRAM.

### Cuentas y edición desde la preview

- **Usuarios**: registro/login desde la barra superior (PBKDF2, sesión en cookie `HttpOnly`). Cada usuario ve solo su historial, persistente en `data/softwareix.db`.
- **Edición dirigida**: con un prototipo listo, activa **✎ Editar elementos** en la pestaña Preview, pasa el mouse (se destaca el elemento bajo el cursor), haz click y describe el cambio en la barra que se abre. `POST /api/jobs/{id}/edit` crea un *job hijo* que le pide al modelo solo los archivos que cambian (a temperatura baja) y produce una **versión nueva** — el original queda intacto en el historial.

### Flujo de datos

- `POST /api/generate` crea un *job* y lanza el pipeline como tarea `asyncio` → responde `job_id` al instante.
- `GET /api/jobs/{id}/events` es un stream **SSE** que primero reproduce el historial (si te conectas tarde o recargas) y luego emite en vivo: `status` (cambio de etapa), `token` (texto del modelo, agrupado en ráfagas), `done` (URLs de preview y descarga) o `error`.
- Los archivos se escriben en `generated/{job_id}/` (+ un `README.md` con el prompt y el manifiesto) y se sirven en `/preview/{job_id}/index.html` para el iframe.
- `GET /api/jobs/{id}/download` entrega el `.zip` del proyecto.

## Requisitos

- **Python 3.11+** (en Windows, si el `python` del `PATH` es Anaconda u otra distribución sin FastAPI, usa el intérprete correcto explícitamente, p. ej. `py -3.13`)
- **[Ollama](https://ollama.com)** corriendo localmente, con al menos un modelo de texto descargado:
  ```
  ollama pull qwen2.5:14b
  ```
- **Node.js (opcional pero recomendado)** — habilita la validación de sintaxis del JSX del stack React. Si no está disponible, la generación sigue funcionando y solo se omite esa validación.

## Instalación y arranque

```powershell
pip install -r requirements.txt
python run.py
```

Abre **http://localhost:8000**, crea una cuenta, escribe tu prompt, elige **stack** (React+Tailwind o vanilla), **modelo** y **dispositivo** (GPU o CPU), y genera.

## Configuración (variables de entorno)

| Variable | Default | Descripción |
|---|---|---|
| `SIX_OLLAMA_URL` | `http://localhost:11434` | URL de Ollama |
| `SIX_MODEL` | `qwen2.5:14b` | Modelo por defecto |
| `SIX_BRIEF_TEMP` | `1.1` | Temperatura de la etapa creativa |
| `SIX_CODE_TEMP` | `0.75` | Temperatura de la etapa de código |
| `SIX_EDIT_TEMP` | `0.5` | Temperatura de las ediciones dirigidas |
| `SIX_NUM_CTX` | `16384` | Ventana de contexto |
| `SIX_CODE_MAX_TOKENS` | `9000` | Máx. tokens de la etapa de código |
| `SIX_GENERATED_DIR` | `./generated` | Carpeta de salida de los prototipos |
| `SIX_DATA_DIR` | `./data` | Carpeta de la base SQLite (usuarios/sesiones/jobs) |
| `SIX_KEEP_ALIVE` | `30m` | Cuánto mantiene Ollama el modelo cargado tras cada llamada |
| `SIX_NUM_GPU` | `-1` | Capas a offloadear a GPU en modo `gpu` (-1 = automático de Ollama) |
| `SIX_DEVICE` | `gpu` | Dispositivo por defecto del selector (`gpu` o `cpu`) |
| `SIX_SESSION_TTL` | 30 días | Vigencia de la cookie de sesión (segundos) |

### Memoria de GPU (ejemplo: RTX 4070 / 12 GB)

Con `num_ctx=16384`, el KV cache f16 de un modelo de 14B no cabe entero en 12 GB de VRAM. El sistema espera que Ollama corra con **flash attention y KV cache cuantizado**, que reducen la huella total a niveles que sí caben (~92-100% en GPU en vez de ~66%). Configúralo una vez como variables de entorno y reinicia Ollama:

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_FLASH_ATTENTION', '1', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_KV_CACHE_TYPE', 'q8_0', 'User')
```

El backend pasa `keep_alive` en cada llamada (el modelo no se descarga entre etapas del pipeline) y lo **precarga al arrancar el servidor**, así la primera generación no paga los ~30-60 s de carga.

## Pruebas

```powershell
pip install pytest
pytest tests
```

La suite corre contra un LLM guionado (sin necesitar Ollama real) y cubre: autenticación (registro/login/sesiones/privacidad entre usuarios), el parser de archivos del pipeline, el ensamblado del stack React (extraer/reensamblar el index autocontenido), el flujo completo de generación y edición, la auto-reparación de JSX con Babel real, la selección de dispositivo GPU/CPU (persistencia, herencia en ediciones y opciones exactas enviadas a Ollama), y que el historial sobrevive reinicios del servidor.

## Estructura

```
backend/
  main.py      # rutas FastAPI (auth, generate, jobs, SSE, download, preview, devices)
  pipeline.py  # etapas del pipeline + parser de archivos + ensamblado + zip
  prompts.py   # prompts anti-plantilla y semilla creativa
  llm.py       # cliente streaming de Ollama
  auth.py      # registro/login, hashing y sesiones
  db.py        # persistencia SQLite (usuarios, sesiones, jobs) + migraciones
  jobs.py      # jobs en memoria, eventos y suscriptores SSE
  config.py    # configuración por variables de entorno (incluye GPU/CPU)
frontend/      # SPA (index.html, styles.css, app.js)
generated/     # prototipos generados y sus .zip
tests/         # suite de pytest
run.py         # arranque (uvicorn en :8000)
```

## Persistencia

El **historial de eventos SSE** (progreso en vivo, tokens) vive en memoria y se pierde al reiniciar el servidor. Los **metadatos del trabajo** (prompt, modelo, stack, dispositivo, estado) y las **cuentas de usuario** quedan en `data/softwareix.db` (SQLite). Los **proyectos generados y sus `.zip`** quedan en `generated/`, así que la **preview y la descarga siguen funcionando tras un reinicio**: si el trabajo ya no está en memoria, se sirve directamente desde disco (el id se valida contra `^[0-9a-f]{12}$` para evitar *path traversal*).
