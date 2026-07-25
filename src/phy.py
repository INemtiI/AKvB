"""
phy.py — настраиваемый физический уровень: 2/4/8/16-FSK, любая длительность
символа, настраиваемые частоты, ПАРАЛЛЕЛЬНЫЕ ПОЛОСЫ (ТУРБО) + АВТО-СОГЛАСОВАНИЕ.

Как устройства договариваются:
  Передатчик всегда начинает с короткой служебной посылки (CFG, 15 байт)
  в БАЗОВОМ режиме (4-FSK, 100 мс, 1500+400·k Гц, маркер 3500 — проверен в
  воздухе). В ней — параметры основной передачи. Приёмник читает CFG и сам
  переключается на объявленный режим. Никаких ручных настроек на приёме.

ТУРБО-режим (bands > 1):
  Спектр делится на bands независимых полос по tones тонов; в каждый момент
  звучит bands тонов ОДНОВРЕМЕННО (по одному на полосу). Скорость растёт в
  bands раз: 6 полос × 16-FSK @ 50 мс = 480 бит/с. Маркер данных в пресете
  turbo вынесен ВНИЗ (1000 Гц) — ниже всех тонов данных.

Структура эфира:
  [тишина] [маркер 3500] [CFG @ базовый] [маркер 3500] [маркер P] [ДАННЫЕ @ P] [маркер P]
где P — объявленные параметры. sender.py/receiver.py не тронуты.
"""

import struct
import zlib
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 48000
MARKER_DURATION = 0.5
CFG_MAGIC = b"AC"
CFG_LEN = 15

#: Пресеты частот: тона = base + k*step, k = 0..bands*tones-1
PRESETS = {
    # Проверен в реальном воздухе. Только для 2/4-FSK
    # (при 8-FSK тон №5 попадает ровно в маркер 3500).
    "standard": dict(base=1500, step=400, marker=3500),
    # Широкая сетка под 8-FSK: тона 1200..3860, маркер вынесен на 4300.
    "wide": dict(base=1200, step=380, marker=4300),
    # Верхний диапазон: дальше от речи и гула помещения, но требовательнее
    # к динамикам (дешёвые ноутбуки выше 5 кГц играют тише).
    "high": dict(base=2000, step=400, marker=5300),
    # Сетка под 16-FSK: 16 тонов 1400..5300 Гц, маркер вынесен на 6100
    # (расстояние до тонов 800 Гц, гармоники 2f/3f/4f всех тонов проверены).
    "ultra": dict(base=1400, step=260, marker=6100),
    # ТУРБО-полосы: до 6×16 = 96 тонов 1400..15650 Гц, маркер вынесен ВНИЗ
    # (1000 Гц) — ниже всех тонов, чтобы не пересекаться ни с одной полосой.
    "turbo": dict(base=1400, step=150, marker=1000),
}


@dataclass(frozen=True)
class ModemParams:
    tones: int = 4        # тонов НА ПОЛОСУ: 2 / 4 / 8 / 16
    symbol_ms: int = 100  # длительность символа, мс
    base: int = 1500      # частота первого тона, Гц
    step: int = 400       # шаг между тонами, Гц
    marker: int = 3500    # частота маркера, Гц
    bands: int = 1        # параллельных частотных полос (ТУРБО при >1)

    @property
    def freqs(self) -> list:
        """Все тона всех полос подряд: полоса b владеет индексами [b*tones, (b+1)*tones)."""
        return [self.base + i * self.step for i in range(self.bands * self.tones)]

    @property
    def bits_per_band(self) -> int:
        return {2: 1, 4: 2, 8: 3, 16: 4}[self.tones]

    @property
    def bits_per_symbol(self) -> int:
        return self.bands * self.bits_per_band

    @property
    def symbol_duration(self) -> float:
        return self.symbol_ms / 1000.0

    @property
    def bitrate(self) -> float:
        return self.bits_per_symbol * 1000.0 / self.symbol_ms

    def describe(self) -> str:
        mode = (f"ТУРБО {self.bands}×{self.tones}" if self.bands > 1
                else f"{self.tones}-FSK")
        return (f"{mode}, {self.symbol_ms} мс/символ, тона {self.base}+{self.step}·k Гц"
                f" ({self.freqs[0]}..{self.freqs[-1]}), маркер {self.marker} Гц,"
                f" {self.bitrate:.0f} бит/с")


CANONICAL = ModemParams()  # базовый режим для служебной посылки — НЕ МЕНЯТЬ


class ConfigError(ValueError):
    pass


