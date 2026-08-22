"""Визначення ШАБЛОНУ документа (не лише домену) -- вибір схеми стає
частиною пайплайна, а не ручною дією людини.

Чому не домен: схема прив'язана до template (`deployment_certificate`), а
один домен містить багато різних бланків. Перевірка "домен схеми == домен
класифікації" не відрізнить "правильний домен, неправильний бланк", і в
пакетному режимі (папка-приймач з docs/spec/project-expectations.md) немає
людини, яка вручну підсуне потрібний YAML на кожен файл.

Кожна схема сама описує, як її впізнати (блок `identification`), тому
"новий шаблон = новий YAML" стає правдою end-to-end: не потрібно ні правити
код, ні синхронізувати окремий довідник ключових фраз.
"""
import glob
import os
import re

import yaml

from pipeline.build_record import CONSISTENCY_RULES, DERIVE_FUNCS
from pipeline.classification.classify import classify_domain_rules, phrase_in_text
from pipeline.extraction.blank_form import (
    BLANK_TEMPLATE_KEY,
    blank_line_coverage,
    blank_template_path,
    blank_template_text,
    printed_cutters,
)
from pipeline.extraction.extract import (
    EMPTY_PATTERN_KEY,
    EXTRACTION_LIMITS_KEY,
    KNOWN_EXTRACTION_LIMITS,
    NAME_PART_ROLES,
    field_part,
    name_group_key,
)
from pipeline.normalization.normalize import (
    PLACEHOLDER_TOKENS_EXCEPT_KEY, PLACEHOLDER_TOKENS_KEY)
from pipeline.subject_kind import DECLARABLE_SUBJECT_KINDS, UNKNOWN_SUBJECT

TITLE_WEIGHT = 5     # заголовок бланка -- найсильніший сигнал
ANCHOR_WEIGHT = 2    # характерні лейбли/номер додатка -- підтверджувальні
DEFAULT_MIN_SCORE = 5

# Нижня межа, ПІД якою LLM навіть не питають, який це бланк. Раніше межі не
# було взагалі: документ з балом 0 (книга обліку техніки, рапорт на відпустку
# -- будь-що, для чого схеми немає) віддавався моделі, а та була обмежена
# grammar до переліку наявних шаблонів + "unknown", тобто мала вибирати серед
# завідомо чужих варіантів. Обраний так шаблон приймався як остаточний, з тим
# самим статусом, що й підтверджений анкорами. Наслідок: чужа схема
# витягувала поля через LLM-фолбек, критичні прогалини закривались, і
# документ ставав confirmed з фактом, якого в ньому немає.
# 2 = рівно один збіглий анкор: хоч один незалежний сигнал, що це той бланк.
DEFAULT_LLM_FLOOR = ANCHOR_WEIGHT

# --- РЕДАКЦІЯ бланка: та сама схожість, лише дрібнішою міркою --------------
#
# ЧОМУ ЦЕ ЖИВЕ ТУТ, А НЕ ОКРЕМИМ РОЗПІЗНАВАЧЕМ. `score_schema` вище вже
# відповідає на питання «це наша форма?»: заголовок ×5, анкори ×2, поріг
# `min_score`, обов'язковий щонайменше один анкор. Кількість знайдених
# ДРУКОВАНИХ РЯДКІВ бланка -- те саме питання з кроком у один рядок замість
# кроку у один анкор. Тримати це другим механізмом означало б мати два
# твердження про одне й те саме: анкори кажуть «бланк той», рядки кажуть
# «редакція інша», і при розходженні ніхто не знав би, яке з них право. Тому
# `identify_template` рахує обидві міри й віддає ОДИН вердикт
# (`form_recognized`), а екстракція його лише читає -- сама вона нічого про
# схожість не рахує.
#
# НАВІЩО ДРІБНІША МІРА. Анкорів у схемі п'ять-шість, і вони підібрані як
# найхарактерніші; інша РЕДАКЦІЯ форми (ті самі поля, перефразовані друковані
# рядки) цілком може зберегти заголовок і кілька анкорів, тобто набрати повний
# бал 15. А `pipeline/extraction/blank_form.py` стоїть не на анкорах, а на
# ТОЧНОМУ тексті бланка: різак меж полів (`resegment_by_blank`) і негативна
# перевірка (`is_printed_form_text`) обидва мовчки перестають працювати, щойно
# формулювання розійшлись. Анкорний бал цього не бачить -- рядкове покриття
# бачить.
#
# МЕЖА ВИВЕДЕНА З ДАНИХ, не з голови. Заміряно на всіх наших документах
# (розподіл -- у docs/known-weak-spots.md розд. 8):
#
#   порожні бланки (leave, deployment) ..................... 1.000
#   leave  docx 16 / pdf 16 ................................ 0.926
#   leave  png (Surya) ..................................... 0.778
#   deployment docx 14 / pdf 14 ............................ 0.852 - 0.889
#
# Фото -- найгірший клас, і це очікувано: розпізнавання губить кілька рядків
# бланка з двадцяти семи. Саме воно, а не чужа редакція, і визначає, наскільки
# низько мусить стояти поріг.
#
# Поріг стоїть СВІДОМО НИЖЧЕ за найгірший ВІДОМО-ДОБРИЙ документ набору, з
# запасом на розпізнавання, тобто на нашому наборі правило не спрацьовує ЗА
# ПОБУДОВОЮ -- інакше це була б регресія, а не захист. Штучно перефразована
# редакція того самого бланка дає 0.148, тобто розділення між класами -- не на
# межі, а в кілька разів. Обидві межі під тестом
# (eval/tests/test_foreign_edition.py): найгірший добрий мусить лежати ще й на
# 0.2 вище порога, а чужа редакція -- удвічі нижче.
DEFAULT_MIN_BLANK_COVERAGE = 0.5
#: Ключ у блоці `identification:` схеми -- поріг можна підняти під конкретний
#: бланк, не правлячи код (той самий підхід, що `min_score` / `llm_floor`).
MIN_BLANK_COVERAGE_KEY = "min_blank_coverage"


