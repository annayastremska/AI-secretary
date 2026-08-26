# Звірка каталогу SQL-шаблонів із незалежним підрахунком (етап 2.4 плану
# docs/tasks/2026-08-24_app-chat-plan.md).
#
# Що робить: для КОЖНОГО шаблона query_catalog.yaml
#   1) виконує його SQL проти живої бази (DSN з .env, як у chat_gradio/db.py);
#   2) рахує очікуване значення НЕЗАЛЕЖНО -- по .md-записах теки виходу
#      пайплайна (та сама тека, з якої базу вантажив лоадер), дзеркалячи
#      семантику airflow/plugins/ai_secretary_loader.py (гард суперечливих
#      дат, факт звання на документ, записи без особи -- лише в documents);
#   3) друкує таблицю «шаблон -> очікуване -> з бази -> OK/РОЗБІЖНІСТЬ»
#      і виходить із кодом 1, якщо є хоч одна розбіжність або помилка SQL.
#
# Запуск (на сервері, де жива база):
#   python demos/upload_app/verify_catalog.py --output-dir data/output-demo \
#       [--as-of 2026-08-28] [--query відпустка]
#   --expected-only  -- порахувати лише очікувані числа без бази (локальна
#                       перевірка самої логіки підрахунку).
#
# КОЛИ звіряти: одразу після завантаження бази. Жива база далі живе своїм
# життям -- людина в рев'ю підтверджує/відхиляє факти (facts.status
# змінюється), і числа законно розходяться з .md-станом. Розбіжність тоді
# вказує на зміни в базі після завантаження, а не на помилку шаблона --
# діагностичний друк нижче допомагає знайти, ЯКИЙ саме документ розійшовся.
#
# ЗВЕДЕННЯ ДУБЛІКАТІВ (24.08): db/scripts/dedupe_existing_facts.py Андрія
# зливає факти-дублікати docx/pdf/фото одного документа, лишаючи слід у
# review_log (changed_by='dedupe_existing_facts'). Очікуваний бік це
# враховує КОРЕКЦІЄЮ по review_log, і кожна корекція друкується явно
# («з .md N, злито зведенням M, скориговане N-M») -- хто читає звірку,
# бачить обидва доданки, а не підігнане число. Межі гранулярності
# (задокументовано в correct_*-функціях):
#   - review_log не пише НІ статус, НІ документ, НІ значення злитого факту:
#     old_value там -- рядок 'дубль фактів [id]' (id злитого рядка), більше
#     нічого. Тому статус судиться за ВЦІЛІЛИМ фактом (rl.fact_id, двійники
#     несуть однаковий confirmed з .md), docs-складова unconfirmed_count
#     рахується симуляцією двійників по .md і звіряється хрестом із сумою
#     review_log;
#   - бакети count_by_reason: епізод рахується один раз зі значенням
#     ВЦІЛІЛОГО факту (його value читається з бази через rl.fact_id);
#     злите значення відновлюється з .md-двійників епізоду (та сама особа +
#     вимір + період) -- якщо значення двійників розходяться (гліф
#     апострофа з OCR), бакет невцілілого значення втрачає епізод.
#
# Що НЕ звіряється числом (позначка «(виконання)» у таблиці):
#   - normative_search: незалежно відтворити український стемінг FTS по .md
#     не можна чесно (substring != ts_query), тому перевіряється лише те, що
#     SQL виконується і віддає <= LIMIT рядків;
#   - subdivision_unknown: SQL немає за задумом -- перевіряється, що шаблон
#     позначений blocked і має текст refusal.

import argparse
import datetime
import glob
import os
import sys

import yaml

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))
CATALOG_PATH = os.path.join(APP_DIR, "query_catalog.yaml")

# Виміри «людина поза частиною» -- як ABSENCE_DIMS у chat_gradio/db.py.
ABSENCE_DIMS = ["leave", "deployment_location"]

# Дзеркало TEMPLATE_TO_DOC_TYPE лоадера + назви з довідника document_types
# (міграції 1283dc745daa і 349d428a0094). Розширювати синхронно з лоадером.
TEMPLATE_TO_DOC_TYPE_NAME = {
    "deployment_certificate": "Відрядження",
    "leave_ticket": "Відпускний квиток",
}
NO_TYPE = "(без типу)"


# ── DSN: та сама логіка, що в chat_gradio/db.py (readonly-користувач) ────────
# Не імпортуємо звідти напряму: паралельно в chat_gradio йде робота іншого
# виконавця, і скрипт звірки не має падати через її проміжний стан.

def _read_env():
    vals = {}
    path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip("\"'")
    return vals


def _dsn():
    url = os.environ.get("APP_DATABASE_URL")
    if url:
        return url.replace("postgresql+psycopg://", "postgresql://")
    env = _read_env()
    return (f"host=localhost port={env.get('POSTGRES_PORT', '5433')} "
            f"dbname={env.get('APP_DB_NAME', 'milidoc')} "
            f"user={env.get('READONLY_DB_USER', 'milidoc_readonly')} "
            f"password={env.get('READONLY_DB_PASSWORD', '')} "
            f"options='-c default_transaction_read_only=on "
            f"-c statement_timeout=15000'")


