"""Векторний ярус маршрутизації: питання -> найближчий шаблон каталогу.

Задача 3.3 плану docs/tasks/2026-08-24_app-chat-plan.md. Місце в порядку
доріг (_extra_tiers у app.py): ПІСЛЯ правил (rules_route), ПЕРЕД
моделлю-класифікатором (model_route): правила -> вектори -> модель ->
фолбек. Вектори коштують мілісекунди на CPU, тому типове питання, яке не
впіймали правила, отримує маршрут БЕЗ виклику моделі (~43 с на CPU).

Механіка: semantic-router (MIT, локальний режим) будує маршрути з examples
query_catalog.yaml -- ті самі приклади, що є єдиним джерелом і для
тест-сету (router_testset.yaml). Encoder -- локальний HuggingFaceEncoder;
ПАСТКА з дослідження (2026-08-24_chat-harness-options.md, розд. 2.6):
за замовчуванням semantic-router бере платний OpenAI-encoder, тому encoder
передається явно і тільки локальний.

Вибір encoder-а -- заміром (measure_router.py, задача 3.2; результати --
docs/research/2026-08-25_encoder-measurement.md), не смаком. Для e5
обов'язкові префікси "query: " до питання і "passage: " до прикладів --
без них якість падає (задокументовано в research); префікси накладає ЦЕЙ
модуль, бо semantic-router про них не знає.

Деградація, не падіння: якщо encoder не завантажується (немає ваг на
машині, немає transformers), чат живе без векторного ярусу -- vector_route
повертає (None, 0.0), у лог їде попередження один раз.
"""
import logging
import os
import threading

import yaml

logger = logging.getLogger(__name__)

CHAT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(CHAT_DIR)                      # demos/upload_app
CATALOG_PATH = os.path.join(APP_DIR, "query_catalog.yaml")

# Що знає модуль про кожен encoder-кандидат (задача 3.2). pooling:
# e5 навчений під mean-пулінг, bge-m3 і arctic-embed віддають вектор
# токена [CLS] -- HuggingFaceEncoder semantic-router вміє лише mean/max,
# тому для cls є підклас _ClsPoolingEncoder нижче. Префікси -- з карток
# моделей: e5 хоче query:/passage:, arctic -- лише query: до питання.
ENCODER_SPECS = {
    "intfloat/multilingual-e5-base": dict(
        query_prefix="query: ", passage_prefix="passage: ",
        pooling="mean", model_kwargs={}),
    # той самий e5, але СИМЕТРИЧНІ префікси: приклади маршрутів -- такі ж
    # питання, як і запит, а "passage:" у картці e5 -- для асиметричного
    # пошуку по документах; для symmetric-задач картка каже "query:" з обох
    # боків. Виміряно окремим кандидатом, бо це інша якість (задача 3.2)
    "intfloat/multilingual-e5-base@sym": dict(
        hf_name="intfloat/multilingual-e5-base",
        query_prefix="query: ", passage_prefix="query: ",
        pooling="mean", model_kwargs={}),
    "BAAI/bge-m3": dict(
        query_prefix="", passage_prefix="",
        pooling="cls", model_kwargs={}),
    "Snowflake/snowflake-arctic-embed-m-v2.0": dict(
        query_prefix="query: ", passage_prefix="",
        # картка моделі: архітектура GTE, код моделі їде з репозиторію;
        # шлях memory-efficient attention вимкнено -- він вимагає xformers,
        # якого нема (і не треба: ставити його = чіпати torch пайплайна)
        pooling="cls", explicit_position_ids=True, model_kwargs={
            "trust_remote_code": True,
            "use_memory_efficient_attention": False,
            "unpad_inputs": False}),
}