def blank_edition_verdict(text: str, schema: dict) -> dict:
    """Наскільки документ схожий на ОГОЛОШЕНИЙ бланк цієї схеми.

    Повертає {found, total, coverage, threshold, recognized}.

    `recognized: True` при `total == 0` БЕЗ оголошеного `blank_template:` --
    НЕ поблажливість, а та сама межа, що вже оголошена в докстрінгу
    `blank_form.py`: схема без `blank_template:` не отримує перевірки взагалі.
    Новий бланк без оголошеного шаблону не має почати мовчки не довіряти
    власним полям -- він має поводитись рівно так, як поводився до цієї зміни.

    А ось ОГОЛОШЕНИЙ шаблон, з якого не читається жоден рядок (файл відсутній
    або порожній) -- це вже НЕ «нема підстав не довіряти», а мовчазна втрата
    трьох захистів одразу (резегментація фото, printed_form_text, вердикт
    редакції). Заміряно (R-A1-02): раніше це давало `recognized: True` при
    нулі доказів. Тепер -- `recognized: False` з окремою причиною; валідатор
    схем ловить це ще раніше, на завантаженні.
    """
    found, total = blank_line_coverage(text, schema)
    threshold = ((schema.get("identification") or {})
                 .get(MIN_BLANK_COVERAGE_KEY, DEFAULT_MIN_BLANK_COVERAGE))
    if total == 0 and schema.get(BLANK_TEMPLATE_KEY):
        return {"found": 0, "total": 0, "coverage": 0.0, "threshold": threshold,
                "recognized": False,
                "reason": "blank_template_missing_or_empty"}
    coverage = (found / total) if total else None
    return {
        "found": found, "total": total, "coverage": coverage,
        "threshold": threshold,
        "recognized": True if coverage is None else coverage >= threshold,
    }


# Режими екстракції, які двигун справді реалізує, і обов'язкові ключі кожного.
# Перевіряється при завантаженні, бо інакше описка в новому YAML має два
# однаково погані наслідки: невідомий режим дає ТИХИЙ no_value (той самий
# вигляд, що "значення в документі немає"), а відсутній ключ -- KeyError уже
# посеред обробки, коли _persist ще не викликався, тобто документ не отримує
# ні запису у сховище, ні рядка в індексі. Обіцянка "новий шаблон = новий
# YAML" без цієї перевірки означає "новий YAML = тихо зламаний прогін".
EXTRACTION_REQUIRED_KEYS = {
    "regex": ("regex_variants",),
    "block_before_label": ("label_before",),
    "derived_from": ("derived_from", "derive"),
    "llm": (),
    # rank_and_name_tokenized свідомо БЕЗ обов'язкових ключів: лейбл несе лише
    # поле rank, а surname/given_name/patronymic беруть результат із кеша того
    # самого розбору рядка. Вимагати label_before на кожному з них -- саме та
    # помилка, яку валідатор видав першою на обох робочих схемах.
    "rank_and_name_tokenized": (),
}
# Групові режими: лейбл потрібен рівно один на групу, і перевіряється окремо.
GROUP_MODES = ("rank_and_name_tokenized",)
KNOWN_DB_TARGETS = {"person", "fact_value", "fact_date_start", "fact_date_end",
                    "additional_info"}
# db_target, для яких схема тримає РІВНО ОДНУ змінну (build_record.py:
# fact_value_code/fact_date_start/fact_date_end) -- друге поле з тим самим
# таргетом мовчки ПЕРЕЗАПИСУЄ перше (на відміну від "person", де дублікат
# явно йде в additional_info). "additional_info" сюди навмисно не входить:
# там ключ -- ім'я поля, а не таргет, тож кілька полів там не конфліктують.
SINGLE_VALUE_DB_TARGETS = {"fact_value", "fact_date_start", "fact_date_end"}
# Типи полів, які генеричний рушій (schema_grammar._field_json_schema)
# розрізняє. Одруківка в type: (напр. "catgory") інакше мовчки отримує ТУ
# САМУ вільну схему {"string","null"}, що й легітимний "text" -- тобто
# категоріальне поле втрачає enum-обмеження LLM без жодного попередження.
KNOWN_FIELD_TYPES = {"category", "text", "date", "number", "object_ref"}
# Значення `normalization:`, які normalize_field справді читає (R-A1-06 +
# R-A2-05). Доти значення цього ключа не перевірялось НІЯК: одруківка
# `null_if_not_isued` не давала жодного повідомлення, а «не видавались»
# ставало реальним значенням поля і їхало в базу окремим фактом.
KNOWN_NORMALIZATIONS = {"nominative_case", "null_if_not_issued"}
# Типи, для яких normalize_field диспетчеризує ДО читання `normalization:` --
# ключ на такому полі мертвий за побудовою (саме так 8 рядків
# `normalization: iso_date` прожили в схемах, не читаючись жодним рядком коду).
TYPE_DISPATCHED_BEFORE_NORMALIZATION = {"category", "number", "date"}
# Режими екстракції, які в build_record ідуть ВЛАСНОЮ гілкою й до
# `normalize_field` не доходять узагалі -- отже `normalization:` на такому полі
# теж мертвий ключ (рев'ю 22.08.2026, A-04). Заміряно: видалення
# `normalization: nominative_case` з полів ПІБ і підміна його на інше значення
# дають ПОБАЙТОВО той самий вихід, а валідатор не казав нічого. Небезпека не в
# самому мертвому ключі, а в тому, що він ЧИТАЄТЬСЯ як гарантія: наступний
# автор схеми, побачивши `nominative_case` на прізвищі, вважатиме, що зняття
# ключа вимкне морфологію -- а вона керується `part:` і не вимикається взагалі.
# `rank_and_name_tokenized` -- морфологія ПІБ за `part:` (build_record.py:324);
# `derived_from` -- значення обчислює DERIVE_FUNCS.
EXTRACTION_DISPATCHED_BEFORE_NORMALIZATION = {"rank_and_name_tokenized",
                                              "derived_from"}
# Ключі, які схема може оголосити, але код їх НЕ читає. Тримаємо перелік явно,
# щоб автор нової схеми дізнався про це з попередження, а не з тихо
# незаповненого поля через тиждень. `note` тут свідомо НЕМА: він і не має
# читатись кодом, це документація для людини, і попередження на нього -- шум.
#
# РОЗДІЛЕНО 14.08.2026 за рівнем, на якому ключ реально стоїть у YAML. Доти в
# одному переліку лежали і ключі ПОЛЯ, і ключ СХЕМИ, а перевірка була рівно
# одна -- `DECLARED_BUT_UNREAD_KEYS & set(field)`. Тобто попередження про
# `out_of_scope:` (він верхнього рівня, полем не буває) **не спрацьовувало
# ніколи**, і твердження docs/known-weak-spots.md п.2.7 «валідатор тепер
# попереджає» для цього ключа було неправдою.
# Розбір кожного з трьох ключів і чому доля в них різна --
# docs/architecture/2026-08-14_multirow-tables-and-multiple-subjects.md розд. 5.
DECLARED_BUT_UNREAD_FIELD_KEYS = {"multiple", "registry"}
DECLARED_BUT_UNREAD_SCHEMA_KEYS = {"out_of_scope"}
# Що саме станеться зі значенням, якщо ключ лишити оголошеним. Загальне «не
# читається кодом» не давало автору схеми ЗРОБИТИ з попередження висновок:
# `registry:` лишається оголошенням назавжди (реєстру як даних немає), а
# `multiple:` -- це нереалізована ПОВЕДІНКА, і на невідкладеному полі вона
# означає тихо неправильне значення, а не порожнє.
UNREAD_KEY_CONSEQUENCE = {
    "registry": "значення піде в facts.value рядком, об'єкт не створиться "
                "-- лишається ОГОЛОШЕННЯМ навмисно (weak-spots п.2.7)",
    "multiple": "витягнеться лише ПЕРШЕ значення (weak-spots п.2.8)",
    "out_of_scope": "документація межі шаблону для людини, як `note`",
}
# Типи зв'язку документ->документ, які build_record складає в
# record["document_links"]. Перелік закритий: невідомий тип означав би, що
# зв'язок оголошений у YAML і мовчки нікуди не пішов.
KNOWN_LINK_TYPES = {"supersedes"}


