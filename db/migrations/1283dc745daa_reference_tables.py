"""reference_tables

Revision ID: 1283dc745daa
Revises: 9e5e83de546b
Create Date: 2026-08-04 10:07:46.619296

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1283dc745daa'
down_revision: Union[str, Sequence[str], None] = '9e5e83de546b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE document_types (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            title_template TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE dimensions (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            value_type TEXT NOT NULL DEFAULT 'text',  -- text | number | date | enum
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE dimension_values (
            id SERIAL PRIMARY KEY,
            dimension_id INTEGER NOT NULL REFERENCES dimensions(id),
            value TEXT NOT NULL,
            label TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (dimension_id, value)
        )
    """)

    op.execute("""
        CREATE TABLE object_kinds (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE sources (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT
        )
    """)

    # object_kinds — фіксований набір, відомий зараз
    op.execute("""
        INSERT INTO object_kinds (code, name) VALUES
            ('person', 'Особа'),
            ('equipment', 'Техніка/майно'),
            ('task', 'Завдання')
    """)

    # dimensions — звання й посада підтверджені як історія з датами й наказами
    # у офіційній формі "Штатної книжки" (колонки "Наказ на присвоєння
    # останнього звання", "Індекси посад, які обіймав(ла)") — тому це виміри
    # facts, а не статичні колонки people. Решта вимірів (відпустка, ремонт
    # техніки тощо) — досі відкрите питання, не засіюю навмання.
    op.execute("""
        INSERT INTO dimensions (code, name, value_type) VALUES
            ('rank', 'Звання', 'text'),
            ('position', 'Посада', 'text')
    """)

    # sources — базові джерела документів (розділ 4 контексту проєкту)
    op.execute("""
        INSERT INTO sources (code, name, description) VALUES
            ('askod_synthetic', 'Синтетика АСКОД', 'Згенерований текст, що симулює електронний експорт з АСКОД'),
            ('mobile_photo', 'Фото з мобільного', 'Реальне фото документа, проходить через OCR'),
            ('unit_export', 'Експорт підрозділу', 'Пакетний експорт документів від підрозділу')
    """)

    # document_types — 5 типів, підтверджених ТЗ_AI-секретар.md (розділ 2.6)
    op.execute("""
        INSERT INTO document_types (code, name, title_template) VALUES
            ('staff_roster', 'Штатна книжка', 'Штатна книжка підрозділу станом на {date}'),
            ('vacation_report', 'Рапорт на відпустку', NULL),
            ('equipment_record', 'Облік техніки', NULL),
            ('business_trip_order', 'Відрядження', NULL),
            ('monetary_allowance', 'Грошове забезпечення (бойові виплати)', NULL)
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE sources")
    op.execute("DROP TABLE object_kinds")
    op.execute("DROP TABLE dimension_values")
    op.execute("DROP TABLE dimensions")
    op.execute("DROP TABLE document_types")