# ── Незалежне читання .md-записів (дзеркало parse_frontmatter лоадера) ───────

def parse_frontmatter(md_path):
    with open(md_path, encoding="utf-8") as f:
        content = f.read()
    _, fm, _body = content.split("---", 2)
    return yaml.safe_load(fm)


def _date10(v):
    """Значення дати -> 'YYYY-MM-DD' або None (дзеркало того, що Postgres
    зробить із рядком/датою при вставці в DATE-колонку)."""
    if v is None:
        return None
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    return s[:10] if s else None


class Rec:
    """Один .md-запис очима лоадера: чи буде особа, які факти ляжуть у базу."""

    def __init__(self, meta):
        self.meta = meta
        self.domain = meta.get("domain")
        self.status = meta.get("status")
        self.template = meta.get("template")
        self.file_hash = meta.get("file_hash")
        subject = meta.get("subject") or {}
        self.subject = subject
        # Дзеркало гарда лоадера: unresolved / person_complete=False /
        # немає ні alias, ні прізвища -> запис лише в documents, без фактів.
        person_incomplete = subject.get("person_complete") is False
        no_person = not ((subject.get("person_alias") or "").strip()
                         or (subject.get("surname") or "").strip())
        self.has_person = not (self.status == "unresolved"
                               or person_incomplete or no_person)
        self.person = None
        if self.has_person:
            alias = (subject.get("person_alias") or "").strip()
            if not alias:
                alias = " ".join(x for x in [subject.get("surname"),
                                             subject.get("given_name"),
                                             subject.get("patronymic")] if x)
            self.person = alias
        self.facts = self._facts() if self.has_person else []

    def _facts(self):
        rows = []
        # Факт звання -- лоадер додає його сам (valid_from = uploaded_at).
        rank = self.subject.get("rank")
        if rank and rank.get("code"):
            prov = ((self.meta.get("field_provenance") or {}).get("rank")
                    or {})
            rows.append({
                "dim": "rank",
                "value": rank.get("code"),
                "vf": _date10(self.meta.get("uploaded_at")),
                "vt": None,
                "status": ("confirmed" if prov.get("resolved", True)
                           else "unconfirmed"),
                "additional_info": {},
            })
        for f in self.meta.get("facts") or []:
            if not f.get("fact_type"):
                continue
            vf = _date10(f.get("date_start"))
            vt = _date10(f.get("date_end"))
            status = "confirmed" if f.get("confirmed") else "unconfirmed"
            # Дзеркало гарда insert_fact: кінець раніше початку -> valid_to
            # знімається, факт стає чернеткою (не приховуємо, не вигадуємо).
            if vf and vt and vt < vf:
                vt = None
                status = "unconfirmed"
            rows.append({
                "dim": f["fact_type"],
                "value": f.get("value_code"),
                "vf": vf,
                "vt": vt,
                "status": status,
                "additional_info": f.get("additional_info") or {},
            })
        return rows

    def queue_type(self):
        """Дзеркало resolve_queue_type лоадера (без new_person -- той
        додається окремо на створення особи)."""
        m = self.meta
        if "review_queue" in m:
            return m.get("review_queue")
        if m.get("status") == "unresolved":
            return "unknown_type"
        if m.get("status") == "needs_review":
            return "unconfirmed_fact"
        if m.get("review_reason") == "random_audit":
            return "qa_sample"
        return None


def load_records(output_dir):
    """Усі записи documents/**/*.md, дедупліковані за file_hash (checksum у
    базі під UNIQUE -- повторне завантаження того самого файлу не створює
    другий рядок)."""
    pattern = os.path.join(output_dir, "documents", "**", "*.md")
    paths = sorted(glob.glob(pattern, recursive=True))
    if not paths:
        sys.exit(f"не знайшла .md-записів у {pattern} -- перевір --output-dir")
    seen, records = set(), []
    for p in paths:
        try:
            meta = parse_frontmatter(p)
        except Exception as e:  # зіпсутий запис -- сказати, не ковтати
            sys.exit(f"не читається {p}: {e}")
        key = meta.get("file_hash") or p
        if key in seen:
            continue
        seen.add(key)
        records.append(Rec(meta))
    return records


# ── Дрібні хелпери підрахунку ────────────────────────────────────────────────

def norm_apos(v):
    """Дзеркало translate(f.value, chr(39), chr(8217)) у ключах дедуплікації
    шаблонів: OCR дає прямий апостроф ' там, де docx дає фігурний ’
    (заміряний випадок DEMO-06, 24.08) -- без нормалізації КЛЮЧА той самий
    епізод рахувався двічі. Значення в бакетах count_by_reason навмисно
    НЕ нормалізуються -- там розбіжність написань має бути видною."""
    return v.replace("'", "’") if isinstance(v, str) else v


