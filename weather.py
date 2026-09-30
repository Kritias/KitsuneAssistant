"""Погода для голосовых команд: wttr.in и WeatherAPI.com.

Модуль сам не озвучивает фразы: он разбирает хвост команды («завтра вечером
в питере»), ходит в выбранный источник и возвращает имя шаблона из
``commands/*.command`` плюс поля для подстановки. Сбой сети, места или
горизонта — отдельные шаблоны, исключения наружу не выходят.

Горизонт запроса — 14 дней. Бесплатный WeatherAPI и JSON wttr.in отдают
три календарных дня, включая сегодня; день за этим пределом озвучивается
как ограничение поставщика, а не как пустой ответ.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import socket
import ssl
import time
import urllib.parse
from datetime import date, datetime, timedelta

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
#: Короткие таймауты: при флапе сети лучше быстро взять второй источник,
#: чем висеть по 8–12 секунд на каждом хосте.
TIMEOUT = 6.0
#: j1 у wttr довольно толстый и иногда качается медленно по chunked;
#: 4 с часто обрывали JSON на полуслове.
WTTR_TIMEOUT = 8.0
CACHE_TTL = 600.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_AHEAD = 14

#: Сколько календарных дней, включая сегодня, реально лежит в бесплатном ответе.
PROVIDER_DAYS = {"weatherapi": 3, "wttr": 3}
PROVIDER_TITLE = {"weatherapi": "WeatherAPI", "wttr": "wttr.in"}
WTTR_HOSTS = ("https://wttr.in", "https://wttr.is")

_cache: dict[tuple, tuple[float, dict]] = {}

# Якоря с фиксированными координатами: «Москва» — Кремль, «Сити» — Москва-Сити,
# остальные — центр города. Падежи и разговорные имена сходятся в один якорь.
PLACES: dict[str, dict] = {
    "dolgoprudny": {
        "where_ru": "в Долгопрудном",
        "where_en": "in Dolgoprudny",
        "lat": 55.9386,
        "lon": 37.5102,
        "aliases": [
            "долгопрудный", "долгопрудного", "долгопрудному", "долгопрудным",
            "долгопрудном", "долгопрудная", "долгопрудной", "долгопрудную",
        ],
    },
    "city": {
        "where_ru": "в Москва-Сити",
        "where_en": "in Moscow City",
        "lat": 55.7494,
        "lon": 37.5374,
        "aliases": [
            "сити", "москва сити", "москве сити", "москвы сити", "москву сити",
            "московское сити", "московском сити", "москвасити",
        ],
    },
    "moscow": {
        "where_ru": "в Москве",
        "where_en": "in Moscow",
        "lat": 55.7520,
        "lon": 37.6175,
        "aliases": [
            "москва", "москве", "москву", "москвы", "москвой",
            "кремль", "кремле", "кремля", "мск",
        ],
    },
    "kolomna": {
        "where_ru": "в Коломне",
        "where_en": "in Kolomna",
        "lat": 55.0794,
        "lon": 38.7783,
        "aliases": ["коломна", "коломны", "коломне", "коломну", "коломной"],
    },
    "spb": {
        "where_ru": "в Петербурге",
        "where_en": "in Saint Petersburg",
        "lat": 59.9343,
        "lon": 30.3351,
        "aliases": [
            "санкт петербург", "санкт петербурга", "санкт петербурге", "санкт петербургу",
            "петербург", "петербурга", "петербурге", "петербургу",
            "питер", "питера", "питере", "питеру",
            "спб", "ленинград", "ленинграда", "ленинграде",
        ],
    },
    "barnaul": {
        "where_ru": "в Барнауле",
        "where_en": "in Barnaul",
        "lat": 53.3470,
        "lon": 83.7780,
        "aliases": ["барнаул", "барнаула", "барнауле", "барнаулу"],
    },
}

#: Подписи в настройках — те же слова, которыми место зовут голосом.
PRESET_LOCATIONS = ["Долгопрудный", "Сити", "Москва", "Коломна", "Питер", "Барнаул"]

_ALIASES: dict[str, dict] = {}
for _place in PLACES.values():
    for _alias in _place["aliases"]:
        _ALIASES[_alias] = _place

PLACE_PREPS = {"в", "во", "на", "in", "at", "on", "for"}
FILLERS = {
    "пожалуйста", "плиз", "мне", "скажи", "подскажи", "расскажи",
    "please", "tell", "me", "the", "a", "an", "этих", "эти", "этом", "этой", "этот", "это",
}
PART_WORDS = {
    "утром": "morning", "утро": "morning", "утречком": "morning", "morning": "morning",
    "днем": "afternoon", "afternoon": "afternoon",
    "вечером": "evening", "вечер": "evening", "вечерок": "evening",
    "evening": "evening", "tonight": "evening",
    "ночью": "night", "ночь": "night", "night": "night",
}
PART_RU = {"morning": "утром", "afternoon": "днём", "evening": "вечером", "night": "ночью"}
PART_EN = {
    "morning": "in the morning",
    "afternoon": "in the afternoon",
    "evening": "in the evening",
    "night": "at night",
}
NOW_WORDS = {"сейчас", "теперь", "now", "currently"}
DAY_WORDS = {
    0: {"сегодня", "today"},
    1: {"завтра", "tomorrow"},
    2: {"послезавтра", "после завтра", "day after tomorrow"},
    3: {"третьего дня", "на третий день", "третий день"},
}
WEEKEND_PHRASES = {
    "на выходных", "на выходные", "в выходные", "в выходных", "выходные", "выходных",
    "на этих выходных", "в эти выходные",
    "on the weekend", "at the weekend", "this weekend", "the weekend", "weekend", "on weekend",
}
WEEKDAYS = {
    "понедельник": 0, "понедельника": 0, "понедельнике": 0, "monday": 0,
    "вторник": 1, "вторника": 1, "вторнике": 1, "tuesday": 1,
    "среда": 2, "среду": 2, "среде": 2, "среды": 2, "wednesday": 2,
    "четверг": 3, "четверга": 3, "четверге": 3, "thursday": 3,
    "пятница": 4, "пятницу": 4, "пятнице": 4, "пятницы": 4, "friday": 4,
    "суббота": 5, "субботу": 5, "субботе": 5, "субботы": 5, "saturday": 5,
    "воскресенье": 6, "воскресенья": 6, "воскресенью": 6, "sunday": 6,
}
WEEKDAY_RU = {
    0: "в понедельник", 1: "во вторник", 2: "в среду", 3: "в четверг",
    4: "в пятницу", 5: "в субботу", 6: "в воскресенье",
}
WEEKDAY_EN = {
    0: "on Monday", 1: "on Tuesday", 2: "on Wednesday", 3: "on Thursday",
    4: "on Friday", 5: "on Saturday", 6: "on Sunday",
}
MONTHS = {
    "января": 1, "январь": 1, "январе": 1, "january": 1,
    "февраля": 2, "февраль": 2, "феврале": 2, "february": 2,
    "марта": 3, "март": 3, "марте": 3, "march": 3,
    "апреля": 4, "апрель": 4, "апреле": 4, "april": 4,
    "мая": 5, "май": 5, "мае": 5, "may": 5,
    "июня": 6, "июнь": 6, "июне": 6, "june": 6,
    "июля": 7, "июль": 7, "июле": 7, "july": 7,
    "августа": 8, "август": 8, "августе": 8, "august": 8,
    "сентября": 9, "сентябрь": 9, "сентябре": 9, "september": 9,
    "октября": 10, "октябрь": 10, "октябре": 10, "october": 10,
    "ноября": 11, "ноябрь": 11, "ноябре": 11, "november": 11,
    "декабря": 12, "декабрь": 12, "декабре": 12, "december": 12,
}
ORDINALS = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "четвёртого": 4,
    "пятого": 5, "шестого": 6, "седьмого": 7, "восьмого": 8, "девятого": 9,
    "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12, "тринадцатого": 13,
    "четырнадцатого": 14, "пятнадцатого": 15, "шестнадцатого": 16,
    "семнадцатого": 17, "восемнадцатого": 18, "девятнадцатого": 19,
    "двадцатого": 20, "тридцатого": 30,
    "first": 1, "second": 2, "third": 3,
}
RU_NUMS = {
    "один": 1, "одну": 1, "одного": 1, "one": 1, "a": 1,
    "два": 2, "две": 2, "двух": 2, "two": 2,
    "три": 3, "трех": 3, "трёх": 3, "three": 3,
    "четыре": 4, "четырех": 4, "четырёх": 4, "four": 4,
    "пять": 5, "пяти": 5, "five": 5,
    "шесть": 6, "шести": 6, "six": 6,
    "семь": 7, "семи": 7, "seven": 7,
    "восемь": 8, "восьми": 8, "eight": 8,
    "девять": 9, "девяти": 9, "nine": 9,
    "десять": 10, "десяти": 10, "ten": 10,
    "одиннадцать": 11, "eleven": 11,
    "двенадцать": 12, "twelve": 12,
    "тринадцать": 13, "thirteen": 13,
    "четырнадцать": 14, "fourteen": 14,
}
MONTHS_RU = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
MONTHS_EN = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
PAST_WORDS = {"вчера", "позавчера", "yesterday"}
HOUR_WINDOW = {
    "morning": range(6, 12),
    "afternoon": range(12, 18),
    "evening": range(18, 24),
    "night": range(0, 6),
}
HOUR_FOCUS = {"morning": 9, "afternoon": 15, "evening": 21, "night": 3}


def normalize(text) -> str:
    """Нижний регистр, ё→е, без пунктуации — как у команд в ядре."""
    text = str(text or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe(text) -> str:
    return str(text or "").replace("{", " ").replace("}", " ").replace("|", " ").strip()


def _is_time_token(token: str) -> bool:
    if token.isdigit() or token in PART_WORDS or token in WEEKDAYS or token in MONTHS:
        return True
    if token in ORDINALS or token in RU_NUMS or token in PAST_WORDS or token in NOW_WORDS:
        return True
    return token in {
        "сегодня", "завтра", "послезавтра", "после", "третьего", "третий",
        "дня", "день", "дней", "через", "выходных", "выходные", "выходных",
        "today", "tomorrow", "tonight", "weekend", "day", "days", "after",
        "next", "следующий", "следующую", "следующей", "следующее", "следующая",
    }


def resolve_location_setting(text):
    """Настройка «локация по умолчанию» → якорь или свободное имя города."""
    tokens = normalize(text).split()
    if not tokens:
        return None, None
    place, rest = _pop_known(tokens)
    if place and not rest:
        return place, None
    if normalize(text) in _ALIASES:
        return _ALIASES[normalize(text)], None
    return None, normalize(text)


def parse_weather_query(text, today=None) -> dict:
    """Хвост фразы → место, вид запроса и сдвиг в днях.

    Пустая строка — погода сейчас в локации по умолчанию (её подставит вызывающий).
    ``error`` — ``past`` / ``too_far`` / ``bad``, если время разобрать нельзя.
    """
    today = today or date.today()
    tokens = [t for t in normalize(text).split() if t not in FILLERS]
    part, tokens = _pop_part(tokens)
    place, tokens = _pop_known(tokens)
    place_text = None
    if place is None:
        place_text, tokens = _pop_free_place(tokens)
    tokens = _strip_time_preps(tokens)

    parsed = _parse_when(tokens, today, part)
    parsed["place"] = place
    parsed["place_text"] = place_text
    if parsed.get("error") == "bad" and place_text is None and place is None and tokens:
        # «погода казань» — всё хвост является городом, времени в нём нет.
        if not any(_is_time_token(t) for t in tokens):
            parsed = {"kind": "now", "offset": 0, "part": None, "error": None,
                      "when_low": "", "when_cap": ""}
            parsed["place"] = None
            parsed["place_text"] = " ".join(tokens)
    return parsed


def answer(query, *, provider="weatherapi", default_location="Долгопрудный",
           api_key="", lang="ru", today=None) -> dict:
    """Полный ответ: шаблон, поля и какой источник реально ответил."""
    today = today or date.today()
    lang = "en" if str(lang).lower().startswith("en") else "ru"
    provider = _canon_provider(provider)
    parsed = parse_weather_query(query, today=today)

    if parsed.get("error"):
        return _error_payload(parsed["error"], provider, parsed.get("place_text") or "", lang)

    place = parsed.get("place")
    place_text = parsed.get("place_text")
    if place is None and not place_text:
        place, place_text = resolve_location_setting(default_location)
    where = _speech_where(place, place_text, lang)
    targets = _api_queries(place, place_text, default_location)

    offsets = _needed_offsets(parsed, today)
    if any(off > MAX_AHEAD for off in offsets):
        return _error_payload("too_far", provider, place_text or "", lang)
    if any(off < 0 for off in offsets):
        return _error_payload("past", provider, place_text or "", lang)

    horizon = PROVIDER_DAYS.get(provider, 3)
    inside = [off for off in offsets if off < horizon]
    if offsets and not inside:
        return _error_payload("horizon", provider, place_text or "", lang)

    fetched = {"ok": False, "error": "place"}
    for target in targets:
        fetched = _fetch_with_fallback(provider, target, api_key, lang)
        if fetched.get("ok") or fetched.get("error") != "place":
            break
    if not fetched.get("ok"):
        err = fetched.get("error") or "network"
        if err == "place":
            return _error_payload("place", provider, place_text or (targets[0] if targets else ""), lang)
        if err == "key":
            return _error_payload("key", provider, place_text or "", lang)
        return _error_payload("network", provider, place_text or "", lang)

    used = fetched.get("provider") or provider
    # Запасной источник мог оказаться короче или длиннее — режем по факту.
    used_horizon = len(fetched.get("days") or []) or PROVIDER_DAYS.get(used, horizon)
    rendered = _render(parsed, fetched, where, lang, today, used_horizon)
    rendered["fallback"] = bool(fetched.get("fallback"))
    rendered["provider"] = used
    return rendered


# ---------------------------------------------------------------------------
# Разбор фразы
# ---------------------------------------------------------------------------

def _pop_part(tokens):
    part = None
    kept = []
    for token in tokens:
        if token in PART_WORDS:
            part = PART_WORDS[token]
        else:
            kept.append(token)
    return part, kept


def _pop_known(tokens):
    best = None
    for i in range(len(tokens)):
        for length in (3, 2, 1):
            if i + length > len(tokens):
                continue
            phrase = " ".join(tokens[i:i + length])
            place = _ALIASES.get(phrase)
            if place and (best is None or length > best[0]):
                best = (length, i, i + length, place)
    if not best:
        return None, tokens
    _, start, end, place = best
    if start > 0 and tokens[start - 1] in PLACE_PREPS:
        start -= 1
    return place, tokens[:start] + tokens[end:]


def _pop_free_place(tokens):
    best = None
    for i, token in enumerate(tokens):
        if token not in PLACE_PREPS or i + 1 >= len(tokens):
            continue
        if _is_time_token(tokens[i + 1]):
            continue
        j = i + 1
        while j < len(tokens) and tokens[j] not in PLACE_PREPS and not _is_time_token(tokens[j]):
            j += 1
        if j > i + 1 and (best is None or (j - i) > (best[1] - best[0])):
            best = (i, j)
    if not best:
        return None, tokens
    start, end = best
    text = " ".join(tokens[start + 1:end]).strip()
    if len(text) < 2:
        return None, tokens
    return text, tokens[:start] + tokens[end:]


def _strip_time_preps(tokens):
    kept = []
    for i, token in enumerate(tokens):
        if token in PLACE_PREPS:
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt is None or _is_time_token(nxt) or nxt in PLACE_PREPS:
                continue
        kept.append(token)
    return kept


def _parse_when(tokens, today, part):
    phrase = " ".join(tokens)
    if not tokens:
        if part:
            return _when_result("part", 0, part, today, "ru_clock")
        return _when_result("now", 0, None, today, "now")

    if phrase in PAST_WORDS:
        return {"kind": "day", "offset": -1, "part": part, "error": "past",
                "when_low": "", "when_cap": ""}
    if phrase in NOW_WORDS:
        return _when_result("now", 0, None, today, "now")
    if phrase in WEEKEND_PHRASES:
        return {"kind": "weekend", "offset": 0, "part": part, "error": None,
                "when_low": "на выходных", "when_cap": "На выходных"}

    for offset, words in DAY_WORDS.items():
        if phrase in words:
            kind = "part" if part else "day"
            return _when_result(kind, offset, part, today, "relative")

    ahead = _parse_ahead(phrase)
    if ahead is not None:
        if ahead < 1:
            return _bad()
        if ahead > MAX_AHEAD:
            return _too_far()
        kind = "part" if part else "day"
        return _when_result(kind, ahead, part, today, "relative")

    weekday = _parse_weekday(tokens)
    if weekday is not None:
        target_wd, following = weekday
        offset = (target_wd - today.weekday()) % 7
        if following:
            offset = 7 if offset == 0 else offset + 7
        if offset > MAX_AHEAD:
            return _too_far()
        kind = "part" if part else "day"
        return _when_result(kind, offset, part, today, "weekday", target_wd)

    found = _parse_calendar(tokens, today)
    if found is not None:
        offset = (found - today).days
        if offset < 0:
            return {"kind": "day", "offset": offset, "part": part, "error": "past",
                    "when_low": "", "when_cap": ""}
        if offset > MAX_AHEAD:
            return _too_far()
        kind = "part" if part else "day"
        return _when_result(kind, offset, part, today, "date")

    return _bad()


def _parse_ahead(phrase):
    match = re.fullmatch(r"(?:через|in)\s+(\w+)", phrase)
    if match:
        raw = match.group(1)
        if raw.isdigit():
            return int(raw)
        return RU_NUMS.get(raw)
    match = re.fullmatch(r"(?:через|in)\s+(\w+)\s+(?:день|дня|дней|day|days)", phrase)
    if not match:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return RU_NUMS.get(raw)


def _parse_weekday(tokens):
    toks = list(tokens)
    if toks and toks[0] in PLACE_PREPS:
        toks = toks[1:]
    following = False
    if toks and toks[0] in {"next", "следующий", "следующую", "следующей", "следующее", "следующая"}:
        following = True
        toks = toks[1:]
    if len(toks) == 1 and toks[0] in WEEKDAYS:
        return WEEKDAYS[toks[0]], following
    return None


def _day_number(tokens, index):
    token = tokens[index]
    if token.isdigit():
        day = int(token)
        if 1 <= day <= 31:
            return day, 1
    if token in ORDINALS and ORDINALS[token] <= 31:
        return ORDINALS[token], 1
    if index + 1 < len(tokens) and token in {"двадцать", "тридцать"} and tokens[index + 1] in ORDINALS:
        base = 20 if token == "двадцать" else 30
        day = base + ORDINALS[tokens[index + 1]]
        if 1 <= day <= 31:
            return day, 2
    return None


def _parse_calendar(tokens, today):
    toks = list(tokens)
    if toks and toks[0] in PLACE_PREPS:
        toks = toks[1:]
    # Точки нормализация уже съела: «15.10.2026» приходит как «15 10 2026».
    if len(toks) >= 2 and toks[0].isdigit() and toks[1].isdigit() and toks[1] not in MONTHS:
        day, month = int(toks[0]), int(toks[1])
        year = today.year
        rest = toks[2:]
        if rest and rest[0].isdigit() and len(rest[0]) in {2, 4}:
            year = int(rest[0])
            if year < 100:
                year += 2000
            rest = rest[1:]
        if rest or not (1 <= day <= 31 and 1 <= month <= 12):
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None

    got = _day_number(toks, 0) if toks else None
    if got:
        day, used = got
        rest = toks[used:]
        if rest and rest[0] in MONTHS:
            month = MONTHS[rest[0]]
            year, rest = _take_year(rest[1:], today.year)
            if rest:
                return None
            try:
                return date(year, month, day)
            except ValueError:
                return None
    if toks and toks[0] in MONTHS and len(toks) >= 2:
        month = MONTHS[toks[0]]
        got = _day_number(toks, 1)
        if not got:
            return None
        day, used = got
        year, rest = _take_year(toks[1 + used:], today.year)
        if rest:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _take_year(tokens, default):
    if tokens and tokens[0].isdigit() and len(tokens[0]) in {2, 4}:
        year = int(tokens[0])
        if year < 100:
            year += 2000
        rest = tokens[1:]
        if rest and rest[0] in {"года", "г", "year"}:
            rest = rest[1:]
        return year, rest
    if tokens and tokens[0] in {"года", "г"}:
        return default, tokens[1:]
    return default, tokens


def _when_result(kind, offset, part, today, style, weekday=None):
    low, high = _when_labels(offset, part, today, style, weekday)
    return {
        "kind": kind,
        "offset": offset,
        "part": part,
        "error": None,
        "when_low": low,
        "when_cap": high,
    }


def _when_labels(offset, part, today, style, weekday):
    if style == "weekday" and weekday is not None:
        low = WEEKDAY_RU[weekday]
    elif style == "date":
        target = today + timedelta(days=offset)
        low = f"{target.day} {MONTHS_RU[target.month]}"
    elif offset == 0:
        low = "сегодня"
    elif offset == 1:
        low = "завтра"
    elif offset == 2:
        low = "послезавтра"
    elif offset == 3:
        low = "третьего дня"
    else:
        target = today + timedelta(days=offset)
        low = f"{target.day} {MONTHS_RU[target.month]}"
    if part:
        low = f"{low} {PART_RU[part]}"
    return low, _cap(low)


def _bad():
    return {"kind": "day", "offset": 0, "part": None, "error": "bad",
            "when_low": "", "when_cap": ""}


def _too_far():
    return {"kind": "day", "offset": MAX_AHEAD + 1, "part": None, "error": "too_far",
            "when_low": "", "when_cap": ""}


def _needed_offsets(parsed, today):
    if parsed["kind"] == "now":
        return []
    if parsed["kind"] == "weekend":
        return [off for _name, off in _weekend_days(today)]
    return [int(parsed.get("offset") or 0)]


def _weekend_days(today):
    wd = today.weekday()
    if wd == 6:
        return [("sun", 0)]
    if wd == 5:
        return [("sat", 0), ("sun", 1)]
    return [("sat", 5 - wd), ("sun", 6 - wd)]


def _speech_where(place, place_text, lang):
    if place:
        return place["where_en"] if lang == "en" else place["where_ru"]
    text = (place_text or "").strip()
    if not text:
        return "here" if lang == "en" else "здесь"
    pretty = " ".join(word[:1].upper() + word[1:] for word in text.split())
    return f"in {pretty}" if lang == "en" else f"в {pretty}"


def _stem_token(word):
    """Именительный падеж для геокодера: «казани» → «казань», «омске» → «омск»."""
    stems = []

    def add(stem):
        if stem and stem != word and len(stem) >= 3 and stem not in stems:
            stems.append(stem)

    if len(word) > 3:
        if word.endswith("и"):
            add(word[:-1] + "ь")
            add(word[:-1] + "а")
        elif word.endswith("е"):
            add(word[:-1])
            add(word[:-1] + "а")
        elif word.endswith("у"):
            add(word[:-1] + "а")
        elif word.endswith("ы"):
            add(word[:-1] + "а")
        elif word.endswith("ем"):
            add(word[:-2] + "ий")
            add(word[:-2])
        elif word.endswith("ом"):
            add(word[:-2] + "ый")
            add(word[:-2])
    return stems


def _city_name_variants(text):
    words = [part for part in str(text or "").split() if part]
    if not words:
        return []
    variants = []

    def push(value):
        value = " ".join(str(value).split())
        if value and value not in variants:
            variants.append(value)

    push(" ".join((_stem_token(word) or [word])[0] for word in words))
    for stem in _stem_token(words[-1]):
        push(" ".join(words[:-1] + [stem]))
    push(" ".join(words))
    return variants[:4]


def _api_queries(place, place_text, default_location):
    if place and place.get("lat") is not None:
        return [f"{place['lat']:.4f},{place['lon']:.4f}"]
    if place_text:
        return _city_name_variants(place_text) or [place_text]
    fallback, fallback_text = resolve_location_setting(default_location)
    if fallback and fallback.get("lat") is not None:
        return [f"{fallback['lat']:.4f},{fallback['lon']:.4f}"]
    if fallback_text:
        return _city_name_variants(fallback_text) or [fallback_text]
    return [default_location or "Москва"]


def _canon_provider(provider) -> str:
    name = str(provider or "").strip().lower()
    if "wttr" in name:
        return "wttr"
    return "weatherapi"


# ---------------------------------------------------------------------------
# Сеть
# ---------------------------------------------------------------------------

def _fetch_with_fallback(provider, query, api_key, lang) -> dict:
    """Гонка источников: кто ответил первым валидным JSON — тот и победил.

    Последовательный fallback на этой сети часто упирается в долгий таймаут
    мёртвого канала, хотя второй хост уже мог бы ответить. Поэтому оба
    провайдера (и оба зеркала wttr) стартуют параллельно; выбранный в
    настройках лишь получает приоритет при одновременном успехе.
    """
    jobs = []
    if provider == "wttr":
        jobs.append(("wttr", lambda: _fetch_wttr(query, lang)))
        if str(api_key or "").strip():
            jobs.append(("weatherapi", lambda: _fetch_weatherapi(query, api_key, lang)))
    else:
        if str(api_key or "").strip():
            jobs.append(("weatherapi", lambda: _fetch_weatherapi(query, api_key, lang)))
        jobs.append(("wttr", lambda: _fetch_wttr(query, lang)))

    if not jobs:
        return {"ok": False, "error": "key"}

    # Один источник — без пула потоков.
    if len(jobs) == 1:
        name, func = jobs[0]
        fetched = _fetch_cached(name, query, lang, func)
        if fetched.get("ok"):
            fetched["provider"] = name
            fetched["fallback"] = False
            return fetched
        return {"ok": False, "error": fetched.get("error") or "network"}

    last_error = "network"
    preferred = provider
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {
            pool.submit(_fetch_cached, name, query, lang, func): name
            for name, func in jobs
        }
        pending = set(futures)
        winner = None
        while pending:
            done, pending = concurrent.futures.wait(
                pending, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for fut in done:
                name = futures[fut]
                try:
                    fetched = fut.result()
                except Exception:
                    fetched = {"ok": False, "error": "network"}
                if fetched.get("ok"):
                    fetched = dict(fetched)
                    fetched["provider"] = name
                    fetched["fallback"] = name != preferred
                    # Предпочтительный уже готов — сразу отдаём.
                    if name == preferred or winner is None:
                        winner = fetched
                    if name == preferred:
                        for other in pending:
                            other.cancel()
                        return winner
                else:
                    err = fetched.get("error") or "network"
                    if err == "place":
                        last_error = "place"
                    elif last_error != "place":
                        last_error = err
        if winner:
            return winner
    return {"ok": False, "error": last_error}


def _fetch_cached(provider, query, lang, func) -> dict:
    key = (provider, query, lang)
    cached = _cache.get(key)
    if cached and (time.time() - cached[0]) < CACHE_TTL:
        return dict(cached[1])
    fetched = func()
    if fetched.get("ok"):
        _cache[key] = (time.time(), fetched)
    return fetched


def _recv_until(sock, marker: bytes, deadline: float, limit: int) -> bytes:
    buf = b""
    while marker not in buf:
        if time.time() >= deadline or len(buf) >= limit:
            break
        try:
            sock.settimeout(max(0.05, deadline - time.time()))
            chunk = sock.recv(8192)
        except (socket.timeout, TimeoutError, OSError):
            break
        if not chunk:
            break
        buf += chunk
    return buf


def _recv_exact(sock, size: int, deadline: float) -> bytes:
    buf = b""
    while len(buf) < size:
        if time.time() >= deadline:
            break
        try:
            sock.settimeout(max(0.05, deadline - time.time()))
            chunk = sock.recv(min(65536, size - len(buf)))
        except (socket.timeout, TimeoutError, OSError):
            break
        if not chunk:
            break
        buf += chunk
    return buf


def _dechunk_http_body(raw: bytes, sock, deadline: float) -> bytes:
    """Собирает chunked-тело; останавливается на нулевом чанке или по дедлайну."""
    out = bytearray()
    view = memoryview(raw)
    pos = 0

    def need(n):
        nonlocal raw, view, pos
        while pos + n > len(raw) and time.time() < deadline and len(raw) < MAX_RESPONSE_BYTES:
            try:
                sock.settimeout(max(0.05, deadline - time.time()))
                more = sock.recv(8192)
            except (socket.timeout, TimeoutError, OSError):
                break
            if not more:
                break
            raw += more
            view = memoryview(raw)
        return pos + n <= len(raw)

    while time.time() < deadline and len(out) <= MAX_RESPONSE_BYTES:
        # строка размера чанка
        while True:
            nl = raw.find(b"\r\n", pos)
            if nl >= 0:
                break
            if not need(len(raw) - pos + 2):
                return bytes(out)
        size_line = raw[pos:nl].split(b";", 1)[0].strip()
        pos = nl + 2
        try:
            size = int(size_line, 16)
        except ValueError:
            return bytes(out)
        if size == 0:
            return bytes(out)
        if not need(size + 2):
            # частичный чанк — отдадим то, что успели, если JSON уже целый
            available = raw[pos:]
            out += available[: max(0, min(size, len(available)))]
            return bytes(out)
        out += raw[pos:pos + size]
        pos += size + 2  # данные + CRLF
    return bytes(out)


def _try_json_payload(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _http_json(url, timeout):
    """HTTPS GET по IPv4 с ручным чтением тела.

    На этой сети urllib/http.client часто зависают на chunked-ответе wttr
    (нет Content-Length и запоздалый EOF). Читаем сокет сами и останавливаемся,
    как только из тела собирается валидный JSON.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    if not host:
        return None, "", "bad_url"
    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    last_error = "network"
    for attempt in range(2):
        sock = None
        try:
            deadline = time.time() + float(timeout)
            context = ssl.create_default_context()
            infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
            for family, socktype, proto, _canon, sockaddr in infos:
                candidate = socket.socket(family, socktype, proto)
                candidate.settimeout(max(0.2, deadline - time.time()))
                try:
                    candidate.connect(sockaddr)
                    sock = candidate
                    break
                except OSError as exc:
                    last_error = exc
                    candidate.close()
            if sock is None:
                continue

            tls = context.wrap_socket(sock, server_hostname=host)
            sock = tls
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: {USER_AGENT}\r\n"
                "Accept: application/json\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            sock.sendall(request.encode("ascii", "ignore"))

            head = _recv_until(sock, b"\r\n\r\n", deadline, 65536)
            if b"\r\n\r\n" not in head:
                last_error = "no_headers"
                continue
            header_bytes, body = head.split(b"\r\n\r\n", 1)
            header_text = header_bytes.decode("iso-8859-1", "replace")
            status_line = header_text.split("\r\n", 1)[0]
            try:
                status = int(status_line.split()[1])
            except (IndexError, ValueError):
                status = 0
            headers = {}
            for line in header_text.split("\r\n")[1:]:
                if ":" in line:
                    key, val = line.split(":", 1)
                    headers[key.strip().lower()] = val.strip()

            if headers.get("transfer-encoding", "").lower() == "chunked":
                raw = _dechunk_http_body(body, sock, deadline)
            elif headers.get("content-length", "").isdigit():
                need = int(headers["content-length"])
                if len(body) < need:
                    body += _recv_exact(sock, need - len(body), deadline)
                raw = body[:need]
            else:
                # Читаем до EOF/дедлайна, но выходим раньше, если JSON уже целый.
                raw = body
                while time.time() < deadline and len(raw) <= MAX_RESPONSE_BYTES:
                    text = raw.decode("utf-8", "replace").lstrip("\ufeff")
                    if text.startswith("{") or text.startswith("["):
                        payload = _try_json_payload(text)
                        if payload is not None:
                            try:
                                sock.close()
                            except Exception:
                                pass
                            return status, text, payload
                    try:
                        sock.settimeout(max(0.05, deadline - time.time()))
                        more = sock.recv(65536)
                    except (socket.timeout, TimeoutError, OSError):
                        break
                    if not more:
                        break
                    raw += more

            try:
                sock.close()
            except Exception:
                pass

            if len(raw) > MAX_RESPONSE_BYTES:
                return status, "", "too_large"
            text = raw.decode("utf-8", "replace").lstrip("\ufeff")
            payload = _try_json_payload(text)
            return status, text, payload
        except Exception as exc:
            last_error = exc
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            if attempt == 0:
                time.sleep(0.15)
                continue
    return None, "", last_error


