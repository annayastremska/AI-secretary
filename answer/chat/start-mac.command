#!/bin/bash
# Двічі клацнути — стенд збереться і відкриється в браузері.
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
python3 seed.py
python3 app.py &
sleep 6
open http://127.0.0.1:7861
wait