def overlaps(f, date_from, date_to):
    return (f["vf"] is not None and f["vf"] <= date_to
            and (f["vt"] is None or f["vt"] >= date_from))


def covers(f, day):
    return overlaps(f, day, day)


def iter_facts(records, dims=None, status=None):
    for r in records:
        for f in r.facts:
            if dims is not None and f["dim"] not in dims:
                continue
            if status is not None and f["status"] != status:
                continue
            yield r, f


def record_return_date(rec, f):
    """Дзеркало LATERAL у returning_on_date: фактична дата повернення з того
    самого документа (ISO-значення), інакше valid_to."""
    for f2 in rec.facts:
        if f2["dim"] in ("leave_actual_return", "deployment_actual_return"):
            v = str(f2["value"] or "")
            if (len(v) == 10 and v[4] == "-" and v[7] == "-"
                    and v.replace("-", "").isdigit()):
                return v
    return f["vt"]


def pick_person_surname(records):
    aliases = sorted({r.person for r in records if r.person})
    return aliases[0].split()[0] if aliases else None


def pick_doc_number(records):
    nums = sorted({str(f["value"]).strip()
                   for _, f in iter_facts(records, dims=["document_number"])
                   if f["value"] is not None and str(f["value"]).strip()})
    return nums[0] if nums else None


def corpus_span(records):
    vfs = [f["vf"] for _, f in iter_facts(records, dims=ABSENCE_DIMS)
           if f["vf"]]
    vts = [f["vt"] or f["vf"] for _, f in iter_facts(records,
                                                     dims=ABSENCE_DIMS)
           if f["vf"]]
    if not vfs:
        today = datetime.date.today().isoformat()
        return today, today
    return min(vfs), max(vts)


# ── Очікувані значення по шаблонах ───────────────────────────────────────────
# Кожна функція: (records, ctx) -> очікуване значення у формі, порівнянній
# з тим, що дістає екстрактор got (див. CHECKS нижче).

def exp_count_state(records, ctx, status):
    return len({r.person for r, f in iter_facts(records, ABSENCE_DIMS, status)
                if covers(f, ctx["as_of"])})


def exp_count_period(records, ctx, status):
    return len({r.person for r, f in iter_facts(records, ABSENCE_DIMS, status)
                if overlaps(f, ctx["date_from"], ctx["date_to"])})


def exp_list_state(records, ctx, status):
    return len({(r.person, f["dim"], norm_apos(f["value"]), f["vf"], f["vt"])
                for r, f in iter_facts(records, ABSENCE_DIMS, status)
                if overlaps(f, ctx["date_from"], ctx["date_to"])})


def _match_person(records, pattern_core):
    pat = pattern_core.lower()
    return [r for r in records if r.person and pat in r.person.lower()]


def exp_person_status(records, ctx):
    matched = _match_person(records, ctx["surname"])
    return len({(r.person, f["dim"], norm_apos(f["value"]), f["vf"], f["vt"],
                 f["status"]) for r in matched for f in r.facts})


def exp_doc_by_number(records, ctx):
    num = ctx["doc_number"]
    keys = set()
    for r in records:
        has_num = any(f["dim"] == "document_number"
                      and str(f["value"] or "").strip() == num
                      for f in r.facts)
        if has_num:
            keys |= {(r.person, f["dim"], norm_apos(f["value"]), f["vf"], f["vt"],
                      f["status"]) for f in r.facts}
    return len(keys)


def exp_review_queue(records, ctx):
    counts = {}
    for r in records:
        qt = r.queue_type()
        if qt:
            counts[qt] = counts.get(qt, 0) + 1
    # new_person: лоадер додає рядок на КОЖНУ вперше створену особу; на
    # свіжій базі це всі особи корпусу (кожен alias новий -- за побудовою).
    persons = {r.person for r in records if r.person}
    if persons:
        counts["new_person"] = len(persons)
    return counts


def exp_unconfirmed_count(records, ctx):
    n = sum(1 for _, f in iter_facts(records, status="unconfirmed"))
    docs = len({r.file_hash for r, f in iter_facts(records,
                                                   status="unconfirmed")})
    return {"n": n, "docs": docs}


def diag_unconfirmed(records, ctx):
    """Розклад дзеркального unconfirmed по файлах-джерелах -- щоб при
    розбіжності одразу бачити, ЯКОГО документа факти в базі змінили статус
    (порівняти з: SELECT source_doc_id, COUNT(*) FROM facts
    WHERE status='unconfirmed' GROUP BY 1 ORDER BY 2 DESC)."""
    per_doc = {}
    for r, f in iter_facts(records, status="unconfirmed"):
        key = r.meta.get("source_file") or r.file_hash
        per_doc[key] = per_doc.get(key, 0) + 1
    return [f"{src}: {n}" for src, n in
            sorted(per_doc.items(), key=lambda kv: (-kv[1], kv[0]))]


