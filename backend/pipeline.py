"""Pipeline de generación: dirección creativa → código → archivos en disco.

Etapa 1 (briefing): el modelo inventa un manifiesto de diseño derivado del
prompt, a temperatura alta. Etapa 2 (coding): implementa ese manifiesto como
index.html / styles.css / app.js emitidos en bloques <<<FILE: ...>>>.
Todos los tokens se re-publican al frontend vía el sistema de eventos del Job.
"""
import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from . import config, llm, prompts
from .jobs import Job

_BACKEND_DIR = Path(__file__).resolve().parent
_JSX_VALIDATOR = _BACKEND_DIR / "validate_jsx.js"
_BABEL_VENDOR = _BACKEND_DIR / "vendor" / "babel.min.js"


def validate_jsx(code: str) -> str | None:
    """Devuelve el mensaje de error de sintaxis del JSX, o None si compila o si la
    validación no está disponible (Node o babel ausentes → se degrada en silencio)."""
    node = shutil.which("node")
    if not node or not _JSX_VALIDATOR.exists() or not _BABEL_VENDOR.exists():
        return None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".jsx", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(code)
            tmp = fh.name
        out = subprocess.run(
            [node, str(_JSX_VALIDATOR), tmp],
            capture_output=True, text=True, timeout=30,
        )
        res = (out.stdout or "").strip()
        if res.startswith("ERR:"):
            return res[4:].strip() or "error de sintaxis desconocido"
        return None
    except Exception:
        return None  # nunca romper la generación por la validación
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)

