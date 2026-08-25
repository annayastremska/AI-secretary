# -*- coding: utf-8 -*-
"""Рейки яруса, де SQL пише модель. Тестів на них НЕ БУЛО.

У критеріях приймання (П7) стояло «(є тести)», а `grep validate_sql` по тестах
не знаходив нічого — це виявив фінальний адверсарний прохід 25.08. Тобто
документ заявляв перевірку, якої не існує. Найгірший вид неправди в нашому
проєкті: вона стосується саме того місця, де ми дозволяємо моделі писати запит
до бази.

Він же знайшов і дірку в самій рейці: фільтр підтвердженості був перевіркою на
ПІДРЯДОК `"confirmed" in sql`, тому проходили `AS confirmed`,
`status = 'unconfirmed'`, `status <> 'confirmed'` і навіть
`value LIKE '%confirmed%'`. Рейка виглядала як рейка й тримала лише випадкові
запити.

Запуск:
    python -m pytest demos/upload_app/tests/test_sql_validator.py -q
"""
import pytest

from demos.upload_app.chat_gradio import app as chat_app

tiers = chat_app.tier_chat
validate = tiers.validate_sql


def _ok(sql):
    out, why = validate(sql)
    assert out is not None, f"відхилено, а мусило пройти: {why}"
    return out


def _rejected(sql):
    out, why = validate(sql)
    assert out is None, f"ПРОПУЩЕНО, а мусило бути відхилено: {sql}"
    return why


# ── Те, що мусить бути відхилено ──────────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "DELETE FROM facts",
    "UPDATE facts SET status = 'confirmed'",
    "INSERT INTO facts (value) VALUES ('x')",
    "DROP TABLE facts",
    "TRUNCATE facts",
    "ALTER TABLE facts ADD COLUMN x int",
    "GRANT ALL ON facts TO public",
])
def test_dml_and_ddl_rejected(sql):
    _rejected(sql)


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE facts",
    "SELECT 1;SELECT 2",
])
def test_several_statements_rejected(sql):
    _rejected(sql)


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM facts WHERE status='confirmed' -- коментар",
    "SELECT /* прихований */ count(*) FROM facts WHERE status='confirmed'",
])
def test_comments_rejected(sql):
    """Коментар у SQL -- звичайний спосіб схову другої половини запиту."""
    _rejected(sql)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM pg_shadow",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM pg_catalog.pg_user",
])
def test_tables_outside_allowlist_rejected(sql):
    _rejected(sql)


@pytest.mark.parametrize("sql", [
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT current_setting('data_directory')",
])
def test_service_functions_rejected(sql):
    _rejected(sql)


# ── Рейка підтвердженості: саме те, що було пробите ──────────────────────────

@pytest.mark.parametrize("sql", [
    # псевдонім колонки, який раніше «зараховувався» як фільтр
    "SELECT count(*) AS confirmed FROM facts",
    # фільтр РІВНО НАВПАКИ
    "SELECT count(*) FROM facts WHERE status = 'unconfirmed'",
    "SELECT count(*) FROM facts WHERE status <> 'confirmed'",
    "SELECT count(*) FROM facts WHERE status != 'confirmed'",
    # слово є, але зовсім не про статус
    "SELECT count(*) FROM facts WHERE value LIKE '%confirmed%'",
    # фільтра немає взагалі
    "SELECT count(*) FROM facts",
])
def test_facts_without_real_confirmed_filter_rejected(sql):
    why = _rejected(sql)
    assert "підтвердженості" in why, why


@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM facts WHERE status = 'confirmed'",
    "SELECT count(*) FROM facts f WHERE f.status='confirmed'",
    "SELECT count(*) FROM facts WHERE status IN ('confirmed')",
])
def test_facts_with_real_filter_pass(sql):
    """Запобіжник проти перегину: правильний запит мусить проходити, інакше
    ярус став би непрацездатним."""
    _ok(sql)


def test_query_without_facts_needs_no_filter():
    _ok("SELECT count(*) FROM documents")


# ── LIMIT: зовнішній, а не будь-який ─────────────────────────────────────────

def test_limit_is_added_when_missing():
    out = _ok("SELECT count(*) FROM documents")
    assert "LIMIT 200" in out


def test_limit_in_subquery_does_not_count_as_ours():
    """Було пробито: ліміт у ПІДЗАПИТІ вважався своїм, і зовнішній запит
    лишався без обмеження -- тобто модель могла витягнути всю таблицю."""
    out = _ok("SELECT * FROM (SELECT id FROM documents LIMIT 5) t")
    assert out.rstrip().upper().endswith("LIMIT 200"), out


def test_too_big_limit_is_capped():
    out = _ok("SELECT id FROM documents LIMIT 100000")
    assert "LIMIT 200" in out and "100000" not in out


def test_own_small_limit_is_kept():
    out = _ok("SELECT id FROM documents LIMIT 10")
    assert "LIMIT 10" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
