"""Flujo completo de generación y edición con un LLM guionado (sin Ollama)."""
import json
import time

from backend import config

BRIEF = """## CONCEPTO
Prueba: una landing mínima.
## PALETA
#ffffff fondo, #111111 tinta, #3366ff acento.
"""

SHELL_HTML = """<<<FILE: index.html>>>
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <title>Test</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="styles.css" />
  <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone@7/babel.min.js"></script>
</head>
<body class="bg-bg text-ink">
  <div id="root"></div>
  <script type="text/babel" src="app.jsx" data-presets="react"></script>
</body>
</html>
<<<END>>>"""

CSS = """<<<FILE: styles.css>>>
@keyframes rise { from { opacity: 0; } to { opacity: 1; } }
.reveal { animation: rise .6s ease; }
<<<END>>>"""

JSX = """<<<FILE: app.jsx>>>
const { useState } = React;
function App() {
  const [n, setN] = useState(0);
  return (
    <main className="p-8 reveal">
      <h1 className="text-3xl">Hola Prueba</h1>
      <button onClick={() => setN(n + 1)}>Clicks: {n}</button>
    </main>
  );
}
ReactDOM.createRoot(document.getElementById('root')).render(<App />);
<<<END>>>"""

JSX_EDITED = JSX.replace("Hola Prueba", "Hola Editado")