# Обраний encoder -- за заміром 25.08 (measure_router.py на
# router_testset.yaml; таблиця -- docs/research/2026-08-25_encoder-measurement.md):
# bge-m3 дає найкращий top-1 (61.3% проти 49-52% в e5) і найширшу шкалу
# балів (правильні 0.52-0.92 проти стиснутих 0.80-0.95 в e5), тобто поріг
# має де стояти. Ціль плану >=90% на тест-сеті НЕ добрана ЖОДНИМ
# кандидатом -- це зафіксований результат заміру, не провал: нижче порога
# питання законно їде у модель (див. «потребує рішення» у прогрес-файлі).
ENCODER_NAME = os.environ.get("CHAT_ENCODER_NAME", "BAAI/bge-m3")

# Агрегація балів маршруту: "max" = правило найближчого прикладу. Дефолт
# semantic-router ("mean" по top-5) заміряно гіршим: він КАРАЄ маршрути з
# кількома схожими прикладами -- бал розбавляється власними сусідами, і
# самотній буквено-близький приклад чужого маршруту виграє
# (bge-m3: 57.3% mean проти 61.3% max).
AGGREGATION = "max"

# Поріг упевненості: НЕ калібрувався, обраний по заміру 25.08 -- вище
# максимального балу ВПЕВНЕНО-НЕПРАВИЛЬНОЇ відповіді bge-m3 на тест-сеті
# (0.918 на повному сеті з leave-one-out; 0.874 на продовому виді «лише те,
# що не ловлять правила»). На порозі 0.92 впевнено-неправильних 0 -- ярус
# відповідає рідко (точні/майже точні збіги з прикладами, smalltalk), але
# ніколи тихо-неправильно; решта їде далі: модель-класифікатор -> фолбек.
# Це деградація швидкості, не правдивості (ризик 1 плану).
_THRESHOLD_DEFAULT = 0.92


def _threshold_from_env():
    """Кривий env НЕ має кладти чат.

    Аудит 25.08 знайшов єдиний шлях, на якому цей ярус переставав «лише
    пришвидшувати»: `CHAT_VECTOR_THRESHOLD=abc` давав ValueError на ІМПОРТІ
    модуля, а app.py імпортує його поза try -- і апка не стартувала взагалі
    (прогнано: exit 1). Ярус-прискорювач, який валить продукт через
    друкарську помилку в змінній оточення, суперечить власному критерію
    приймання, тому значення, що не читається, дає дефолт і попередження.
    """
    raw = os.environ.get("CHAT_VECTOR_THRESHOLD")
    if raw is None:
        return _THRESHOLD_DEFAULT
    try:
        return float(raw)
    except (TypeError, ValueError):
        logging.warning(
            "CHAT_VECTOR_THRESHOLD=%r не число -- беру дефолт %.2f",
            raw, _THRESHOLD_DEFAULT)
        return _THRESHOLD_DEFAULT


THRESHOLD = _threshold_from_env()

_ROUTER = None
_FAILED = False
_LOCK = threading.Lock()


def catalog_routes(path=CATALOG_PATH):
    """-> [(template_id, [examples])] з каталогу. Єдине джерело маршрутів --
    examples каталогу; окремого списку фраз цей модуль не тримає."""
    with open(path, encoding="utf-8") as f:
        templates = yaml.safe_load(f)["templates"]
    return [(t["id"], list(t.get("examples") or [])) for t in templates
            if t.get("examples")]


