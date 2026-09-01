#!/usr/bin/env python3
"""Генератор синтетичних документів.

Одна команда — і в output/ лежить набір документів за травень 2026,
до кожного є картинка і файл із правильними відповідями.

Що робить:
  1. читає налаштування.yaml і data/unit_roster.csv;
  2. складає план набору: правильні документи, зіпсовані, пари «документ
     скасовує документ»;
  3. підставляє значення в бланки з мітками (templates/*_мітки.docx);
  4. .docx -> .pdf (LibreOffice) -> .png (pdftoppm);
  5. пише .json на кожен документ, ЕТАЛОН.csv і КАЛЕНДАР.csv на весь набір.

Реєстр data/unit_roster.csv тільки читається, ніколи не змінюється.

Відмінювання прізвищ не робиться: бібліотеки відмінювання в системі немає,
нових залежностей не ставимо. Прізвища йдуть у називному відмінку — так
друкують і в реальних документах.
"""

import copy
import csv
import json
import random
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    import yaml
    from docx import Document
    from docx.text.paragraph import Paragraph
except ImportError as error:
    sys.exit(f"Немає бібліотеки: {error}. Потрібні python-docx і pyyaml.")

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ROOT = Path(__file__).resolve().parent
SOFFICE = "/opt/homebrew/bin/soffice"
PDFTOPPM = "/opt/homebrew/bin/pdftoppm"
PDFTOTEXT = "/opt/homebrew/bin/pdftotext"

MONTHS = {
    1: "січня", 2: "лютого", 3: "березня", 4: "квітня", 5: "травня", 6: "червня",
    7: "липня", 8: "серпня", 9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня",
}

NUMBER_WORDS = {
    1: "один", 2: "два", 3: "три", 4: "чотири", 5: "п’ять", 6: "шість", 7: "сім",
    8: "вісім", 9: "дев’ять", 10: "десять", 11: "одинадцять", 12: "дванадцять",
    13: "тринадцять", 14: "чотирнадцять", 15: "п’ятнадцять", 16: "шістнадцять",
    17: "сімнадцять", 18: "вісімнадцять", 19: "дев’ятнадцять", 20: "двадцять",
    21: "двадцять один", 22: "двадцять два", 23: "двадцять три", 24: "двадцять чотири",
    25: "двадцять п’ять", 26: "двадцять шість", 27: "двадцять сім", 28: "двадцять вісім",
    29: "двадцять дев’ять", 30: "тридцять",
}

# Чим підміняються цифри у ваді «шум у цифрах»
OCR_NOISE = {"0": "О", "3": "З", "6": "б", "1": "І"}


# ---------------------------------------------------------------- допоміжне

def iso(day):
    return day.isoformat() if day else ""


def dotted(day):
    return f"{day.day:02d}.{day.month:02d}.{day.year}" if day else ""


def day_parts(day):
    """(«04», «травня», «26») — так дати друкуються в бланку."""
    if not day:
        return "", "", ""
    return f"{day.day:02d}", MONTHS[day.month], f"{day.year % 100:02d}"


def words(count):
    return NUMBER_WORDS.get(count, str(count))


def add_noise(text):
    return "".join(OCR_NOISE.get(char, char) for char in text)


def short_name(rank, last_name, first_name, patronymic):
    initials = f"{first_name[:1]}.{patronymic[:1]}." if first_name else ""
    return f"{rank} {last_name.upper()} {initials}".strip()


def full_name(rank, last_name, first_name, patronymic):
    return f"{rank} {last_name.upper()} {first_name} {patronymic}".strip()


def companions_text(person):
    """Супутники у відпустці — з колонки relatives_info."""
    raw = (person["relatives_info"] or "").strip()
    if not raw or raw.startswith("неодружений"):
        return "—"
    parts = []
    spouse = re.search(r"дружина/чоловік — ([^,]+)", raw)
    if spouse:
        word = "дружина" if person["gender"] == "чоловіча" else "чоловік"
        parts.append(f"{word} {person['last_name']} {spouse.group(1).strip()[:1]}.")
    children = re.search(r"діти — (\d+)", raw)
    if children:
        parts.append(f"діти — {children.group(1)}")
    return ", ".join(parts) if parts else "—"


