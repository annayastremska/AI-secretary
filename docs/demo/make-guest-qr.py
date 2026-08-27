# -*- coding: utf-8 -*-
"""Скласти QR гостьового входу. Запускати ЛОКАЛЬНО, не на сервері.

Чому локально. На сервері немає пакета `qrcode`, і ставити його в спільний
venv ми не будемо: рівно такою дією 25.08 `llama-cpp-python` тихо стала
процесорною (нічого не впало, логи чисті, у 200 разів повільніше). Картинка --
це артефакт розгортання: складається тут, копіюється туди.

Чому файл не в git. У картинці зашитий КЛЮЧ доступу. `data/qr-guest.png` у
`.gitignore`.

Запуск:
    python docs/demo/make-guest-qr.py --token <ключ> [--base http://185.9.41.1:7302]
    python docs/demo/make-guest-qr.py --new          # згенерувати новий ключ

Далі:
    scp data/qr-guest.png <сервер>:~/anya/ai-secretary/data/
    і дописати APP_GUEST_TOKEN=<ключ> у .env сервісу
"""
import argparse
import os
import secrets

import qrcode

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "data", "qr-guest.png")
#: Куди веде QR. Одразу в ЧАТ, а не на першу сторінку: людина, яка сканує код
#: із екрана в залі, хоче поставити питання, а не читати про завантаження.
DEFAULT_BASE = "http://185.9.41.1:7302"
LANDING = "/chat/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", help="ключ доступу; або --new")
    ap.add_argument("--new", action="store_true",
                    help="згенерувати новий випадковий ключ і показати його")
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()

    token = args.token
    if args.new or not token:
        # 32 символи з urlsafe-алфавіту: досить довго, щоб не підбиралось, і
        # досить коротко, щоб QR лишався розбірливим на проєкторі.
        token = secrets.token_urlsafe(24)
    url = f"{args.base}{LANDING}?k={token}"

    # box_size і border -- не оздоба: код читають камерою телефона з екрана
    # проєктора, тобто з відблиском і під кутом. Висока корекція помилок
    # (ERROR_CORRECT_Q, ~25%) дає код, який читається навіть частково
    # затіненим; вона ж робить код щільнішим, тому box_size більший.
    qr = qrcode.QRCode(version=None,
                       error_correction=qrcode.constants.ERROR_CORRECT_Q,
                       box_size=10, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT)

    print("КЛЮЧ (у .env сервісу як APP_GUEST_TOKEN):")
    print(token)
    print()
    print("ПОСИЛАННЯ під кодом:")
    print(url)
    print()
    print(f"картинка: {OUT} (у git НЕ їде -- у ній ключ)")
    print(f"модулів у коді: {qr.version}, розмір: {img.size[0]}px")


if __name__ == "__main__":
    main()
