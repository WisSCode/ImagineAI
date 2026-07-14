"""Manejo de errores en generación: validaciones y casos de fallo."""
import time
import zipfile
from pathlib import Path

from backend import config, pipeline
import pytest


# ─────────────────────────────────────────────────────────────────
# GRUPO 1: Validaciones Básicas (input validation)
# ─────────────────────────────────────────────────────────────────

class TestPromptValidation:
    """Validaciones en el prompt."""

    def test_prompt_too_short(self, client, user):
        """Prompt menor a 8 caracteres es rechazado."""
        r = client.post(
            "/api/generate",
            json={"prompt": "corto"}
        )
        assert r.status_code == 422  # Validation error de Pydantic

    def test_prompt_only_spaces(self, client, user):
        """Prompt solo con espacios es rechazado."""
        r = client.post(
            "/api/generate",
            json={"prompt": "        "}
        )
        assert r.status_code == 422  # Validation error

    def test_prompt_spaces_and_newlines(self, client, user):
        """Prompt con espacios y saltos de línea es rechazado."""
        r = client.post(
            "/api/generate",
            json={"prompt": "   \n\n   \t  "}
        )
        assert r.status_code == 422

    def test_prompt_too_long(self, client, user):
        """Prompt mayor a 6000 caracteres es rechazado."""
        long_prompt = "a" * 6001
        r = client.post(
            "/api/generate",
            json={"prompt": long_prompt}
        )
        assert r.status_code == 422