# ---------------------------------------------------------------- заповнення бланка

def walk(element, doc):
    """Усі абзаци документа в порядку обходу — тіло і клітинки таблиць."""
    out = []
    for child in element:
        tag = child.tag.replace(W, "")
        if tag == "p":
            out.append(Paragraph(child, doc))
        elif tag == "tbl":
            for row in child.findall(W + "tr"):
                for cell in row.findall(W + "tc"):
                    out += walk(cell, doc)
    return out


def fill_template(template_path, values, target_path):
    """Підставляє значення замість міток {{...}}. Невідомі мітки прибирає."""
    doc = Document(str(template_path))
    pattern = re.compile(r"\{\{(\w+)\}\}")
    for paragraph in walk(doc.element.body, doc):
        for run in paragraph.runs:
            if "{{" in run.text:
                run.text = pattern.sub(lambda m: values.get(m.group(1), ""), run.text)
    doc.save(str(target_path))


# ---------------------------------------------------------------- складання набору

class Builder:
    def __init__(self, config, roster):
        self.config = config
        self.rng = random.Random(config["seed"])
        self.year = config["year"]
        self.month = config["month"]
        self.unit = config["unit_number"]
        self.home_city = f"м. {config['unit_city']}"
        self.signer = config["signer"]
        self.pool = list(roster)
        self.rng.shuffle(self.pool)
        self.cursor = 0
        self.leave_counter = 0
        self.trip_counter = 0
        self.leave_number = 100
        self.trip_number = 200

    # -- ресурси

    def take_person(self, gender=None):
        """Наступна людина з перемішаного реєстру. gender — узяти тільки таку стать."""
        while self.cursor < len(self.pool):
            person = self.pool[self.cursor]
            self.cursor += 1
            if gender is None or person["gender"] == gender:
                return person
        sys.exit("У реєстрі забракло людей на набір. Зменш counts у налаштування.yaml")

    def next_id(self, kind):
        if kind == "leave":
            self.leave_counter += 1
            return f"LEAVE-{self.leave_counter:03d}"
        self.trip_counter += 1
        return f"TRIP-{self.trip_counter:03d}"

    def next_number(self, kind):
        if kind == "leave":
            self.leave_number += self.rng.randint(1, 7)
            return str(self.leave_number)
        self.trip_number += self.rng.randint(1, 7)
        return str(self.trip_number)

    def day(self, low, high):
        return date(self.year, self.month, self.rng.randint(low, high))

    def first_day(self):
        return date(self.year, self.month, 1)

    def issued(self, start):
        """Дата видачі — за 1–3 дні до початку, але не раніше 1 числа місяця.

        ТЗ: усі дати в межах місяця набору. На перші дні місяця запас
        у 1–3 дні впав би на попередній місяць, тому дата підтягується.
        """
        return max(start - timedelta(days=self.rng.randint(1, 3)), self.first_day())

    # -- документи

    def leave(self, person, start, days, category="правильний", **extra):
        end = start + timedelta(days=days - 1)
        doc = {
            "id": self.next_id("leave"),
            "kind": "leave",
            "type": "відпускний квиток",
            "template": "Додаток 30",
            "category": category,
            "defect": extra.get("defect"),
            "pair": extra.get("pair"),
            "effective": extra.get("effective", True),
            "in_calendar": extra.get("in_calendar", True),
            "note": extra.get("note", ""),
            "person": person,
            "number": extra.get("number") or self.next_number("leave"),
            "issue_date": self.issued(start),
            "start": start,
            "end": end,
            "return": end + timedelta(days=1),
            "days": days,
            "place": extra.get("place") or self.rng.choice(self.config["cities"]),
            "leave_type": extra.get("leave_type") or self.rng.choice(self.config["leave_types"]),
            "vpd": self.rng.choice([f"{self.rng.randint(1000, 9999)}/26", "не видавались"]),
        }
        doc["companions"] = companions_text(person) if person["in_roster"] else "—"
        return doc

    def trip(self, person, start, days, category="правильний", **extra):
        end = start + timedelta(days=days - 1)
        doc = {
            "id": self.next_id("trip"),
            "kind": "trip",
            "type": "посвідчення про відрядження",
            "template": "Додаток 28",
            "category": category,
            "defect": extra.get("defect"),
            "pair": extra.get("pair"),
            "effective": extra.get("effective", True),
            "in_calendar": extra.get("in_calendar", True),
            "note": extra.get("note", ""),
            "person": person,
            "number": extra.get("number") or self.next_number("trip"),
            "issue_date": self.issued(start),
            "start": start,
            "end": end,
            "return": end,
            "days": days,
            "place": extra.get("place") or self.rng.choice(self.config["cities"]),
            "org": extra.get("org") or self.rng.choice(self.config["trip_orgs"]),
            "purpose": extra.get("purpose") or self.rng.choice(self.config["trip_purposes"]),
            "order_number": str(self.rng.randint(40, 400)),
        }
        doc["order_date"] = max(doc["issue_date"] - timedelta(days=1), self.first_day())
        doc["companions"] = "—"
        return doc

    # -- план набору

    def build(self):
        docs = []
        counts = self.config["counts"]
        defects = self.config["defects"]
        pairs = self.config["pairs"]

        # Перший документ кожного типу — на жінку. Інакше на випадковому seed
        # у наборі може не бути жодного жіночого роду, і закінчення
        # «звільнена / зобов’язана / відрядженій» лишаться неперевіреними.
        for index in range(counts["leave_valid"]):
            person = self.take_person("жіноча" if index == 0 else None)
            docs.append(self.leave(person, self.day(2, 18), self.rng.randint(5, 14)))

        for index in range(counts["trip_valid"]):
            person = self.take_person("жіноча" if index == 0 else None)
            docs.append(self.trip(person, self.day(2, 24), self.rng.randint(2, 7)))

        if defects["empty_fields"]:
            docs.append(self.leave(
                self.take_person(), self.day(6, 12), self.rng.randint(7, 12),
                category="зіпсований", defect="empty_fields",
                note="У документі не заповнені звання, ПІБ і дати відпустки.",
            ))

        if defects["unknown_person"]:
            stranger = {
                "service_id": "", "rank": "старший прапорщик", "last_name": "Ковердюк",
                "first_name": "Мирослав", "patronymic": "Ілларіонович",
                "full_name": "Ковердюк Мирослав Ілларіонович", "gender": "чоловіча",
                "position_title": "начальник складу озброєння", "subdivision": "Взвод забезпечення",
                "relatives_info": "неодружений(а)", "in_roster": False,
            }
            docs.append(self.leave(
                stranger, self.day(8, 20), self.rng.randint(5, 10),
                category="зіпсований", defect="unknown_person",
                note="Такої людини в реєстрі немає — ані прізвища, ані звання.",
            ))

        if defects["swapped_dates"]:
            docs.append(self.trip(
                self.take_person(), self.day(8, 20), self.rng.randint(3, 6),
                category="зіпсований", defect="swapped_dates",
                note="У документі кінець відрядження надрукований раніше за початок.",
            ))

        if defects["ocr_noise"]:
            docs.append(self.trip(
                self.take_person(), self.day(2, 20), self.rng.randint(3, 6),
                category="зіпсований", defect="ocr_noise",
                note="У номері й датах літери замість цифр: О замість 0, б замість 6, З замість 3.",
            ))

        if pairs["leave_interrupted"]:
            person = self.take_person()
            start = self.day(4, 10)
            first = self.leave(
                person, start, 14, category="пара",
                pair={"group": "П1", "role": "перший", "relation": "відпустку перервано"},
                effective=False, in_calendar=False,
                note="Первинний квиток. Скасований — людину відкликали з відпустки.",
            )
            second = self.leave(
                person, start, 6, category="пара",
                place=first["place"], leave_type=first["leave_type"],
                pair={"group": "П1", "role": "чинний", "relation": "відпустку перервано",
                      "replaces": first["id"]},
                note="Чинний квиток. Відпустку перервано, строк скорочено.",
            )
            recalled = "відкликана" if person["gender"] == "жіноча" else "відкликаний"
            second["leave_type"] = f"{first['leave_type']} (перервана, {recalled} з відпустки)"
            docs += [first, second]

        if pairs["trip_reissued"]:
            person = self.take_person()
            first = self.trip(
                person, self.day(6, 12), 5, category="пара",
                pair={"group": "П2", "role": "перший", "relation": "відрядження переоформлене"},
                effective=False, in_calendar=False,
                note="Первинне посвідчення. Скасоване — відрядження переоформлене на інші дати.",
            )
            second = self.trip(
                person, first["end"] + timedelta(days=5), 4, category="пара",
                place=first["place"], org=first["org"], purpose=first["purpose"],
                pair={"group": "П2", "role": "чинний", "relation": "відрядження переоформлене",
                      "replaces": first["id"]},
                note="Чинне посвідчення. Видане замість попереднього, дати інші.",
            )
            second["purpose"] = f"{first['purpose']} (переоформлено замість посвідчення № {first['number']})"
            docs += [first, second]

        if pairs["leave_cancelled"]:
            person = self.take_person()
            first = self.leave(
                person, self.day(12, 18), 10, category="пара",
                pair={"group": "П3", "role": "перший", "relation": "квиток анульований"},
                effective=False, in_calendar=False,
                note="Анульований квиток. Виписаний новий із тим самим номером.",
            )
            second = self.leave(
                person, first["start"] + timedelta(days=4), 7, category="пара",
                number=first["number"], place=first["place"],
                pair={"group": "П3", "role": "чинний", "relation": "квиток анульований",
                      "replaces": first["id"]},
                note="Чинний квиток. Той самий номер, інші дати.",
            )
            second["leave_type"] = f"{second['leave_type']} (виданий замість анульованого квитка № {first['number']})"
            docs += [first, second]

        return docs


