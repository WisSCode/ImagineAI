"""Pruebas de carga no funcionales con Locust para ImagineAI.

Escenarios:
1. Picos de Generaciones Concurrentes
2. Descarga Masiva de Archivos
3. Autenticación Masiva (registro/login)
"""
import json
import time
import uuid
from locust import HttpUser, task, between, events
from locust.contrib.fasthttp import FastHttpUser


# ─────────────────────────────────────────────────────────────────
# Scenario 1: Picos de Generaciones Concurrentes
# ─────────────────────────────────────────────────────────────────

class GenerationUser(HttpUser):
    """Usuario que genera proyectos con el LLM."""
    
    wait_time = between(2, 5)  # Espera realista entre intentos
    
    def on_start(self):
        """Registrarse y logearse antes de empezar."""
        # Generar credenciales únicas
        unique_id = str(uuid.uuid4())[:8]
        self.username = f"gen_user_{unique_id}"
        self.password = "securepass123"
        
        # Registrarse
        self.register()
    
    def register(self):
        """Registrar nuevo usuario."""
        with self.client.post(
            "/api/auth/register",
            json={"username": self.username, "password": self.password},
            catch_response=True
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 409:
                # Usuario ya existe (de run anterior)
                resp.success()
            else:
                resp.failure(f"Register failed: {resp.status_code}")
    
    @task(3)
    def health_check(self):
        """Verificar salud del servidor."""
        self.client.get("/api/health")
    
    @task(2)
    def list_jobs(self):
        """Listar trabajos del usuario."""
        self.client.get("/api/jobs")
    
    @task(10)
    def generate_project(self):
        """Generar un proyecto (la tarea más pesada)."""
        prompts = [
            "una landing page minimalista para una startup de AI",
            "una página de portafolio para un diseñador gráfico",
            "un blog moderno con tema oscuro",
            "una tienda online para productos electrónicos",
            "una página de contacto interactiva",
        ]
        
        prompt = prompts[hash(time.time()) % len(prompts)]
        
        with self.client.post(
            "/api/generate",
            json={
                "prompt": prompt,
                "stack": "react-tailwind",
                "device": "gpu"
            },
            catch_response=True,
            timeout=30
        ) as resp:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    job_id = data.get("job_id")
                    if job_id:
                        resp.success()
                        # Esperar a que complete sin bloquear (polling ligero)
                        self.wait_for_job_completion(job_id)
                    else:
                        resp.failure("No job_id returned")
                except json.JSONDecodeError:
                    resp.failure("Invalid JSON response")
            else:
                resp.failure(f"Generate failed: {resp.status_code}")
    
    def wait_for_job_completion(self, job_id, timeout=30):
        """Esperar a que un job complete (con timeout)."""
        start = time.time()
        while time.time() - start < timeout:
            resp = self.client.get(f"/api/jobs/{job_id}")
            if resp.status_code == 200:
                job = resp.json()
                if job.get("status") in ("done", "error"):
                    return job
            time.sleep(1)
        return None


# ─────────────────────────────────────────────────────────────────
# Scenario 4: Descarga Masiva de Archivos
# ─────────────────────────────────────────────────────────────────

class DownloadUser(HttpUser):
    """Usuario que descarga proyectos generados."""
    
    wait_time = between(1, 3)
    
    def on_start(self):
        """Preparar: registrarse y generar algunos proyectos."""
        unique_id = str(uuid.uuid4())[:8]
        self.username = f"download_user_{unique_id}"
        self.password = "securepass123"
        self.job_ids = []
        
        # Registrarse
        with self.client.post(
            "/api/auth/register",
            json={"username": self.username, "password": self.password},
            catch_response=True
        ) as resp:
            if resp.status_code in (200, 409):
                resp.success()
    
    @task(1)
    def health_check(self):
        """Verificar salud."""
        self.client.get("/api/health")
    
    @task(5)
    def generate_for_download(self):
        """Generar un proyecto para luego descargarlo."""
        with self.client.post(
            "/api/generate",
            json={
                "prompt": "una página simple de prueba para descargar",
                "stack": "react-tailwind"
            },
            catch_response=True,
            timeout=30
        ) as resp:
            if resp.status_code == 200:
                try:
                    job_id = resp.json().get("job_id")
                    if job_id:
                        self.job_ids.append(job_id)
                        resp.success()
                except:
                    resp.failure("Failed to parse job_id")
    
    @task(15)
    def download_project(self):
        """Descargar un proyecto ZIP."""
        if not self.job_ids:
            return
        
        # Usar el último job_id generado
        job_id = self.job_ids[-1]
        
        with self.client.get(
            f"/api/jobs/{job_id}/download",
            catch_response=True,
            timeout=30
        ) as resp:
            if resp.status_code == 200:
                # Validar que es un ZIP válido (comienza con PK)
                if resp.content[:2] == b'PK':
                    resp.success()
                else:
                    resp.failure("Response is not a valid ZIP file")
            elif resp.status_code == 404:
                # Job aún no existe o fue limpiado
                self.job_ids.remove(job_id)
                resp.failure("Job not found")
            else:
                resp.failure(f"Download failed: {resp.status_code}")
    
    @task(2)
    def preview_project(self):
        """Acceder a la preview HTML del proyecto."""
        if not self.job_ids:
            return
        
        job_id = self.job_ids[-1]
        
        with self.client.get(
            f"/preview/{job_id}/index.html",
            catch_response=True,
            timeout=10
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 404:
                resp.failure("Preview not found")
            else:
                resp.failure(f"Preview failed: {resp.status_code}")


# ─────────────────────────────────────────────────────────────────
# Scenario 5: Autenticación Masiva
# ─────────────────────────────────────────────────────────────────

class AuthenticationUser(HttpUser):
    """Usuario que realiza registros y logins concurrentes."""
    
    wait_time = between(0.5, 2)  # Más rápido para simular picos
    
    @task(7)
    def register_new_user(self):
        """Registrar un nuevo usuario único."""
        unique_id = str(uuid.uuid4())[:12]
        username = f"auth_user_{unique_id}"
        password = "test_password_123"
        
        with self.client.post(
            "/api/auth/register",
            json={"username": username, "password": password},
            catch_response=True,
            timeout=10
        ) as resp:
            if resp.status_code == 200:
                resp.success()
                self.last_username = username
                self.last_password = password
            elif resp.status_code == 409:
                # Usuario duplicado (muy raro pero posible)
                resp.failure("Duplicate username (unexpected)")
            else:
                resp.failure(f"Register failed: {resp.status_code} - {resp.text[:100]}")
    
    @task(5)
    def login_user(self):
        """Hacer login con un usuario registrado."""
        if not hasattr(self, 'last_username'):
            return
        
        with self.client.post(
            "/api/auth/login",
            json={"username": self.last_username, "password": self.last_password},
            catch_response=True,
            timeout=10
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Login failed: {resp.status_code}")
    
    @task(3)
    def get_current_user(self):
        """Obtener datos del usuario actual."""
        with self.client.get(
            "/api/auth/me",
            catch_response=True,
            timeout=5
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Get user failed: {resp.status_code}")
    
    @task(2)
    def logout_user(self):
        """Logout del usuario."""
        with self.client.post(
            "/api/auth/logout",
            catch_response=True,
            timeout=5
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Logout failed: {resp.status_code}")


# ─────────────────────────────────────────────────────────────────
# Event Handlers para Reportes
# ─────────────────────────────────────────────────────────────────

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Al iniciar la prueba."""
    print("\n" + "="*70)
    print("🚀 Iniciando prueba de carga de ImagineAI")
    print("="*70)
    print(f"Host: {environment.host}")
    print(f"Usuarios: {len(environment.locusts)} (configurados)")
    print("="*70 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Al terminar la prueba."""
    print("\n" + "="*70)
    print("✅ Prueba de carga completada")
    print("="*70)
    print("\nResumen:")
    print(f"  Duración total: {environment.stats.total.get_response_time_percentile(1):.2f}s")
    print(f"  Total requests: {environment.stats.total.num_requests}")
    print(f"  Total failures: {environment.stats.total.num_failures}")
    print(f"  Failure rate: {environment.stats.total.failure_rate * 100:.2f}%")
    print("="*70 + "\n")


# Notas de uso:
# 
# Picos de Generación (Scenario 1):
#   locust -f locustfile.py -u 50 -r 5 --run-time 10m --headless \
#     -t GenerationUser --host http://localhost:8000
#
# Descarga Masiva (Scenario 4):
#   locust -f locustfile.py -u 30 -r 3 --run-time 5m --headless \
#     -t DownloadUser --host http://localhost:8000
#
# Autenticación Masiva (Scenario 5):
#   locust -f locustfile.py -u 100 -r 10 --run-time 2m --headless \
#     -t AuthenticationUser --host http://localhost:8000
#
# Web UI (todas simultáneamente):
#   locust -f locustfile.py --host http://localhost:8000
#   Luego acceder a http://localhost:8089