def _fetch_weatherapi(query, api_key, lang) -> dict:
    params = urllib.parse.urlencode({
        "key": api_key,
        "q": query,
        "days": PROVIDER_DAYS["weatherapi"],
        "lang": lang,
    })
    url = f"https://api.weatherapi.com/v1/forecast.json?{params}"
    status, body, payload = _http_json(url, TIMEOUT)
    if isinstance(payload, dict) and not payload.get("error"):
        try:
            return {
                "ok": True,
                "current": _map_weatherapi_current(payload),
                "days": _map_weatherapi_days(payload),
            }
        except (KeyError, TypeError, ValueError):
            pass
    # Толстый forecast иногда не доезжает — для «сейчас» хватит current.json.
    params_now = urllib.parse.urlencode({"key": api_key, "q": query, "lang": lang})
    status, body, payload = _http_json(
        f"https://api.weatherapi.com/v1/current.json?{params_now}", TIMEOUT
    )
    if not isinstance(payload, dict):
        return {"ok": False, "error": _weatherapi_error(status, body)}
    if payload.get("error"):
        return {"ok": False, "error": _weatherapi_error(status, body)}
    try:
        return {"ok": True, "current": _map_weatherapi_current(payload), "days": []}
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "error": "network"}


def _weatherapi_error(status, body) -> str:
    code = None
    try:
        code = json.loads(body or "{}").get("error", {}).get("code")
    except (json.JSONDecodeError, AttributeError, TypeError):
        code = None
    if code == 1006:
        return "place"
    if code in {2006, 2007, 2008, 1002, 2009}:
        return "key"
    if status in {401, 403}:
        return "key"
    return "network"