# ---------------------------------------------------------------- значення для бланка

def leave_values(doc, config, signer):
    person = doc["person"]
    female = person["gender"] == "жіноча"
    start_d, start_m, start_y = day_parts(doc["start"])
    end_d, end_m, end_y = day_parts(doc["end"])
    ret_d, ret_m, ret_y = day_parts(doc["return"])
    values = {
        "UNIT": config["unit_number"],
        "DOC_NUMBER": doc["number"],
        "ISSUE_DATE": dotted(doc["issue_date"]),
        "PERSON_FULL": full_name(person["rank"], person["last_name"],
                                 person["first_name"], person["patronymic"]),
        "PERSON_SHORT": short_name(person["rank"], person["last_name"],
                                   person["first_name"], person["patronymic"]),
        "RELEASED": "звільнена" if female else "звільнений",
        "OBLIGED": "зобов’язана" if female else "зобов’язаний",
        "LEAVE_TYPE": doc["leave_type"],
        "LEAVE_PLACE": doc["place"],
        "DAYS_WORDS": words(doc["days"]),
        "START_D": start_d, "START_M": start_m, "START_Y": start_y,
        "END_D": end_d, "END_M": end_m, "END_Y": end_y,
        "RET_D": ret_d, "RET_M": ret_m, "RET_Y": ret_y,
        "RETURN_UNIT": f"військова частина {config['unit_number']}",
        "VPD": doc["vpd"],
        "COMPANIONS": doc["companions"],
        "SIGNER_RANK": signer["rank"],
        "SIGNER_NAME": signer["name"],
    }
    return values


