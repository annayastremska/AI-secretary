# -*- coding: utf-8 -*-
"""Апка мусить читати вихід ТОГО профілю, який сама передає пайплайну.

Дефект, заміряний 25.08.2026 на сервері першим же живим завантаженням через
браузер — тобто на сценарії, який на демо показуємо першим.

`config-gpu.yaml` пише вихід у `data/output-demo` (окремий вихід під
демо-набір — навмисно, щоб у базу їхав рівно він). А в апці шлях до індексу
був літералом `data/output`. Наслідок: пайплайн відпрацював правильно, запис
ліг куди належить, а апка його не знайшла і сказала «пайплайн завершився, але
запису з таким хешем немає».

Це найгірший вид поломки — та, що бреше про успішну роботу: у логах пайплайна
жодної помилки, у відповіді апки «не вдалося».

Тест не про рядок у конфігу, а про ЗВ'ЯЗОК: змінюємо профіль → мусить
змінитись місце, де апка шукає.

Запуск:
    python -m pytest demos/upload_app/tests/test_output_root_follows_profile.py -q
"""
import importlib
import io
import os

import pytest
import yaml


def _reload_app(monkeypatch, config_path):
    monkeypatch.setenv("APP_PIPELINE_CONFIG", config_path)
    from demos.upload_app import app as app_module
    return importlib.reload(app_module)


def test_output_root_comes_from_the_active_profile(monkeypatch, tmp_path):
    """Головне твердження: інший профіль -- інший корінь виходу."""
    cfg = tmp_path / "config-test.yaml"
    io.open(cfg, "w", encoding="utf-8").write(
        yaml.safe_dump({"paths": {"output_dir": "data/output-somewhere-else"},
                        "storage": {"local_root": "data/output-somewhere-else"}}))
    mod = _reload_app(monkeypatch, str(cfg))
    assert mod.OUTPUT_ROOT.endswith("output-somewhere-else"), mod.OUTPUT_ROOT
    assert mod.INDEX_PATH.startswith(mod.OUTPUT_ROOT), mod.INDEX_PATH


def test_gpu_profile_of_the_repo_points_where_it_says(monkeypatch):
    """Не абстрактний профіль, а НАШ серверний: саме на ньому дефект і
    стрелив. Якщо хтось поміняє вихід у config-gpu.yaml, тест це побачить."""
    mod = _reload_app(monkeypatch, "demos/upload_app/config-gpu.yaml")
    declared = yaml.safe_load(io.open(
        os.path.join(mod.PROJECT_ROOT, "demos", "upload_app",
                     "config-gpu.yaml"), encoding="utf-8"))
    assert mod.OUTPUT_ROOT.endswith(
        declared["paths"]["output_dir"].replace("/", os.sep).replace(
            "/", os.sep)) or mod.OUTPUT_ROOT.endswith(
        declared["paths"]["output_dir"]), (mod.OUTPUT_ROOT, declared)


def test_unreadable_profile_does_not_break_import(monkeypatch, tmp_path):
    """Запобіжник: профіль, якого немає, не має валити апку на імпорті --
    інакше друкарська помилка в змінній оточення = немає демо. Тоді беремо
    попередній дефолт, а про проблему апка скаже на першому завантаженні."""
    mod = _reload_app(monkeypatch, str(tmp_path / "no-such-config.yaml"))
    assert mod.OUTPUT_ROOT.endswith(os.path.join("data", "output"))


def test_absolute_output_dir_is_respected(monkeypatch, tmp_path):
    """Абсолютний шлях не має склеюватись із коренем репозиторію."""
    target = tmp_path / "elsewhere"
    cfg = tmp_path / "abs.yaml"
    io.open(cfg, "w", encoding="utf-8").write(
        yaml.safe_dump({"paths": {"output_dir": str(target)}}))
    mod = _reload_app(monkeypatch, str(cfg))
    assert mod.OUTPUT_ROOT == str(target), mod.OUTPUT_ROOT


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