def _extract_json_objects(array_text: str, limit: int) -> list:
    """Достаёт целые `{...}` верхнего уровня из текста JSON-массива."""
    objects = []
    depth = 0
    start = None
    in_string = False
    escape = False
    for index, ch in enumerate(array_text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = index
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                blob = array_text[start:index + 1]
                try:
                    obj = json.loads(blob)
                except json.JSONDecodeError:
                    break
                if isinstance(obj, dict):
                    objects.append(obj)
                start = None
                if len(objects) >= limit:
                    break
        elif ch == "]" and depth == 0:
            break
    return objects


def _salvage_wttr_payload(text: str):
    """Достаёт current/weather из обрезанного j1, если полный JSON не доехал."""
    if not text:
        return None
    payload = _try_json_payload(text)
    if isinstance(payload, dict) and "current_condition" in payload:
        return payload

    current = []
    current_match = re.search(r'"current_condition"\s*:\s*\[', text)
    if current_match:
        current = _extract_json_objects(text[current_match.end():], 1)

    days = []
    weather_match = re.search(r'"weather"\s*:\s*\[', text)
    if weather_match:
        days = [
            day for day in _extract_json_objects(text[weather_match.end():], PROVIDER_DAYS["wttr"])
            if day.get("date")
        ]

    if not current and not days:
        return None
    return {"current_condition": current, "weather": days}


def _fetch_wttr(query, lang) -> dict:
    quoted = urllib.parse.quote(query, safe=",.")
    # Для координат wttr надёжнее понимает форму с «~».
    if re.fullmatch(r"-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?", query.strip()):
        quoted = "~" + quoted
    last = "network"
    # Зеркала тоже гоняем параллельно — одно из двух часто живее.
    def one(host):
        status, body, payload = _http_json(f"{host}/{quoted}?format=j1&lang={lang}", WTTR_TIMEOUT)
        if not isinstance(payload, dict) or "current_condition" not in payload:
            payload = _salvage_wttr_payload(body or "")
        if not isinstance(payload, dict) or (
            "current_condition" not in payload and "weather" not in payload
        ):
            return {"ok": False, "error": "place" if status == 404 else "network"}
        try:
            current = _map_wttr_current(payload, lang)
            days = _map_wttr_days(payload, lang)
            if not _current_useful(current) and days:
                current = _current_from_day(days[0])
            mapped = {"ok": True, "current": current, "days": days}
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "network"}
        if mapped["days"] or _current_useful(mapped["current"]):
            return mapped
        return {"ok": False, "error": "network"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(WTTR_HOSTS)) as pool:
        futures = [pool.submit(one, host) for host in WTTR_HOSTS]
        for fut in concurrent.futures.as_completed(futures):
            try:
                result = fut.result()
            except Exception:
                continue
            if result.get("ok"):
                for other in futures:
                    other.cancel()
                return result
            last = result.get("error") or last
    return {"ok": False, "error": last}