def trip_values(doc, config, signer):
    person = doc["person"]
    female = person["gender"] == "жіноча"
    start_d, start_m, start_y = day_parts(doc["start"])
    end_d, end_m, end_y = day_parts(doc["end"])
    values = {
        "UNIT": config["unit_number"],
        "DOC_NUMBER": doc["number"],
        "ISSUE_DATE": dotted(doc["issue_date"]),
        "PERSON_FULL": full_name(person["rank"], person["last_name"],
                                 person["first_name"], person["patronymic"]),
        "POSITION": f"{person['position_title']}, {person['subdivision']}, "
                    f"військова частина {config['unit_number']}",
        "SENT_TO": "відрядженій" if female else "відрядженому",
        "LEFT": "Вибула" if female else "Вибув",
        "ARRIVED": "Прибула" if female else "Прибув",
        "DEST": doc["place"],
        "DEST_ORG": doc["org"],
        "DAYS": str(doc["days"]),
        "START_D": start_d, "START_M": start_m, "START_Y": start_y,
        "END_D": end_d, "END_M": end_m, "END_Y": end_y,
        "PURPOSE": doc["purpose"],
        "ORDER_BASIS": f"наказ командира військової частини {config['unit_number']} "
                       f"від {dotted(doc['order_date'])} № {doc['order_number']}",
        "HOME_CITY": f"м. {config['unit_city']}",
        "DEST_CITY": doc["place"],
        "DEP1_D": start_d, "DEP1_M": start_m, "DEP1_Y": start_y,
        "ARR1_D": start_d, "ARR1_M": start_m, "ARR1_Y": start_y,
        "DEP2_D": end_d, "DEP2_M": end_m, "DEP2_Y": end_y,
        "ARR2_D": end_d, "ARR2_M": end_m, "ARR2_Y": end_y,
        "MEAL_FROM": f"{doc['start'].day:02d}.{doc['start'].month:02d}",
        "MEAL_TO": f"{doc['end'].day:02d}.{doc['end'].month:02d}",
        "MEAL_Y": start_y,
        "SIGNER_RANK": signer["rank"],
        "SIGNER_NAME": signer["name"],
    }
    return values


