#!/bin/zsh
# Подвійний клік на Mac: ставить оточення (якщо треба) і відкриває вікно чата.
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Створюю .venv (перший запуск, займе хвилину)…"
  python3 -m venv .venv
fi

./.venv/bin/pip install -q -r requirements.txt

if [ ! -f stand.sqlite ]; then
  echo "Бази ще нема — збираю з data/…"
  ./.venv/bin/python seed.py
fi

# Браузер відкриваємо після старту сервера, з фонового процесу
( sleep 4 && open "http://127.0.0.1:7860" ) &

exec ./.venv/bin/python app.py
