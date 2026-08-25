# -*- coding: utf-8 -*-
"""Втрата GPU має давати ПОВІЛЬНО, а не ЗЛАМАНО.

Причина існування тесту -- два заміряні випадки на тому самому сервері:

  24.08.2026 -- vGPU втратив ліцензію: карта в списку лишилась, обчислення
                зникли. Автовизначення OCR-бекенда обрало GPU-шлях, той не
                піднявся, і кожне фото витрачало 600 с таймауту й виходило
                `unresolved`;
  25.08.2026 -- установка CUDA-toolkit знесла GRID-драйвер: NVML перестав
                відповідати взагалі, хоч обчислення ще йшли.

Спільне в обох: «карта є» і «карта рахує» -- різні твердження, і система не
має права падати, коли друге виявилось хибним. Демо мусить пережити втрату
карти з деградацією швидкості.

Запуск:
    python -m pytest eval/tests/test_gpu_fallback.py -q
"""
import os
import sys

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from pipeline.llm.client import LlamaClient


class _FakeLlama:
    """Макет llama_cpp.Llama: із шарами на GPU падає, на процесорі -- ні.
    Саме така поведінка в реальності: конструктор кидає при ініціалізації
    CUDA-бекенда."""

    created = []

    def __init__(self, **kwargs):
        _FakeLlama.created.append(kwargs)
        if kwargs.get("n_gpu_layers"):
            raise RuntimeError("CUDA error: operation not supported")


@pytest.fixture()
def model_file(tmp_path):
    """Клієнт перевіряє наявність вагів у конструкторі (і правильно робить),
    тому потрібен реальний файл -- порожній, бо саме llama_cpp ми й підміняємо."""
    p = tmp_path / "model.gguf"
    p.write_bytes(b"")
    return str(p)


@pytest.fixture()
def fake_llama(monkeypatch):
    _FakeLlama.created = []
    module = type(sys)("llama_cpp")
    module.Llama = _FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", module)
    return _FakeLlama


def test_gpu_failure_falls_back_to_cpu(fake_llama, model_file):
    client = LlamaClient(model_path=model_file, n_gpu_layers=-1)
    assert client.llm is not None, "клієнт мусить піднятись на процесорі"
    tried = [k.get("n_gpu_layers") for k in fake_llama.created]
    assert tried == [-1, 0], tried
    # І стан мусить оновитись: наступні виклики не мають знову пробувати GPU
    assert client.n_gpu_layers == 0


def test_second_call_does_not_retry_gpu(fake_llama, model_file):
    client = LlamaClient(model_path=model_file, n_gpu_layers=99)
    client.llm
    n_after_first = len(fake_llama.created)
    client.llm
    assert len(fake_llama.created) == n_after_first, "модель кешується"


def test_cpu_only_client_still_raises(fake_llama, model_file):
    """Запобіжник проти маскування: якщо GPU не просили, помилка моделі --
    це справжня помилка, і глушити її не можна."""
    class _AlwaysFails(_FakeLlama):
        def __init__(self, **kwargs):
            _FakeLlama.created.append(kwargs)
            raise RuntimeError("зіпсований файл моделі")

    sys.modules["llama_cpp"].Llama = _AlwaysFails
    client = LlamaClient(model_path=model_file, n_gpu_layers=0)
    with pytest.raises(RuntimeError, match="зіпсований"):
        client.llm


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
