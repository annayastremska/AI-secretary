# -*- coding: utf-8 -*-
"""Скласти QR на пам'ятку журі. Запускати ЛОКАЛЬНО, не на сервері.

Чому локально й чому не в git -- те саме, що в `make-guest-qr.py`: на сервері
немає `qrcode`, а в картинці зашитий КЛЮЧ доступу.

Куди веде. На `/jury?k=<ключ>` -- ТОЙ САМИЙ гостьовий ключ, що й у чат.
Окремого відкритого маршруту немає навмисно: пам'ятка веде в чат, і якби вона
відкривалась без ключа, то вела б у вікно з паролем. Ключ у посиланні кладеться
в cookie гейтом, тому «Open the chat» усередині пам'ятки працює вже без ключа
в адресі.

Запуск:
    python docs/demo/make-jury-qr.py --token <ключ>

Далі (обидва файли -- поза git):
    scp data/qr-jury.png     <сервер>:~/anya/ai-secretary/data/
    scp <пам'ятка>.html      <сервер>:~/anya/ai-secretary/data/jury-guide.html
"""
import argparse
import os

import qrcode

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "data", "qr-jury.png")
#: УВАГА: ця адреса більше не існує -- віртуалку вимкнули 01.09.2026.
#: Лишена як приклад формату; для нової машини передавати --base.
DEFAULT_BASE = "http://185.9.41.1:7302"
LANDING = "/jury"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True,
                    help="той самий APP_GUEST_TOKEN, що в .env сервісу")
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()

    url = f"{args.base}{LANDING}?k={args.token}"
    # Параметри ті самі, що в гостьового коду: код читають камерою телефона з
    # екрана проєктора -- з відблиском і під кутом. ERROR_CORRECT_Q (~25%)
    # читається навіть частково затіненим.
    qr = qrcode.QRCode(version=None,
                       error_correction=qrcode.constants.ERROR_CORRECT_Q,
                       box_size=10, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT)

    print("ПОСИЛАННЯ під кодом:")
    print(url)
    print()
    print(f"картинка: {OUT} (у git НЕ їде -- у ній ключ)")
    print(f"модулів у коді: {qr.version}, розмір: {img.size[0]}px")


if __name__ == "__main__":
    main()