# ── Дані ПОЗА пайплайном (штатка) ────────────────────────────────────────────
#
# 25.08 Андрій залив штатку: у базі з'явився документ «Штатна книжка»
# (domain='staffing'), 300 осіб зі service_id і ~714 фактів про них. Наш вихід
# (.md-файли) цього не містить і не може містити -- це інше джерело.
#
# Через це прилад показав 5 «розбіжностей», яких насправді немає: він
# порівнював «база» з «наш вихід» і будував на припущенні, що це те саме.
# Припущення перестало бути правдою.
#
# Тому очікуване коригується ЯВНО і з бази, а не константою в коді: скільком
# документів/осіб/фактів прийшло не від нас. Якщо штатку переллють іншою --
# корекція поїде за нею.
OUTSIDE_SQL = """
SELECT
  (SELECT count(*) FROM documents WHERE domain = 'staffing')      AS docs,
  (SELECT count(DISTINCT f.object_id) FROM facts f
     JOIN documents d ON d.id = f.source_doc_id
    WHERE d.domain = 'staffing')                                  AS people,
  (SELECT count(*) FROM facts f
     JOIN documents d ON d.id = f.source_doc_id
    WHERE d.domain = 'staffing')                                  AS facts
"""


def outside_pipeline():
    """-> {'docs': n, 'people': n, 'facts': n} або нулі, якщо не прочиталось."""
    try:
        row = run_sql(OUTSIDE_SQL, {})[0]
        return {k: int(row[k] or 0) for k in ("docs", "people", "facts")}
    except Exception:
        return {"docs": 0, "people": 0, "facts": 0}


def exp_documents_count(records, ctx):
    counts = {}
    for r in records:
        key = r.domain or "(без домену)"
        counts[key] = counts.get(key, 0) + 1
    # штатка -- не наш вихід, але вона в базі є (див. OUTSIDE_SQL)
    if ctx.get("outside", {}).get("docs"):
        counts["staffing"] = ctx["outside"]["docs"]
    return counts


def exp_count_by_doc_type(records, ctx):
    counts = {}
    for r in records:
        key = TEMPLATE_TO_DOC_TYPE_NAME.get(r.template, NO_TYPE)
        counts[key] = counts.get(key, 0) + 1
    if ctx.get("outside", {}).get("docs"):
        counts["Штатна книжка"] = ctx["outside"]["docs"]
    return counts


def exp_count_by_reason(records, ctx, status):
    episodes = {}
    for r, f in iter_facts(records, ABSENCE_DIMS, status):
        if overlaps(f, ctx["date_from"], ctx["date_to"]):
            episodes.setdefault(f["value"], set()).add(
                (r.person, f["vf"], f["vt"]))
    return {("(без значення)" if k is None else k): len(v)
            for k, v in episodes.items()}


def exp_returning(records, ctx, status):
    return len({(r.person, f["dim"], norm_apos(f["value"]), f["vf"], f["vt"])
                for r, f in iter_facts(records, ABSENCE_DIMS, status)
                if record_return_date(r, f) == ctx["as_of"]})


def exp_absent_breakdown(records, ctx, status):
    counts = {}
    total = set()
    for r, f in iter_facts(records, ABSENCE_DIMS, status):
        if covers(f, ctx["as_of"]):
            counts.setdefault(f["dim"], set()).add(r.person)
            total.add(r.person)
    out = {k: len(v) for k, v in counts.items()}
    if total:
        out["(разом)"] = len(total)
    return out


def exp_co_travelers(records, ctx):
    keys = set()
    for r, f in iter_facts(records, ["leave"], "confirmed"):
        co = f["additional_info"].get("co_travelers")
        co = str(co).strip() if co is not None else ""
        if co and co not in ("—", "-") and overlaps(f, ctx["date_from"],
                                                    ctx["date_to"]):
            keys.add((r.person, norm_apos(f["value"]), f["vf"], f["vt"]))
    return len(keys)


def exp_travel_document(records, ctx):
    keys = set()
    for r, f in iter_facts(records, ["travel_document"], "confirmed"):
        primary = next((f2 for f2 in r.facts
                        if f2["dim"] in ("leave", "deployment_location")),
                       None)
        if (primary is None or primary["vf"] is None
                or overlaps(primary, ctx["date_from"], ctx["date_to"])):
            keys.add((r.person, f["value"]))
    return len(keys)


def exp_drafts(records, ctx):
    return len({(r.person, f["dim"], norm_apos(f["value"]), f["vf"], f["vt"])
                for r, f in iter_facts(records, ABSENCE_DIMS, "unconfirmed")})


def exp_date_conflicts(records, ctx):
    n = 0
    for r in records:
        err = r.meta.get("date_range_error")
        if err and str(err).strip():
            n += 1
    return n