def apply_defect(doc, values):
    """Псує вже готові значення — по одній ваді на документ."""
    defect = doc["defect"]
    if defect == "empty_fields":
        for key in ("PERSON_FULL", "PERSON_SHORT", "DAYS_WORDS",
                    "START_D", "START_M", "START_Y", "END_D", "END_M", "END_Y",
                    "RET_D", "RET_M", "RET_Y"):
            values[key] = ""
    elif defect == "swapped_dates":
        for a, b in (("START_D", "END_D"), ("START_M", "END_M"), ("START_Y", "END_Y")):
            values[a], values[b] = values[b], values[a]
        values["DEP1_D"], values["DEP2_D"] = values["DEP2_D"], values["DEP1_D"]
        values["ARR1_D"], values["ARR2_D"] = values["ARR2_D"], values["ARR1_D"]
        # той самий перевернутий діапазон і в періоді харчування — як у реальних помилках
        values["MEAL_FROM"], values["MEAL_TO"] = values["MEAL_TO"], values["MEAL_FROM"]
    elif defect == "ocr_noise":
        for key in ("DOC_NUMBER", "ISSUE_DATE", "DAYS", "ORDER_BASIS",
                    "START_D", "START_Y", "END_D", "END_Y",
                    "DEP1_D", "DEP1_Y", "ARR1_D", "ARR1_Y",
                    "DEP2_D", "DEP2_Y", "ARR2_D", "ARR2_Y",
                    "MEAL_FROM", "MEAL_TO", "MEAL_Y"):
            if key in values:
                values[key] = add_noise(values[key])
    return values


# ---------------------------------------------------------------- вихідні файли

def document_json(doc, values):
    person = doc["person"]
    return {
        "id": doc["id"],
        "тип": doc["type"],
        "бланк": doc["template"],
        "категорія": doc["category"],
        "вада": doc["defect"],
        "пара": doc["pair"],
        "чинний": doc["effective"],
        "примітка": doc["note"],
        "людина": {
            "service_id": person["service_id"],
            "звання": person["rank"],
            "ПІБ": person["full_name"],
            "стать": person["gender"],
            "посада": person["position_title"],
            "підрозділ": person["subdivision"],
            "є_в_реєстрі": person["in_roster"],
        },
        "надруковано": values,
        "правильні_відповіді": {
            "номер_документа": doc["number"],
            "дата_видачі": iso(doc["issue_date"]),
            "початок": iso(doc["start"]),
            "кінець": iso(doc["end"]),
            "повернення": iso(doc["return"]),
            "днів": doc["days"],
            "місце": doc["place"],
            "підстава": doc.get("leave_type") or doc.get("purpose"),
            "організація": doc.get("org", ""),
            "супутники": doc["companions"],
            "військова_частина": values["UNIT"],
            "підписант": f"{values['SIGNER_RANK']} {values['SIGNER_NAME']}",
        },
    }


