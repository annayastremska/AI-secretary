"""Генерує фейкову штатну книжку підрозділу (300 людей) для синтетичних даних.

Колонки відповідають офіційній формі штатної книжки (звання, ПІБ, індекс
посади, дати й накази, вид служби, контракт, РНОКПП, документ, дата й місце
народження, стать, призов, освіта, родичі, додаткова інформація).

Це вміст ДОКУМЕНТА (як він виглядає на папері) — звання й посада тут є,
хоча в БД вони йдуть не як статичні колонки people, а як facts з історією
(дивись migration 60ea874484ed / 1283dc745daa): один рядок цього списку —
це стан "станом на зараз", а не єдине значення на все служби.

Запуск: .venv/Scripts/python.exe db/seeds/generate_unit_roster.py
Вихід: db/seeds/unit_roster.csv (детермінований, Faker.seed фіксований — щоб
той самий список відтворювався між прогонами і різні джерела синтетики
(АСКОД-потік, фото-потік) посилались на одних і тих самих людей).
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

SEED = 42
TOTAL = 300
OUT_PATH = Path(__file__).parent / "unit_roster.csv"

SERVICE_ENTRY_START = date(2022, 2, 24)
TODAY_REFERENCE = date(2026, 8, 1)  # фіксована "точка відліку", не datetime.now()

SUBDIVISIONS = [
    ("00", "Управління батальйону", 15),
    ("01", "1-ша механізована рота", 90),
    ("02", "2-га механізована рота", 90),
    ("03", "3-тя механізована рота", 90),
    ("04", "Взвод забезпечення", 15),
]

# (звання, вага) — грубо пірамідальний розподіл для підрозділу ~300 осіб
RANKS = [
    ("рядовий", 40),
    ("старший солдат", 15),
    ("молодший сержант", 10),
    ("сержант", 10),
    ("старший сержант", 8),
    ("старшина", 5),
    ("молодший лейтенант", 3),
    ("лейтенант", 3),
    ("старший лейтенант", 2),
    ("капітан", 2),
    ("майор", 1),
    ("підполковник", 0.7),
    ("полковник", 0.3),
]

ENLISTED_POSITIONS = [
    "стрілець", "кулеметник", "навідник", "водій", "сапер",
    "зв'язківець", "санітар-інструктор", "оператор БПЛА", "снайпер",
]
SERGEANT_POSITIONS = [
    "командир відділення", "заступник командира взводу", "старшина роти",
]
OFFICER_POSITIONS = [
    "командир взводу", "командир роти", "начальник штабу батальйону",
    "заступник командира батальйону", "командир батальйону",
]

SERGEANT_RANKS = {"молодший сержант", "сержант", "старший сержант", "старшина"}
OFFICER_RANKS = {
    "молодший лейтенант", "лейтенант", "старший лейтенант",
    "капітан", "майор", "підполковник", "полковник",
}

SERVICE_TYPES = [
    ("військова служба за мобілізацією", 55),
    ("військова служба за контрактом", 35),
    ("базова військова служба", 10),
]

ARRIVED_FROM = [
    "новобранець (мобілізація)",
    "Навчальний центр «Десна»",
    "121 окрема бригада ТрО",
    "Навчальний центр Сухопутних військ",
    "переведення з іншої військової частини",
    "запас (повторний призов)",
]

ORDER_ISSUERS = [
    "командир батальйону",
    "командир бригади",
    "начальник штабу батальйону",
    "командувач Сухопутних військ ЗСУ",
]

RECRUITMENT_CENTERS = [
    "Шевченківський РТЦК та СП м. Києва",
    "Личаківський РТЦК та СП м. Львова",
    "Соборний РТЦК та СП м. Дніпра",
    "Приморський РТЦК та СП м. Одеси",
    "Київський РТЦК та СП Полтавської області",
]

EDUCATION = [
    ("повна загальна середня", 25),
    ("професійно-технічна", 30),
    ("базова вища", 15),
    ("вища", 30),
]

ID_DOCUMENT_TYPES = [
    ("Паспорт громадянина України (книжечка)", 55),
    ("Паспорт громадянина України (ID-картка)", 45),
]

ADDITIONAL_INFO_OPTIONS = [
    ("", 70),
    ("має посвідчення водія категорії C", 10),
    ("проходить строкову медичну комісію", 8),
    ("володіє англійською мовою (розмовний рівень)", 7),
    ("має досвід роботи з БПЛА", 5),
]

UKR_LETTERS = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЮЯ"


def weighted_choice(rng: random.Random, options: list[tuple[str, float]]) -> str:
    values = [v for v, _ in options]
    weights = [w for _, w in options]
    return rng.choices(values, weights=weights, k=1)[0]


def pick_position_title(rank: str, rng: random.Random) -> str:
    if rank in OFFICER_RANKS:
        return rng.choice(OFFICER_POSITIONS)
    if rank in SERGEANT_RANKS:
        return rng.choice(SERGEANT_POSITIONS)
    return rng.choice(ENLISTED_POSITIONS)


def build_subdivision_slots() -> list[tuple[str, str]]:
    """Повертає [(код_підрозділу, назва_підрозділу), ...] по одному на людину."""
    result = []
    for code, name, count in SUBDIVISIONS:
        result.extend([(code, name)] * count)
    assert len(result) == TOTAL, f"subdivision counts must sum to {TOTAL}, got {len(result)}"
    return result


def make_order_number(rng: random.Random) -> str:
    return f"№{rng.randint(1, 999)}"


def make_id_document(rng: random.Random, fake: Faker) -> tuple[str, str, str]:
    doc_type = weighted_choice(rng, ID_DOCUMENT_TYPES)
    if "книжечка" in doc_type:
        series = rng.choice(UKR_LETTERS) + rng.choice(UKR_LETTERS)
        number = fake.numerify("######")
    else:
        series = ""
        number = fake.numerify("#########")
    return doc_type, series, number


def make_relatives_info(rng: random.Random, fake: Faker) -> str:
    if rng.random() < 0.3:
        return "неодружений(а)"
    spouse_name = fake.first_name_female() if rng.random() < 0.5 else fake.first_name_male()
    children = rng.choice([0, 0, 1, 1, 2, 3])
    if children == 0:
        return f"дружина/чоловік — {spouse_name}"
    return f"дружина/чоловік — {spouse_name}, діти — {children}"


def main() -> None:
    Faker.seed(SEED)
    rng = random.Random(SEED)
    fake = Faker("uk_UA")

    subdivision_slots = build_subdivision_slots()
    rng.shuffle(subdivision_slots)

    per_subdivision_counter: dict[str, int] = {}

    rows = []
    for i in range(1, TOTAL + 1):
        is_male = rng.random() < 0.9  # переважно чоловічий особовий склад
        if is_male:
            last_name = fake.last_name_male()
            first_name = fake.first_name_male()
            patronymic = fake.middle_name_male()
        else:
            last_name = fake.last_name_female()
            first_name = fake.first_name_female()
            patronymic = fake.middle_name_female()
        full_name = f"{last_name} {first_name} {patronymic}"

        rank = weighted_choice(rng, RANKS)
        position_title = pick_position_title(rank, rng)
        subdivision_code, subdivision_name = subdivision_slots[i - 1]
        per_subdivision_counter[subdivision_code] = per_subdivision_counter.get(subdivision_code, 0) + 1
        position_index = f"{subdivision_code}-{per_subdivision_counter[subdivision_code]:03d}"

        service_entry_date = fake.date_between(start_date=SERVICE_ENTRY_START, end_date=TODAY_REFERENCE)
        enrollment_date = service_entry_date + timedelta(days=rng.randint(0, 14))
        enrollment_order_date = enrollment_date + timedelta(days=rng.randint(0, 3))
        position_assigned_date = fake.date_between(start_date=enrollment_date, end_date=TODAY_REFERENCE)
        appointment_order_date = position_assigned_date + timedelta(days=rng.randint(0, 5))
        rank_order_date = fake.date_between(start_date=service_entry_date, end_date=TODAY_REFERENCE)

        service_type = weighted_choice(rng, SERVICE_TYPES)
        contract_start_date = ""
        contract_end_date = ""
        conscription_period = ""
        if service_type == "військова служба за контрактом":
            contract_start_date = service_entry_date.isoformat()
            contract_end_date = date(
                service_entry_date.year + rng.choice([1, 3, 5]),
                service_entry_date.month,
                service_entry_date.day,
            ).isoformat()
        elif service_type == "військова служба за мобілізацією":
            conscription_period = "на весь період мобілізації"
        else:
            conscription_period = "12 місяців"

        id_document_type, id_document_series, id_document_number = make_id_document(rng, fake)
        birth_date = fake.date_of_birth(minimum_age=20, maximum_age=55)

        rows.append({
            "service_id": f"UNIT-{i:04d}",
            "rank": rank,
            "last_name": last_name,
            "first_name": first_name,
            "patronymic": patronymic,
            "full_name": full_name,
            "gender": "чоловіча" if is_male else "жіноча",
            "position_index": position_index,
            "position_title": position_title,
            "subdivision": subdivision_name,
            "position_assigned_date": position_assigned_date.isoformat(),
            "arrived_from": rng.choice(ARRIVED_FROM),
            "enrollment_date": enrollment_date.isoformat(),
            "enrollment_order_date": enrollment_order_date.isoformat(),
            "enrollment_order_number": make_order_number(rng),
            "appointment_order_date": appointment_order_date.isoformat(),
            "appointment_order_issuer": rng.choice(ORDER_ISSUERS),
            "appointment_order_number": make_order_number(rng),
            "rank_order_date": rank_order_date.isoformat(),
            "rank_order_issuer": rng.choice(ORDER_ISSUERS),
            "rank_order_number": make_order_number(rng),
            "service_type": service_type,
            "contract_start_date": contract_start_date,
            "contract_end_date": contract_end_date,
            "conscription_period": conscription_period,
            "rnokpp": fake.numerify("##########"),
            "id_document_type": id_document_type,
            "id_document_series": id_document_series,
            "id_document_number": id_document_number,
            "birth_date": birth_date.isoformat(),
            "birth_place": fake.city(),
            "service_entry_date": service_entry_date.isoformat(),
            "service_entry_authority": rng.choice(RECRUITMENT_CENTERS),
            "education": weighted_choice(rng, EDUCATION),
            "relatives_info": make_relatives_info(rng, fake),
            "additional_info": weighted_choice(rng, ADDITIONAL_INFO_OPTIONS),
            "phone": fake.phone_number(),
        })

    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    main()