def exp_provenance(records, ctx):
    matched = _match_person(records, ctx["surname"])
    return sum(len(r.facts) for r in matched)


def exp_normative_list(records, ctx):
    return sum(1 for r in records if r.domain == "normative")


def exp_failed(records, ctx):
    failed = sum(1 for r in records if r.status == "unresolved")
    unknown = sum(1 for r in records if r.queue_type() == "unknown_type")
    return {"failed": failed, "unknown_type_docs": unknown,
            "total": len(records) + ctx.get("outside", {}).get("docs", 0)}


# ── Корекція очікуваного на зведення дублікатів (review_log) ────────────────

DEDUPE_SQL = """
SELECT f.status     AS survivor_status,
       f.value      AS survivor_value,
       d.code       AS dim,
       o.canonical_name AS survivor_person,
       f.valid_from, f.valid_to
FROM review_log rl
JOIN facts f ON f.id = rl.fact_id
JOIN dimensions d ON d.id = f.dimension_id
JOIN objects o ON o.id = f.object_id
WHERE rl.changed_by = %(who)s
"""


def fetch_dedupe_log():
    """Слід зведення дублікатів. rl.fact_id вказує на ВЦІЛІЛИЙ факт -- його
    статус/значення/особа/дати читаються з бази. Значення ЗЛИТОГО факту слід
    НЕ зберігає: rl.old_value -- лише рядок 'дубль фактів [id]' (id злитого
    рядка), тому його тут і не вибираємо -- злите значення відновлюється з
    .md-двійників (див. _correct_reason_buckets). Порожній список = зведення
    не запускалось, корекції стають no-op."""
    return run_sql(DEDUPE_SQL, {"who": "dedupe_existing_facts"})


def md_twin_unconfirmed_merge(records):
    """Симуляція зведення для docs-складової unconfirmed_count: двійники
    (та сама особа + домен + номер документа), чиї набори незатверджених
    фактів змістовно ідентичні (з нормалізацією гліфа апострофа) -- після
    зведення чернетки лишаються лише в одному записі пари.
    Повертає (документів злито, фактів злито) -- фактова сума звіряється
    хрестом із review_log."""
    groups = {}
    for r in records:
        if not r.has_person:
            continue
        unc = frozenset((f["dim"], norm_apos(f["value"]), f["vf"], f["vt"])
                        for f in r.facts if f["status"] == "unconfirmed")
        if not unc:
            continue
        num = next((str(f["value"]).strip() for f in r.facts
                    if f["dim"] == "document_number"
                    and f["value"] is not None), None)
        groups.setdefault((r.person, r.domain, num, unc), []).append(r)
    docs_merged = facts_merged = 0
    for key, rs in groups.items():
        if len(rs) > 1:
            docs_merged += len(rs) - 1
            facts_merged += (len(rs) - 1) * len(key[3])
    return docs_merged, facts_merged


def correct_unconfirmed_count(expected, dedupe, records, ctx):
    """n коригується сумою review_log (статус -- за вцілілим фактом, межа
    задокументована в шапці); docs -- симуляцією двійників по .md, бо
    review_log не пише, з якого документа злитий факт."""
    merged_unc = sum(1 for row in dedupe
                     if row["survivor_status"] == "unconfirmed")
    docs_merged, facts_merged = md_twin_unconfirmed_merge(records)
    corrected = {"n": expected["n"] - merged_unc,
                 "docs": expected["docs"] - docs_merged}
    cross = ("збігається" if facts_merged == merged_unc
             else "НЕ збігається -- межа гранулярності, дивитись руками")
    lines = [
        f"з .md: n={expected['n']}, docs={expected['docs']}",
        f"злито зведенням (review_log): {merged_unc} unconfirmed "
        f"із {len(dedupe)} усього",
        f"двійники в .md з тим самим набором чернеток: {docs_merged} док. "
        f"({facts_merged} фактів) -- {cross} з review_log",
        f"скориговане: n={corrected['n']}, docs={corrected['docs']}",
    ]
    return corrected, lines


