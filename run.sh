#!/usr/bin/env bash
# =============================================================================
#  KITSUNE // LIVING FLAME HUD  —  лаунчер проекта
# =============================================================================
#  Что делает:
#    1. создаёт локальную виртуальную среду в .venv (если её ещё нет);
#    2. ставит/обновляет зависимости из requirements.txt;
#    3. запускает ассистента (main.py).
#
#  Использование:
#    ./run.sh               подготовить окружение и запустить ассистента в фоне
#    ./run.sh --setup       только создать venv и установить зависимости
#    ./run.sh --foreground  запустить с консолью (видны логи Vosk и ошибки)
#    ./run.sh --reinstall   переустановить зависимости с нуля
#    ./run.sh --no-tts      пропустить локальный синтез речи (только сеть)
#    ./run.sh --help        эта справка
#
#  Окружение: Windows (Git Bash / MSYS2 / Cygwin) или Linux/macOS.
#  Внимание: сам ассистент работает только на Windows (SAPI5, powercfg,
#  блокировка клавиатуры, буфер обмена).
# =============================================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

VENV_DIR="$PROJECT_DIR/.venv"
REQ_FILE="$PROJECT_DIR/requirements.txt"
LOCK_FILE="$VENV_DIR/.requirements.lock"
ENTRYPOINT="$PROJECT_DIR/main.py"

SETUP_ONLY=0
FOREGROUND=0
REINSTALL=0
SKIP_TTS=0

print_help() {
    sed -n '2,22p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//'
}

for arg in "$@"; do
    case "$arg" in
        --setup|--install|-s)   SETUP_ONLY=1 ;;
        --foreground|--debug|-f) FOREGROUND=1 ;;
        --reinstall|--force)    REINSTALL=1 ;;
        --no-tts)               SKIP_TTS=1 ;;
        -h|--help)              print_help; exit 0 ;;
        *)
            echo "❌ Неизвестный аргумент: $arg" >&2
            echo "   Запусти ./run.sh --help, чтобы увидеть доступные флаги." >&2
            exit 1
            ;;
    esac
done

# --- Пути внутри venv зависят от платформы ----------------------------------
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        VENV_PY="$VENV_DIR/Scripts/python.exe"
        VENV_PY_W="$VENV_DIR/Scripts/pythonw.exe"
        ;;
    *)
        VENV_PY="$VENV_DIR/bin/python"
        VENV_PY_W="$VENV_PY"
        ;;
esac

