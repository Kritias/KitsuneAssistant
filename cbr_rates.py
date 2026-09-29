"""Официальные курсы валют ЦБ РФ на дату.

Источник — SOAP-метод ``GetCursOnDate`` веб-службы ЦБ
(https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx). При сбое SOAP
используется запасной XML-выгрузка ``XML_daily.asp``.

Модуль не бросает исключений наружу: сбой сети/даты/валюты → ``None``,
а формулировки фраз живут в ``commands/*.command``.
"""

from __future__ import annotations

import re
import ssl
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = 8.0
SOAP_URL = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
SOAP_ACTION = "http://web.cbr.ru/GetCursOnDate"
XML_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp?date_req={dd}/{mm}/{yyyy}"

CACHE_TTL = 300.0
MAX_RESPONSE_BYTES = 2_000_000
_cache: dict[str, tuple[float, dict]] = {}

# ---------------------------------------------------------------------------
# Валюты: код ISO → названия и голосовые алиасы (все падежи)
# ---------------------------------------------------------------------------

CURRENCIES: dict[str, dict] = {
    "USD": {
        "ru": "Доллар США",
        "en": "US Dollar",
        "aliases": [
            "usd", "доллар", "доллара", "доллару", "долларе", "доллары", "долларов",
            "доллар сша", "доллара сша", "американский доллар", "американского доллара",
            "бакс", "бакса", "баксы",
        ],
    },
    "EUR": {
        "ru": "Евро",
        "en": "Euro",
        "aliases": ["eur", "евро", "еврик", "еврика"],
    },
    "CNY": {
        "ru": "Китайский юань",
        "en": "Chinese Yuan",
        "aliases": [
            "cny", "юань", "юаня", "юаню", "юане", "юани", "юаней",
            "китайский юань", "китайского юаня", "женьминьби",
        ],
    },
    "GBP": {
        "ru": "Фунт стерлингов",
        "en": "Pound Sterling",
        "aliases": [
            "gbp", "фунт", "фунта", "фунту", "фунте", "фунты", "фунтов",
            "фунт стерлингов", "фунта стерлингов", "стерлинг", "стерлинга",
            "британский фунт", "британского фунта",
        ],
    },
    "JPY": {
        "ru": "Японская иена",
        "en": "Japanese Yen",
        "aliases": [
            "jpy", "иена", "иены", "иене", "иену", "иен",
            "йена", "йены", "японская иена", "японской иены",
        ],
    },
    "CHF": {
        "ru": "Швейцарский франк",
        "en": "Swiss Franc",
        "aliases": [
            "chf", "франк", "франка", "франку", "франке", "франки", "франков",
            "швейцарский франк", "швейцарского франка",
        ],
    },
    "TRY": {
        "ru": "Турецкая лира",
        "en": "Turkish Lira",
        "aliases": [
            "try", "лира", "лиры", "лире", "лиру", "лир",
            "турецкая лира", "турецкой лиры",
        ],
    },
    "KZT": {
        "ru": "Казахстанский тенге",
        "en": "Kazakhstani Tenge",
        "aliases": ["kzt", "тенге", "казахстанский тенге", "казахстанского тенге"],
    },
    "BYN": {
        "ru": "Белорусский рубль",
        "en": "Belarusian Ruble",
        "aliases": [
            "byn", "белорусский рубль", "белорусского рубля",
            "белорусский", "белорусского",
        ],
    },
    "UAH": {
        "ru": "Украинская гривна",
        "en": "Ukrainian Hryvnia",
        "aliases": [
            "uah", "гривна", "гривны", "гривне", "гривну", "гривен",
            "украинская гривна", "украинской гривны",
        ],
    },
    "AMD": {
        "ru": "Армянский драм",
        "en": "Armenian Dram",
        "aliases": ["amd", "драм", "драма", "драму", "армянский драм", "армянского драма"],
    },
    "AZN": {
        "ru": "Азербайджанский манат",
        "en": "Azerbaijani Manat",
        "aliases": ["azn", "манат", "маната", "азербайджанский манат"],
    },
    "GEL": {
        "ru": "Грузинский лари",
        "en": "Georgian Lari",
        "aliases": ["gel", "лари", "грузинский лари"],
    },
    "KRW": {
        "ru": "Вон Республики Корея",
        "en": "South Korean Won",
        "aliases": ["krw", "вон", "вона", "корейский вон", "южнокорейский вон"],
    },
    "INR": {
        "ru": "Индийская рупия",
        "en": "Indian Rupee",
        "aliases": ["inr", "рупия", "рупии", "индийская рупия", "индийской рупии"],
    },
    "AED": {
        "ru": "Дирхам ОАЭ",
        "en": "UAE Dirham",
        "aliases": ["aed", "дирхам", "дирхама", "дирхам оаэ"],
    },
    "CAD": {
        "ru": "Канадский доллар",
        "en": "Canadian Dollar",
        "aliases": ["cad", "канадский доллар", "канадского доллара"],
    },
    "AUD": {
        "ru": "Австралийский доллар",
        "en": "Australian Dollar",
        "aliases": ["aud", "австралийский доллар", "австралийского доллара"],
    },
    "SEK": {
        "ru": "Шведская крона",
        "en": "Swedish Krona",
        "aliases": ["sek", "шведская крона", "шведской кроны", "крона швеции"],
    },
    "NOK": {
        "ru": "Норвежская крона",
        "en": "Norwegian Krone",
        "aliases": ["nok", "норвежская крона", "норвежской кроны"],
    },
    "DKK": {
        "ru": "Датская крона",
        "en": "Danish Krone",
        "aliases": ["dkk", "датская крона", "датской кроны"],
    },
    "PLN": {
        "ru": "Польский злотый",
        "en": "Polish Zloty",
        "aliases": ["pln", "злотый", "злотого", "злотые", "польский злотый"],
    },
    "CZK": {
        "ru": "Чешская крона",
        "en": "Czech Koruna",
        "aliases": ["czk", "чешская крона", "чешской кроны"],
    },
    "HUF": {
        "ru": "Венгерский форинт",
        "en": "Hungarian Forint",
        "aliases": ["huf", "форинт", "форинта", "венгерский форинт"],
    },
    "SGD": {
        "ru": "Сингапурский доллар",
        "en": "Singapore Dollar",
        "aliases": ["sgd", "сингапурский доллар", "сингапурского доллара"],
    },
    "HKD": {
        "ru": "Гонконгский доллар",
        "en": "Hong Kong Dollar",
        "aliases": ["hkd", "гонконгский доллар", "гонконгского доллара"],
    },
    "THB": {
        "ru": "Таиландский бат",
        "en": "Thai Baht",
        "aliases": ["thb", "бат", "бата", "тайский бат", "таиландский бат"],
    },
    "BRL": {
        "ru": "Бразильский реал",
        "en": "Brazilian Real",
        "aliases": ["brl", "реал", "реала", "бразильский реал"],
    },
    "MXN": {
        "ru": "Мексиканский песо",
        "en": "Mexican Peso",
        "aliases": ["mxn", "песо", "мексиканский песо"],
    },
    "ZAR": {
        "ru": "Южноафриканский рэнд",
        "en": "South African Rand",
        "aliases": ["zar", "рэнд", "рэнда", "ранд", "южноафриканский рэнд"],
    },
    "NZD": {
        "ru": "Новозеландский доллар",
        "en": "New Zealand Dollar",
        "aliases": ["nzd", "новозеландский доллар", "новозеландского доллара"],
    },
    "UZS": {
        "ru": "Узбекский сум",
        "en": "Uzbekistani Som",
        "aliases": ["uzs", "сум", "сума", "узбекский сум"],
    },
    "TJS": {
        "ru": "Таджикский сомони",
        "en": "Tajikistani Somoni",
        "aliases": ["tjs", "сомони", "таджикский сомони"],
    },
    "KGS": {
        "ru": "Киргизский сом",
        "en": "Kyrgyzstani Som",
        "aliases": ["kgs", "сом", "сома", "киргизский сом", "кыргызский сом"],
    },
    "MDL": {
        "ru": "Молдавский лей",
        "en": "Moldovan Leu",
        "aliases": ["mdl", "лей", "лея", "молдавский лей"],
    },
    "BGN": {
        "ru": "Болгарский лев",
        "en": "Bulgarian Lev",
        "aliases": ["bgn", "лев", "лева", "болгарский лев"],
    },
    "RON": {
        "ru": "Румынский лей",
        "en": "Romanian Leu",
        "aliases": ["ron", "румынский лей", "румынского лея"],
    },
    "RSD": {
        "ru": "Сербский динар",
        "en": "Serbian Dinar",
        "aliases": ["rsd", "динар", "динара", "сербский динар"],
    },
    "EGP": {
        "ru": "Египетский фунт",
        "en": "Egyptian Pound",
        "aliases": ["egp", "египетский фунт", "египетского фунта"],
    },
    "VND": {
        "ru": "Вьетнамский донг",
        "en": "Vietnamese Dong",
        "aliases": ["vnd", "донг", "донга", "вьетнамский донг"],
    },
}

