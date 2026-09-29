"""Курсы криптовалют для голосового ответа.

Задача — сказать «курс биткоина» и услышать цену в долларах и рублях, поэтому
источники выбраны по фактической доступности (проверено с этой машины):

* Binance  — публичные тикеры вида ``BTCUSDT`` и ``BTCRUB``, без ключа;
* Coinbase — резерв для долларовой цены, если Binance не ответил;
* ЦБ РФ    — официальный курс USD/RUB, чтобы посчитать рублёвую цену для
             монет, у которых нет прямой пары с рублём.

CoinGecko и CryptoCompare сознательно не используются: первый отдаёт 403, а
второй требует ключ, то есть в России оба бесполезны.

Модуль никогда не бросает исключений: любой сбой — это ``None``, и вызывающий
код просто скажет, что курс достать не удалось. Формулировки фраз живут в
``commands/*.command``, а здесь только данные и грамматика (падежи валют),
чтобы одинаковые правила не расползались по локалям.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request

#: Обычный браузерный UA: часть CDN отдаёт 403 на «питоновские» запросы.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = 5.0

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/{base}-USD/spot"
CBR_DAILY_URL = "https://www.cbr-xml-daily.ru/daily_json.js"

#: Держим курс минуту: за это время он не уедет, а лишние запросы при
#: нескольких подряд командах «курс ...» никому не нужны.
CACHE_TTL = 60.0
#: Курс ЦБ меняется раз в сутки, а нужен для каждой монеты без прямой пары
#: с рублём: без своего кэша «курс биткоина» и «курс эфириума» подряд дважды
#: ходили бы за одним и тем же числом.
CBR_CACHE_TTL = 3600.0
#: Верхняя граница ответа API: тикер — это десятки байт, и всё, что сильно
#: больше, — не цена, а чужая страница ошибки или мусор прокси.
MAX_RESPONSE_BYTES = 1_000_000
_cache: dict[str, tuple[float, dict]] = {}
_cbr_cache: tuple[float, float] | None = None


# ---------------------------------------------------------------------------
# Монеты: тикер → названия и слова, которыми монету называют голосом
# ---------------------------------------------------------------------------

#: Алиасы намеренно перечислены вместе с падежами: «курс биткоина» — обычная
#: формулировка, и подбирать окончания регулярками тут дороже, чем перечислить
#: формы руками. Сравнение идёт по нормализованному тексту (нижний регистр,
#: ё→е), поэтому все алиасы тоже нормализованы.
COINS: dict[str, dict] = {
    "BTC": {
        "ru": "Биткоин",
        "en": "Bitcoin",
        "aliases": [
            "btc", "биткоин", "биткоина", "биткоину", "биткоине", "биткойн",
            "биткойна", "bitcoin", "битк", "битка", "бтс",
        ],
    },
    "ETH": {
        "ru": "Эфириум",
        "en": "Ethereum",
        "aliases": [
            "eth", "ethereum", "эфириум", "эфириума", "эфириуме", "эфир",
            "эфира", "эфирка", "эфирки", "этериум", "эфириум",
        ],
    },
    "BNB": {
        "ru": "BNB",
        "en": "BNB",
        "aliases": ["bnb", "бнб", "бинанс", "бинанс коин", "binance coin"],
    },
    "SOL": {
        "ru": "Солана",
        "en": "Solana",
        "aliases": ["sol", "солана", "соланы", "соль", "solana"],
    },
    "XRP": {
        "ru": "XRP",
        "en": "XRP",
        "aliases": ["xrp", "рипл", "рипла", "ripple", "хрп"],
    },
    "TON": {
        "ru": "Toncoin",
        "en": "Toncoin",
        "aliases": ["ton", "тон", "тона", "toncoin", "тонкоин", "тонкоина"],
    },
    "DOGE": {
        "ru": "Догикоин",
        "en": "Dogecoin",
        "aliases": [
            "doge", "доги", "доге", "додж", "догкоин", "догикоин", "dogecoin",
            "собака", "собаки",
        ],
    },
    "ADA": {
        "ru": "Кардано",
        "en": "Cardano",
        "aliases": ["ada", "кардано", "карданы", "cardano"],
    },
    "USDT": {
        "ru": "Tether",
        "en": "Tether",
        "aliases": ["usdt", "тезер", "тезера", "tether", "юсдт"],
    },
    "LTC": {
        "ru": "Лайткоин",
        "en": "Litecoin",
        "aliases": ["ltc", "лайткоин", "лайткоина", "litecoin"],
    },
}

#: Обратный индекс: алиас → тикер. Собирается один раз при импорте.
_ALIAS_TO_TICKER: dict[str, str] = {}
for _ticker, _info in COINS.items():
    _ALIAS_TO_TICKER[_ticker.lower()] = _ticker
    for _alias in _info["aliases"]:
        _ALIAS_TO_TICKER.setdefault(_alias, _ticker)


def normalize(text: str) -> str:
    """Приводит текст к виду, в котором записаны алиасы."""
    if not isinstance(text, str):
        return ""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _similar(left: str, right: str) -> float:
    """Похожесть двух строк без внешних зависимостей."""
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left, right).ratio()


def resolve_coin(text: str) -> str | None:
    """Ищет монету в произвольной фразе. Возвращает тикер или None.

    Три прохода, от строгого к мягкому: точное совпадение фразы, затем алиас
    как отдельные слова, затем опечатки распознавания. Границы слов обязательны,
    иначе «тон» поймалось бы внутри «тонна».
    """
    norm = normalize(text)
    if not norm:
        return None

    if norm in _ALIAS_TO_TICKER:
        return _ALIAS_TO_TICKER[norm]

    words = norm.split()
    best, best_len = None, 0
    for alias, ticker in _ALIAS_TO_TICKER.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", norm):
            if len(alias) > best_len:
                best, best_len = ticker, len(alias)
    if best:
        return best

    # Опечатки ASR: сверяем каждый токен с алиасами длиной от пяти символов.
    # Короткие вроде «тон» и «соль» из этой проверки исключены — они слишком
    # легко сходятся с посторонними словами («тонна» ≈ «тона»), а точные формы
    # таких названий уже перечислены в алиасах.
    for word in words:
        if len(word) < 5:
            continue
        for alias, ticker in _ALIAS_TO_TICKER.items():
            if not 5 <= len(alias) <= len(word) + 2:
                continue
            if _similar(word, alias) >= 0.82:
                return ticker
    return None


def display_name(ticker: str, lang: str = "ru") -> str:
    """Человеческое имя монеты; для незнакомого тикера — сам тикер."""
    info = COINS.get(ticker.upper())
    if not info:
        return ticker.upper()
    return info.get(lang) or info.get("ru") or ticker.upper()


# ---------------------------------------------------------------------------
# Числа: падежи валют и формат для синтеза речи
# ---------------------------------------------------------------------------

def plural_ru(value: float, one: str, few: str, many: str) -> str:
    """Русское согласование существительного с числом."""
    number = abs(int(value))
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def plural_en(value: float, one: str, many: str) -> str:
    return one if abs(int(value)) == 1 else many


def _format_value(value: float) -> tuple[str, bool]:
    """Готовит число к синтезу. Возвращает (текст, есть ли дробная часть).

    Разряды намеренно не разделяются: «83 018» часть движков читает как два
    числа подряд, а «83018» — всегда как одно. Копейки нужны только дешёвым
    монетам, поэтому до десятка оставляем два знака, а мелочь — четыре.
    """
    if value >= 10:
        return f"{value:.0f}", False
    text = f"{value:.2f}" if value >= 1 else f"{value:.4f}"
    text = text.rstrip("0").rstrip(".")
    return text, "." in text


def format_number(value: float, lang: str = "ru") -> str:
    if value is None:
        return ""
    text, _ = _format_value(value)
    # В русской типографике дробная часть отделяется запятой, и TTS это ожидает:
    # «6,94» читается как «шесть целых девяносто четыре сотых».
    return text.replace(".", ",") if lang == "ru" else text


def _noun_ru(value: float, has_fraction: bool, one: str, few: str, many: str) -> str:
    """Согласование валюты с числом.

    С дробной частью в русском идёт родительный единственного числа: «6,94
    рубля». Без неё — обычные правила по последним цифрам, причём по тем же,
    что человек слышит: число для речи округляется, и «82981,6» звучит как
    «82982 доллара», а не «82982 доллар».
    """
    if has_fraction:
        return few
    return plural_ru(round(value), one, few, many)


# ---------------------------------------------------------------------------
# Источники курсов
# ---------------------------------------------------------------------------

def _get_json(url: str) -> dict | None:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            # Лимит защищает от «ответа» на несколько гигабайт: при сбое DNS
            # или подмене адреса some-CDN может отдать страницу-заглушку.
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                return None
            return json.loads(data.decode("utf-8", "replace"))
    except Exception:
        return None


def _binance_price(symbol: str) -> float | None:
    data = _get_json(BINANCE_TICKER_URL.format(symbol=symbol))
    if not data or "price" not in data:
        return None
    try:
        return float(data["price"])
    except (TypeError, ValueError):
        return None


def fetch_usd(ticker: str) -> tuple[float | None, str]:
    """Долларовая цена. USDT взят как эквивалент доллара — это стандартная
    практика, расхождение с Coinbase в пределах десятых долей процента."""
    ticker = ticker.upper()
    if ticker == "USDT":
        return 1.0, "Tether"

    price = _binance_price(f"{ticker}USDT")
    if price is not None:
        return price, "Binance"

    data = _get_json(COINBASE_SPOT_URL.format(base=ticker))
    try:
        return float(data["data"]["amount"]), "Coinbase"
    except (TypeError, KeyError, ValueError):
        return None, ""


def usd_rub_rate() -> float | None:
    """Официальный курс ЦБ РФ: рублей за один доллар.

    Кэшируется на час: курс обновляется раз в сутки, а без кэша каждая монета
    без прямой рублёвой пары ходила бы за ним отдельно.
    """
    global _cbr_cache
    now = time.time()
    if _cbr_cache and now - _cbr_cache[0] < CBR_CACHE_TTL:
        return _cbr_cache[1]

    data = _get_json(CBR_DAILY_URL)
    try:
        usd = data["Valute"]["USD"]
        nominal = float(usd.get("Nominal") or 1)
        rate = float(usd["Value"]) / nominal
    except (TypeError, KeyError, ValueError, ZeroDivisionError):
        return None
    if rate > 0:
        _cbr_cache = (now, rate)
    return rate or None


def fetch_rub(ticker: str, usd: float | None = None) -> tuple[float | None, str]:
    """Рублёвая цена: прямая пара на Binance, иначе доллары по курсу ЦБ."""
    ticker = ticker.upper()
    if ticker == "USDT":
        rate = usd_rub_rate()
        return (rate, "ЦБ РФ") if rate else (None, "")

    price = _binance_price(f"{ticker}RUB")
    if price is not None:
        return price, "Binance"

    if usd is None:
        return None, ""
    rate = usd_rub_rate()
    if not rate:
        return None, ""
    return usd * rate, "ЦБ РФ"


# ---------------------------------------------------------------------------
# Публичный вход
# ---------------------------------------------------------------------------

def get_rate(ticker: str, lang: str = "ru", use_cache: bool = True) -> dict | None:
    """Собирает всё для ответа одной фразой.

    Возвращает словарь с готовыми строками и словами валют, например
    ``{'name': 'Биткоин', 'usd': '83018', 'usd_word': 'долларов', ...}``.
    ``None`` — если не удалось достать ни одной цены.
    """
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return None

    key = f"{ticker}:{lang}"
    now = time.time()
    if use_cache:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]

    usd, source_usd = fetch_usd(ticker)
    rub, source_rub = fetch_rub(ticker, usd)
    if usd is None and rub is None:
        return None

    name = display_name(ticker, lang)
    usd_text, usd_fraction = _format_value(usd) if usd is not None else ("", False)
    rub_text, rub_fraction = _format_value(rub) if rub is not None else ("", False)
    if lang == "ru":
        usd_word = _noun_ru(usd, usd_fraction, "доллар", "доллара", "долларов") if usd is not None else ""
        rub_word = _noun_ru(rub, rub_fraction, "рубль", "рубля", "рублей") if rub is not None else ""
    else:
        usd_word = plural_en(round(usd), "dollar", "dollars") if usd is not None else ""
        rub_word = plural_en(round(rub), "ruble", "rubles") if rub is not None else ""

    parts = {
        "ticker": ticker,
        "name": name,
        "usd": usd_text,
        "usd_word": usd_word,
        "rub": rub_text,
        "rub_word": rub_word,
        "source_usd": source_usd,
        "source_rub": source_rub,
        "has_usd": usd is not None,
        "has_rub": rub is not None,
    }
    _cache[key] = (now, parts)
    return parts


if __name__ == "__main__":  # ручная проверка: python crypto_rates.py [тикер]
    import sys

    for symbol in (sys.argv[1:] or ["BTC", "ETH"]):
        print(symbol, "->", get_rate(symbol, use_cache=False))