def _correct_reason_buckets(expected, dedupe, records, ctx, status):
    """Правило зведення для бакетів: епізод рахується ОДИН раз зі значенням
    ВЦІЛІЛОГО факту (rl.fact_id -> facts.value, читається з бази). Значення
    ЗЛИТОГО факту в review_log НЕМАЄ (old_value = 'дубль фактів [id]'), тому
    воно відновлюється з .md-двійників епізоду: та сама особа + вимір +
    період. Якщо значення двійників РОЗХОДЯТЬСЯ (заміряний випадок DEMO-06:
    docx фігурний ’ проти png прямого ') -- бакет невцілілого значення
    втрачає цей епізод, корекція друкується явно. Однакові значення нічого
    не міняють: епізодний ключ дзеркала (особа, vf, vt) їх уже склеїв."""
    # Значення епізодів у .md: (особа, вимір, vf, vt) -> множина значень
    episode_values = {}
    for r, f in iter_facts(records, ABSENCE_DIMS, status):
        episode_values.setdefault(
            (r.person, f["dim"], f["vf"], f["vt"]), set()).add(f["value"])
    corrected = dict(expected)
    lines = []
    seen = set()
    for row in dedupe:
        if row["dim"] not in ABSENCE_DIMS:
            continue
        if row["survivor_status"] != status:
            continue
        fk = {"vf": _date10(row["valid_from"]), "vt": _date10(row["valid_to"])}
        if not overlaps(fk, ctx["date_from"], ctx["date_to"]):
            continue
        ep = (row["survivor_person"], row["dim"], fk["vf"], fk["vt"])
        if ep in seen:
            # Кілька злитих файлів одного епізоду (docx+pdf+фото) -- усі
            # невцілілі значення епізоду знімаються один раз.
            continue
        seen.add(ep)
        survivor = row["survivor_value"]
        for y in sorted(episode_values.get(ep, set()) - {survivor}, key=str):
            key = "(без значення)" if y is None else y
            if key in corrected:
                was = corrected[key]
                corrected[key] = was - 1
                note = (f"двійники розходяться значенням: «{survivor}» "
                        f"(вижив, з бази) проти «{key}» (злито) -- "
                        f"бакет «{key}»: {was} -> {was - 1}")
                if corrected[key] <= 0:
                    del corrected[key]
                    note += " (знято)"
                lines.append(note)
    return corrected, lines


def correct_count_by_reason(expected, dedupe, records, ctx):
    return _correct_reason_buckets(expected, dedupe, records, ctx, "confirmed")


def correct_count_by_reason_unc(expected, dedupe, records, ctx):
    return _correct_reason_buckets(expected, dedupe, records, ctx,
                                   "unconfirmed")


# ── Екстрактори «got» із рядків SQL ──────────────────────────────────────────

def got_scalar_n(rows):
    return rows[0]["n"] if rows else 0


def got_rowcount(rows):
    return len(rows)


def got_dict(key, val):
    def _f(rows):
        return {("(разом)" if r[key] is None else r[key]): r[val]
                for r in rows}
    return _f


def got_first_row(rows):
    return dict(rows[0]) if rows else {}


# ── Реєстр перевірок: 100% шаблонів каталогу ────────────────────────────────
# kind:
#   compare -- порівняти expected із got (є expected/got; unc_* -- те саме
#              для sql_unconfirmed, якщо він у шаблона є);
#   execute -- лише виконати SQL (числом не звіряється, причина в шапці);
#   blocked -- SQL немає за задумом: перевірити blocked + refusal.

