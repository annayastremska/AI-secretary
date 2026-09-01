"""unit kind, leave_ticket type, dimensions validity_model

Revision ID: 349d428a0094
Revises: d765af54fbe6
Create Date: 2026-08-11 17:47:49.511221

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '349d428a0094'
down_revision: Union[str, Sequence[str], None] = 'd765af54fbe6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # unit -- узгоджено з командою AI-secretary: "частина, до якої прибути"
    # (destination_org у їхніх схемах) лишається голим текстом у facts.value
    # без цього виду об'єкта. Без нього неможливо колись порахувати "скільки
    # людей приписано до частини Х".
    op.execute("""
        INSERT INTO object_kinds (code, name) VALUES ('unit', 'Військова частина')
    """)

    # leave_ticket -- "Відпускний квиток" (Додаток 30 до Інструкції з
    # діловодства ЗСУ, виданий документ ПІСЛЯ затвердження) — це не те саме,
    # що "Рапорт на відпустку" (vacation_report, заявка ДО затвердження).
    # Дотепер схема Ані leave_ticket мапилась на vacation_report помилково.
    op.execute("""
        INSERT INTO document_types (code, name, title_template)
        VALUES ('leave_ticket', 'Відпускний квиток', NULL)
    """)

    # validity_model -- дзеркалить dictionaries/fact_type_registry.yaml на
    # боці AI-secretary (ranged / current_state / permanent_event). Без цього
    # рішення "закривати valid_to при новому факті" було б захардкоджене в
    # Python за назвою виміру, а не даними -- нове current_state-вимір
    # (напр. equipment_status) тихо ламав би цю логіку, доки хтось не
    # згадає поправити код.
    op.execute("""
        ALTER TABLE dimensions
        ADD COLUMN validity_model TEXT NOT NULL DEFAULT 'ranged'
        CHECK (validity_model IN ('ranged', 'current_state', 'permanent_event'))
    """)
    op.execute("""
        UPDATE dimensions SET validity_model = 'current_state' WHERE code IN ('rank', 'position')
    """)
    op.execute("""
        UPDATE dimensions SET validity_model = 'ranged' WHERE code = 'deployment_location'
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE dimensions DROP COLUMN validity_model")
    op.execute("DELETE FROM document_types WHERE code = 'leave_ticket'")
    op.execute("DELETE FROM object_kinds WHERE code = 'unit'")
