#!/usr/bin/env bash
# Запускає MamayLM локально для експериментів -- із запобіжниками.
#
# Запуск:   bash db/scripts/start_local_llm.sh
# Спинити:  bash db/scripts/start_local_llm.sh stop
#
# Сервер спільний, і GPU на ньому вже зайнятий чужим процесом. Тому тут не
# «запусти модель», а «запусти, якщо це нікому не зашкодить».
#
# Чому саме такі обмеження:
#
# * --host 127.0.0.1 -- НЕ 0.0.0.0. На цій машині порт 80 уже публічний, і
#   відкривати ще один порт із моделлю в інтернет ми не будемо. Доступ -- лише
#   з самої машини.
# * -c 4096 -- титульний блок це ~900 токенів, більше не потрібно. Контекст
#   прямо визначає розмір KV-кешу, тобто зайвий контекст це зайнята пам'ять
#   ні за що.
# * MIN_FREE_MIB -- відмовляємось стартувати, якщо вільної пам'яті мало.
#   vLLM за замовчуванням резервує 90% GPU і саме так убиває сусідів; ми
#   робимо навпаки -- перевіряємо, що місце є, і беремо стільки, скільки
#   треба моделі.
# * llama.cpp виділяє пам'ять під ваги (16.5 ГБ) + KV. Це передбачувано,
#   на відміну від частки від усієї карти.
set -euo pipefail

MODEL="${MODEL:-$HOME/shared/models/mamaylm-27b-q4_k_m.gguf}"
BIN="${BIN:-$HOME/anya/llamacpp-src/build/bin/llama-server}"
PORT="${PORT:-8081}"
CTX="${CTX:-4096}"
MIN_FREE_MIB="${MIN_FREE_MIB:-25000}"   # ваги 16.5 ГБ + запас
PIDFILE="$HOME/andriy/run/llm.pid"
LOG="$HOME/andriy/run/llm.log"

mkdir -p "$(dirname "$PIDFILE")"

if [ "${1:-start}" = "stop" ]; then
    if [ -f "$PIDFILE" ]; then
        pid=$(cat "$PIDFILE")
        # Вбиваємо ЛИШЕ свій процес із pidfile. Ніяких pkill за іменем:
        # на спільній машині це шлях покласти чуже.
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" && echo "спинено pid=$pid"
        else
            echo "процес $pid уже не живий"
        fi
        rm -f "$PIDFILE"
    else
        echo "pidfile немає -- нічого не спиняю"
    fi
    exit 0
fi

# llama-server зібраний під CUDA 12, а драйвер на машині -- 13, тому
# libcudart.so.12 у системі немає і бінарник не стартує взагалі. Потрібний
# рантайм лежить у pip-пакетах Ані; беремо його ЛИШЕ на читання, нічого в її
# теці не змінюючи.
NV="$HOME/anya/ai-secretary/.venv/lib/python3.12/site-packages/nvidia"
if [ -d "$NV" ]; then
    for d in "$NV"/*/lib; do
        [ -d "$d" ] && LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$d"
    done
    export LD_LIBRARY_PATH
fi

[ -f "$MODEL" ] || { echo "немає моделі: $MODEL" >&2; exit 1; }
[ -x "$BIN" ]   || { echo "немає llama-server: $BIN" >&2; exit 1; }

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "уже запущено, pid=$(cat "$PIDFILE") на порті $PORT"
    exit 0
fi

if ss -tln | grep -q ":$PORT "; then
    echo "порт $PORT уже зайнятий -- не займаю" >&2
    exit 1
fi

free_mib=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
used_by_others=$(nvidia-smi --query-compute-apps=used_memory --format=csv,noheader,nounits | paste -sd+ | bc 2>/dev/null || echo 0)
echo "GPU: вільно ${free_mib} MiB, чужі процеси тримають ${used_by_others} MiB"
if [ "$free_mib" -lt "$MIN_FREE_MIB" ]; then
    echo "вільно менше за ${MIN_FREE_MIB} MiB -- НЕ стартую, щоб не зачепити чуже" >&2
    exit 1
fi

echo "старт: ctx=$CTX port=$PORT (лише 127.0.0.1)"
nohup "$BIN" \
    --model "$MODEL" \
    --host 127.0.0.1 --port "$PORT" \
    --ctx-size "$CTX" \
    --n-gpu-layers 99 \
    --parallel 1 \
    --no-webui \
    > "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "pid=$(cat "$PIDFILE"), лог: $LOG"

for i in $(seq 1 90); do
    if curl -s --max-time 2 "http://127.0.0.1:$PORT/health" | grep -q '"ok"'; then
        echo "готовий за ~${i} с"
        nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
        exit 0
    fi
    sleep 2
done
echo "не піднявся за 180 с -- дивись $LOG" >&2
exit 1
