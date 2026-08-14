"""CLI-обгортка над airflow/plugins/ai_secretary_loader.py -- та сама логіка,
що й у DAG (airflow/dags/load_ai_secretary_output_dag.py), просто для
разового ручного запуску з хосту.

Використання:
    python db/scripts/load_ai_secretary_output.py <шлях_до .md з data/output> [оригінал_docx]
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "airflow", "plugins"))
import ai_secretary_loader  # noqa: E402


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Використання: python db/scripts/load_ai_secretary_output.py <output.md> [оригінал_docx]", file=sys.stderr)
        raise SystemExit(2)

    md_path = sys.argv[1]
    original = sys.argv[2] if len(sys.argv) == 3 else None
    result = ai_secretary_loader.load(md_path, original)

    if result["doc_state"] == "unchanged":
        print(f"Той самий .md уже завантажено: documents.id={result['document_id']}")
    else:
        if result["doc_state"] == "reprocessed":
            print(f"REPROCESS: попередня версія фактів позначена rejected, documents.id={result['document_id']}")
        else:
            print(f"documents.id = {result['document_id']}")
        for fact_type, fact_id in result["facts_inserted"]:
            print(f"facts.id ({fact_type}) = {fact_id}")