def _map_weatherapi_current(payload) -> dict:
    current = payload.get("current") or {}
    condition = (current.get("condition") or {}).get("text") or ""
    return {
        "temp": _num(current.get("temp_c")),
        "feels": _num(current.get("feelslike_c"), _num(current.get("temp_c"))),
        "text": condition,
        "rain": 0.0,
        "snow": 0.0,
        "wind": _num(current.get("wind_kph")),
    }


def _map_weatherapi_days(payload) -> list:
    days = []
    for item in (payload.get("forecast") or {}).get("forecastday") or []:
        day = item.get("day") or {}
        hours = []
        for hour in item.get("hour") or []:
            stamp = str(hour.get("time") or "")
            clock = stamp[-5:] if len(stamp) >= 5 else "0"
            try:
                hh = int(clock.split(":")[0])
            except ValueError:
                hh = 0
            hours.append({
                "h": hh,
                "temp": _num(hour.get("temp_c")),
                "feels": _num(hour.get("feelslike_c"), _num(hour.get("temp_c"))),
                "text": (hour.get("condition") or {}).get("text") or "",
                "rain": _num(hour.get("chance_of_rain")),
                "snow": _num(hour.get("chance_of_snow")),
                "wind": _num(hour.get("wind_kph")),
            })
        days.append({
            "date": str(item.get("date") or ""),
            "tmin": _num(day.get("mintemp_c")),
            "tmax": _num(day.get("maxtemp_c")),
            "text": (day.get("condition") or {}).get("text") or "",
            "rain": _num(day.get("daily_chance_of_rain")),
            "snow": _num(day.get("daily_chance_of_snow")),
            "wind": _num(day.get("maxwind_kph")),
            "hours": hours,
        })
    return days