def wait_done(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    raise TimeoutError("el job no terminó a tiempo")


def start_generation(client, fake_llm, prompt="una landing de prueba para tests"):
    fake_llm.push(BRIEF, SHELL_HTML, CSS, JSX)
    resp = client.post("/api/generate", json={"prompt": prompt, "stack": "react-tailwind"})
    assert resp.status_code == 200, resp.text
    return resp.json()["job_id"]


def test_full_generation(client, user, fake_llm):
    job_id = start_generation(client, fake_llm)
    job = wait_done(client, job_id)
    assert job["status"] == "done", job.get("error")
    assert set(job["files"]) >= {"index.html", "styles.css", "app.jsx", "README.md"}

    # El proyecto quedó en disco con el index autocontenido (CSS y JSX embebidos).
    html = (config.GENERATED_DIR / job_id / "index.html").read_text(encoding="utf-8")
    assert "Hola Prueba" in html and "@keyframes rise" in html
    assert 'src="app.jsx"' not in html  # ya no referencia archivos sueltos

    # Descarga del .zip
    resp = client.get(f"/api/jobs/{job_id}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    # SSE replay tras terminar: contiene el evento done con las URLs
    with client.stream("GET", f"/api/jobs/{job_id}/events") as s:
        events = [json.loads(l[6:]) for l in s.iter_lines() if l.startswith("data: ")]
    assert events[-1]["type"] == "done"
    assert events[-1]["preview_url"].endswith("index.html")

    # La preview se sirve
    resp = client.get(f"/preview/{job_id}/index.html")
    assert resp.status_code == 200 and "Hola Prueba" in resp.text


def test_generation_repairs_broken_jsx(client, user, fake_llm):
    broken = JSX.replace("</main>", "")  # etiqueta sin cerrar: Babel no compila
    fake_llm.push(BRIEF, SHELL_HTML, CSS, broken, JSX)  # la 5ª respuesta es la reparación
    resp = client.post("/api/generate", json={"prompt": "landing con jsx roto"})
    job = wait_done(client, resp.json()["job_id"])
    assert job["status"] == "done"
    html = (config.GENERATED_DIR / job["id"] / "index.html").read_text(encoding="utf-8")
    assert "</main>" in html  # quedó la versión reparada


def test_edit_flow(client, user, fake_llm):
    job_id = start_generation(client, fake_llm)
    wait_done(client, job_id)

    fake_llm.push(JSX_EDITED)
    resp = client.post(
        f"/api/jobs/{job_id}/edit",
        json={
            "instruction": "cambia el titular a 'Hola Editado'",
            "selector": "main > h1",
            "element_html": '<h1 class="text-3xl">Hola Prueba</h1>',
        },
    )
    assert resp.status_code == 200, resp.text
    edit_id = resp.json()["job_id"]
    assert edit_id != job_id
    job = wait_done(client, edit_id)
    assert job["status"] == "done", job.get("error")

    # La versión nueva tiene el cambio; la original queda intacta.
    new_html = (config.GENERATED_DIR / edit_id / "index.html").read_text(encoding="utf-8")
    old_html = (config.GENERATED_DIR / job_id / "index.html").read_text(encoding="utf-8")
    assert "Hola Editado" in new_html and "@keyframes rise" in new_html
    assert "Hola Prueba" in old_html

    # El contexto que vio el modelo no duplica el JSX dentro del index (cascarón).
    edit_messages = fake_llm.calls[-1]
    user_msg = edit_messages[-1]["content"]
    assert "cambia el titular" in user_msg
    assert user_msg.count("Hola Prueba") == 2  # una en app.jsx fuente + una en el elemento

    # El historial registra la edición como hija del original.
    jobs = client.get("/api/jobs").json()["jobs"]
    edit_row = next(j for j in jobs if j["id"] == edit_id)
    assert edit_row["kind"] == "edit" and edit_row["parent_id"] == job_id


def test_generation_with_cpu_device(client, user, fake_llm):
    """El dispositivo se elige por generación, se persiste y llega a Ollama."""
    fake_llm.push(BRIEF, SHELL_HTML, CSS, JSX)
    resp = client.post(
        "/api/generate",
        json={"prompt": "landing procesada en cpu", "stack": "react-tailwind", "device": "cpu"},
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    job = wait_done(client, job_id)
    assert job["status"] == "done", job.get("error")

    # Cada llamada al modelo durante la generación fue forzada a CPU.
    assert fake_llm.options_calls, "no se registraron llamadas al modelo"
    for options in fake_llm.options_calls:
        assert options["num_gpu"] == 0
        assert options["num_thread"] > 0

    # El historial recuerda el dispositivo elegido.
    jobs = client.get("/api/jobs").json()["jobs"]
    row = next(j for j in jobs if j["id"] == job_id)
    assert row["device"] == "cpu"

    # Una edición sobre este proyecto hereda el mismo dispositivo.
    fake_llm.push(JSX_EDITED)
    resp = client.post(
        f"/api/jobs/{job_id}/edit",
        json={"instruction": "cambia el titular", "selector": "h1", "element_html": "<h1>x</h1>"},
    )
    assert resp.status_code == 200, resp.text
    edit_id = resp.json()["job_id"]
    wait_done(client, edit_id)
    edit_options = fake_llm.options_calls[-1]
    assert edit_options["num_gpu"] == 0


def test_generation_defaults_to_gpu_device(client, user, fake_llm):
    job_id = start_generation(client, fake_llm)
    job = wait_done(client, job_id)
    assert job["status"] == "done", job.get("error")
    jobs = client.get("/api/jobs").json()["jobs"]
    row = next(j for j in jobs if j["id"] == job_id)
    assert row["device"] == "gpu"
    for options in fake_llm.options_calls:
        assert "num_gpu" not in options or options["num_gpu"] != 0


def test_jobs_are_private_per_user(client, user, fake_llm):
    job_id = start_generation(client, fake_llm)
    wait_done(client, job_id)

    # Otro usuario no ve ni puede tocar el trabajo.
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/register", json={"username": f"intruso{time.time_ns()}", "password": "clave123"}
    )
    assert all(j["id"] != job_id for j in client.get("/api/jobs").json()["jobs"])
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 404
    assert (
        client.post(
            f"/api/jobs/{job_id}/edit", json={"instruction": "haz algo"}
        ).status_code
        == 404
    )


def test_history_survives_restart_simulation(client, user, fake_llm):
    """El historial sale de SQLite: aunque el job no esté en memoria, se lista."""
    from backend.jobs import manager

    job_id = start_generation(client, fake_llm)
    wait_done(client, job_id)
    manager._jobs.pop(job_id)  # simula reinicio del servidor

    jobs = client.get("/api/jobs").json()["jobs"]
    row = next(j for j in jobs if j["id"] == job_id)
    assert row["status"] == "done"
    assert row["preview_url"] and row["download_url"]
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 200