_ALIAS_TO_CODE: dict[str, str] = {}
for _code, _info in CURRENCIES.items():
    _ALIAS_TO_CODE[_code.lower()] = _code
    for _alias in _info["aliases"]:
        _ALIAS_TO_CODE.setdefault(_alias, _code)

MONTHS_RU = {
    "января": 1, "январь": 1, "январе": 1,
    "февраля": 2, "февраль": 2, "феврале": 2,
    "марта": 3, "март": 3, "марте": 3,
    "апреля": 4, "апрель": 4, "апреле": 4,
    "мая": 5, "май": 5, "мае": 5,
    "июня": 6, "июнь": 6, "июне": 6,
    "июля": 7, "июль": 7, "июле": 7,
    "августа": 8, "август": 8, "августе": 8,
    "сентября": 9, "сентябрь": 9, "сентябре": 9,
    "октября": 10, "октябрь": 10, "октябре": 10,
    "ноября": 11, "ноябрь": 11, "ноябре": 11,
    "декабря": 12, "декабрь": 12, "декабре": 12,
}
MONTHS_EN = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}
MONTH_NAMES_RU = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
MONTH_NAMES_EN = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

DAY_ORDINALS_RU = {
    "первое": 1, "первого": 1, "второе": 2, "второго": 2,
    "третье": 3, "третьего": 3, "четвертое": 4, "четвертого": 4,
    "пятое": 5, "пятого": 5, "шестое": 6, "шестого": 6,
    "седьмое": 7, "седьмого": 7, "восьмое": 8, "восьмого": 8,
    "девятое": 9, "девятого": 9, "десятое": 10, "десятого": 10,
    "одиннадцатое": 11, "одиннадцатого": 11,
    "двенадцатое": 12, "двенадцатого": 12,
    "тринадцатое": 13, "тринадцатого": 13,
    "четырнадцатое": 14, "четырнадцатого": 14,
    "пятнадцатое": 15, "пятнадцатого": 15,
    "шестнадцатое": 16, "шестнадцатого": 16,
    "семнадцатое": 17, "семнадцатого": 17,
    "восемнадцатое": 18, "восемнадцатого": 18,
    "девятнадцатое": 19, "девятнадцатого": 19,
    "двадцатое": 20, "двадцатого": 20,
    "двадцать первое": 21, "двадцать первого": 21,
    "двадцать второе": 22, "двадцать второго": 22,
    "двадцать третье": 23, "двадцать третьего": 23,
    "двадцать четвертое": 24, "двадцать четвертого": 24,
    "двадцать пятое": 25, "двадцать пятого": 25,
    "двадцать шестое": 26, "двадцать шестого": 26,
    "двадцать седьмое": 27, "двадцать седьмого": 27,
    "двадцать восьмое": 28, "двадцать восьмого": 28,
    "двадцать девятое": 29, "двадцать девятого": 29,
    "тридцатое": 30, "тридцатого": 30,
    "тридцать первое": 31, "тридцать первого": 31,
}