def write_reference(docs, path):
    """ЕТАЛОН.csv — усі документи одним списком."""
    columns = ["документ", "тип", "категорія", "вада", "пара", "роль_у_парі", "скасовує",
               "service_id", "ПІБ", "звання", "підрозділ", "номер_документа", "дата_видачі",
               "початок", "кінець", "повернення", "днів", "місце", "чинний"]
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for doc in docs:
            pair = doc["pair"] or {}
            person = doc["person"]
            writer.writerow([
                doc["id"], doc["type"], doc["category"], doc["defect"] or "",
                pair.get("relation", ""), pair.get("role", ""), pair.get("replaces", ""),
                person["service_id"], person["full_name"], person["rank"], person["subdivision"],
                doc["number"], iso(doc["issue_date"]), iso(doc["start"]), iso(doc["end"]),
                iso(doc["return"]), doc["days"], doc["place"],
                "так" if doc["effective"] else "ні",
            ])


def write_calendar(docs, roster, config, path):
    """КАЛЕНДАР.csv — хто де перебуває кожного дня місяця.

    Рядок на кожен день місяця для кожної людини реєстру, плюс вигадана людина
    зі зіпсованого документа. Хто не у відпустці й не у відрядженні — «у частині»,
    тож календар відповідає і на «хто був поза частиною», і на «хто був у частині».
    Статус береться з чинного документа: скасовані документи в календар не йдуть.
    Зіпсовані документи йдуть за справжніми датами — календар це правда набору,
    а документ може її спотворювати.
    """
    year, month = config["year"], config["month"]
    first = date(year, month, 1)
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]

    people = {person["service_id"] or person["full_name"]: person for person in roster}
    absences = {}
    for doc in docs:
        person = doc["person"]
        key = person["service_id"] or person["full_name"]
        people.setdefault(key, person)
        if not doc["in_calendar"]:
            continue
        status = "відпустка" if doc["kind"] == "leave" else "відрядження"
        current = doc["start"]
        while current <= doc["end"]:
            if first <= current <= last:
                absences[(key, current)] = (status, doc["place"], doc["id"], doc["defect"])
            current += timedelta(days=1)

    columns = ["дата", "service_id", "ПІБ", "звання", "підрозділ", "статус", "місце",
               "документ", "примітка"]
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for day in days:
            for key, person in sorted(people.items()):
                status, place, doc_id, defect = absences.get(
                    (key, day), ("у частині", f"м. {config['unit_city']}", "", None))
                note = ""
                if not person["in_roster"]:
                    note = "особи немає в реєстрі"
                elif defect == "swapped_dates":
                    note = "у документі дати переплутані"
                elif defect == "empty_fields":
                    note = "у документі дати не заповнені"
                elif defect == "ocr_noise":
                    note = "у документі шум у цифрах"
                writer.writerow([
                    day.isoformat(), person["service_id"], person["full_name"],
                    person["rank"], person["subdivision"], status, place, doc_id, note,
                ])
    return len(days) * len(people)


# ---------------------------------------------------------------- конвертація

def check_tools():
    """Перевіряє зовнішні програми до генерації — щоб помилка була зрозуміла."""
    needed = [
        (SOFFICE, "LibreOffice", "brew install --cask libreoffice"),
        (PDFTOPPM, "pdftoppm (poppler)", "brew install poppler"),
        (PDFTOTEXT, "pdftotext (poppler)", "brew install poppler"),
    ]
    missing = [(name, fix) for path, name, fix in needed
               if not (Path(path).exists() or shutil.which(Path(path).name))]
    if missing:
        lines = ["Немає програм, без яких не буде PDF і картинок:"]
        lines += [f"  {name} — постав так: {fix}" for name, fix in missing]
        sys.exit("\n".join(lines))


def tool(path):
    """Шлях до програми: спершу очікуваний, інакше з PATH."""
    return path if Path(path).exists() else shutil.which(Path(path).name)


def convert_to_pdf(docx_paths, out_dir):
    profile = out_dir / ".soffice"
    command = [tool(SOFFICE), "--headless", f"-env:UserInstallation=file://{profile}",
               "--convert-to", "pdf", "--outdir", str(out_dir)]
    command += [str(path) for path in docx_paths]
    subprocess.run(command, check=True, capture_output=True)
    shutil.rmtree(profile, ignore_errors=True)