def validate_schema(schema: dict, known_fact_types=None) -> list:
    """Повертає список проблем: (severity, message). severity error | warning.
    Не кидає -- вирішує викликач, бо в пакетному режимі краще сказати про всі
    схеми одразу, ніж падати на першій."""
    problems = []
    path = schema.get("_path", schema.get("template", "?"))

    def err(msg):
        problems.append(("error", f"{path}: {msg}"))

    def warn(msg):
        problems.append(("warning", f"{path}: {msg}"))

    fact_type = schema.get("fact_type")
    if not fact_type:
        err("немає fact_type -- факт не отримає виміру в БД")
    # `is not None`, а не просто truthy: викликач МІГ передати завантажений,
    # але порожній реєстр (fact_type_registry.yaml відсутній/побитий/без
    # жодного code -- pipeline/run.py:load_fact_types повертає саме {} у
    # цьому випадку). Порожній словник як "перевірку вимкнено" тихо
    # вимикав саме ту перевірку, яку цей реєстр мав вмикати -- документ зі
    # схемою, що має друкарську помилку у fact_type, проходив без жодної
    # помилки, якщо реєстр з якоїсь причини не завантажився.
    elif known_fact_types is not None and fact_type not in known_fact_types:
        err(f"fact_type '{fact_type}' не зареєстрований у "
            f"dictionaries/fact_type_registry.yaml")

    # ВИД СУБ'ЄКТА -- закритий перелік, як part / db_target / type / dimension,
    # і рівно з тієї самої причини: кожне значення відповідає рядку в чужій
    # таблиці (`object_kinds`), а `objects.kind_id` -- NOT NULL. Опечатка в
    # `subject_kind:` без цієї перевірки не проявилась би НІДЕ на нашому боці:
    # вид просто пройшов би у вихід рядком і осів у базі.
    subject_kind = schema.get("subject_kind")
    if subject_kind is not None and subject_kind not in DECLARABLE_SUBJECT_KINDS:
        err(f"невідомий subject_kind '{subject_kind}' (допустимі: "
            f"{list(DECLARABLE_SUBJECT_KINDS)}) -- вид пішов би у вихід рядком і "
            "далі в objects.kind_id, якого в object_kinds немає")
    elif subject_kind is None:
        # Попередження, а не помилка -- НЕ поблажливість, а різниця в наслідках.
        # Помилка виключає схему з набору (run.py:build_resources), тобто ВСІ
        # документи цього шаблону пішли б в unresolved через відсутній один
        # рядок YAML. А відсутнє оголошення має робочий фолбек: мапінг
        # «домен -> вид» (`domain:` схема оголошує завжди). Тихо це не
        # проходить: попередження тут + `subject_kind_reason` у кожному записі.
        warn("немає subject_kind -- вид суб'єкта визначатиметься мапінгом "
             "домену; якщо мапінгу для цього домену теж немає, кожен документ "
             f"отримає '{UNKNOWN_SUBJECT}' і об'єкт у БД не створиться")

    # Рівень СХЕМИ, не поля. Доти цієї перевірки не було взагалі -- див.
    # коментар до DECLARED_BUT_UNREAD_SCHEMA_KEYS.
    for key in sorted(DECLARED_BUT_UNREAD_SCHEMA_KEYS & set(schema)):
        warn(f"ключ схеми '{key}' не читається кодом -- "
             f"{UNREAD_KEY_CONSEQUENCE[key]}")

    # Перевизначення констант родини бланків (R-A1-08) -- закритий перелік
    # ключів, як усе в схемі: одруківка мусить бути помилкою, а не тихо
    # проігнорованим налаштуванням.
    limits = schema.get(EXTRACTION_LIMITS_KEY)
    if limits is not None:
        if not isinstance(limits, dict):
            err(f"{EXTRACTION_LIMITS_KEY} мусить бути словником "
                f"{{ключ: ціле}} (відомі ключі: {sorted(KNOWN_EXTRACTION_LIMITS)})")
        else:
            for key, value in limits.items():
                if key not in KNOWN_EXTRACTION_LIMITS:
                    err(f"{EXTRACTION_LIMITS_KEY}.{key}: невідомий ключ "
                        f"(відомі: {sorted(KNOWN_EXTRACTION_LIMITS)}) -- "
                        "налаштування мовчки не діяло б")
                elif not isinstance(value, int) or value <= 0:
                    err(f"{EXTRACTION_LIMITS_KEY}.{key}: мусить бути додатним "
                        f"цілим, отримано {value!r}")

    # Оголошений blank_template, який не читається, раніше не давав ЖОДНОГО
    # повідомлення (R-A1-02): _read_lines неіснуючого шляху -> [], вердикт
    # `recognized: True` при `total: 0` -- і мовчки вимикались одразу три
    # захисти (резегментація фото 2.11a, printed_form_text 5.9, вердикт
    # редакції розд. 8). Схема БЕЗ ключа перевірки не отримує навмисно --
    # помилка лише для ОГОЛОШЕНОГО, але непрацездатного шляху.
    if schema.get(BLANK_TEMPLATE_KEY):
        blank_path = blank_template_path(schema)
        if not os.path.exists(blank_path):
            err(f"blank_template '{schema[BLANK_TEMPLATE_KEY]}' не існує -- "
                "резегментація фото, перевірка друкованого тексту бланка й "
                "вердикт редакції мовчки вимкнулись би")
        elif not blank_path.lower().endswith(".docx"):
            # ОКРЕМИМ повідомленням, а не разом із «не дає рядків»: раніше
            # не-docx шлях узагалі не доходив до перевірки -- `_read_lines`
            # кидав PackageNotFoundError назовні й валив увесь батч (A-14).
            # Тепер причина названа словами: читач бланка один і той самий, що
            # для документів-docx, і pdf/фото він не читає за побудовою.
            err(f"blank_template '{schema[BLANK_TEMPLATE_KEY]}' не .docx -- "
                "порожній бланк читається тим самим docx-інжестом, що й "
                "документи, тому pdf/зображення тут не працюють; захисти за "
                "бланком мовчки вимкнулись би")
        elif not printed_cutters(schema):
            err(f"blank_template '{schema[BLANK_TEMPLATE_KEY]}' не дає жодного "
                "друкованого рядка (порожній або нечитабельний файл) -- "
                "захисти за бланком не працювали б")

    seen_names = set()
    targets = set()
    single_value_targets_seen = {}
    for field in schema.get("fields") or []:
        name = field.get("name")
        if not name:
            err("поле без name")
            continue
        if name in seen_names:
            err(f"поле '{name}' оголошене двічі")
        seen_names.add(name)

        for key in sorted(DECLARED_BUT_UNREAD_FIELD_KEYS & set(field)):
            # `multiple: true` на НЕвідкладеному полі -- не попередження.
            # Відкладене поле й так оголошене як невитягуване (воно йде в
            # not_implemented_fields, тобто споживач знає, що значення немає).
            # А на діючому полі оголошення обіцяє СПИСОК, рушій же візьме одне
            # значення й віддасть його як повне -- «120 осіб у книзі
            # штатно-посадового обліку» перетворюються на одну, і в записі
            # ніде не сказано, що решту втрачено. Це тихо неправильна ЦИФРА в
            # підрахунках, а не порожнє поле, тому error, а не warning.
            if (key == "multiple" and field.get("multiple")
                    and field.get("priority") != "deferred"):
                err(f"поле '{name}': multiple: true на невідкладеному полі -- "
                    "рушій візьме лише ПЕРШЕ значення й віддасть його як "
                    "повний список (сегментації на рядки немає). Або "
                    "priority: deferred, або поле не повинно бути multiple")
                continue
            warn(f"поле '{name}': ключ '{key}' не читається кодом -- "
                 f"{UNREAD_KEY_CONSEQUENCE[key]}")

        target = field.get("db_target", "additional_info")
        targets.add(target)
        if target not in KNOWN_DB_TARGETS:
            err(f"поле '{name}': невідомий db_target '{target}' -- значення "
                f"мовчки пішло б у additional_info, який БД не читає")
        if target in SINGLE_VALUE_DB_TARGETS:
            single_value_targets_seen.setdefault(target, []).append(name)
        if (target == "fact_value" and field.get("type") != "category"
                and not field.get("category") and not field.get("value_free_text")):
            # ЗНАЧЕННЯ ОСНОВНОГО ФАКТУ вільним текстом (рев'ю 22.08.2026,
            # A-11). Обидва наявні шаблони саме такі: `facts.value_code`
            # отримує рядок із паперу («щорічна основна відпустка за 2026
            # рік»), а не код довідника, тому питання «скільком людям
            # СІМЕЙНА відпустка» на боці БД -- порівняння рядків, не GROUP BY
            # по коду. Це може бути свідомим рішенням (на бланку вид відпустки
            # злитий із населеним пунктом в одне значення), і тоді схема каже
            # це вголос: `value_free_text: true`. Попередження, не помилка --
            # рішення тут за автором схеми, але воно мусить бути ОГОЛОШЕНИМ, а
            # не таким, що вгадується з відсутності `category:`.
            warn(f"поле '{name}': db_target fact_value без `category:` -- "
                 "значення основного факту піде в БД вільним текстом, "
                 "підрахунок за видом стане порівнянням рядків; якщо це "
                 "свідомо, оголоси `value_free_text: true`")

        dimension = field.get("dimension")
        # `is not None` -- та сама причина, що й для fact_type вище.
        if dimension and known_fact_types is not None and dimension not in known_fact_types:
            err(f"поле '{name}': dimension '{dimension}' не зареєстрований у "
                "dictionaries/fact_type_registry.yaml")

        field_type = field.get("type")
        if field_type not in KNOWN_FIELD_TYPES:
            err(f"поле '{name}': невідомий type '{field_type}' (відомі: "
                f"{sorted(KNOWN_FIELD_TYPES)}) -- отримає ту саму вільну "
                "схему, що й text (schema_grammar._field_json_schema), тож "
                "LLM зможе повернути що завгодно замість enum-коду")

        # Межі числа -- лише для type: number (R-A1-03): на іншому типі ключ
        # не читається ніде, і оголошена межа мовчки не діяла б.
        for bound_key in ("min_value", "max_value"):
            bound = field.get(bound_key)
            if bound is None:
                continue
            if field_type != "number":
                err(f"поле '{name}': {bound_key} читається лише для type: "
                    f"number -- на type '{field_type}' межа мовчки не діяла б")
            elif not isinstance(bound, int):
                err(f"поле '{name}': {bound_key} мусить бути цілим, "
                    f"отримано {bound!r}")
        if (isinstance(field.get("min_value"), int)
                and isinstance(field.get("max_value"), int)
                and field["min_value"] > field["max_value"]):
            err(f"поле '{name}': min_value > max_value -- жодне число не "
                "пройшло б")

        # `normalization:` -- закритий перелік, як type / part / db_target, і
        # з тієї самої причини (R-A1-06 + R-A2-05): невідоме значення означає,
        # що оголошена нормалізація мовчки НЕ виконується. Найдорожчий випадок
        # заміряний: одруківка в null_if_not_issued перетворює
        # підтверджено-порожнє поле («не видавались») на текстове значення,
        # яке їде в БД окремим фактом.
        normalization = field.get("normalization")
        if normalization is not None:
            if normalization not in KNOWN_NORMALIZATIONS:
                err(f"поле '{name}': невідома normalization '{normalization}' "
                    f"(відомі: {sorted(KNOWN_NORMALIZATIONS)}) -- нормалізація "
                    "мовчки не виконувалась би, а сентинел/відмінок пішов би "
                    "в БД сирим значенням")
            elif field_type in TYPE_DISPATCHED_BEFORE_NORMALIZATION:
                err(f"поле '{name}': normalization '{normalization}' не "
                    f"читається для type '{field_type}' -- normalize_field "
                    "диспетчеризує за типом раніше, ключ мертвий")
            elif field.get("extraction") in EXTRACTION_DISPATCHED_BEFORE_NORMALIZATION:
                err(f"поле '{name}': normalization '{normalization}' не "
                    f"читається для extraction '{field.get('extraction')}' -- "
                    "build_record обробляє цей режим власною гілкою й до "
                    "normalize_field не доходить (морфологією ПІБ керує "
                    "part:, а не цей ключ); ключ мертвий")
            if normalization == "null_if_not_issued" and not field.get("not_issued_sentinel"):
                err(f"поле '{name}': null_if_not_issued без not_issued_sentinel "
                    "-- порівнювати нема з чим, нормалізація інертна")
        if field.get("not_issued_sentinel") and normalization != "null_if_not_issued":
            err(f"поле '{name}': not_issued_sentinel оголошено без "
                "normalization: null_if_not_issued -- сентинел "
                f"'{field.get('not_issued_sentinel')}' пішов би в БД як "
                "реальне значення поля")

        part = field.get("part")
        if part is not None and part not in NAME_PART_ROLES:
            err(f"поле '{name}': part '{part}' невідома -- допустимі лише "
                f"{list(NAME_PART_ROLES)} (до кожної прив'язана морфологічна "
                "граммема, тому перелік закритий навмисно)")

        criticality = field.get("criticality")
        if criticality not in (None, "critical", "optional"):
            err(f"поле '{name}': criticality '{criticality}' -- допустимо "
                "лише critical або optional")

        # Опечатка в `consistency:` не має означати "перевірки немає" -- це
        # рівно той клас тихої помилки, проти якого сама перевірка й стоїть.
        rule = field.get("consistency")
        if rule is not None:
            if not isinstance(rule, dict) or not rule.get("rule"):
                err(f"поле '{name}': consistency без ключа 'rule'")
            elif rule["rule"] not in CONSISTENCY_RULES:
                err(f"поле '{name}': consistency.rule '{rule['rule']}' не "
                    f"реалізовано (відомі: {sorted(CONSISTENCY_RULES)}) -- "
                    "перевірка узгодженості мовчки не виконувалась би")
            else:
                for ref in CONSISTENCY_RULES[rule["rule"]][0]:
                    target = rule.get(ref)
                    if not target:
                        err(f"поле '{name}': consistency '{rule['rule']}' "
                            f"вимагає посилання '{ref}'")
                    elif target not in {f.get("name") for f in schema.get("fields") or []}:
                        err(f"поле '{name}': consistency.{ref} посилається на "
                            f"поле '{target}', якого в схемі немає")
        elif (field_type == "number" and field.get("llm_fallback") is not False
                and field.get("extraction") != "derived_from"):
            # ЧИСЛОВЕ поле, яке може прийти від моделі, БЕЗ перевірки
            # узгодженості (рев'ю 22.08.2026, C-07). Заземлення для number
            # питає лише «чи є це число в документі» -- заміряно на
            # «Відпускний квиток № 4180/26 від 31.07.2026»: 12 (правильне), 26
            # (хвіст номера), 31 (день) і 7 (місяць) проходять ОДНАКОВО, а
            # відсіюється тільки те, що не влазить у межі поля. Тобто другий
            # шар (`consistency`) -- єдине, що відрізняє прочитане число від
            # правдоподібно вигаданого, і досі він був ЗБІГОМ КОНФІГУРАЦІЇ:
            # обидва наявні number-поля його оголосили, а валідатор не вимагав.
            # Вибір автора схеми лишається реальний: або правило узгодженості,
            # або `llm_fallback: false` (краще відмова, ніж вигадка) --
            # derived_from сюди не входить, бо там значення обчислює код.
            err(f"поле '{name}': type number без consistency -- заземлення "
                "числа перевіряє лише наявність числа В ДОКУМЕНТІ, тому день, "
                "місяць чи хвіст номера пройдуть як значення. Оголоси "
                "consistency (правило узгодженості) або llm_fallback: false")

        link_type = field.get("link_type")
        if link_type is not None and link_type not in KNOWN_LINK_TYPES:
            err(f"поле '{name}': link_type '{link_type}' невідомий (відомі: "
                f"{sorted(KNOWN_LINK_TYPES)}) -- зв'язок мовчки не потрапив би "
                "у record['document_links']")

        if field.get("llm_fallback") not in (None, True, False):
            err(f"поле '{name}': llm_fallback мусить бути true або false")

        for key in (PLACEHOLDER_TOKENS_KEY, PLACEHOLDER_TOKENS_EXCEPT_KEY):
            value = field.get(key)
            if value is not None and not isinstance(value, list):
                err(f"поле '{name}': {key} мусить бути списком рядків")

        if field.get("type") == "category" and not field.get("category"):
            err(f"поле '{name}': type: category без ключа category")

        mode = field.get("extraction")

        # `empty_pattern:` -- схемний доказ ПОРОЖНЬОГО слота (R2-П5-Б).
        # Перевіряється ДО `continue` за deferred/None нижче навмисно: ключ,
        # оголошений на полі, яке в LLM не їде взагалі, не дасть жодного
        # ефекту -- а мовчки недіюче налаштування це рівно те, від чого
        # захищає весь цей валідатор.
        declared_empty = field.get(EMPTY_PATTERN_KEY)
        if declared_empty is not None:
            patterns = ([declared_empty] if isinstance(declared_empty, str)
                        else declared_empty)
            if (not isinstance(patterns, list)
                    or not patterns
                    or not all(isinstance(p, str) and p.strip() for p in patterns)):
                err(f"поле '{name}': {EMPTY_PATTERN_KEY} мусить бути непорожнім "
                    "рядком або списком непорожніх рядків")
                patterns = []
            if mode is None or mode == "derived_from" or \
                    field.get("priority") == "deferred":
                err(f"поле '{name}': {EMPTY_PATTERN_KEY} на полі з режимом "
                    f"'{mode}' (або deferred) не діє -- таке поле в LLM не "
                    "їде, тобто скіпати нема чого")
            elif field.get("llm_fallback") is False:
                warn(f"поле '{name}': {EMPTY_PATTERN_KEY} разом із "
                     "llm_fallback: false -- фолбеку й так немає, ключ лише "
                     "додає провенанс confirmed_empty_slot")
            blank_text = blank_template_text(schema)
            for pattern in patterns:
                try:
                    compiled = re.compile(pattern)
                except re.error as exc:
                    err(f"поле '{name}': невалідний {EMPTY_PATTERN_KEY} ({exc})")
                    continue
                # Скелет порожнечі ІСНУЄ в порожньому бланку за визначенням.
                # Якщо він там не збігається -- патерн написаний під щось інше
                # й не спрацює НІКОЛИ, а поле й далі витрачатиме виклик моделі
                # на відомо порожній слот (заміряно: 89-198 с на групу).
                if blank_text and not compiled.search(blank_text):
                    err(f"поле '{name}': {EMPTY_PATTERN_KEY} не збігається з "
                        f"оголошеним blank_template -- скелет порожнього слота "
                        "мусить бути в порожньому бланку, інакше патерн мовчки "
                        "не діяв би ніколи")

        if field.get("priority") == "deferred" or mode is None:
            continue
        if mode not in EXTRACTION_REQUIRED_KEYS:
            err(f"поле '{name}': невідомий режим extraction '{mode}' "
                f"(відомі: {sorted(EXTRACTION_REQUIRED_KEYS)})")
            continue
        for key in EXTRACTION_REQUIRED_KEYS[mode]:
            if not field.get(key):
                err(f"поле '{name}': режим '{mode}' вимагає ключ '{key}'")
        if mode == "derived_from":
            derive_name = field.get("derive")
            if derive_name and derive_name not in DERIVE_FUNCS:
                err(f"поле '{name}': derive '{derive_name}' не реалізовано "
                    f"(відомі: {sorted(DERIVE_FUNCS)}) -- інакше KeyError "
                    "посеред обробки документа, коли значення справді "
                    "знадобиться")
        for variant in field.get("regex_variants") or []:
            pattern = (variant or {}).get("pattern")
            if not pattern:
                err(f"поле '{name}': regex_variants містить запис без pattern")
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                err(f"поле '{name}': невалідний regex ({exc})")

    # Груповий режим: лейбл рівно один на ГРУПУ (особу), не на поле.
    for mode in GROUP_MODES:
        members = [f for f in (schema.get("fields") or []) if f.get("extraction") == mode]
        by_group = {}
        for f in members:
            by_group.setdefault(name_group_key(f), []).append(f)
        for group, fields_in_group in by_group.items():
            labeled = [f.get("name") for f in fields_in_group if f.get("label_before")]
            if not labeled:
                err(f"режим '{mode}', група '{group}': жодне з полів "
                    f"{[f.get('name') for f in fields_in_group]} не має "
                    "label_before -- рядок для розбору неможливо знайти")
            elif len(labeled) > 1:
                # РІВНО один: resolve_name_groups (extract.py) бере лейбл із
                # ПЕРШОГО поля групи, що його має (`next(...)`) -- друге
                # оголошення тихо ігнорується, а не помічається як помилка,
                # тож копіпаст-помилка (label_before скопійований на два
                # поля однієї групи) досі проходила без жодного сигналу.
                err(f"режим '{mode}', група '{group}': label_before "
                    f"оголошено на кількох полях {labeled} -- має бути рівно "
                    "одне; resolve_name_groups мовчки бере перше за списком")
            parts = [field_part(f) for f in fields_in_group]
            duplicated = {p for p in parts if p in NAME_PART_ROLES and parts.count(p) > 1}
            if duplicated:
                err(f"режим '{mode}', група '{group}': роль(і) {sorted(duplicated)} "
                    "оголошені двічі в одній групі -- друге поле перетерло б "
                    "перше; для другої особи потрібен окремий `group:`")

    if "fact_value" not in targets:
        warn("жодне поле не має db_target: fact_value -- основний факт піде в "
             "БД зі значенням null")
    for target, names in single_value_targets_seen.items():
        if len(names) > 1:
            err(f"db_target '{target}' оголошено на кількох полях {names} -- "
                "build_record тримає ОДНУ змінну на цей таргет, кожне "
                "наступне поле мовчки перезаписує попереднє")
    return problems


