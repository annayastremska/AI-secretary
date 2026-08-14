"""new_person queue type, rank dimension_values

Revision ID: 02ca65e93d52
Revises: 07936af7080d
Create Date: 2026-08-14 12:38:26.928820

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '02ca65e93d52'
down_revision: Union[str, Sequence[str], None] = '07936af7080d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # new_person -- команда AI-secretary просить: коли resolve_or_create_object
    # СТВОРЮЄ новий objects-рядок (а не знаходить наявний за псевдонімом),
    # документ не має лишатись confirmed мовчки -- шляху видалення помилково
    # створеної особи в завантажувачі немає, тож людина має підтвердити ЩО
    # новий рядок у реєстрі справді нова людина, а не пропущений псевдонім.
    op.execute("ALTER TABLE review_queue DROP CONSTRAINT review_queue_queue_type_check")
    op.execute("""
        ALTER TABLE review_queue ADD CONSTRAINT review_queue_queue_type_check
        CHECK (queue_type = ANY (ARRAY['unknown_type', 'unconfirmed_fact', 'handwritten', 'qa_sample', 'new_person']))
    """)

    # dimension_values для rank -- мітки кодів (dictionaries/military_rank.yaml
    # на боці AI-secretary, стан 13.08.2026). Без цього код 'soldier' у
    # facts.value не мав НІДЕ читабельної мітки "Солдат" -- чат-боту чи
    # дашборду нічим було б показати людині.
    op.execute("""
        INSERT INTO dimension_values (dimension_id, value, label)
        SELECT (SELECT id FROM dimensions WHERE code = 'rank'), v.code, v.label
        FROM (VALUES
            ('recruit', 'Рекрут'),
            ('soldier', 'Солдат'),
            ('senior_soldier', 'Старший солдат'),
            ('junior_sergeant', 'Молодший сержант'),
            ('sergeant', 'Сержант'),
            ('senior_sergeant', 'Старший сержант'),
            ('chief_sergeant', 'Головний сержант'),
            ('staff_sergeant', 'Штаб-сержант'),
            ('master_sergeant', 'Майстер-сержант'),
            ('senior_master_sergeant', 'Старший майстер-сержант'),
            ('chief_master_sergeant', 'Головний майстер-сержант'),
            ('sergeant_major', 'Старшина'),
            ('warrant_officer', 'Прапорщик'),
            ('senior_warrant_officer', 'Старший прапорщик'),
            ('junior_lieutenant', 'Молодший лейтенант'),
            ('lieutenant', 'Лейтенант'),
            ('senior_lieutenant', 'Старший лейтенант'),
            ('captain', 'Капітан'),
            ('major', 'Майор'),
            ('lieutenant_colonel', 'Підполковник'),
            ('colonel', 'Полковник'),
            ('brigadier_general', 'Бригадний генерал'),
            ('major_general', 'Генерал-майор'),
            ('lieutenant_general', 'Генерал-лейтенант'),
            ('general', 'Генерал')
        ) AS v(code, label)
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM dimension_values WHERE dimension_id = (SELECT id FROM dimensions WHERE code = 'rank')")
    op.execute("ALTER TABLE review_queue DROP CONSTRAINT review_queue_queue_type_check")
    op.execute("""
        ALTER TABLE review_queue ADD CONSTRAINT review_queue_queue_type_check
        CHECK (queue_type = ANY (ARRAY['unknown_type', 'unconfirmed_fact', 'handwritten', 'qa_sample']))
    """)