def _wttr_text(obj, lang) -> str:
    preferred = "lang_ru" if lang == "ru" else "lang_en"
    for key in (preferred, "lang_ru", "weatherDesc"):
        val = obj.get(key)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            text = val[0].get("value")
            if text:
                return str(text)
    return ""


def _current_useful(current: dict) -> bool:
    if not current:
        return False
    # temp=0 при пустом current_condition — не считаем это ответом.
    return bool(str(current.get("text") or "").strip())


def _current_from_day(day: dict) -> dict:
    """Если current_condition обрезался, берём ближайший час из дневного блока."""
    hours = day.get("hours") or []
    now_h = datetime.now().hour
    picked = min(hours, key=lambda h: abs(int(h.get("h", 12)) - now_h)) if hours else None
    if picked:
        return {
            "temp": picked.get("temp", 0),
            "feels": picked.get("feels", picked.get("temp", 0)),
            "text": picked.get("text") or day.get("text") or "",
            "rain": picked.get("rain", 0),
            "snow": picked.get("snow", 0),
            "wind": picked.get("wind", 0),
        }
    return {
        "temp": day.get("tmax", 0),
        "feels": day.get("tmax", 0),
        "text": day.get("text") or "",
        "rain": day.get("rain", 0),
        "snow": day.get("snow", 0),
        "wind": day.get("wind", 0),
    }