def validate_schema_set(schemas: list) -> list:
    """Перевірки, що стосуються НАБОРУ схем разом, а не однієї окремо --
    validate_schema() бачить лише один YAML за раз і не може помітити, що
    ДВІ схеми оголосили однаковий template. identify_template ключує обидва
    scores і by_template за template (identification.py) -- за колізії одна
    з двох схем мовчки випадає з підрахунку без жодної помилки, і документ
    цього типу могли б розпізнавати за ГІРШОЮ (чи взагалі не тою) схемою."""
    problems = []
    seen = {}
    for schema in schemas:
        template = schema.get("template")
        path = schema.get("_path", "?")
        if template in seen:
            problems.append(("warning",
                f"{path}: template '{template}' збігається з {seen[template]} "
                "-- одна зі схем мовчки випадає з ідентифікації (обидві "
                "ключуються за template); перейменуйте одну з них"))
        else:
            seen[template] = path
    return problems


def _spread_schema_defaults(schema: dict) -> None:
    """Розкладає ключі, оголошені на рівні СХЕМИ, по її полях -- один раз, при
    завантаженні. Потрібно тому, що normalize_field бачить лише field_def і не
    має доступу до схеми, а перелік токенів-заповнювачів природно оголошувати
    на бланк цілком ("у книзі обліку техніки «немає» -- значення"), а не
    переписувати в кожне поле. Ключ, оголошений на самому полі, важить більше
    й не перетирається."""
    for key in (PLACEHOLDER_TOKENS_KEY, PLACEHOLDER_TOKENS_EXCEPT_KEY):
        if key not in schema:
            continue
        for field in schema.get("fields") or []:
            field.setdefault(key, schema[key])