def normalize(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s./-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _ssl_contexts() -> list[ssl.SSLContext]:
    """Сначала обычный SSL, затем без проверки — у ЦБ часто нет цепочки в CA."""
    contexts = [ssl.create_default_context()]
    try:
        contexts.append(ssl._create_unverified_context())
    except Exception:
        pass
    return contexts


def _http(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes | None:
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    request = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data else "GET")
    last_error = None
    for ctx in _ssl_contexts():
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=ctx) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    return None
                return payload
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        print(f"[CBR] HTTP: {last_error}")
    return None


# ---------------------------------------------------------------------------
# Разбор валюты и даты из голосовой фразы
# ---------------------------------------------------------------------------

def resolve_currency(text: str) -> str | None:
    """Ищет код валюты в произвольной фразе."""
    norm = normalize(text)
    if not norm:
        return None
    if norm in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[norm]

    best, best_len = None, 0
    for alias, code in _ALIAS_TO_CODE.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", norm):
            if len(alias) > best_len:
                best, best_len = code, len(alias)
    return best


def parse_date(text: str, today: date | None = None) -> date | None:
    """Разбирает дату: сегодня/вчера, 15.03.2024, «15 марта», «пятнадцатое марта»."""
    today = today or date.today()
    norm = normalize(text)
    if not norm:
        return today

    if norm in ("сегодня", "today", "сейчас"):
        return today
    if norm in ("вчера", "yesterday"):
        return today - timedelta(days=1)
    if norm in ("позавчера", "day before yesterday"):
        return today - timedelta(days=2)

    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", norm)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        return _safe_date(year, month, day)

    m = re.fullmatch(r"(\d{1,2})\s+(\w+)(?:\s+(\d{4}))?", norm)
    if m:
        day = int(m.group(1))
        month = MONTHS_RU.get(m.group(2)) or MONTHS_EN.get(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if month:
            return _safe_date(year, month, day)

    # «пятнадцатое марта» / «двадцать первого марта 2024»
    for ordinal, day in sorted(DAY_ORDINALS_RU.items(), key=lambda x: -len(x[0])):
        if ordinal in norm:
            rest = norm.replace(ordinal, " ", 1).strip()
            words = rest.split()
            month = None
            year = today.year
            for word in words:
                if word in MONTHS_RU:
                    month = MONTHS_RU[word]
                elif word.isdigit() and len(word) == 4:
                    year = int(word)
            if month:
                return _safe_date(year, month, day)

    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def split_currency_and_date(text: str) -> tuple[str, str]:
    """Делит хвост слота: «доллара на 15 марта» → («доллара», «15 марта»)."""
    norm = normalize(text)
    if not norm:
        return "", ""
    # «на» / «on» / «for» — разделитель даты. Границы слов, чтобы не резать
    # «канадский» и «pound».
    parts = re.split(r"(?<!\w)(?:на|on|for)(?!\w)", norm, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return norm, ""


def parse_currency_query(text: str) -> tuple[str | None, date | None]:
    """Возвращает (код валюты, дата или сегодня). Дата None только при ошибке разбора."""
    currency_text, date_text = split_currency_and_date(text)
    code = resolve_currency(currency_text) or resolve_currency(text)
    if not code:
        return None, None
    if date_text:
        parsed = parse_date(date_text)
        if parsed is None:
            return code, None  # валюта есть, дата битая — отдельный ответ
        return code, parsed
    return code, date.today()


# ---------------------------------------------------------------------------
# Форматирование для речи
# ---------------------------------------------------------------------------

def _format_rate(value: float) -> str:
    """Число с запятой для русского TTS: «92,45»."""
    if value >= 100:
        text = f"{value:.2f}"
    elif value >= 1:
        text = f"{value:.2f}"
    else:
        text = f"{value:.4f}"
    text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _plural_ru(value: float, one: str, few: str, many: str) -> str:
    number = abs(int(round(value)))
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def speak_date(day: date, lang: str = "ru", is_today: bool = False) -> str:
    if is_today:
        return "сегодня" if lang == "ru" else "today"
    if lang == "en":
        return f"{MONTH_NAMES_EN[day.month]} {day.day}, {day.year}"
    # «пятнадцатое марта две тысячи двадцать четвёртого» — для TTS достаточно
    # цифр: Silero/kokoro сами развернут «15 марта 2024».
    return f"{day.day} {MONTH_NAMES_RU[day.month]} {day.year}"


# ---------------------------------------------------------------------------
# Запросы к ЦБ
# ---------------------------------------------------------------------------

def _parse_soap_rates(xml_bytes: bytes) -> dict[str, dict]:
    """Код → {name, nominal, value, unit_rate}."""
    root = ET.fromstring(xml_bytes)
    rates = {}
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag != "ValuteCursOnDate":
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
                  for child in list(node)}
        code = (fields.get("VchCode") or "").upper()
        if not code:
            continue
        try:
            nominal = float((fields.get("Vnom") or "1").replace(",", "."))
            value = float((fields.get("Vcurs") or "0").replace(",", "."))
            unit = fields.get("VunitRate")
            unit_rate = float(unit.replace(",", ".")) if unit else (value / nominal if nominal else value)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        rates[code] = {
            "name": (fields.get("Vname") or code).strip(),
            "nominal": nominal,
            "value": value,
            "unit_rate": unit_rate,
        }
    return rates


def _parse_xml_daily(xml_bytes: bytes) -> dict[str, dict]:
    root = ET.fromstring(xml_bytes)
    rates = {}
    for node in root.findall("Valute"):
        code = (node.findtext("CharCode") or "").upper()
        if not code:
            continue
        try:
            nominal = float((node.findtext("Nominal") or "1").replace(",", "."))
            value = float((node.findtext("Value") or "0").replace(",", "."))
            unit = node.findtext("VunitRate")
            unit_rate = float(unit.replace(",", ".")) if unit else (value / nominal if nominal else value)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        rates[code] = {
            "name": (node.findtext("Name") or code).strip(),
            "nominal": nominal,
            "value": value,
            "unit_rate": unit_rate,
        }
    return rates


def _fetch_soap(on_date: date) -> dict[str, dict] | None:
    on_iso = on_date.strftime("%Y-%m-%dT00:00:00")
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        "<soap:Body>"
        '<GetCursOnDate xmlns="http://web.cbr.ru/">'
        f"<On_date>{on_iso}</On_date>"
        "</GetCursOnDate>"
        "</soap:Body>"
        "</soap:Envelope>"
    ).encode("utf-8")
    raw = _http(
        SOAP_URL,
        data=body,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{SOAP_ACTION}"',
        },
    )
    if not raw:
        return None
    try:
        rates = _parse_soap_rates(raw)
        return rates or None
    except ET.ParseError as exc:
        print(f"[CBR] SOAP parse: {exc}")
        return None