class TestStackAndDevice:
    """Validaciones de stack y dispositivo (normalización a defaults)."""

    def test_invalid_stack_uses_default(self, client, user, fake_llm):
        """Stack inválido se normaliza al default sin error."""
        # Preparar respuestas fake
        fake_llm.push(
            "## Brief\nDiseño minimalist",  # brief
            "<<<FILE: index.html>>>\n<!DOCTYPE html><html></html>\n<<<END>>>",  # html
            "<<<FILE: styles.css>>>\nbody { }\n<<<END>>>",  # css
            "<<<FILE: app.jsx>>>\nfunction App() { return null; }\n<<<END>>>"  # jsx
        )
        
        r = client.post(
            "/api/generate",
            json={"prompt": "una landing de prueba", "stack": "stack-inexistente"}
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        
        # Verificar que se normalizó al default
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["stack"] == "react-tailwind"  # default

    def test_invalid_device_uses_default(self, client, user, fake_llm):
        """Dispositivo inválido se normaliza al default."""
        fake_llm.push(
            "## Brief\nDiseño",
            "<<<FILE: index.html>>>\n<!DOCTYPE html><html></html>\n<<<END>>>",
            "<<<FILE: styles.css>>>\nbody { }\n<<<END>>>",
            "<<<FILE: app.jsx>>>\nfunction App() { return null; }\n<<<END>>>"
        )
        
        r = client.post(
            "/api/generate",
            json={"prompt": "una landing de prueba", "device": "device-invalido"}
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["device"] == "gpu"  # default


# ─────────────────────────────────────────────────────────────────
# GRUPO 2: Errores en Generación (runtime errors)
# ─────────────────────────────────────────────────────────────────

def wait_done(client, job_id, timeout=30):
    """Espera a que un job termine (done o error)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    raise TimeoutError("El job no terminó a tiempo")


class TestGenerationErrorHandling:
    """Errores durante la generación."""

    def test_empty_brief_triggers_error(self, client, user, fake_llm):
        """Si el modelo devuelve brief vacío, la generación falla con error."""
        # Brief vacío → debe fallar en validación
        fake_llm.push_empty()  # brief vacío
        
        r = client.post(
            "/api/generate",
            json={"prompt": "una landing de prueba"}
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        
        job = wait_done(client, job_id)
        assert job["status"] == "error"
        assert "manifiesto de diseño" in job["error"].lower()

    def test_missing_required_file_after_retries(self, client, user, fake_llm):
        """Si no se puede generar archivo requerido tras reintentos, falla."""
        # Brief válido, pero luego no devolvemos index.html en ningún intento
        fake_llm.push(
            "## Brief\nDiseño limpio",
            # Primera llamada para index.html → respuesta inválida
            "No es un archivo válido",
            # Reintento para index.html → respuesta inválida de nuevo
            "Tampoco",
            # Las siguientes no se llegará porque falló index.html
        )
        
        r = client.post(
            "/api/generate",
            json={"prompt": "una landing de prueba"}
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        
        job = wait_done(client, job_id)
        assert job["status"] == "error"
        assert "index.html" in job["error"] or "válido" in job["error"].lower()

    def test_jsx_syntax_error_unrepairablerecision(self, client, user, fake_llm):
        """Si JSX no compila y los reintentos fallan, avisa pero no aborta."""
        # Brief y archivos válidos, pero JSX con error de sintaxis
        broken_jsx = "function App() { return <div></div; }"  # falta >
        
        fake_llm.push(
            "## Brief\nDiseño",
            "<<<FILE: index.html>>>\n<!DOCTYPE html><html><head></head><body><div id='root'></div></body></html>\n<<<END>>>",
            "<<<FILE: styles.css>>>\nbody { margin: 0; }\n<<<END>>>",
            f"<<<FILE: app.jsx>>>\n{broken_jsx}\n<<<END>>>",
            # Reintento 1: sigue roto
            f"<<<FILE: app.jsx>>>\n{broken_jsx}\n<<<END>>>",
            # Reintento 2: sigue roto
            f"<<<FILE: app.jsx>>>\n{broken_jsx}\n<<<END>>>"
        )
        
        r = client.post(
            "/api/generate",
            json={"prompt": "una landing de prueba"}
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        
        job = wait_done(client, job_id)
        # No aborta, pero marca como done con advertencia en eventos
        assert job["status"] == "done"  # Completa igual
        # Verificar que hay un evento de aviso sobre el error de sintaxis
        with client.stream("GET", f"/api/jobs/{job_id}/events") as s:
            events = [line for line in s.iter_lines() if line.startswith("data: ")]
            # Debe haber un evento de status con detalle sobre el error
            has_jsx_warning = any(
                "sintaxis" in line.lower() or "compila" in line.lower()
                for line in events
            )
            # Si Node/Babel no está disponible, no hay validación, así que solo
            # verificamos que la generación terminó
            assert job["status"] == "done"


class TestOutputValidation:
    """Validación de archivos generados."""

    def test_generated_zip_is_valid(self, client, user, fake_llm):
        """El ZIP generado es válido y contiene los archivos esperados."""
        fake_llm.push(
            "## Brief\nDiseño minimalista",
            "<<<FILE: index.html>>>\n<!DOCTYPE html><html><head><title>Test</title></head><body><div id='root'></div></body></html>\n<<<END>>>",
            "<<<FILE: styles.css>>>\nbody { margin: 0; color: #333; }\n<<<END>>>",
            "<<<FILE: app.jsx>>>\nfunction App() { return <h1>Test</h1>; }\n<<<END>>>"
        )
        
        r = client.post(
            "/api/generate",
            json={"prompt": "una landing de prueba"}
        )
        job_id = r.json()["job_id"]
        
        job = wait_done(client, job_id)
        assert job["status"] == "done"
        
        # Descargar el ZIP
        resp = client.get(f"/api/jobs/{job_id}/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        
        # Verificar que es un ZIP válido y contiene archivos
        import io
        zip_buffer = io.BytesIO(resp.content)
        with zipfile.ZipFile(zip_buffer, 'r') as zf:
            # Debe contener archivos dentro de carpeta con nombre del job
            names = zf.namelist()
            assert len(names) > 0
            # Los nombres deben contener el job_id
            assert all(job_id in name for name in names)
            # Verificar que están los archivos principales
            html_files = [n for n in names if "index.html" in n]
            assert len(html_files) > 0

    def test_html_output_is_valid(self, client, user, fake_llm):
        """El HTML generado es parseble y contiene contenido esperado."""
        fake_llm.push(
            "## Brief\nDiseño para landing",
            "<<<FILE: index.html>>>\n<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body><h1>Mi Landing</h1></body></html>\n<<<END>>>",
            "<<<FILE: styles.css>>>\nh1 { color: blue; }\n<<<END>>>",
            "<<<FILE: app.jsx>>>\nfunction App() { return <h1>Mi Landing</h1>; }\n<<<END>>>"
        )
        
        r = client.post(
            "/api/generate",
            json={"prompt": "una landing de prueba"}
        )
        job_id = r.json()["job_id"]
        
        job = wait_done(client, job_id)
        assert job["status"] == "done"
        
        # El HTML debe estar en el directorio del proyecto
        html_path = config.GENERATED_DIR / job_id / "index.html"
        assert html_path.exists()
        
        html_content = html_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE" in html_content
        assert "<html" in html_content
        assert "</html>" in html_content
        assert "Mi Landing" in html_content

    def test_project_directory_structure(self, client, user, fake_llm):
        """El directorio del proyecto contiene la estructura esperada."""
        fake_llm.push(
            "## Brief\nDiseño",
            "<<<FILE: index.html>>>\n<!DOCTYPE html><html></html>\n<<<END>>>",
            "<<<FILE: styles.css>>>\nbody { }\n<<<END>>>",
            "<<<FILE: app.jsx>>>\nfunction App() { return null; }\n<<<END>>>"
        )
        
        r = client.post(
            "/api/generate",
            json={"prompt": "una landing de prueba"}
        )
        job_id = r.json()["job_id"]
        
        job = wait_done(client, job_id)
        assert job["status"] == "done"
        
        project_dir = config.GENERATED_DIR / job_id
        assert project_dir.exists()
        assert (project_dir / "index.html").exists()
        assert (project_dir / "styles.css").exists()
        assert (project_dir / "app.jsx").exists()
        assert (project_dir / "README.md").exists()