def _make_encoder(name=None):
    """Локальний encoder за специфікацією. Імпорти важкі (torch,
    transformers) -- тому всередині функції, а не модуля: без ваг чат
    стартує швидко і без векторного ярусу."""
    name = name or ENCODER_NAME
    spec = ENCODER_SPECS[name]
    hf_name = spec.get("hf_name", name)
    from semantic_router.encoders import HuggingFaceEncoder

    if spec["pooling"] == "cls":
        class _ClsPoolingEncoder(HuggingFaceEncoder):
            """CLS-пулінг для bge-m3/arctic: вектор першого токена, не
            середнє (HuggingFaceEncoder уміє лише mean/max)."""

            def __call__(self, docs, batch_size=32,
                         normalize_embeddings=True, pooling_strategy="cls"):
                all_embeddings = []
                for i in range(0, len(docs), batch_size):
                    batch = docs[i:i + batch_size]
                    enc = self._tokenizer(
                        batch, padding=True, truncation=True,
                        return_tensors="pt").to(self.device)
                    # arctic-embed (архітектура GTE): без xformers її
                    # remote-code не будує position_ids сам і читає
                    # rope-таблицю за сміттєвими індексами (IndexError на
                    # першому ж батчі). Тобто це не «модель не працює», а
                    # ми не передали те, що вона в цьому режимі вимагає.
                    if spec.get("explicit_position_ids"):
                        n_tok = enc["input_ids"].shape[1]
                        enc["position_ids"] = (
                            self._torch.arange(n_tok, device=self.device)
                            .unsqueeze(0)
                            .expand(enc["input_ids"].shape[0], -1))
                    with self._torch.no_grad():
                        out = self._model(**enc)
                    emb = out[0][:, 0]          # [CLS]
                    if normalize_embeddings:
                        emb = self._torch.nn.functional.normalize(
                            emb, p=2, dim=1)
                    all_embeddings.extend(emb.tolist())
                return all_embeddings

        cls = _ClsPoolingEncoder
    else:
        cls = HuggingFaceEncoder
    # score_threshold=0.0: semantic-router сам нічого не фільтрує -- поріг
    # застосовує vector_route() нижче, щоб він був ОДНИМ числом у нашому
    # коді (з коментарем про походження), а не розсипаним по маршрутах.
    return cls(name=hf_name, device="cpu", score_threshold=0.0,
               model_kwargs=spec["model_kwargs"])


def _build_router(encoder=None, encoder_name=None):
    """SemanticRouter з маршрутами з каталогу. Будується один раз на процес
    (див. _get_router); окремий параметр encoder -- для тестів і заміру."""
    from semantic_router import Route
    from semantic_router.routers import SemanticRouter

    name = encoder_name or ENCODER_NAME
    spec = ENCODER_SPECS[name]
    routes = [
        Route(name=tid,
              utterances=[spec["passage_prefix"] + u for u in examples],
              score_threshold=0.0)
        for tid, examples in catalog_routes()
    ]
    if encoder is None:
        encoder = _make_encoder(name)
    # auto_sync="local": індекс (LocalIndex) наповнюється одразу при
    # створенні -- один раз при старті, далі лише запити
    return SemanticRouter(encoder=encoder, routes=routes,
                          aggregation=AGGREGATION, auto_sync="local")


def _get_router():
    """Router або None (деградація). Падіння запам'ятовується -- не смикаємо
    диск/мережу на кожне питання."""
    global _ROUTER, _FAILED
    with _LOCK:
        if _ROUTER is not None:
            return _ROUTER
        if _FAILED:
            return None
        try:
            _ROUTER = _build_router()
        except Exception as exc:
            _FAILED = True
            logger.warning(
                "Векторний ярус вимкнено (encoder %s не завантажився: %s). "
                "Чат працює без нього: правила -> модель -> фолбек. "
                "Це деградація швидкості, не падіння.",
                ENCODER_NAME, exc)
            return None
        return _ROUTER


def warm():
    """Збудувати роутер при старті апки (щоб перший користувач не чекав
    завантаження ваг). Повертає True, якщо ярус живий."""
    return _get_router() is not None


def vector_route(question):
    """-> (template_id | None, score). None -- нижче порога або ярус
    вимкнено: питання їде далі (модель -> фолбек), тихої неправильної
    відповіді не буде (впевнено-неправильний маршрут гірший за фолбек --
    критерій 3.3 плану)."""
    router = _get_router()
    if router is None:
        return None, 0.0
    spec = ENCODER_SPECS[ENCODER_NAME]
    try:
        choice = router(spec["query_prefix"] + question)
    except Exception as exc:
        logger.warning("Векторний ярус: помилка запиту (%s)", exc)
        return None, 0.0
    if choice is None or not getattr(choice, "name", None):
        return None, 0.0
    score = float(choice.similarity_score or 0.0)
    if score < THRESHOLD:
        return None, score
    return choice.name, score
