"""Unidades de hardware.py: uso REAL de cómputo (GPU vía nvidia-smi, CPU vía
psutil) para la barra de hardware del frontend — distinto de llm.gpu_status()
(fracción de VRAM que ocupa el modelo cargado)."""
import asyncio

from backend import hardware


class _FakeProc:
    def __init__(self, output: bytes):
        self._output = output

    async def communicate(self):
        return self._output, b""


def test_gpu_utilization_parses_nvidia_smi_output(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

    async def fake_exec(*args, **kwargs):
        return _FakeProc(b"37\n")

    monkeypatch.setattr(hardware.asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(hardware.gpu_utilization()) == 37


def test_gpu_utilization_clamps_out_of_range_values(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

    async def fake_exec(*args, **kwargs):
        return _FakeProc(b"142\n")

    monkeypatch.setattr(hardware.asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(hardware.gpu_utilization()) == 100


def test_gpu_utilization_none_when_binary_missing(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name: None)
    assert asyncio.run(hardware.gpu_utilization()) is None


def test_gpu_utilization_none_on_subprocess_failure(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

    async def failing_exec(*args, **kwargs):
        raise OSError("nvidia-smi no se pudo ejecutar")

    monkeypatch.setattr(hardware.asyncio, "create_subprocess_exec", failing_exec)
    assert asyncio.run(hardware.gpu_utilization()) is None


def test_gpu_utilization_none_on_garbage_output(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/nvidia-smi")

    async def fake_exec(*args, **kwargs):
        return _FakeProc(b"no soy un numero\n")

    monkeypatch.setattr(hardware.asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(hardware.gpu_utilization()) is None


def test_cpu_utilization_delegates_to_psutil(monkeypatch):
    captured = {}

    def fake_cpu_percent(interval=None):
        captured["interval"] = interval
        return 42.7

    monkeypatch.setattr(hardware.psutil, "cpu_percent", fake_cpu_percent)
    assert hardware.cpu_utilization() == 42.7
    assert captured["interval"] == 0.15


def test_snapshot_combines_gpu_and_cpu(monkeypatch):
    async def fake_gpu():
        return 55

    monkeypatch.setattr(hardware, "gpu_utilization", fake_gpu)
    monkeypatch.setattr(hardware, "cpu_utilization", lambda: 12.3)

    result = asyncio.run(hardware.snapshot())
    assert result == {
        "gpu": {"available": True, "utilization": 55},
        "cpu": {"available": True, "utilization": 12.3},
    }


def test_snapshot_reports_gpu_unavailable(monkeypatch):
    async def fake_gpu():
        return None

    monkeypatch.setattr(hardware, "gpu_utilization", fake_gpu)
    monkeypatch.setattr(hardware, "cpu_utilization", lambda: 5.0)

    result = asyncio.run(hardware.snapshot())
    assert result["gpu"] == {"available": False, "utilization": None}


def test_hardware_endpoint_exposes_snapshot(client, monkeypatch):
    async def fake_snapshot():
        return {
            "gpu": {"available": True, "utilization": 71},
            "cpu": {"available": True, "utilization": 8.4},
        }

    monkeypatch.setattr(hardware, "snapshot", fake_snapshot)
    resp = client.get("/api/hardware")
    assert resp.status_code == 200
    assert resp.json() == {
        "gpu": {"available": True, "utilization": 71},
        "cpu": {"available": True, "utilization": 8.4},
    }