def build_checks(ctx):
    span = {"date_from": ctx["date_from"], "date_to": ctx["date_to"]}
    dims = {"dims": ABSENCE_DIMS}
    return {
        "count_by_state_on_date": dict(
            kind="compare", params={**dims, "on_date": ctx["as_of"]},
            expected=lambda rs: exp_count_state(rs, ctx, "confirmed"),
            expected_unc=lambda rs: exp_count_state(rs, ctx, "unconfirmed"),
            got=got_scalar_n),
        "count_by_state_period": dict(
            kind="compare", params={**dims, **span},
            expected=lambda rs: exp_count_period(rs, ctx, "confirmed"),
            expected_unc=lambda rs: exp_count_period(rs, ctx, "unconfirmed"),
            got=got_scalar_n),
        "list_by_state": dict(
            kind="compare", params={**dims, **span},
            expected=lambda rs: exp_list_state(rs, ctx, "confirmed"),
            expected_unc=lambda rs: exp_list_state(rs, ctx, "unconfirmed"),
            got=got_rowcount),
        "person_status": dict(
            kind="compare",
            params={"name_pattern": f"%{ctx['surname']}%"},
            expected=lambda rs: exp_person_status(rs, ctx),
            got=got_rowcount),
        "doc_by_number": dict(
            kind="compare", params={"doc_number": ctx["doc_number"]},
            expected=lambda rs: exp_doc_by_number(rs, ctx),
            got=got_rowcount),
        "review_queue_count": dict(
            kind="compare", params={},
            expected=lambda rs: exp_review_queue(rs, ctx),
            got=got_dict("queue_type", "n")),
        "unconfirmed_count": dict(
            kind="compare", params={},
            expected=lambda rs: exp_unconfirmed_count(rs, ctx),
            got=got_first_row,
            correct=lambda e, dd, rs: correct_unconfirmed_count(e, dd, rs, ctx),
            diag=lambda rs: diag_unconfirmed(rs, ctx)),
        "documents_count": dict(
            kind="compare", params={},
            expected=lambda rs: exp_documents_count(rs, ctx),
            got=got_dict("domain", "n")),
        "count_by_doc_type": dict(
            kind="compare", params={},
            expected=lambda rs: exp_count_by_doc_type(rs, ctx),
            got=got_dict("doc_type", "n")),
        "count_by_reason": dict(
            kind="compare", params={**dims, **span},
            expected=lambda rs: exp_count_by_reason(rs, ctx, "confirmed"),
            expected_unc=lambda rs: exp_count_by_reason(rs, ctx,
                                                        "unconfirmed"),
            correct=lambda e, dd, rs: correct_count_by_reason(e, dd, rs, ctx),
            correct_unc=lambda e, dd, rs: correct_count_by_reason_unc(
                e, dd, rs, ctx),
            got=got_dict("reason", "n")),
        "returning_on_date": dict(
            kind="compare", params={**dims, "on_date": ctx["as_of"]},
            expected=lambda rs: exp_returning(rs, ctx, "confirmed"),
            expected_unc=lambda rs: exp_returning(rs, ctx, "unconfirmed"),
            got=got_rowcount),
        "absent_breakdown_on_date": dict(
            kind="compare", params={**dims, "on_date": ctx["as_of"]},
            expected=lambda rs: exp_absent_breakdown(rs, ctx, "confirmed"),
            expected_unc=lambda rs: exp_absent_breakdown(rs, ctx,
                                                         "unconfirmed"),
            got=got_dict("dim", "n")),
        "with_co_travelers": dict(
            kind="compare", params=span,
            expected=lambda rs: exp_co_travelers(rs, ctx),
            got=got_rowcount),
        "with_travel_document": dict(
            kind="compare", params=span,
            expected=lambda rs: exp_travel_document(rs, ctx),
            got=got_rowcount),
        "drafts_list": dict(
            kind="compare", params=dims,
            expected=lambda rs: exp_drafts(rs, ctx),
            got=got_rowcount),
        "date_conflict_docs": dict(
            kind="compare", params={},
            expected=lambda rs: exp_date_conflicts(rs, ctx),
            got=got_rowcount),
        "fact_provenance": dict(
            kind="compare",
            params={"name_pattern": f"%{ctx['surname']}%"},
            expected=lambda rs: exp_provenance(rs, ctx),
            got=got_rowcount),
        "normative_list": dict(
            kind="compare", params={},
            expected=lambda rs: exp_normative_list(rs, ctx),
            got=got_rowcount),
        "normative_search": dict(
            kind="execute", params={"query": ctx["query"]}),
        "failed_docs_count": dict(
            kind="compare", params={},
            expected=lambda rs: exp_failed(rs, ctx),
            got=got_first_row),
        "subdivision_unknown": dict(kind="blocked", params={}),
        "absent_without_docs_impossible": dict(kind="blocked",
                                              params={}),
        # Склад за штаткою: цифру звірити з .md неможливо -- це дані
        # штатки, яких пайплайн не виробляє. Перевіряється виконанням.
        "roster_total": dict(kind="execute", params={}),
        # Штатка в базі з 25.08 -> питання про підрозділи стали
        # відповідними. Обидва нові шаблони перевіряються ВИКОНАННЯМ
        # (kind="execute"): точну цифру по роті звірити нічим -- .md-файли
        # пайплайна підрозділів не містять, це дані штатки.
        "count_by_state_in_subdivision": dict(
            kind="execute",
            params={"dims": ["leave"], "on_date": "2026-08-28",
                    "subdivision": "%2%рота%"}),
        "subdivision_breakdown": dict(
            kind="execute",
            params={"dims": ["leave", "deployment_location"],
                    "on_date": "2026-08-28"}),
        # розмовний маршрут (етап 3.5): SQL немає за задумом, як у
        # subdivision_unknown -- перевіряється blocked + refusal
        "smalltalk": dict(kind="blocked", params={}),
    }


# ── Головний прохід ──────────────────────────────────────────────────────────

def fmt(v):
    if isinstance(v, dict):
        return "; ".join(f"{k}={v[k]}" for k in sorted(v, key=str))
    return str(v)