# --- 1. Ищем системный Python ----------------------------------------------
find_system_python() {
    local candidate
    for candidate in python3 python py; do
        if command -v "$candidate" >/dev/null 2>&1 \
           && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if ! SYSTEM_PYTHON="$(find_system_python)"; then
    echo "❌ Не найден Python 3.12 или новее." >&2
    echo "   Установи Python с https://www.python.org/downloads/ и включи опцию 'Add python.exe to PATH'." >&2
    exit 1
fi

# --- 2. Создаём виртуальную среду ------------------------------------------
if [ ! -f "$VENV_PY" ]; then
    echo "📦 Создаю виртуальную среду: $VENV_DIR"
    "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
    REINSTALL=1
fi

# --- 3. Устанавливаем зависимости, если они изменились ---------------------
NEED_DEPS=0
if [ "$REINSTALL" -eq 1 ]; then
    NEED_DEPS=1
elif [ ! -f "$LOCK_FILE" ]; then
    NEED_DEPS=1
elif ! cmp -s "$REQ_FILE" "$LOCK_FILE"; then
    NEED_DEPS=1
    echo "🔔 requirements.txt изменился — обновляю зависимости."
fi

if [ "$NEED_DEPS" -eq 1 ]; then
    echo "⬇️  Устанавливаю зависимости (это может занять пару минут)..."
    "$VENV_PY" -m pip install --upgrade pip
    "$VENV_PY" -m pip install -r "$REQ_FILE"
    cp "$REQ_FILE" "$LOCK_FILE"
    echo "✅ Зависимости готовы."
else
    echo "✅ Виртуальная среда готова (зависимости актуальны)."
fi

# --- 3b. Опциональные GPU-зависимости для распознавания на видеокарте -------
# Ставятся только если в системе есть NVIDIA: без них ассистент работает на
# Vosk (CPU), что предусмотрено логикой config.json -> asr_engine = auto.
GPU_REQ_FILE="$PROJECT_DIR/requirements-gpu.txt"
GPU_LOCK_FILE="$VENV_DIR/.requirements-gpu.lock"

has_nvidia() {
    command -v nvidia-smi >/dev/null 2>&1 && return 0
    [ -x "/c/Windows/System32/nvidia-smi.exe" ] && return 0
    [ -x "/c/Program Files/NVIDIA Corporation/NVSMI/nvidia-smi.exe" ] && return 0
    return 1
}

if [ -f "$GPU_REQ_FILE" ] && has_nvidia; then
    GPU_NEED=0
    if [ "$REINSTALL" -eq 1 ]; then
        GPU_NEED=1
    elif [ ! -f "$GPU_LOCK_FILE" ] || ! cmp -s "$GPU_REQ_FILE" "$GPU_LOCK_FILE"; then
        GPU_NEED=1
    fi
    if [ "$GPU_NEED" -eq 1 ]; then
        echo "🎮 Найдена NVIDIA — ставлю GPU-зависимости распознавания (~0.5 ГБ, долго)..."
        if "$VENV_PY" -m pip install -r "$GPU_REQ_FILE"; then
            cp "$GPU_REQ_FILE" "$GPU_LOCK_FILE"
            echo "✅ GPU-распознавание готово."
        else
            echo "⚠️  GPU-зависимости не встали — ассистент продолжит на Vosk (CPU)."
        fi
    else
        echo "🎮 GPU-зависимости распознавания актуальны."
    fi
fi

# --- 3c. Локальный синтез речи: лёгкий путь (kokoro, ONNX) ------------------
# Ставится на любой машине: работает на CPU и делает озвучку офлайн.
# Сбой установки не критичен — ассистент просто продолжит говорить через
# edge-tts, а затем через SAPI5, как и до появления локального синтеза.
TTS_REQ_FILE="$PROJECT_DIR/requirements-tts.txt"
TTS_LOCK_FILE="$VENV_DIR/.requirements-tts.lock"

if [ "$SKIP_TTS" -eq 0 ] && [ -f "$TTS_REQ_FILE" ]; then
    TTS_NEED=0
    if [ "$REINSTALL" -eq 1 ]; then
        TTS_NEED=1
    elif [ ! -f "$TTS_LOCK_FILE" ] || ! cmp -s "$TTS_REQ_FILE" "$TTS_LOCK_FILE"; then
        TTS_NEED=1
    fi
    if [ "$TTS_NEED" -eq 1 ]; then
        echo "🗣️  Ставлю локальный синтез речи (kokoro, ~0.4 ГБ)..."
        if "$VENV_PY" -m pip install -r "$TTS_REQ_FILE"; then
            cp "$TTS_REQ_FILE" "$TTS_LOCK_FILE"
            echo "✅ Локальный синтез готов."
        else
            echo "⚠️  Локальный синтез не встал — озвучка останется сетевой (edge-tts)."
        fi
    else
        echo "🗣️  Локальный синтез актуален."
    fi
elif [ "$SKIP_TTS" -eq 1 ]; then
    echo "🗣️  Локальный синтез пропущен флагом --no-tts."
fi

# --- 3d. Локальный синтез речи: лучший путь (Silero на PyTorch + CUDA) ------
# Нужен только при живой NVIDIA: torch с CUDA-рантаймом занимает гигабайты,
# а CPU-сборка с PyPI — причина, по которой torch.cuda.is_available() даёт
# False даже на исправной видеокарте. Поэтому проверка по возможности, а не
# по факту «torch установлен»: CPU-сборка считается требующей замены.
# Файл-маркер не даёт перекачивать гигабайты при каждом запуске, если
# установка прошла, а драйвер оказался слишком старым.
TTS_GPU_REQ_FILE="$PROJECT_DIR/requirements-tts-gpu.txt"
TTS_GPU_LOCK_FILE="$VENV_DIR/.requirements-tts-gpu.lock"
CUDA_MARKER="$VENV_DIR/.tts-cuda-unavailable"
# Должны совпадать с --index-url / --extra-index-url в requirements-tts-gpu.txt.
TORCH_CUDA_INDEX="https://download.pytorch.org/whl/cu126"
PYPI_INDEX="https://pypi.org/simple"

cuda_available() {
    "$VENV_PY" -c 'import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)' \
        >/dev/null 2>&1
}

if [ "$REINSTALL" -eq 1 ]; then
    rm -f "$CUDA_MARKER"
fi

CUDA_READY=0
cuda_available && CUDA_READY=1

if [ "$SKIP_TTS" -eq 0 ] && has_nvidia && [ -f "$TTS_GPU_REQ_FILE" ]; then
    TTS_GPU_NEED=0
    if [ ! -f "$TTS_GPU_LOCK_FILE" ] || ! cmp -s "$TTS_GPU_REQ_FILE" "$TTS_GPU_LOCK_FILE"; then
        TTS_GPU_NEED=1
    fi
    if [ "$CUDA_READY" -eq 0 ] && [ ! -f "$CUDA_MARKER" ]; then
        TTS_GPU_NEED=1
    fi

    if [ "$TTS_GPU_NEED" -eq 1 ]; then
        echo "🎙️  Найдена NVIDIA — ставлю Silero на CUDA (несколько ГБ, долго)..."
        FAILED=0
        if [ "$CUDA_READY" -eq 0 ]; then
            # Именно здесь нужен --force-reinstall: на PyPI лежит CPU-сборка с той
            # же версией, и без него pip решит, что torch уже стоит.
            # Переустанавливаем только torch, чтобы правка requirements-tts-gpu.txt
            # не тянула 2.6 ГБ колёса заново и не дёргала numpy, setuptools и прочее.
            "$VENV_PY" -m pip install --upgrade --force-reinstall \
                --index-url "$TORCH_CUDA_INDEX" --extra-index-url "$PYPI_INDEX" torch || FAILED=1
        fi
        if [ "$FAILED" -eq 0 ]; then
            "$VENV_PY" -m pip install -r "$TTS_GPU_REQ_FILE" || FAILED=1
        fi

        if [ "$FAILED" -ne 0 ]; then
            echo "⚠️  Silero не встал — говорить будет kokoro (CPU)."
            : > "$CUDA_MARKER"
        else
            cp "$TTS_GPU_REQ_FILE" "$TTS_GPU_LOCK_FILE"
            if cuda_available; then
                rm -f "$CUDA_MARKER"
                echo "✅ Silero готов."
            else
                echo "⚠️  torch установлен, но CUDA недоступна (старый драйвер?)."
                echo "   Речь пойдёт через kokoro на CPU. Обнови драйвер NVIDIA и удали"
                echo "   $CUDA_MARKER, чтобы включить Silero."
                : > "$CUDA_MARKER"
            fi
        fi
    else
        echo "🎙️  Silero актуален."
    fi
elif [ "$SKIP_TTS" -eq 1 ]; then
    echo "🎙️  Silero пропущен флагом --no-tts."
elif ! has_nvidia; then
    echo "🎙️  NVIDIA не найдена — Silero не нужен, говорит kokoro (CPU)."
else
    echo "🎙️  Silero недоступен на этом драйвере — говорит kokoro (CPU)."
fi

# --- 3e. Модель ударений kokoro ---------------------------------------------
# Текстовый фронтенд kokoro тянет свою модель ударений с huggingface.co не при
# установке пакетов и не в tts_models/, а в момент построения движка, и держит
# её внутри своего пакета. Без неё движок не поднимется, а озвучка молча уйдёт
# на edge-tts, которому нужна сеть. Проверяем и предлагаем догрузить заранее.
if [ "$SKIP_TTS" -eq 1 ]; then
    echo "🗣️  Проверка kokoro пропущена флагом --no-tts."
else
    "$VENV_PY" "$PROJECT_DIR/tts_local.py" kokoro --check
    KOKORO_STATUS=$?

    if [ "$KOKORO_STATUS" -eq 0 ]; then
        echo "🗣️  Kokoro готов, модель ударений на месте."
    elif [ "$KOKORO_STATUS" -eq 11 ]; then
        echo "⚠️  Пакеты kokoro не установлены — локальная озвучка недоступна."
        echo "   Починить: ./run.sh --reinstall"
    else
        echo "🗣️  Kokoro нужна модель ударений (~690 МБ), иначе он не заговорит."
        echo "   Без неё озвучка идёт через edge-tts, а ему нужна сеть."
        KOKORO_ANSWER=""
        read -r -p "   Скачать сейчас? [y/N]: " KOKORO_ANSWER || KOKORO_ANSWER=""
        if [ "$KOKORO_ANSWER" = "y" ] || [ "$KOKORO_ANSWER" = "Y" ]; then
            echo "   Качаю, нужен доступ к huggingface.co..."
            if "$VENV_PY" "$PROJECT_DIR/tts_local.py" kokoro; then
                echo "   Готово: kokoro будет говорить локально."
            else
                echo "⚠️  Скачать не удалось — озвучка останется на edge-tts."
            fi
        else
            echo "   Пропускаю. Позже: python tts_local.py kokoro"
        fi
    fi
fi

if [ "$SETUP_ONLY" -eq 1 ]; then
    echo "🚀 Установка завершена. Запуск ассистента: ./run.sh"
    exit 0
fi
# --- 4. Запускаем ассистента ------------------------------------------------
if [ "$FOREGROUND" -eq 1 ]; then
    echo "🦊 Запускаю Kitsune Assistant (в консоли)..."
    exec "$VENV_PY" "$ENTRYPOINT"
fi

LAUNCH_PY="$VENV_PY"
if [ -f "$VENV_PY_W" ]; then
    LAUNCH_PY="$VENV_PY_W"   # pythonw.exe — запуск без окна консоли
fi

nohup "$LAUNCH_PY" "$ENTRYPOINT" >/dev/null 2>&1 &
disown 2>/dev/null || true

echo "🦊 Kitsune Assistant запущен в фоне (иконки — в системном трее)."
echo "   Логи и ошибки: ./run.sh --foreground"
