"""Определение границ фразы по энергии сигнала.

Нужен обоим движкам распознавания, но по разным причинам:

* у whisper своей сегментации нет вообще — он распознаёт то, что ему дали;
* Vosk сегментирует сам, но его пороги тишины в Python-API не настраиваются
  (`KaldiRecognizer` умеет только `AcceptWaveform`/`Result`/`Reset`). На записи
  видно, что он завершает фразу уже после ~300 мс паузы, и «включи саус парк,
  серия триста двенадцать» приходит двумя обрывками: «включи саус парк» и
  «серия триста двенадцать». В grammar-режиме вторая половина вообще
  превращается в «серия [unk]», потому что числительных в словаре нет.

Поэтому границу фразы считаем сами, а движку отдаём готовый результат.

Почему по 10 мс, а не по блокам: блок от микрофона — 100 мс, и решение «фраза
кончилась» на его границе означает, что последний слог либо обрывается, либо
тянет лишнюю тишину. Пересчёт по 10 мс убирает эту лотерею.

Почему с гистерезисом: начать речь требует громкого слога, а продолжаться речь
может вдвое тише. Без этого тихие окончания слов («...парк», «...биткоина»)
проваливаются под порог, и фраза обрывается на середине.
"""

from __future__ import annotations

import numpy as np

SAMPLERATE = 16000

# Блок аудио приходит по 1600 сэмплов = ровно 100 мс
FRAME_MS = 100
# Внутри блока энергия считается шагами по 10 мс
SUBFRAME_MS = 10
SUBFRAME_SAMPLES = SAMPLERATE * SUBFRAME_MS // 1000

# Абсолютный порог энергии: тише этого считаем тишиной при любом шуме
ABS_ENERGY_FLOOR = 0.006
# Потолок для оценки шума: без него громкая музыка или вентилятор поднимают
# порог выше человеческой речи, и ассистент глохнет
MAX_NOISE_FLOOR = 0.02

# Во сколько раз порог удержания речи ниже порога её старта
HYSTERESIS = 0.5

# Сколько тишины оставить после последнего громкого слога при обрезке. Whisper
# любит немного контекста после слова, но длинная тишина провоцирует
# галлюцинации вроде «субтитры сделал».
TAIL_PAD_MS = 240


class EnergyVad:
    """Копит аудио и говорит, когда фраза закончилась.

    Состояние намеренно живёт здесь, а не в движке распознавания: движок может
    перезапускаться, а границы фразы считаются по одному и тому же правилу.
    """

    def __init__(self, silence_ms=700, energy_factor=2.2, max_utterance_s=12.0,
                 min_speech_ms=200):
        self.silence_limit_ms = int(silence_ms)
        self.energy_factor = float(energy_factor)
        self.max_utterance_ms = int(max_utterance_s * 1000)
        self.min_speech_ms = int(min_speech_ms)
        self.reset()

    def reset(self):
        """Забывает незаконченную фразу."""
        self._frames = []
        self.speech_ms = 0
        self._silence_ms = 0
        self.in_speech = False
        self._noise_floor = ABS_ENERGY_FLOOR

    def push(self, pcm_int16_bytes):
        """Принимает блок аудио. True — фраза закончилась.

        После True нужно забрать аудио через `take_audio()` (для whisper) либо
        просто начать новую фразу (для Vosk).
        """
        samples = np.frombuffer(pcm_int16_bytes, dtype=np.int16)
        if samples.size < SUBFRAME_SAMPLES:
            return False

        audio = samples.astype(np.float32) / 32768.0
        whole = audio[: (audio.size // SUBFRAME_SAMPLES) * SUBFRAME_SAMPLES]
        for frame in whole.reshape(-1, SUBFRAME_SAMPLES):
            if self._step(frame):
                return True
        return False

    def _step(self, frame):
        rms = float(np.sqrt(np.mean(frame ** 2)))
        start_threshold = max(self._noise_floor * self.energy_factor, ABS_ENERGY_FLOOR)
        keep_threshold = max(start_threshold * HYSTERESIS, ABS_ENERGY_FLOOR)

        if not self.in_speech:
            if rms > start_threshold:
                self.in_speech = True
                self.speech_ms = SUBFRAME_MS
                self._silence_ms = 0
                self._frames = [frame]
            else:
                # Тишина до начала речи — подстраиваем уровень шума под комнату.
                self._noise_floor = min(
                    0.9 * self._noise_floor + 0.1 * max(rms, 1e-5),
                    MAX_NOISE_FLOOR,
                )
            return False

        self._frames.append(frame)
        if rms > keep_threshold:
            self.speech_ms += SUBFRAME_MS
            self._silence_ms = 0
        else:
            self._silence_ms += SUBFRAME_MS

        return (self._silence_ms >= self.silence_limit_ms
                or self.speech_ms >= self.max_utterance_ms)

    def take_audio(self):
        """Отдаёт накопленную фразу и начинает новую.

        Возвращает ``(аудио, мс речи)``. Мс речи нужны вызывающему: слишком
        короткий всплеск — это щелчок, а не команда, и его надо отбросить.
        """
        frames = self._frames
        speech_ms = self.speech_ms
        tail_silence_ms = self._silence_ms
        self.reset()

        if not frames:
            return None, 0

        excess_ms = max(0, tail_silence_ms - TAIL_PAD_MS)
        drop = excess_ms // SUBFRAME_MS
        if drop:
            frames = frames[:-drop] if drop < len(frames) else frames[-1:]

        return np.concatenate(frames), speech_ms
