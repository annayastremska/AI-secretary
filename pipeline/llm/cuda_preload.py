# -*- coding: utf-8 -*-
"""Підвантажити CUDA-рантайм ДО імпорту llama_cpp, якщо він лежить у venv.

Причина -- заміряна 25.08.2026 на GPU-сервері, і вона неочевидна.

`llama-cpp-python` у нас стоїть як CUDA-збірка: її `libllama.so` слінкована з
`libcudart.so.12`. Самого CUDA-рантайму в системі немає -- він приїхав
пакетами `nvidia-*`, які тягне torch, і лежить у
`site-packages/nvidia/cuda_runtime/lib`. Динамічний завантажувач туди не
дивиться, тому:

    >>> from llama_cpp import llama_cpp
    RuntimeError: Failed to load shared library '.../libllama.so':
    libcudart.so.12: cannot open shared object file

Тобто без `LD_LIBRARY_PATH` модель на сервері не просто «їде на процесорі» --
вона не завантажується ВЗАГАЛІ. Обхід через змінну оточення працює, але це
міна: він залежить від того, ЯК саме хтось запустив апку. Один запуск із
іншого термінала -- і на демо немає моделі.

`LD_LIBRARY_PATH` після старту процесу правити марно (завантажувач читає її
один раз), а от відкрити потрібні бібліотеки вручну -- можна: після
`dlopen` вони вже в процесі, і залежність `libllama.so` вирішується ними.
Саме це тут і робиться, до першого імпорту llama_cpp.

Політика: тихо і без побічних ефектів. Немає тек -- нічого не робимо
(машина без GPU, наш звичайний випадок на ноуті); не відкрилось -- теж
нічого: llama_cpp сам скаже про це своєю помилкою, а ми не маскуємо її
своєю.
"""
import ctypes
import glob
import os
import site
import sys

#: Порядок має значення: cudart перший (від нього залежать решта),
#: далі cublas/cublasLt і nvrtc -- саме їх вимагає CUDA-збірка llama.cpp.
_LIBS = (
    ("cuda_runtime", "libcudart.so*"),
    ("cublas", "libcublasLt.so*"),
    ("cublas", "libcublas.so*"),
    ("cuda_nvrtc", "libnvrtc.so*"),
)

_done = False


def _site_dirs():
    dirs = []
    try:
        dirs.extend(site.getsitepackages())
    except AttributeError:          # venv без getsitepackages (рідко)
        pass
    user = getattr(site, "getusersitepackages", None)
    if callable(user):
        try:
            dirs.append(user())
        except Exception:
            pass
    # Шлях самого інтерпретатора -- останній рубіж для нестандартних збірок
    dirs.append(os.path.join(sys.prefix, "lib"))
    return [d for d in dirs if d and os.path.isdir(d)]


def preload():
    """-> перелік реально відкритих файлів (порожній = нічого не робили).

    Ідемпотентна: другий виклик нічого не робить. Виняток нагору не йде
    ніколи -- це допоміжний крок, а не функція продукту.
    """
    global _done
    if _done:
        return []
    _done = True
    opened = []
    for base in _site_dirs():
        for pkg, pattern in _LIBS:
            for path in sorted(glob.glob(
                    os.path.join(base, "nvidia", pkg, "lib", pattern))):
                try:
                    # RTLD_GLOBAL -- обов'язково: символи мусять стати
                    # видимими для libllama.so, яку завантажать ПІЗНІШЕ.
                    ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                    opened.append(path)
                    break           # одного файла на бібліотеку досить
                except OSError:
                    continue
    return opened
