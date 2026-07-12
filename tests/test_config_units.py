"""Unidades de config: selección de dispositivo de cómputo (GPU/CPU)."""
import os

from backend import config


def test_device_options_cpu_forces_zero_gpu_layers():
    opts = config.device_options("cpu")
    assert opts["num_gpu"] == 0
    assert opts["num_thread"] == (os.cpu_count() or 4)


def test_device_options_gpu_respects_num_gpu_env(monkeypatch):
    monkeypatch.setattr(config, "NUM_GPU", -1)
    assert config.device_options("gpu") == {}

    monkeypatch.setattr(config, "NUM_GPU", 20)
    assert config.device_options("gpu") == {"num_gpu": 20}


def test_device_options_unknown_device_falls_back_to_gpu_behavior(monkeypatch):
    monkeypatch.setattr(config, "NUM_GPU", -1)
    assert config.device_options("algo-raro") == {}


def test_devices_registry_has_gpu_and_cpu():
    assert set(config.DEVICES) == {"gpu", "cpu"}
    assert config.DEFAULT_DEVICE in config.DEVICES