def page_is_blank(pdf_path, number):
    result = subprocess.run(
        [tool(PDFTOTEXT), "-f", str(number), "-l", str(number), str(pdf_path), "-"],
        capture_output=True, text=True, check=True)
    return not result.stdout.strip()


def convert_to_png(pdf_path, dpi):
    """Робить картинки. Порожні сторінки не зберігає — від них немає користі."""
    prefix = pdf_path.with_suffix("")
    subprocess.run([tool(PDFTOPPM), "-r", str(dpi), "-png", str(pdf_path), str(prefix)],
                   check=True, capture_output=True)
    pages = sorted(prefix.parent.glob(f"{prefix.name}-*.png"),
                   key=lambda path: int(path.stem.rsplit("-", 1)[1]))
    made = []
    for page in pages:
        number = int(page.stem.rsplit("-", 1)[1])
        if page_is_blank(pdf_path, number):
            page.unlink()
            continue
        position = len(made) + 1
        if position == 1:
            target = prefix.with_suffix(".png")
        elif position == 2:
            target = prefix.parent / f"{prefix.name}_зворот.png"
        else:
            target = prefix.parent / f"{prefix.name}_стор{position}.png"
        page.replace(target)
        made.append(target)
    return made


# ---------------------------------------------------------------- головне

def load_roster(path):
    with open(path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    people = []
    for row in rows:
        person = dict(row)
        person["in_roster"] = True
        people.append(person)
    return people


def main():
    config = yaml.safe_load((ROOT / "налаштування.yaml").read_text(encoding="utf-8"))
    roster = load_roster(ROOT / "data" / "unit_roster.csv")
    check_tools()

    leave_template = ROOT / "templates" / "відпускний_квиток_мітки.docx"
    trip_template = ROOT / "templates" / "посвідчення_відрядження_мітки.docx"
    for path in (leave_template, trip_template):
        if not path.exists():
            sys.exit(f"Немає бланка з мітками: {path.name}. Запусти prepare_templates.py")

    out_dir = ROOT / config["output_dir"]
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    print(f"Реєстр: {len(roster)} людей. Номер набору (seed): {config['seed']}.")
    docs = Builder(config, roster).build()
    print(f"У наборі {len(docs)} документів. Заповнюю бланки…")

    docx_paths = []
    for doc in docs:
        if doc["kind"] == "leave":
            values = leave_values(doc, config, config["signer"])
            template = leave_template
        else:
            values = trip_values(doc, config, config["signer"])
            template = trip_template
        values = apply_defect(doc, values)
        doc["values"] = values
        target = out_dir / f"{doc['id']}.docx"
        fill_template(template, values, target)
        docx_paths.append(target)
        (out_dir / f"{doc['id']}.json").write_text(
            json.dumps(document_json(doc, values), ensure_ascii=False, indent=2),
            encoding="utf-8")

    print("Роблю PDF (LibreOffice)…")
    convert_to_pdf(docx_paths, out_dir)

    print(f"Роблю картинки {config['dpi']} dpi…")
    overflow = []
    for doc in docs:
        pages = convert_to_png(out_dir / f"{doc['id']}.pdf", config["dpi"])
        if len(pages) != 2:
            overflow.append((doc["id"], len(pages)))
    if overflow:
        # Верстка бланка розрахована на два боки. Три сторінки — значить довгий
        # текст (мета відрядження, посада, вид відпустки) виштовхнув зворот.
        print("  УВАГА: не два боки — довгий текст поламав верстку:")
        for doc_id, count in overflow:
            print(f"    {doc_id}: сторінок {count}. Скороти текст у налаштування.yaml")

    write_reference(docs, out_dir / "ЕТАЛОН.csv")
    rows = write_calendar(docs, roster, config, out_dir / "КАЛЕНДАР.csv")

    print()
    print(f"Готово. {out_dir}")
    print(f"  документів: {len(docs)}"
          f"  (відпустки {sum(1 for d in docs if d['kind'] == 'leave')},"
          f" відрядження {sum(1 for d in docs if d['kind'] == 'trip')})")
    print(f"  ЕТАЛОН.csv — {len(docs)} рядків")
    print(f"  КАЛЕНДАР.csv — {rows} рядків")


if __name__ == "__main__":
    main()
