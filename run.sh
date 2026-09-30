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
#    ./run.sh --full        также поставить зависимости полного режима
#    ./run.sh --help        эта справка
#
#  Окружение: Windows (Git Bash / MSYS2 / Cygwin) или Linux/macOS.
#  Внимание: сам ассистент работает только на Windows (SAPI5, powercfg,
#  блокировка клавиатуры, буфер обмена).
#
#  Первый запуск ставит только базовые зависимости (Vosk + edge-tts).
#  Whisper / Silero / kokoro догружаются из приложения при включении
#  полного режима, либо заранее флагом --full.
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
INSTALL_FULL=0

print_help() {
    cat <<'EOF'
KITSUNE // LIVING FLAME HUD — лаунчер

  ./run.sh               подготовить базовое .venv и запустить ассистента
  ./run.sh --setup       только создать venv и поставить базовые зависимости
  ./run.sh --foreground  запустить с консолью (логи Vosk / ошибки)
  ./run.sh --reinstall   переустановить базовые зависимости
  ./run.sh --full        также поставить зависимости полного режима
  ./run.sh --help        эта справка

Полный режим обычно догружается из приложения («О лисе» → включить фулл).
Флаг --full нужен для офлайн/предварительной установки.
EOF
}

for arg in "$@"; do
    case "$arg" in
        --setup|--install|-s)   SETUP_ONLY=1 ;;
        --foreground|--debug|-f) FOREGROUND=1 ;;
        --reinstall|--force)    REINSTALL=1 ;;
        --full)                 INSTALL_FULL=1 ;;
        --no-tts)               ;; # устаревший флаг: базовый режим и так без TTS-моделей
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

# --- 3b. Зависимости полного режима (опционально) ---------------------------
# Базовая установка заканчивается на requirements.txt. Whisper / Silero /
# kokoro догружаются из приложения при включении полного режима.
if [ "$INSTALL_FULL" -eq 1 ]; then
    echo "🚀 Ставлю зависимости полного режима (Whisper / Silero / kokoro)..."
    if "$VENV_PY" "$PROJECT_DIR/full_deps.py"; then
        echo "✅ Полный режим готов."
    else
        echo "⚠️  Установка полного режима завершилась с ошибками. Базовый режим работает."
    fi
else
    echo "🌙 Полный режим пропущен. Включи его в приложении («О лисе»), чтобы скачать."
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