def _map_wttr_current(payload, lang) -> dict:
    items = payload.get("current_condition") or []
    current = items[0] if items else {}
    if not current:
        return {"temp": 0, "feels": 0, "text": "", "rain": 0.0, "snow": 0.0, "wind": 0.0}
    return {
        "temp": _num(current.get("temp_C")),
        "feels": _num(current.get("FeelsLikeC"), _num(current.get("temp_C"))),
        "text": _wttr_text(current, lang),
        "rain": 100.0 if _num(current.get("precipMM")) >= 0.2 else 0.0,
        "snow": 0.0,
        "wind": _num(current.get("windspeedKmph")),
    }


def _map_wttr_days(payload, lang) -> list:
    days = []
    for item in payload.get("weather") or []:
        hours = []
        rains, snows, winds = [], [], []
        for hour in item.get("hourly") or []:
            try:
                hh = int(str(hour.get("time") or "0")) // 100
            except ValueError:
                hh = 0
            rain = _num(hour.get("chanceofrain"))
            snow = _num(hour.get("chanceofsnow"))
            wind = _num(hour.get("windspeedKmph"))
            rains.append(rain)
            snows.append(snow)
            winds.append(wind)
            hours.append({
                "h": hh,
                "temp": _num(hour.get("tempC")),
                "feels": _num(hour.get("FeelsLikeC"), _num(hour.get("tempC"))),
                "text": _wttr_text(hour, lang),
                "rain": rain,
                "snow": snow,
                "wind": wind,
            })
        midday = next((h for h in hours if h["h"] in {12, 15}), hours[len(hours) // 2] if hours else None)
        days.append({
            "date": str(item.get("date") or ""),
            "tmin": _num(item.get("mintempC")),
            "tmax": _num(item.get("maxtempC")),
            "text": (midday or {}).get("text") or "",
            "rain": max(rains) if rains else 0.0,
            "snow": max(snows) if snows else 0.0,
            "wind": max(winds) if winds else 0.0,
            "hours": hours,
        })
    return days


# ---------------------------------------------------------------------------
# Текст для шаблонов
# ---------------------------------------------------------------------------

def _parts(**kwargs):
    base = {
        "where": "", "where_cap": "", "temp": "", "temp_word": "",
        "feels": "", "feels_word": "", "condition": "", "when": "", "when_cap": "",
        "tmin": "", "tmax": "", "tmax_word": "", "details": "",
        "provider": "", "days": "", "place": "",
    }
    base.update(kwargs)
    return base


def _error_payload(kind, provider, place_text, lang="ru") -> dict:
    titles = {
        "past": "weather_past",
        "too_far": "weather_too_far",
        "bad": "weather_bad_when",
        "horizon": "weather_horizon",
        "place": "weather_unknown_place",
        "key": "weather_no_key",
        "network": "weather_fail",
        "missing": "weather_missing",
    }
    return {
        "template": titles.get(kind, "weather_fail"),
        "parts": _parts(
            provider=PROVIDER_TITLE.get(provider, provider),
            days=_days_phrase(PROVIDER_DAYS.get(provider, 3), lang),
            place=_safe(place_text),
        ),
        "fallback": False,
        "provider": provider,
    }


def _days_phrase(count, lang) -> str:
    count = int(count)
    if lang == "en":
        return "1 day" if count == 1 else f"{count} days"
    if 11 <= count % 100 <= 14:
        word = "дней"
    elif count % 10 == 1:
        word = "день"
    elif count % 10 in {2, 3, 4}:
        word = "дня"
    else:
        word = "дней"
    return f"{count} {word}"


#: WeatherAPI иногда отдаёт английский текст даже при lang=ru (например Smog).
CONDITION_RU = {
    "smog": "смог",
    "smoke": "дым",
    "haze": "мгла",
    "mist": "дымка",
    "fog": "туман",
    "freezing fog": "ледяной туман",
    "clear": "ясно",
    "sunny": "солнечно",
    "cloudy": "облачно",
    "overcast": "пасмурно",
    "partly cloudy": "переменная облачность",
    "patchy rain possible": "местами возможен дождь",
    "patchy snow possible": "местами возможен снег",
    "patchy sleet possible": "местами возможен мокрый снег",
    "thundery outbreaks possible": "возможны грозы",
    "blowing snow": "низовая метель",
    "blizzard": "метель",
    "light rain": "небольшой дождь",
    "moderate rain": "умеренный дождь",
    "heavy rain": "сильный дождь",
    "light snow": "небольшой снег",
    "moderate snow": "умеренный снег",
    "heavy snow": "сильный снег",
    "light drizzle": "морось",
    "freezing drizzle": "ледяная морось",
    "ice pellets": "ледяная крупа",
    "torrential rain shower": "ливень",
    "light rain shower": "небольшой ливень",
    "moderate or heavy rain shower": "ливень",
}


def _degree_word(value, lang) -> str:
    number = abs(int(round(float(value))))
    if lang == "en":
        return "degree" if number == 1 else "degrees"
    try:
        import tts_local
        return tts_local.plural_form(number, ("градус", "градуса", "градусов"))
    except Exception:
        if 11 <= number % 100 <= 14:
            return "градусов"
        if number % 10 == 1:
            return "градус"
        if number % 10 in {2, 3, 4}:
            return "градуса"
        return "градусов"


def _temp_spoken(value, lang) -> tuple[str, str]:
    """Температура уже словами: Silero иначе читает «11» как «один один»."""
    number = int(round(float(value)))
    word = _degree_word(number, lang)
    if lang == "en":
        if number < 0:
            return f"minus {abs(number)}", word
        return str(number), word
    try:
        import tts_local
        words = tts_local.int_to_words(abs(number), "муж")
    except Exception:
        words = str(abs(number))
    if number < 0:
        return f"минус {words}", word
    return words, word


def _localize_condition(text, lang) -> str:
    text = _safe(text)
    if not text:
        return ""
    if lang != "ru":
        return text[:1].lower() + text[1:] if text else text
    # Уже кириллица — оставляем как есть.
    if re.search(r"[а-яё]", text.lower()):
        return text[:1].lower() + text[1:]
    key = re.sub(r"\s+", " ", text.strip().lower())
    mapped = CONDITION_RU.get(key)
    if mapped:
        return mapped
    # Частичное совпадение для «Patchy light rain in area with thunder» и т.п.
    for eng, rus in sorted(CONDITION_RU.items(), key=lambda item: -len(item[0])):
        if eng in key:
            return rus
    # Латиницу в речь не пускаем: Silero/kokoro её мямлят.
    return "без ясных примет"


def _decorate(text, rain, snow, wind, lang) -> str:
    text = _localize_condition(text, lang)
    extras = []
    if snow >= 40 and snow >= rain:
        extras.append("snow possible" if lang == "en" else "возможен снег")
    elif rain >= 40:
        extras.append("rain possible" if lang == "en" else "возможен дождь")
    if wind >= 40:
        extras.append("windy" if lang == "en" else "ветрено")
    if extras:
        text = f"{text}, {', '.join(extras)}" if text else ", ".join(extras)
    if not text:
        text = "no clear details" if lang == "en" else "без ясных примет"
    return text


def _find_day(days, today, offset):
    target = (today + timedelta(days=offset)).isoformat()
    for item in days:
        if item.get("date") == target:
            return item
    if 0 <= offset < len(days):
        return days[offset]
    return None


def _hour_slice(day, part):
    window = set(HOUR_WINDOW[part])
    hours = [h for h in day.get("hours") or [] if h["h"] in window]
    if not hours:
        return None
    focus = HOUR_FOCUS[part]
    picked = min(hours, key=lambda h: abs(h["h"] - focus))
    return {
        "temp": picked["temp"],
        "feels": picked["feels"],
        "text": picked["text"] or day.get("text") or "",
        "rain": max(h["rain"] for h in hours),
        "snow": max(h["snow"] for h in hours),
        "wind": max(h["wind"] for h in hours),
    }


def _render(parsed, fetched, where, lang, today, horizon) -> dict:
    # Подписи «завтра / в пятницу» в ответе — на языке интерфейса.
    # Разбор фразы русский и английский понимает одинаково, а ярлык
    # пересобираем здесь, чтобы английский шаблон не получил «завтра».
    where_cap = _cap(where)
    provider_name = PROVIDER_TITLE.get(fetched.get("provider"), "")
    if lang == "en":
        parsed = _relabel_en(parsed, today)

    if parsed["kind"] == "now":
        current = fetched.get("current") or {}
        temp, temp_word = _temp_spoken(current.get("temp", 0), lang)
        feels, feels_word = _temp_spoken(current.get("feels", current.get("temp", 0)), lang)
        return {
            "template": "weather_now",
            "parts": _parts(
                where=where, where_cap=where_cap, temp=temp, temp_word=temp_word,
                feels=feels, feels_word=feels_word,
                condition=_decorate(current.get("text"), current.get("rain", 0), current.get("snow", 0), current.get("wind", 0), lang),
            ),
        }

    if parsed["kind"] == "weekend":
        details = _weekend_details(parsed, fetched, lang, today, horizon)
        if not details:
            return _error_payload("horizon", fetched.get("provider") or "weatherapi", "", lang)
        return {
            "template": "weather_weekend",
            "parts": _parts(where=where, where_cap=where_cap, details=details, provider=provider_name),
        }

    day = _find_day(fetched.get("days") or [], today, int(parsed.get("offset") or 0))
    if not day:
        return _error_payload("missing", fetched.get("provider") or "weatherapi", "", lang)

    when_low = parsed.get("when_low") or ""
    when_cap = parsed.get("when_cap") or _cap(when_low)
    if parsed.get("part"):
        slot = _hour_slice(day, parsed["part"])
        if not slot:
            return _error_payload("missing", fetched.get("provider") or "weatherapi", "", lang)
        temp, temp_word = _temp_spoken(slot["temp"], lang)
        feels, feels_word = _temp_spoken(slot["feels"], lang)
        return {
            "template": "weather_part",
            "parts": _parts(
                where=where, where_cap=where_cap, when=when_low, when_cap=when_cap,
                temp=temp, temp_word=temp_word, feels=feels, feels_word=feels_word,
                condition=_decorate(slot["text"], slot["rain"], slot["snow"], slot["wind"], lang),
            ),
        }

    tmin, _ = _temp_spoken(day["tmin"], lang)
    tmax, tmax_word = _temp_spoken(day["tmax"], lang)
    return {
        "template": "weather_day",
        "parts": _parts(
            where=where, where_cap=where_cap, when=when_low, when_cap=when_cap,
            tmin=tmin, tmax=tmax, tmax_word=tmax_word,
            condition=_decorate(day["text"], day["rain"], day["snow"], day["wind"], lang),
        ),
    }


def _relabel_en(parsed, today):
    """Английские ярлыки времени поверх уже разобранного сдвига."""
    kind = parsed["kind"]
    part = parsed.get("part")
    offset = int(parsed.get("offset") or 0)
    if kind in {"now", "weekend"}:
        if kind == "weekend":
            parsed = dict(parsed)
            parsed["when_low"] = "on the weekend"
            parsed["when_cap"] = "On the weekend"
        return parsed
    if offset == 0:
        low = "today"
    elif offset == 1:
        low = "tomorrow"
    elif offset == 2:
        low = "the day after tomorrow"
    else:
        target = today + timedelta(days=offset)
        low = f"on {MONTHS_EN[target.month]} {target.day}"
    if part:
        low = f"{low} {PART_EN[part]}"
    parsed = dict(parsed)
    parsed["when_low"] = low
    parsed["when_cap"] = _cap(low)
    return parsed


def _weekend_details(parsed, fetched, lang, today, horizon) -> str:
    bits = []
    skipped = False
    for name, offset in _weekend_days(today):
        if offset >= horizon or offset > MAX_AHEAD:
            skipped = True
            continue
        day = _find_day(fetched.get("days") or [], today, offset)
        if not day:
            skipped = True
            continue
        label = _weekend_label(name, lang)
        if parsed.get("part"):
            slot = _hour_slice(day, parsed["part"])
            if not slot:
                skipped = True
                continue
            temp, word = _temp_spoken(slot["temp"], lang)
            cond = _decorate(slot["text"], slot["rain"], slot["snow"], slot["wind"], lang)
            piece = f"{label} {temp} {word}, {cond}" if lang == "en" else f"{label} около {temp} {word}, {cond}"
        else:
            tmin, _ = _temp_spoken(day["tmin"], lang)
            tmax, word = _temp_spoken(day["tmax"], lang)
            cond = _decorate(day["text"], day["rain"], day["snow"], day["wind"], lang)
            if lang == "en":
                piece = f"{label} {tmin} to {tmax} {word}, {cond}"
            else:
                piece = f"{label} от {tmin} до {tmax} {word}, {cond}"
        bits.append(piece)
    if not bits:
        return ""
    note = ""
    if skipped:
        note = " The rest of the weekend is outside this forecast." if lang == "en" else " Дальше этих выходных прогноз уже не достаёт."
    if len(bits) == 1:
        text = bits[0]
    else:
        text = bits[0] + ". " + ". ".join(_cap(bit) for bit in bits[1:])
    if not text.endswith("."):
        text += "."
    return text + note


def _weekend_label(name, lang) -> str:
    if lang == "en":
        return "Saturday" if name == "sat" else "Sunday"
    return "в субботу" if name == "sat" else "в воскресенье"


if __name__ == "__main__":
    sample_day = date(2026, 9, 29)  # вторник
    samples = [
        ("", "now", None),
        ("в долгопрудном", "now", "в Долгопрудном"),
        ("завтра", "day", None),
        ("завтра вечером в сити", "part", "в Москва-Сити"),
        ("на выходных", "weekend", None),
        ("третьего дня в коломне", "day", "в Коломне"),
        ("в питере", "now", "в Петербурге"),
        ("в барнауле", "now", "в Барнауле"),
        ("в москве", "now", "в Москве"),
        ("в казани", "now", "в казани"),
        ("через 5 дней", "day", None),
        ("вчера", "past", None),
    ]
    for text, kind, where in samples:
        got = parse_weather_query(text, today=sample_day)
        place = got.get("place") or {}
        seen = place.get("where_ru") or (("в " + got["place_text"]) if got.get("place_text") else None)
        err = got.get("error")
        flag = "OK" if (err or got["kind"]) and (kind == "past" and err == "past" or kind != "past" and got["kind"] == kind) and (where is None or seen == where) else "FAIL"
        if kind == "past":
            flag = "OK" if err == "past" else "FAIL"
        print(f"{flag} {text!r} -> {got['kind']} off={got.get('offset')} err={err} where={seen} when={got.get('when_low')}")