def run_sql(sql, params):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(_dsn(), row_factory=dict_row,
                         autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def main():
    ap = argparse.ArgumentParser(
        description="Звірка query_catalog.yaml із незалежним підрахунком по "
                    ".md-записах")
    ap.add_argument("--output-dir", required=True,
                    help="тека виходу пайплайна (в ній documents/**/*.md), "
                         "та сама, з якої вантажилась база")
    ap.add_argument("--as-of", default=datetime.date.today().isoformat(),
                    help="дата зрізу YYYY-MM-DD (дефолт: сьогодні)")
    ap.add_argument("--query", default="відпустка",
                    help="тема для normative_search")
    ap.add_argument("--expected-only", action="store_true",
                    help="лише незалежний підрахунок, без бази")
    args = ap.parse_args()

    with open(CATALOG_PATH, encoding="utf-8") as f:
        templates = yaml.safe_load(f)["templates"]

    records = load_records(args.output_dir)
    date_from, date_to = corpus_span(records)
    ctx = {
        "as_of": args.as_of,
        "date_from": date_from,
        "date_to": date_to,
        "surname": pick_person_surname(records) or "",
        "doc_number": pick_doc_number(records) or "",
        "query": args.query,
    }
    # Дані поза пайплайном (штатка Андрія) -- читаємо з бази ДО побудови
    # перевірок: без цієї корекції прилад показував 5 «розбіжностей», яких
    # немає, бо порівнював базу з нашим виходом як із тим самим джерелом.
    ctx["outside"] = ({"docs": 0, "people": 0, "facts": 0}
                      if args.expected_only else outside_pipeline())
    if ctx["outside"]["docs"]:
        print(f"поза пайплайном (штатка): документів {ctx['outside']['docs']}, "
              f"осіб {ctx['outside']['people']}, фактів "
              f"{ctx['outside']['facts']} -- очікувані числа скориговані")
    checks = build_checks(ctx)

    print(f"записів у {args.output_dir}: {len(records)} "
          f"(з особою: {sum(1 for r in records if r.has_person)})")
    print(f"зріз: {ctx['as_of']}; період: {date_from} — {date_to}; "
          f"особа: «{ctx['surname']}»; документ №{ctx['doc_number']}; "
          f"тема: «{ctx['query']}»")
    print()

    dedupe_rows = []
    if not args.expected_only:
        try:
            dedupe_rows = fetch_dedupe_log()
        except Exception as e:
            print(f"review_log недоступний ({type(e).__name__}: {e}) -- "
                  f"корекція на зведення дублікатів НЕ застосована")
    if dedupe_rows:
        print(f"зведення дублікатів у базі (review_log, "
              f"changed_by='dedupe_existing_facts'): злито "
              f"{len(dedupe_rows)} фактів -- очікувані числа коригуються, "
              f"кожна корекція друкується під своїм рядком")
        print()

    header = f"{'шаблон':38} {'очікуване':>28} {'з бази':>28} статус"
    print(header)
    print("-" * len(header))

    failures = 0
    covered = set()
    for t in templates:
        tid = t["id"]
        check = checks.get(tid)
        if check is None:
            # Новий шаблон без перевірки -- це теж розбіжність: скрипт
            # мусить покривати 100% каталогу.
            print(f"{tid:38} {'—':>28} {'—':>28} НЕМАЄ ПЕРЕВІРКИ")
            failures += 1
            continue
        covered.add(tid)

        if check["kind"] == "blocked":
            ok = bool(t.get("blocked")) and bool(t.get("refusal")) \
                and "sql" not in t
            print(f"{tid:38} {'blocked+refusal':>28} "
                  f"{('так' if ok else 'ні'):>28} "
                  f"{'OK' if ok else 'РОЗБІЖНІСТЬ'}")
            failures += 0 if ok else 1
            continue

        variants = [("", "sql", check.get("expected"),
                     check.get("correct"))]
        if t.get("sql_unconfirmed") and check.get("expected_unc"):
            variants.append((" (чернетки)", "sql_unconfirmed",
                             check["expected_unc"],
                             check.get("correct_unc")))

        for suffix, sql_key, exp_fn, correct_fn in variants:
            label = tid + suffix
            corr_lines = []
            if check["kind"] == "execute":
                expected = "(виконання)"
            else:
                expected = exp_fn(records)
                if dedupe_rows and correct_fn:
                    expected, corr_lines = correct_fn(expected, dedupe_rows,
                                                      records)
            if args.expected_only:
                print(f"{label:38} {fmt(expected):>28} {'—':>28} (без бази)")
                continue
            try:
                rows = run_sql(t[sql_key], check["params"])
            except Exception as e:
                print(f"{label:38} {fmt(expected):>28} "
                      f"{'ПОМИЛКА SQL':>28} {type(e).__name__}: {e}")
                failures += 1
                continue
            if check["kind"] == "execute":
                got = f"{len(rows)} рядків"
                ok = len(rows) <= 3  # LIMIT шаблона; сам факт виконання
            else:
                got = check["got"](rows)
                ok = got == expected
            print(f"{label:38} {fmt(expected):>28} {fmt(got):>28} "
                  f"{'OK' if ok else 'РОЗБІЖНІСТЬ'}")
            for line in corr_lines:
                print(f"    корекція: {line}")
            if not ok and check.get("diag"):
                print("    діагностика (очікуване по файлах-джерелах):")
                for line in check["diag"](records):
                    print(f"      {line}")
            failures += 0 if ok else 1

    missing = covered.symmetric_difference({t["id"] for t in templates})
    if missing:
        print(f"\nшаблони без перевірки: {sorted(missing)}")

    print()
    if args.expected_only:
        print("режим --expected-only: базу не питали, розбіжності не "
              "рахувались.")
        return 0
    if failures:
        print(f"РОЗБІЖНОСТЕЙ/ПОМИЛОК: {failures}")
        return 1
    print("усі шаблони зійшлись із незалежним підрахунком.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