def load_schemas(schemas_dir: str) -> list:
    """Усі *.yaml, що є схемами (мають template + fields). Довідники й
    domain_keyphrases сюди не потрапляють -- визначається за ВМІСТОМ, не за
    назвою файлу (порядок glob не алфавітний, а назва довідника не містить
    жодного маркера "це не схема")."""
    schemas = []
    for path in sorted(glob.glob(os.path.join(schemas_dir, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            content = yaml.safe_load(f)
        if isinstance(content, dict) and "template" in content and "fields" in content:
            content["_path"] = path
            _spread_schema_defaults(content)
            schemas.append(content)
    return schemas


def schema_title_phrases(schema: dict) -> list:
    return list(schema.get("identification", {}).get("title", []))


def score_schema(text: str, schema: dict) -> int:
    """phrase_in_text, а не `p in low`: підрядковий збіг без межі слова давав
    бал чужій схемі, коли короткий анкор випадково траплявся всередині
    іншого слова або в цитаті документа іншого типу."""
    low = (text or "").lower()
    ident = schema.get("identification") or {}
    title_hits = sum(1 for p in ident.get("title", []) if phrase_in_text(low, p))
    anchor_hits = sum(1 for p in ident.get("anchors", []) if phrase_in_text(low, p))
    return title_hits * TITLE_WEIGHT + anchor_hits * ANCHOR_WEIGHT


def identify_template(text: str, schemas: list, domains: dict = None, llm_choose=None) -> dict:
    """Повертає словник-результат, ніколи не кидає виняток:
      schema     -- обрана схема або None
      template   -- її template або None
      domain     -- домен обраної схеми, або грубий домен із domain_keyphrases,
                    якщо шаблон не визначено (корисно для черги unresolved)
      source     -- anchors | llm | None
      score, runner_up, scores, reason
      blank_edition -- вердикт про РЕДАКЦІЮ бланка (blank_edition_verdict) для
                    ОБРАНОЇ схеми, або None, якщо схему не обрано. Це ЄДИНЕ
                    джерело істини про «це наша форма» для всього, що йде далі:
                    екстракція його читає, але не рахує (див. коментар до
                    DEFAULT_MIN_BLANK_COVERAGE).

    llm_choose(prompt, choices) -> str -- grammar-constrained вибір рівно
    одного з choices; "unknown" ЗАВЖДИ серед choices, інакше модель,
    обмежена лише реальними шаблонами, змушена вибрати щось навіть коли
    жоден варіант не підходить.
    """
    scores = {s["template"]: score_schema(text, s) for s in schemas}
    by_template = {s["template"]: s for s in schemas}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    best_template, best_score = ranked[0] if ranked else (None, 0)
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0
    coarse_domain = None
    domain_scores = None
    if domains:
        # Скори доменів їдуть далі в запис (R-B1-01): раніше в
        # `identification` лежали лише скори ШАБЛОНІВ, і вирок про домен
        # неможливо було ні перевірити, ні оскаржити.
        coarse_domain, domain_scores = classify_domain_rules(text, domains)

    # ЗВІД ПРАВИЛ НЕ Є БЛАНКОМ, скільком би анкорам він не відповідав.
    # Перевірка стоїть ПЕРЕД вибором шаблону навмисно, і це виправлення
    # заміряного дефекту, а не обережність:
    #
    # "Положення про проходження військової служби" і "Статут гарнізонної та
    # вартової служб" ОДИН РАЗ згадують фразу "посвідчення про відрядження"
    # (вони ж описують правила щодо цього бланка) -- це дає бал рівно 5 при
    # НУЛІ анкорів, тобто рівно min_score, і документ визнавався посвідченням
    # про відрядження. Далі домен брався зі схеми (`deployment`), suject_kind
    # ставав `person`, і нормативний документ створював об'єкт-людину в
    # реєстрі. Гейт subject_kind його не ловив, бо гейт спирається на домен, а
    # домен уже був підмінений схемою.
    #
    # Раніше цей клас закривало правило multiple_templates_matched -- але воно
    # ловить лише документ, що впізнається як КІЛЬКА бланків одночасно
    # (Інструкція з діловодства містить обидва додатки). Документ, що згадує
    # ЛИШЕ один бланк, проходив мимо.
    # АЛЕ: бланк, упізнаний ВЛАСНИМИ АНКОРАМИ, лишається бланком -- навіть
    # якщо цитує закон. Це виправлення регресії, яку відкрив сліпий рецензент
    # коду 22.08.2026 (C-01), і причина в моїй же правці того самого дня:
    # процедурний вирок отримав третій шлях (формальні ознаки акта) БЕЗ гейту
    # довжини, а стоїть він перед вибором шаблону -- тож справжній відпускний
    # квиток, у якому трапились «НАКАЗУЮ:» і «набирає чинності», ставав
    # `normative`. Далі run.py давав йому status=confirmed, facts=[] і НЕ
    # ставив у чергу: документ зникав тихо, без цифри, без прогалини, без
    # аудиту. Наказ командира про надання відпустки -- типовий носій обох
    # маркерів, тобто це не екзотика.
    #
    # Розрізнювач саме АНКОРИ, а не бал: заміряний початковий дефект (статут
    # згадує «посвідчення про відрядження» один раз -> бал рівно 5) має НУЛЬ
    # анкорів, а всі 60 реальних бланків набору мають 5 із 5. Тому умова нижче
    # зберігає обидві поведінки: звід правил, що лише згадує бланк, і далі
    # процедурний; заповнений бланк -- ні.
    procedural = coarse_domain and (domains.get(coarse_domain) or {}).get("kind") == "procedural"
    if procedural and best_template is not None:
        best_ident = (by_template[best_template].get("identification") or {})
        best_anchors = list(best_ident.get("anchors") or [])
        best_anchor_hits = sum(1 for p in best_anchors
                               if phrase_in_text((text or "").lower(), p))
        # Анкорів САМИХ ПО СОБІ не досить -- потрібне ще й покриття
        # оголошеного бланка. Це друга половина виправлення C-01, і її
        # знайшов робочий прогін 22.08: наказ МОУ № 280 (нормативний!) бере
        # анкори відпускного квитка, бо описує саме цей бланк, і після
        # першої версії гейта ставав `template: leave_ticket`. При цьому в
        # ТОМУ Ж записі власна перевірка редакції казала 4 мітки з 27
        # (покриття 0.15) -- тобто відповідь уже була, лише не питалась.
        #
        # Тепер питається: анкори перебивають процедурний вирок лише тоді,
        # коли документ справді схожий на наш бланк. Схема без оголошеного
        # `blank_template:` отримує recognized=True за побудовою
        # (blank_edition_verdict), тобто для неї поведінка не змінюється.
        edition = blank_edition_verdict(text, by_template[best_template])
        if (best_score >= best_ident.get("min_score", DEFAULT_MIN_SCORE)
                and best_score > runner_up_score
                and (not best_anchors or best_anchor_hits > 0)
                and edition.get("recognized")):
            procedural = False

    if procedural:
        return {
            "schema": None, "template": None, "domain": coarse_domain,
            "source": None, "score": best_score, "runner_up": runner_up_score,
            "scores": scores, "domain_scores": domain_scores,
            "reason": f"procedural_document:{coarse_domain}",
            "blank_edition": None,
        }

    if best_template is not None:
        ident = by_template[best_template].get("identification") or {}
        min_score = ident.get("min_score", DEFAULT_MIN_SCORE)
        # ОДНОГО ЗАГОЛОВКА НЕ ДОСИТЬ -- потрібен хоч один підтверджувальний
        # анкор. Анкори для цього й існують: заголовок бланка може бути
        # ЗГАДАНИЙ у будь-якому документі, що на цей бланк посилається.
        #
        # Заміряно 14.08.2026 на реальних документах: "Положення про
        # проходження військової служби" і "Статут гарнізонної та вартової
        # служб" один раз згадують "посвідчення про відрядження" -- це давало
        # бал рівно 5 при НУЛІ анкорів, тобто рівно min_score, і документ
        # визнавався посвідченням. Процедурна перевірка вище тепер ловить саме
        # ці два, але лише тому, що вони процедурні: НЕпроцедурний документ,
        # який просто згадує бланк (напр. наказ про конкретне відрядження),
        # проходив би далі.
        #
        # Ціна перевірки заміряна й дорівнює нулю: усі 60 реальних бланків
        # набору (docx + pdf) мають 1 заголовок І 5 анкорів. Тобто вимога не
        # відкидає жодного справжнього документа, лише прибирає вирок за
        # одним згадуванням.
        anchors_declared = list(ident.get("anchors") or [])
        anchor_hits = sum(1 for p in anchors_declared
                          if phrase_in_text((text or "").lower(), p))
        anchor_ok = (not anchors_declared) or anchor_hits > 0
        # Строга нерівність: рівний бал двох шаблонів -- це неоднозначність,
        # а не перемога того, хто випадково перший у списку.
        if best_score >= min_score and best_score > runner_up_score and anchor_ok:
            schema = by_template[best_template]
            return {
                "schema": schema, "template": best_template, "domain": schema.get("domain"),
                "source": "anchors", "score": best_score, "runner_up": runner_up_score,
                "scores": scores, "reason": None,
                "blank_edition": blank_edition_verdict(text, schema),
            }

    # Скільки схем упевнено збіглося. Документ, що містить ДЕКІЛЬКА бланків --
    # це майже завжди не бланк, а джерело, яке їх у собі несе: Інструкція з
    # діловодства має і Додаток 28, і Додаток 30 з їхніми заголовками, тому
    # чесно набирає високий бал за обома схемами. Тобто чим БІЛЬШЕ схем
    # збіглося, тим МЕНШ імовірно, що це якась одна з них.
    # Живе репро від команди БД: інструкція_діловодство.docx, 402898 символів,
    # бал 9 за deployment_certificate і 9 за leave_ticket -- і на LLM це
    # виходило як упевнено визначене "посвідчення про відрядження".
    strong_matches = [t for t, sc in scores.items()
                      if sc >= ((by_template[t].get("identification") or {})
                                .get("min_score", DEFAULT_MIN_SCORE))]

    llm_error = None
    llm_floor = DEFAULT_LLM_FLOOR
    if best_template is not None:
        llm_floor = ((by_template[best_template].get("identification") or {})
                     .get("llm_floor", DEFAULT_LLM_FLOOR))
    # Дві причини НЕ питати модель, обидві -- позитивне свідчення, а не
    # відсутність свідчень:
    #   1. нічия за балом: рівний бал двох бланків означає, що жоден із них не
    #      підтверджений, а не що треба вибрати одного;
    #   2. декілька впевнених збігів: див. strong_matches вище.
    # Додатковий аргумент саме для цього класу документів: інструкція має
    # 400k символів, а в модель іде _trim до max_context_chars (6000) --
    # 60% початку й 40% кінця. Вона фізично не бачить документа, про який
    # її питають, тож її відповідь тут не може бути кращою за здогадку.
    ambiguous_tie = bool(best_score) and best_score == runner_up_score
    container_like = len(strong_matches) > 1
    if llm_choose is not None and schemas and best_score >= llm_floor \
            and not ambiguous_tie and not container_like:
        options = "\n".join(
            f"- {s['template']}: " + ((s.get("identification") or {}).get("description")
                                       or s.get("domain", ""))
            for s in schemas
        )
        prompt = (
            "Визнач, який це бланк документа, з переліку нижче, або 'unknown', "
            f"якщо жоден не підходить.\n\n{options}\n\nТекст документа:\n{text}"
        )
        try:
            answer = llm_choose(prompt, [s["template"] for s in schemas] + ["unknown"]).strip()
        except Exception as exc:
            # Збій LLM на ідентифікації не має валити прогін: документ
            # деградує в unresolved (з причиною), а решта батчу обробляється.
            # Без цього try/except будь-яка помилка моделі тут валила все.
            answer, llm_error = None, f"llm_error:{type(exc).__name__}"
        if answer in by_template:
            schema = by_template[answer]
            return {
                "schema": schema, "template": answer, "domain": schema.get("domain"),
                "source": "llm", "score": scores.get(answer, 0), "runner_up": runner_up_score,
                "scores": scores, "reason": None,
                "blank_edition": blank_edition_verdict(text, schema),
            }

    if llm_error:
        reason = llm_error
    elif container_like:
        # Окрема причина, бо дія людини інша: не "напиши схему для цього
        # бланка", а "це не бланк -- це джерело з додатками".
        reason = f"multiple_templates_matched:{','.join(sorted(strong_matches))}"
    elif ambiguous_tie:
        reason = "ambiguous"
    elif best_score < llm_floor:
        # Окрема причина, а не загальний no_template_match: у черзі рев'ю це
        # означає "жодного сигналу, потрібна нова схема", а не "схема є, але
        # не набрала порога".
        reason = "below_llm_floor"
    else:
        reason = "no_template_match"
    return {
        "schema": None, "template": None, "domain": coarse_domain,
        "source": None, "score": best_score, "runner_up": runner_up_score,
        "scores": scores, "domain_scores": domain_scores,
        "reason": reason, "blank_edition": None,
    }


def missing_dictionaries(schema: dict, dictionaries: dict) -> set:
    """Категорії, на які схема посилається, але довідник не завантажено --
    інакше поле мовчки лишається "unknown" без жодного пояснення чому."""
    required = {f["category"] for f in schema["fields"]
                if f.get("type") == "category" and f.get("category")}
    return required - set(dictionaries)


def unused_dictionaries(schemas: list, dictionaries: dict) -> set:
    """Довідники, на які НЕ посилається жодне поле жодної схеми.

    Дзеркало `missing_dictionaries`, і потрібне з тієї самої причини:
    завантажений довідник виглядає як робоча частина пайплайна (прогін друкує
    «Довідники: ['leave_type', 'military_rank']»), хоч `leave_type` не читає
    жодне `category:` -- вид відпустки на реальному бланку злитий із населеним
    пунктом в одне вільнотекстове поле (рев'ю 22.08.2026, A-16). Файл
    лишається свідомо (коди знадобляться, якщо вид відпустки виділять в окреме
    категоріальне поле), але «лишається на майбутнє» і «працює зараз» мусять
    виглядати по-різному.
    """
    used = {f["category"] for schema in schemas for f in schema.get("fields") or []
            if f.get("type") == "category" and f.get("category")}
    return set(dictionaries) - used