def _fetch_xml_daily(on_date: date) -> dict[str, dict] | None:
    url = XML_DAILY_URL.format(
        dd=f"{on_date.day:02d}", mm=f"{on_date.month:02d}", yyyy=on_date.year
    )
    raw = _http(url)
    if not raw:
        return None
    try:
        rates = _parse_xml_daily(raw)
        return rates or None
    except ET.ParseError as exc:
        print(f"[CBR] XML parse: {exc}")
        return None


def fetch_rates(on_date: date) -> dict[str, dict] | None:
    """Курсы на дату: SOAP GetCursOnDate, запасной — XML_daily.asp."""
    rates = _fetch_soap(on_date)
    if rates:
        return rates
    return _fetch_xml_daily(on_date)


def get_rate(
    code: str,
    on_date: date | None = None,
    lang: str = "ru",
    use_cache: bool = True,
) -> dict | None:
    """Готовые строки для голосового ответа. ``None`` — сеть/валюта/дата."""
    code = (code or "").upper().strip()
    if not code:
        return None
    on_date = on_date or date.today()
    today = date.today()
    # Будущие даты ЦБ не отдаёт — берём сегодня.
    if on_date > today:
        on_date = today

    key = f"{code}:{on_date.isoformat()}:{lang}"
    now = time.time()
    if use_cache:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]

    rates = fetch_rates(on_date)
    if not rates or code not in rates:
        # На выходных ЦБ публикует курс предыдущего рабочего дня — пробуем
        # откат на несколько дней назад, если точная дата пустая.
        if rates is not None and code not in rates:
            return None
        for back in range(1, 6):
            rates = fetch_rates(on_date - timedelta(days=back))
            if rates and code in rates:
                on_date = on_date - timedelta(days=back)
                break
        else:
            return None

    info = rates[code]
    unit = float(info["unit_rate"])
    name = CURRENCIES.get(code, {}).get(lang) or info["name"] or code
    rate_text = _format_rate(unit)
    if lang == "ru":
        rate_word = _plural_ru(unit, "рубль", "рубля", "рублей")
        # С дробью в речи привычнее «рубля»: «92,45 рубля».
        if "," in rate_text:
            rate_word = "рубля"
    else:
        rate_word = "ruble" if abs(int(round(unit))) == 1 else "rubles"

    is_today = on_date == today
    parts = {
        "code": code,
        "name": name,
        "rate": rate_text,
        "rate_word": rate_word,
        "date": on_date.strftime("%d.%m.%Y"),
        "date_spoken": speak_date(on_date, lang, is_today=is_today),
        "is_today": is_today,
        "nominal": int(info["nominal"]) if info["nominal"] == int(info["nominal"]) else info["nominal"],
    }
    _cache[key] = (now, parts)
    return parts


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "доллара"
    code, day = parse_currency_query(query)
    print("query:", query, "->", code, day)
    if code and day:
        print(get_rate(code, day, use_cache=False))