# Los modelos locales a veces deforman los marcadores (p. ej. emiten `>>` en vez
# de `>>>`), así que el parser acepta 2+ ángulos, espacios y mayúsculas variables.
FILE_OPEN_RE = re.compile(r"<{2,}\s*FILE\s*:\s*([^>\r\n]+?)\s*>{2,}", re.IGNORECASE)
FILE_END_RE = re.compile(r"<{2,}\s*END\s*>{2,}", re.IGNORECASE)
FENCE_RE = re.compile(r"```(html|css|javascript|js)\s*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)
FENCE_NAMES = {"html": "index.html", "css": "styles.css", "javascript": "app.js", "js": "app.js"}

SAFE_NAME_RE = re.compile(r"^[\w][\w.\-]*$")

# Assets locales que index.html referencia (href/src relativos, no http/data).
LOCAL_REF_RE = re.compile(r"""(?:href|src)\s*=\s*["'](?!https?:|//|data:|#|mailto:)([\w.\-/]+)["']""")


def _strip_fences(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\r?\n", "", content)
        content = re.sub(r"\r?\n?```$", "", content)
    return content.strip()


def parse_files(raw: str) -> dict[str, str]:
    """Extrae los archivos de la salida del modelo, con fallbacks tolerantes."""
    files: dict[str, str] = {}
    opens = list(FILE_OPEN_RE.finditer(raw))
    for i, m in enumerate(opens):
        name = Path(m.group(1).strip().strip("`'\"")).name  # neutraliza cualquier ruta
        if not SAFE_NAME_RE.match(name):
            continue
        limit = opens[i + 1].start() if i + 1 < len(opens) else len(raw)
        segment = raw[m.end():limit]
        end = FILE_END_RE.search(segment)
        content = segment[: end.start()] if end else segment
        content = _strip_fences(content)
        if content:
            files[name] = content + "\n"
    if files:
        return files
    # Fallback 1: bloques de código con lenguaje
    for lang, content in FENCE_RE.findall(raw):
        name = FENCE_NAMES[lang.lower()]
        if name not in files:
            files[name] = content.strip() + "\n"
    if files:
        return files
    # Fallback 2: la salida entera parece un HTML autocontenido
    if "<!DOCTYPE" in raw or "<html" in raw:
        start = raw.find("<!DOCTYPE")
        if start == -1:
            start = raw.find("<html")
        end = raw.rfind("</html>")
        if end != -1:
            files["index.html"] = raw[start : end + len("</html>")] + "\n"
    return files


def missing_assets(files: dict[str, str]) -> list[str]:
    """Assets locales que index.html referencia pero que no fueron generados."""
    html = files.get("index.html", "")
    referenced = {Path(ref).name for ref in LOCAL_REF_RE.findall(html)}
    return sorted(ref for ref in referenced if ref not in files)


# ── Ensamblado del stack React ───────────────────────────────────
# El index.html generado referencia styles.css y app.jsx por separado (bueno para
# la calidad de generación y para editar). Pero Babel standalone no puede hacer
# fetch de app.jsx desde file:// (CORS), así que para que el .zip funcione con
# doble clic ensamblamos un index.html AUTOCONTENIDO: el CSS va inline en <style>
# y el JSX inline en <script type="text/babel">. Los archivos sueltos se conservan
# en el proyecto como fuente editable, pero el index ya no los referencia.
STYLES_LINK_RE = re.compile(r"""<link\b[^>]*href=["']styles\.css["'][^>]*>""", re.IGNORECASE)
APP_SCRIPT_RE = re.compile(
    r"""<script\b[^>]*src=["']app\.jsx["'][^>]*>\s*</script>""", re.IGNORECASE
)


# El modelo, pese a la regla del prompt, a veces emite <img> con rutas a archivos
# inexistentes → íconos de imagen rota. Los reemplazamos de forma determinista por
# un bloque decorativo con gradiente de Tailwind, conservando las clases de tamaño.
IMG_JSX_RE = re.compile(r"<img\b([^>]*?)\s*/?>", re.IGNORECASE | re.DOTALL)
_CLASSNAME_STR_RE = re.compile(r'className\s*=\s*"([^"]*)"')
_ALT_STR_RE = re.compile(r'alt\s*=\s*"([^"]*)"')


def neutralize_jsx_images(jsx: str) -> tuple[str, int]:
    """Sustituye <img …> por un placeholder con gradiente. Devuelve (jsx, nº sustituidos)."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        attrs = m.group(1)
        cls = _CLASSNAME_STR_RE.search(attrs)
        alt = _ALT_STR_RE.search(attrs)
        base = "bg-gradient-to-br from-accent to-accent2"
        classes = f"{cls.group(1)} {base}".strip() if cls else f"w-full h-48 {base}"
        label = (alt.group(1) if alt else "imagen decorativa").replace('"', "'")
        return f'<div className="{classes}" role="img" aria-label="{label}"></div>'

    return IMG_JSX_RE.sub(repl, jsx), count


# Anti-patrón recurrente del modelo: `if (!shown) return null;` con useReveal.
# Es un deadlock — el componente no se monta hasta ser visible, pero el observer
# solo puede verlo si está montado — y deja secciones enteras sin renderizar
# jamás. Se elimina de forma determinista (el prompt lo prohíbe, pero no basta).
_REVEAL_DESTRUCTURE_RE = re.compile(r"const\s*\[\s*\w+\s*,\s*(\w+)\s*\]\s*=\s*useReveal")


def unblock_reveal_gates(jsx: str) -> tuple[str, int]:
    """Elimina `if (!<shown>) return null;` cuando <shown> viene de useReveal.
    Devuelve (jsx, nº de guardas eliminadas)."""
    names = set(_REVEAL_DESTRUCTURE_RE.findall(jsx))
    if not names:
        return jsx, 0
    alternation = "|".join(re.escape(n) for n in sorted(names))
    gate_re = re.compile(
        r"if\s*\(\s*!\s*(?:" + alternation + r")\s*\)\s*"
        r"(?:\{\s*return\s+null\s*;?\s*\}|return\s+null\s*;?)"
    )
    out, count = gate_re.subn("", jsx)
    return out, count


def _sanitize_jsx(jsx: str) -> str:
    """Neutraliza sintaxis de módulos que rompería con React/Babel por CDN."""
    out = []
    for line in jsx.splitlines():
        if re.match(r"\s*import\s.+;?\s*$", line):
            continue  # 'import React from "react"' etc.
        line = re.sub(r"\bexport\s+default\s+", "", line)
        line = re.sub(r"\bexport\s+(?=(function|const|let|class)\b)", "", line)
        out.append(line)
    return "\n".join(out)


def assemble_react_index(files: dict[str, str]) -> str:
    """Devuelve un index.html autocontenido con el CSS y el JSX embebidos."""
    html = files.get("index.html", "")
    css = files.get("styles.css", "").strip()
    jsx = _sanitize_jsx(files.get("app.jsx", "").strip())

    if css:
        style_block = "<style>\n" + css + "\n</style>"
        if STYLES_LINK_RE.search(html):
            html = STYLES_LINK_RE.sub(lambda _m: style_block, html, count=1)
        elif "</head>" in html:
            html = html.replace("</head>", style_block + "\n</head>", 1)
    if jsx:
        script_block = '<script type="text/babel" data-presets="react">\n' + jsx + "\n</script>"
        if APP_SCRIPT_RE.search(html):
            html = APP_SCRIPT_RE.sub(lambda _m: script_block, html, count=1)
        elif "</body>" in html:
            html = html.replace("</body>", script_block + "\n</body>", 1)
        else:
            html += "\n" + script_block
    return html


# El index.html guardado en disco es el AUTOCONTENIDO (CSS y JSX embebidos).
# Para editar necesitamos el cascarón sin esos bloques (el CSS/JSX fuente ya va
# aparte en el contexto) y poder re-embeber tras la edición.
INLINE_STYLE_RE = re.compile(r"<style>\r?\n?(.*?)</style>", re.DOTALL)
INLINE_BABEL_RE = re.compile(
    r"""<script type=["']text/babel["'][^>]*>\r?\n?(.*?)</script>""", re.DOTALL
)


def extract_react_shell(assembled_html: str) -> str:
    """Devuelve el cascarón del index autocontenido, con placeholders en vez del
    CSS/JSX embebidos (para no duplicarlos en el contexto del modelo)."""
    shell = INLINE_STYLE_RE.sub(
        "<style>/* styles.css se embebe aquí al empaquetar */</style>",
        assembled_html, count=1,
    )
    shell = INLINE_BABEL_RE.sub(
        '<script type="text/babel" data-presets="react">'
        "/* app.jsx se embebe aquí al empaquetar */</script>",
        shell, count=1,
    )
    return shell


def reassemble_react_index(shell: str, css: str, jsx: str) -> str:
    """Re-embebe CSS y JSX en el cascarón (inverso de extract_react_shell)."""
    jsx = _sanitize_jsx(jsx.strip())
    html = INLINE_STYLE_RE.sub(
        lambda _m: "<style>\n" + css.strip() + "\n</style>", shell, count=1
    )
    html = INLINE_BABEL_RE.sub(
        lambda _m: '<script type="text/babel" data-presets="react">\n' + jsx + "\n</script>",
        html, count=1,
    )
    return html


def extract_single_file(raw: str, name: str) -> str | None:
    """Recupera el archivo `name` de una salida que debía contener solo ese archivo."""
    parsed = parse_files(raw)
    if name in parsed:
        return parsed[name]
    if len(parsed) == 1:
        # Emitió un único bloque con otro nombre (p. ej. "style.css"): lo aceptamos.
        return next(iter(parsed.values()))
    if parsed:
        return None  # varios bloques y ninguno es el pedido: ambiguo, mejor reintentar
    # Sin marcadores ni fences: heurística por tipo de archivo sobre la salida cruda.
    text = _strip_fences(raw)
    if name.endswith(".css") and "{" in text and "}" in text and "<html" not in text:
        return text + "\n"
    if name.endswith((".js", ".jsx")) and any(
        s in text for s in ("addEventListener", "document.", "function", "=>", "React", "useState")
    ) and "<html" not in text:
        return text + "\n"
    return None


def _opts(base: dict, job: Job) -> dict:
    """Mezcla las opciones base de la etapa con las del dispositivo elegido por
    el usuario para ESTE job (gpu/cpu): el dispositivo se decide por generación,
    no globalmente, así que no puede vivir precomputado en config.*_OPTIONS."""
    return {**base, **config.device_options(job.device)}


async def _stream_stage(
    job: Job, stage: str, messages: list[dict], options: dict, retries: int = 1
) -> str:
    """Consume el stream del modelo re-publicando cada token; devuelve el texto completo.

    Reintenta ante fallos transitorios de Ollama (p. ej. la conexión se cae mientras
    el modelo se (re)carga), pero solo si aún no se emitió ningún token de esta etapa.
    """
    attempt = 0
    while True:
        parts: list[str] = []
        buffer = ""
        try:
            async for token in llm.stream_chat(messages, job.model, options):
                parts.append(token)
                buffer += token
                # Agrupamos tokens en ráfagas pequeñas para no saturar el SSE.
                if len(buffer) >= 24 or "\n" in buffer:
                    job.publish({"type": "token", "stage": stage, "text": buffer})
                    buffer = ""
            if buffer:
                job.publish({"type": "token", "stage": stage, "text": buffer})
            return "".join(parts)
        except llm.OllamaError:
            # Solo reintentamos si no habíamos emitido nada (si ya salió texto, el
            # reintento duplicaría contenido en el stream del frontend).
            if attempt >= retries or parts:
                raise
            attempt += 1
            job.publish({
                "type": "status", "status": job.status,
                "detail": f"Fallo transitorio de Ollama; reintentando etapa ({attempt}/{retries})…",
            })
            await asyncio.sleep(3)


def _write_project(job: Job, files: dict[str, str], brief: str) -> Path:
    project_dir = config.GENERATED_DIR / job.id
    project_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (project_dir / name).write_text(content, encoding="utf-8")
    if job.stack == "react-tailwind":
        run_note = (
            "Abre `index.html` en un navegador (con conexión a internet: React y "
            "Tailwind se cargan por CDN y el JSX se transpila en el navegador). "
            "`app.jsx` y `styles.css` se incluyen como fuente editable; el propio "
            "`index.html` ya los lleva embebidos para funcionar con doble clic."
        )
    else:
        run_note = "Abre `index.html` en un navegador para ver el prototipo."
    readme = (
        f"# Prototipo generado por ImagineAI\n\n"
        f"**Prompt original:**\n\n> {job.prompt}\n\n"
        f"**Modelo:** {job.model}  ·  **Stack:** {job.stack}  ·  "
        f"**Dispositivo:** {job.device}\n\n"
        f"{run_note}\n\n"
        f"---\n\n## Manifiesto de diseño\n\n{brief}\n"
    )
    (project_dir / "README.md").write_text(readme, encoding="utf-8")
    return project_dir


def read_manifesto(job_id: str) -> str:
    """Recupera el manifiesto de diseño desde el README.md del proyecto ya escrito.

    Permite volver a mostrar el manifiesto al reabrir un trabajo terminado (la
    generación en vivo lo transmite por streaming, pero al reabrir desde el
    historial ese contexto se había perdido). Los README de ediciones heredan el
    manifiesto del padre y anexan secciones "## Edición"; esas se recortan."""
    readme_path = config.GENERATED_DIR / job_id / "README.md"
    if not readme_path.is_file():
        return ""
    marker = "## Manifiesto de diseño"
    text = readme_path.read_text(encoding="utf-8")
    idx = text.find(marker)
    if idx == -1:
        return ""
    body = text[idx + len(marker):].split("\n## Edición ", 1)[0].strip()
    if body.endswith("---"):  # separador que precede a las ediciones anexadas
        body = body[:-3].strip()
    return body


def make_zip(job_id: str) -> Path:
    """Empaqueta el proyecto generado en un .zip (idempotente)."""
    project_dir = config.GENERATED_DIR / job_id
    zip_path = config.GENERATED_DIR / f"{job_id}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(project_dir.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=f"prototipo-{job_id}/{path.relative_to(project_dir)}")
    return zip_path


async def run_generation(job: Job) -> None:
    try:
        # ── Etapa 1: dirección creativa ──────────────────────────────
        job.set_status("briefing", "El modelo está inventando la dirección de diseño…")
        brief = await _stream_stage(
            job, "brief", prompts.build_brief_messages(job.prompt), _opts(config.BRIEF_OPTIONS, job)
        )
        if not brief.strip():
            raise RuntimeError("El modelo no devolvió un manifiesto de diseño.")

        # ── Etapa 2: implementación, un archivo por llamada ─────────
        # Cada archivo recibe una llamada dedicada al modelo (con los anteriores
        # como contexto): concentra el presupuesto de tokens y evita que el
        # modelo "resuma" el CSS/JS como pasa cuando emite todo de una vez.
        stack_meta = prompts.STACKS.get(job.stack, prompts.STACKS[prompts.DEFAULT_STACK])
        job.set_status("coding", f"Escribiendo el código ({stack_meta['label']})…")
        files: dict[str, str] = {}
        for name in stack_meta["files"]:
            job.publish({"type": "status", "status": "coding", "detail": f"Escribiendo {name}…"})
            job.publish({"type": "token", "stage": "code", "text": f"\n\n═══ {name} ═══\n"})
            messages = prompts.build_file_messages(name, job.prompt, brief, files, job.stack)
            raw = await _stream_stage(job, "code", messages, _opts(config.CODE_OPTIONS, job))
            content = extract_single_file(raw, name)
            if not content:
                # Un reintento dirigido antes de rendirnos con este archivo.
                job.publish({
                    "type": "status", "status": "coding",
                    "detail": f"{name} no llegó en el formato esperado; pidiendo corrección…",
                })
                messages += [
                    {"role": "assistant", "content": raw[-4000:]},
                    {"role": "user", "content": prompts.REPAIR_USER.format(name=name)},
                ]
                raw = await _stream_stage(job, "code", messages, _opts(config.CODE_OPTIONS, job))
                content = extract_single_file(raw, name)
            if content:
                files[name] = content
            elif name in stack_meta["required"]:
                raise RuntimeError(
                    f"El modelo no produjo un {name} válido tras dos intentos. "
                    "Prueba de nuevo o usa un modelo más capaz."
                )
            else:
                job.publish({
                    "type": "status", "status": "coding",
                    "detail": f"Aviso: el modelo no completó {name}; el prototipo puede verse incompleto.",
                })

        # ── Validación de sintaxis del JSX (React) + auto-reparación ──
        # Un error de sintaxis en app.jsx hace fallar la transpilación de Babel y
        # deja la página en blanco. Lo detectamos con Babel (Node) y pedimos al
        # modelo que lo corrija, pasándole el error exacto del compilador.
        if job.stack == "react-tailwind" and "app.jsx" in files:
            # Reemplazo determinista de <img> por placeholders (el prompt no basta).
            files["app.jsx"], n_img = neutralize_jsx_images(files["app.jsx"])
            if n_img:
                job.publish({
                    "type": "status", "status": "coding",
                    "detail": f"Reemplazadas {n_img} imagen(es) inexistente(s) por bloques con gradiente.",
                })
            files["app.jsx"], n_gates = unblock_reveal_gates(files["app.jsx"])
            if n_gates:
                job.publish({
                    "type": "status", "status": "coding",
                    "detail": f"Corregidas {n_gates} sección(es) que nunca se habrían mostrado (guarda de revelado).",
                })
            base = prompts.build_file_messages("app.jsx", job.prompt, brief, files, job.stack)
            await _repair_jsx_loop(job, base, files)

        # ── Etapa 3: empaquetado ─────────────────────────────────────
        job.set_status("packaging", "Guardando archivos y empaquetando…")
        source_files = dict(files)  # archivos sueltos tal cual, para editar
        if job.stack == "react-tailwind":
            # index.html autocontenido para que el .zip funcione con doble clic.
            source_files["index.html"] = assemble_react_index(files)
        else:
            rotas = missing_assets(files)
            if rotas:
                job.publish({
                    "type": "status", "status": "coding",
                    "detail": f"Aviso: el HTML referencia archivos que no existen ({', '.join(rotas)}).",
                })
        _write_project(job, source_files, brief)
        make_zip(job.id)
        job.files = sorted(source_files) + ["README.md"]

        job.finished_at = time.time()
        job.status = "done"
        job.persist()
        job.publish({
            "type": "done",
            "files": job.files,
            "preview_url": f"/preview/{job.id}/index.html",
            "download_url": f"/api/jobs/{job.id}/download",
        })
    except Exception as exc:  # noqa: BLE001 — el error viaja al frontend
        job.status = "error"
        # Nunca dejar un mensaje vacío: algunas excepciones (httpx) tienen str() "".
        job.error = str(exc).strip() or f"{exc.__class__.__name__} (sin detalle)"
        job.finished_at = time.time()
        job.persist()
        job.publish({"type": "error", "message": job.error})


async def _repair_jsx_loop(job: Job, base_messages: list[dict], files: dict[str, str]) -> None:
    """Valida app.jsx con Babel y pide correcciones al modelo (máx. 2 intentos).

    Muta files["app.jsx"]. Si tras los intentos sigue sin compilar, avisa por el
    stream pero no aborta: la preview mostrará lo que haya.
    """
    jsx_error = validate_jsx(files["app.jsx"])
    for intento in range(1, 3):
        if not jsx_error:
            return
        job.publish({
            "type": "status", "status": job.status,
            "detail": f"app.jsx no compila ({jsx_error.splitlines()[0]}); reparando (intento {intento})…",
        })
        job.publish({"type": "token", "stage": "code", "text": f"\n\n═══ app.jsx (reparación {intento}) ═══\n"})
        messages = base_messages + [
            {"role": "assistant", "content": files["app.jsx"][-4000:]},
            {"role": "user", "content": prompts.REPAIR_JSX_USER.format(error=jsx_error)},
        ]
        raw = await _stream_stage(job, "code", messages, _opts(config.CODE_OPTIONS, job))
        fixed = extract_single_file(raw, "app.jsx")
        if not fixed:
            break
        files["app.jsx"] = fixed
        jsx_error = validate_jsx(fixed)
    if jsx_error:
        job.publish({
            "type": "status", "status": job.status,
            "detail": f"Aviso: app.jsx sigue con un error de sintaxis ({jsx_error.splitlines()[0]}); la preview puede verse en blanco.",
        })


async def run_edit(job: Job, selector: str, element_html: str, instruction: str) -> None:
    """Edición dirigida: el usuario señaló un elemento en la preview y describió
    el cambio. Se crea un proyecto NUEVO (job hijo) con los archivos editados,
    de modo que cada versión queda en el historial y es descargable."""
    try:
        stack_meta = prompts.STACKS.get(job.stack, prompts.STACKS[prompts.DEFAULT_STACK])
        parent_dir = config.GENERATED_DIR / (job.parent_id or "")
        if not job.parent_id or not parent_dir.is_dir():
            raise RuntimeError("El proyecto original ya no existe en disco.")

        job.set_status("editing", "El modelo está aplicando tu cambio…")
        job.publish({"type": "token", "stage": "code", "text": f"\n═══ edición: {instruction[:80]} ═══\n"})

        # Archivos fuente actuales. En react, el index guardado es el autocontenido:
        # se le extrae el cascarón para no duplicar CSS/JSX en el contexto.
        source: dict[str, str] = {}
        for name in stack_meta["files"]:
            path = parent_dir / name
            if path.is_file():
                source[name] = path.read_text(encoding="utf-8")
        if job.stack == "react-tailwind" and "index.html" in source:
            source["index.html"] = extract_react_shell(source["index.html"])

        messages = prompts.build_edit_messages(
            source, selector, element_html, instruction, job.stack
        )
        raw = await _stream_stage(job, "code", messages, _opts(config.EDIT_OPTIONS, job))
        changed = {n: c for n, c in parse_files(raw).items() if n in stack_meta["files"]}
        if not changed:
            job.publish({
                "type": "status", "status": "editing",
                "detail": "La edición no llegó en el formato esperado; pidiendo corrección…",
            })
            messages += [
                {"role": "assistant", "content": raw[-4000:]},
                {"role": "user", "content": prompts.EDIT_REPAIR_USER},
            ]
            raw = await _stream_stage(job, "code", messages, _opts(config.EDIT_OPTIONS, job))
            changed = {n: c for n, c in parse_files(raw).items() if n in stack_meta["files"]}
        if not changed:
            raise RuntimeError(
                "El modelo no devolvió ningún archivo modificado tras dos intentos. "
                "Prueba a reformular la instrucción."
            )

        files = {**source, **changed}
        if job.stack == "react-tailwind" and "app.jsx" in changed:
            files["app.jsx"], _ = neutralize_jsx_images(files["app.jsx"])
            files["app.jsx"], _ = unblock_reveal_gates(files["app.jsx"])
            await _repair_jsx_loop(job, messages[:2], files)

        # ── Empaquetado del proyecto editado como versión nueva ─────
        job.set_status("packaging", "Guardando la versión editada…")
        out = dict(files)
        if job.stack == "react-tailwind":
            out["index.html"] = reassemble_react_index(
                files.get("index.html", ""), files.get("styles.css", ""),
                files.get("app.jsx", ""),
            )
        project_dir = config.GENERATED_DIR / job.id
        project_dir.mkdir(parents=True, exist_ok=True)
        for name, content in out.items():
            (project_dir / name).write_text(content, encoding="utf-8")
        parent_readme = parent_dir / "README.md"
        readme = parent_readme.read_text(encoding="utf-8") if parent_readme.exists() else ""
        readme += (
            f"\n\n---\n\n## Edición ({time.strftime('%Y-%m-%d %H:%M')})\n\n"
            f"> {instruction}\n\nElemento: `{selector or '(página)'}` · "
            f"Versión anterior: {job.parent_id}\n"
        )
        (project_dir / "README.md").write_text(readme, encoding="utf-8")
        make_zip(job.id)
        job.files = sorted(out) + ["README.md"]

        job.finished_at = time.time()
        job.status = "done"
        job.persist()
        job.publish({
            "type": "done",
            "files": job.files,
            "preview_url": f"/preview/{job.id}/index.html",
            "download_url": f"/api/jobs/{job.id}/download",
        })
    except Exception as exc:  # noqa: BLE001 — el error viaja al frontend
        job.status = "error"
        job.error = str(exc).strip() or f"{exc.__class__.__name__} (sin detalle)"
        job.finished_at = time.time()
        job.persist()
        job.publish({"type": "error", "message": job.error})