def validate(p: ModemParams) -> tuple:
    """Проверка параметров. Возвращает (ошибки, предупреждения)."""
    errors, warnings = [], []
    if p.tones not in (2, 4, 8, 16):
        errors.append("число тонов должно быть 2, 4, 8 или 16")
    if not 1 <= p.bands <= 6:
        errors.append("число полос (bands): от 1 до 6")
    if not 20 <= p.symbol_ms <= 500:
        errors.append("длительность символа: от 20 до 500 мс")
    if p.base < 500 or p.marker < 500:
        errors.append("частоты ниже 500 Гц встроенные динамики почти не играют")
    if errors:
        return errors, warnings
    for f in list(p.freqs) + [p.marker]:
        if f > 16500:
            errors.append(f"частота {f} Гц выше 16.5 кГц — встроенное аудио её не пропустит")
            break
    for f in p.freqs:
        if abs(f - p.marker) < 300:
            errors.append(f"тон {f} Гц слишком близок к маркеру {p.marker} Гц (нужно ≥300 Гц)")
    if errors:
        return errors, warnings

    # Предупреждения (не блокируют, но снижают надёжность)
    min_step = int(2000 / p.symbol_ms) + 100  # ширина спектра символа ~1/T + запас
    if p.step < min_step:
        warnings.append(f"шаг {p.step} Гц мал для {p.symbol_ms} мс/символ"
                        f" (рекомендуется ≥{min_step} Гц)")
    if p.symbol_ms < 50:
        warnings.append("меньше 50 мс/символ — на встроенных динамиках часто сбоит"
                        " (проверено: 50 мс уже на грани)")
    if max(p.freqs) > 9000:
        warnings.append("тона выше 9 кГц — встроенные динамики играют их тихо,"
                        " держите устройства ближе (20–40 см)")
    for f in p.freqs:
        for k in (2, 3, 4):
            if abs(k * f - p.marker) < 100:
                warnings.append(f"маркер {p.marker} Гц ≈ {k}-я гармоника тона {f} Гц —"
                                " возможны ложные маркеры (та самая ошибка,"
                                " которую мы уже ловили на 500 мс)")
    return errors, warnings


def make_params(tones: int, symbol_ms: int, preset: str = "standard",
                base: int = None, step: int = None, marker: int = None,
                bands: int = 1) -> ModemParams:
    """Собирает параметры из пресета с возможностью точечно переопределить частоты."""
    cfg = PRESETS[preset]
    return ModemParams(
        tones=tones,
        symbol_ms=symbol_ms,
        base=base if base is not None else cfg["base"],
        step=step if step is not None else cfg["step"],
        marker=marker if marker is not None else cfg["marker"],
        bands=bands,
    )


# ────────── биты/байты/символы ──────────

def bytes_to_bits(data: bytes) -> list:
    bits = []
    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    return bits


def bits_to_bytes(bits: list) -> bytes:
    usable = len(bits) - (len(bits) % 8)
    out = bytearray()
    for i in range(0, usable, 8):
        byte = 0
        for bit in bits[i:i + 8]:
            byte = (byte << 1) | bit
        out.append(byte)
    return bytes(out)


def bits_to_symbols(bits: list, bits_per_symbol: int) -> list:
    padded = list(bits)
    while len(padded) % bits_per_symbol:
        padded.append(0)
    symbols = []
    for i in range(0, len(padded), bits_per_symbol):
        value = 0
        for bit in padded[i:i + bits_per_symbol]:
            value = (value << 1) | bit
        symbols.append(value)
    return symbols


# ────────── служебная посылка CFG ──────────

def pack_config(p: ModemParams) -> bytes:
    # Старшие 3 бита байта режима — число полос минус 1, младшие 5 бит — тона.
    # Старые значения 2/4/8/16 читаются как 1 полоса: формат обратно совместим.
    mode_byte = p.tones + (p.bands - 1) * 32
    body = struct.pack(">2sBHHHH", CFG_MAGIC, mode_byte, p.symbol_ms, p.base, p.step, p.marker)
    return body + struct.pack(">I", zlib.crc32(body))


def unpack_config(data: bytes) -> ModemParams:
    if len(data) < CFG_LEN:
        raise ConfigError("служебная посылка короче 15 байт")
    body, crc = data[:CFG_LEN - 4], struct.unpack(">I", data[CFG_LEN - 4:CFG_LEN])[0]
    if body[:2] != CFG_MAGIC:
        raise ConfigError("нет магической последовательности AC")
    if zlib.crc32(body) != crc:
        raise ConfigError("CRC32 служебной посылки не сошёлся")
    _, mode_byte, symbol_ms, base, step, marker = struct.unpack(">2sBHHHH", body)
    bands, tones = mode_byte // 32 + 1, mode_byte % 32
    p = ModemParams(tones, symbol_ms, base, step, marker, bands)
    errors, _ = validate(p)
    if errors:
        raise ConfigError("недопустимые параметры: " + "; ".join(errors))
    return p


# ────────── модулятор ──────────

