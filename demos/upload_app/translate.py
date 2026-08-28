# -*- coding: utf-8 -*-
"""Машинний переклад українською→англійською для демо: локальна модель + кеш.

## Чому це з'явилось і що змінилось у рішенні

Спершу переклад робив словник, який я написала руками. Аня 28.08 це відкинула,
і аргументи named прямо: **це демо, дані синтетичні, сторінка вже відкрита**, а
на демо будуть іноземці — тобто перекласти треба ВСЕ, включно з нормативкою, а
не рівно те, на що я знайшла час.

## Що обрано і що відкинуто (research 28.08)

| варіант | чому не він |
|---|---|
| вбудований перекладач браузера (`window.Translator`, Chrome 138+) | працює **лише в захищеному контексті** (HTTPS). Наша сторінка — HTTP на IP, тобто API там недоступний за побудовою |
| віджет Google Translate (`element.js`) | офіційно припинений для нових сайтів із 2019, лишився для некомерційних; «deprecated but functional» — тримати демо на цьому не варто |
| хмарний API (Google/DeepL/Azure) | ключ у сторінці = відданий ключ; серверний проксі означав би залежність демо від чужого сервісу в момент показу |
| `opus-mt-uk-en` (MarianMT) | потребує `sentencepiece`, якого в venv немає, а ставити пакети на спільний сервер — окреме рішення (правило Антона) |
| **`facebook/nllb-200-distilled-600M`** | **обрано**: швидкий токенізатор (нічого ставити не треба), спеціалізована модель перекладу, детермінована при beam search, працює локально |

Заміряно на сервері: завантаження ~24 с, переклад п'яти рядків 3.3 с на
процесорі (на карті швидше). Юридичне речення перекладає добре.

## Головне про конструкцію: КЕШ -- це продукт, модель -- генератор

На демо не має бути жодної залежності від того, чи піднялась модель і скільком
вона думає. Тому:

  * **кеш на диску -- перше й основне.** Він у git, він і є перекладом;
  * **модель -- необов'язкова.** Вмикається змінною `TRANSLATE_MODEL=1` і
    потрібна лише щоб ПОПОВНИТИ кеш (скриптом заздалегідь або на льоту);
  * **немає ні кешу, ні моделі -- рядок віддається як є.** Сторінка лишається
    українською в цьому місці, але не ламається й не бреше.

## Що НЕ перекладається навіть тут

Рівно те, що зламалось би від перекладу: SQL у блоці «джерело» (він мусить
збігатися з тим, що виконали) і шестизначні номери звернень. Нормативні цитати
й назви законів ПЕРЕКЛАДАЮТЬСЯ -- рішення Ані 28.08 для демо; у пілоті з
реальними даними це рішення треба переглянути, і саме тому воно записане тут, а
не заховане в коді.
"""
import io
import json
import os
import re
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(APP_DIR))

#: Кеш у git навмисно: переклад, який живе лише в чиємусь процесі, на демо не
#: існує. Дані синтетичні (правило проєкту дозволяє `data/eval/`).
CACHE_PATH = os.environ.get(
    "TRANSLATION_CACHE",
    os.path.join(PROJECT_ROOT, "data", "eval", "translation-cache.json"))

MODEL_NAME = os.environ.get("TRANSLATE_MODEL_NAME",
                            "facebook/nllb-200-distilled-600M")

#: Модель за замовчуванням ВИМКНЕНА: на демо працює кеш, і жодного очікування
#: моделі бути не має. Вмикається явно, коли кеш поповнюють.
MODEL_ENABLED = os.environ.get("TRANSLATE_MODEL", "0").strip().lower() in (
    "1", "true", "yes", "on")

#: Скільком рядків перекладаємо одним викликом. Більше -- швидше, але довший
#: рядок у батчі тягне за собою всі інші (padding).
BATCH = 16

_cache = None
_cache_lock = threading.Lock()
_model = None
_tok = None
_model_lock = threading.Lock()

#: Рядки, які не перекладаються НІКОЛИ. Не з обережності, а тому що переклад їх
#: зламав би: SQL мусить збігатися з виконаним запитом, номер звернення -- ключ
#: у журналі.
_SKIP = (
    re.compile(r"\bSELECT\b|\bFROM\b|\bJOIN\b|\bWHERE\b|%\(|::"),
    re.compile(r"^[0-9a-f]{6}$"),
)


def _skip(text):
    t = (text or "").strip()
    if len(t) < 2:
        return True
    if not re.search(r"[А-ЯІЇЄҐа-яіїєґ]", t):
        return True                      # уже не українською -- нема що робити
    return any(rx.search(t) for rx in _SKIP)


def cache():
    """Кеш перекладів. Читається один раз на процес."""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                try:
                    with io.open(CACHE_PATH, encoding="utf-8") as fh:
                        _cache = json.load(fh)
                except (OSError, ValueError):
                    _cache = {}
    return _cache


def save_cache():
    """Записати кеш. Пишемо через тимчасовий файл: обірваний запис зробив би
    кеш непрочитним, а це на демо означало б українську сторінку."""
    data = cache()
    os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, CACHE_PATH)


def _load_model():
    """Підняти модель. -> (tokenizer, model) або (None, None)."""
    global _model, _tok
    if _model is not None or not MODEL_ENABLED:
        return _tok, _model
    with _model_lock:
        if _model is None:
            try:
                from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
                _tok = AutoTokenizer.from_pretrained(MODEL_NAME,
                                                     src_lang="ukr_Cyrl")
                _model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME)
                try:
                    import torch
                    if torch.cuda.is_available():
                        _model = _model.to("cuda")
                except Exception:
                    pass                 # процесор теж годиться, просто довше
            except Exception:
                # Немає моделі -- працює кеш. Це деградація, не падіння.
                _model, _tok = None, None
    return _tok, _model


def _machine(texts):
    """Перекласти моделлю. -> {укр: англ} (лише те, що вдалося)."""
    tok, model = _load_model()
    if model is None:
        return {}
    out = {}
    eng = tok.convert_tokens_to_ids("eng_Latn")
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        try:
            ids = tok(chunk, return_tensors="pt", padding=True,
                      truncation=True, max_length=512)
            ids = {k: v.to(model.device) for k, v in ids.items()}
            # num_beams=4 і жодного сімплінгу: переклад мусить бути ОДНАКОВИЙ
            # між прогонами, інакше кеш і жива відповідь розійдуться.
            gen = model.generate(**ids, forced_bos_token_id=eng,
                                 max_new_tokens=512, num_beams=4,
                                 do_sample=False)
            for src, dst in zip(chunk, tok.batch_decode(
                    gen, skip_special_tokens=True)):
                if dst and dst.strip():
                    out[src] = dst.strip()
        except Exception:
            continue                     # батч не вдався -- решта не винна
    return out


def translate(texts, allow_model=None):
    """-> {укр: англ} для тих рядків, які є в кеші (або переклала модель).

    Рядок, якого немає ні там, ні там, у відповідь НЕ потрапляє -- клієнт
    лишає його як є. Порожній переклад гірший за український текст: він
    виглядав би як поломка сторінки.
    """
    use_model = MODEL_ENABLED if allow_model is None else allow_model
    known = cache()
    result, missing = {}, []
    for t in texts or []:
        if _skip(t):
            continue
        if t in known:
            result[t] = known[t]
        else:
            missing.append(t)
    if missing and use_model:
        fresh = _machine(missing)
        if fresh:
            with _cache_lock:
                known.update(fresh)
            try:
                save_cache()
            except OSError:
                pass                     # кеш не записався -- переклад усе одно є
            result.update(fresh)
    return result