def create_tone(freq: float, duration: float, volume: float) -> np.ndarray:
    n = int(duration * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    tone = (volume * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    fade = min(int(0.005 * SAMPLE_RATE), n // 4)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        tone[:fade] *= ramp
        tone[-fade:] *= ramp[::-1]
    return tone


def create_multitone(freqs: list, duration: float, volume: float) -> np.ndarray:
    """Несколько тонов одновременно (ТУРБО): амплитуда делится на число полос."""
    n = int(duration * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    amp = volume / len(freqs)
    tone = np.zeros(n, dtype=np.float64)
    for f in freqs:
        tone += amp * np.sin(2 * np.pi * f * t)
    tone = tone.astype(np.float32)
    fade = min(int(0.005 * SAMPLE_RATE), n // 4)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        tone[:fade] *= ramp
        tone[-fade:] *= ramp[::-1]
    return tone


def create_silence(duration: float) -> np.ndarray:
    return np.zeros(int(duration * SAMPLE_RATE), dtype=np.float32)


def symbol_freqs(value: int, p: ModemParams) -> list:
    """Частоты символа: по одному тону на каждую полосу (старшие биты — полоса 0)."""
    freqs = []
    for b in range(p.bands):
        sub = (value >> ((p.bands - 1 - b) * p.bits_per_band)) & (p.tones - 1)
        freqs.append(p.freqs[b * p.tones + sub])
    return freqs


def modulate(data: bytes, p: ModemParams, volume: float) -> np.ndarray:
    parts = [create_multitone(symbol_freqs(s, p), p.symbol_duration, volume)
             for s in bits_to_symbols(bytes_to_bits(data), p.bits_per_symbol)]
    return np.concatenate(parts) if parts else create_silence(0)


def build_transmission(frame: bytes, p: ModemParams, volume: float = 0.6) -> np.ndarray:
    """Полный эфир: CFG в базовом режиме + данные в режиме p."""
    return np.concatenate([
        create_silence(0.5),
        create_tone(CANONICAL.marker, MARKER_DURATION, volume),
        modulate(pack_config(p), CANONICAL, volume),
        create_tone(CANONICAL.marker, MARKER_DURATION, volume),
        create_tone(p.marker, MARKER_DURATION, volume),
        modulate(frame, p, volume),
        create_tone(p.marker, MARKER_DURATION, volume),
        create_silence(0.3),
    ])


def transmission_duration(frame_len: int, p: ModemParams) -> float:
    cfg_symbols = (CFG_LEN * 8 + CANONICAL.bits_per_symbol - 1) // CANONICAL.bits_per_symbol
    data_symbols = (frame_len * 8 + p.bits_per_symbol - 1) // p.bits_per_symbol
    return (0.8 + 3 * MARKER_DURATION
            + cfg_symbols * CANONICAL.symbol_duration
            + data_symbols * p.symbol_duration)


# ────────── демодулятор ──────────

def _rms(seg: np.ndarray) -> float:
    return float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0


def tone_energy(seg: np.ndarray, freq: float) -> float:
    t = np.arange(len(seg)) / SAMPLE_RATE
    return abs(np.dot(seg, np.exp(-2j * np.pi * freq * t))) / len(seg)


def classify_segment(seg: np.ndarray, p: ModemParams):
    """Определяет символ: в каждой полосе — argmax своих тонов, биты склеиваются."""
    energies = [tone_energy(seg, f) for f in p.freqs]
    marker_energy = tone_energy(seg, p.marker)
    value, peak = 0, 0.0
    for b in range(p.bands):
        band = energies[b * p.tones:(b + 1) * p.tones]
        k = int(np.argmax(band))
        value = (value << p.bits_per_band) | k
        peak = max(peak, band[k])
    if marker_energy > peak:
        return "marker", energies, marker_energy
    return value, energies, marker_energy


def _band_separation(energies: list, p: ModemParams) -> float:
    """Средний по полосам отрыв победителя от второго места (0..1)."""
    total = 0.0
    for b in range(p.bands):
        band = sorted(energies[b * p.tones:(b + 1) * p.tones], reverse=True)
        second = band[1] if len(band) > 1 else 0.0
        total += (band[0] - second) / (band[0] + second + 1e-12)
    return total / p.bands


def _band_quality(energies: list, marker_energy: float, p: ModemParams) -> float:
    """Средняя по полосам чистота символа (для следящего декодера)."""
    total = 0.0
    for b in range(p.bands):
        band = sorted(energies[b * p.tones:(b + 1) * p.tones], reverse=True)
        second = band[1] if len(band) > 1 else 0.0
        total += band[0] / (second + marker_energy + 1e-12)
    return total / p.bands


def find_data_start(signal: np.ndarray, p: ModemParams, start: int = 0):
    """Грубый поиск конца маркерного тона (начала данных) от позиции start."""
    hop = int(0.01 * SAMPLE_RATE)
    win = int(0.05 * SAMPLE_RATE)
    required_run = 25
    run = 0
    count = (len(signal) - start - win) // hop
    for index in range(max(count, 0)):
        s = start + index * hop
        seg = signal[s:s + win]
        is_marker = False
        if _rms(seg) > 1e-4:
            _, energies, marker_energy = classify_segment(seg, p)
            is_marker = marker_energy > 2 * max(energies)
        if is_marker:
            run += 1
        else:
            if run >= required_run:
                return start + index * hop + win // 2 - hop // 2
            run = 0
    return None


def refine_data_start(signal: np.ndarray, coarse_start: int, p: ModemParams) -> int:
    """Точная подстройка границы (решает рассинхрон на полсимвола)."""
    sym = int(p.symbol_duration * SAMPLE_RATE)
    guard = sym // 4
    max_shift = int(0.6 * sym)
    step = max(1, sym // 20)
    edge = int(0.005 * SAMPLE_RATE)
    edge_win = guard
    best_offset, best_score = 0, -1.0

    for offset in range(-max_shift, max_shift + 1, step):
        start = coarse_start + offset
        if start < 0:
            continue
        score, count, position = 0.0, 0, start
        if start >= edge + edge_win and start + edge + edge_win <= len(signal):
            pre, _, _ = classify_segment(signal[start - edge - edge_win:start - edge], p)
            post, _, _ = classify_segment(signal[start + edge:start + edge + edge_win], p)
            if pre == "marker" and post != "marker":
                score += 10.0
        while position + sym <= len(signal) and count < 16:
            result, energies, _ = classify_segment(signal[position + guard:position + sym - guard], p)
            if result == "marker":
                break
            score += _band_separation(energies, p)
            count += 1
            position += sym
        if count > 0:
            score /= count
            if score > best_score + 1e-9 or (abs(score - best_score) <= 1e-9
                                             and abs(offset) < abs(best_offset)):
                best_score = score
                best_offset = offset
    return coarse_start + best_offset


def decode_symbols(signal: np.ndarray, start: int, p: ModemParams):
    """Читает символы до маркера/тишины, следя за дрейфом часов.

    Early-late tracking: каждый символ оценивается в трёх положениях окна
    (чуть раньше / по сетке / чуть позже), и сетка сдвигается туда, где
    победившие тона сильнее всего отрываются от остальных (в среднем по
    полосам). Это компенсирует расхождение частот дискретизации передатчика
    и приёмника (clock drift).
    """
    sym = int(p.symbol_duration * SAMPLE_RATE)
    guard = sym // 4
    delta = max(1, sym // 20)  # запас слежения ~5% на символ (дрейф ~0.1-0.3%)
    bits = []
    position = start
    while position + sym <= len(signal):
        seg = signal[position + guard:position + sym - guard]
        if _rms(seg) < 2e-4:
            break
        result, _, _ = classify_segment(seg, p)
        if result == "marker":
            break
        best_off = 0
        best_result = result
        best_score = -1.0
        for off in (-delta, 0, delta):
            s0 = position + off
            if s0 < 0 or s0 + sym > len(signal):
                continue
            seg2 = signal[s0 + guard:s0 + sym - guard]
            r2, energies, marker_e = classify_segment(seg2, p)
            if r2 == "marker":
                continue
            score = _band_quality(energies, marker_e, p)
            if score > best_score:
                best_score = score
                best_off = off
                best_result = r2
        for shift in range(p.bits_per_symbol - 1, -1, -1):
            bits.append((best_result >> shift) & 1)
        position += sym + best_off
    return bits, position


def decode_auto(signal: np.ndarray):
    """Авто-приём: читает CFG в базовом режиме, переключается на объявленный.

    Возвращает dict: params, bits, legacy (bool), error (str | None).
    legacy=True — передатчик старого формата без CFG (всё в базовом режиме).
    """
    coarse = find_data_start(signal, CANONICAL)
    if coarse is None:
        return {"params": CANONICAL, "bits": [], "legacy": False,
                "error": f"стартовый маркер {CANONICAL.marker} Гц не найден"}
    start = refine_data_start(signal, coarse, CANONICAL)
    first_bits, end = decode_symbols(signal, start, CANONICAL)

    try:
        p = unpack_config(bits_to_bytes(first_bits))
    except ConfigError:
        # старый передатчик: первая же секция — сразу кадр данных
        return {"params": CANONICAL, "bits": first_bits, "legacy": True, "error": None}

    coarse2 = find_data_start(signal, p, start=end)
    if coarse2 is None:
        return {"params": p, "bits": [], "legacy": False,
                "error": f"маркер данных {p.marker} Гц не найден после служебной посылки"}
    start2 = refine_data_start(signal, coarse2, p)
    bits, _ = decode_symbols(signal, start2, p)
    return {"params": p, "bits": bits, "legacy": False, "error": None}
