"""
sender.py — передатчик текстового сообщения через звук (4-FSK).

Как работает:
  1. Читает текст из файла MESSAGE_FILE (по умолчанию message.txt).
  2. Переводит текст в биты (UTF-8, 8 бит на байт, старший бит первым).
  3. Группирует биты по 2 (дибиты): каждый символ несёт 2 бита.
  4. Воспроизводит:
       - стартовый маркер 3500 Гц (0.5 с);
       - символы по 100 мс: 00 = 1500 Гц, 01 = 1900 Гц,
         10 = 2300 Гц, 11 = 2700 Гц;
       - конечный маркер 3500 Гц (0.5 с).

  Скорость: 2 бита за 100 мс = 20 бит/с — как у BFSK на 50 мс,
  но с запасом на эхо как у 100 мс.

ПОЧЕМУ ТАКИЕ ЧАСТОТЫ:
  Гармоники (удвоенные частоты) тонов 1500/1900/2300/2700 Гц —
  это 3000/3800/4600/5400 Гц: ни одна не совпадает ни с другим тоном,
  ни с маркером 3500 Гц. Искажения динамика не путают символы.

Запуск:
    python sender.py
    python sender.py --file message.txt --volume 0.8
"""

import argparse
import time
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

# ==== Параметры протокола (должны совпадать с receiver.py!) ====
SAMPLE_RATE = 48000        # частота дискретизации
SYMBOL_DURATION = 0.1      # 100 мс на символ (2 бита)
SYMBOL_FREQS = [1500, 1900, 2300, 2700]  # 00, 01, 10, 11
FREQ_MARKER = 3500         # стартовый/конечный маркер
MARKER_DURATION = 0.5

# Файл с сообщением по умолчанию.
MESSAGE_FILE = "message.txt"


def create_tone(frequency: float, duration: float, volume: float) -> np.ndarray:
    """Синусоидальный тон с плавными краями (без щелчков)."""
    sample_count = int(duration * SAMPLE_RATE)
    t = np.arange(sample_count) / SAMPLE_RATE
    signal = volume * np.sin(2 * np.pi * frequency * t)

    fade_samples = min(int(0.005 * SAMPLE_RATE), sample_count // 4)  # 5 мс
    if fade_samples > 0:
        signal[:fade_samples] *= np.linspace(0, 1, fade_samples)
        signal[-fade_samples:] *= np.linspace(1, 0, fade_samples)

    return signal.astype(np.float32)


def create_silence(duration: float) -> np.ndarray:
    return np.zeros(int(duration * SAMPLE_RATE), dtype=np.float32)


def text_to_bits(text: str) -> list[int]:
    """Текст -> UTF-8 байты -> список битов (старший бит первым)."""
    bits: list[int] = []
    for byte in text.encode("utf-8"):
        for position in range(7, -1, -1):
            bits.append((byte >> position) & 1)
    return bits


def bits_to_symbols(bits: list[int]) -> list[int]:
    """Список битов -> список символов 0..3 (по 2 бита, старший первым).
    Длина всегда чётная: байт = 8 бит = 4 символа."""
    return [bits[i] * 2 + bits[i + 1] for i in range(0, len(bits), 2)]


def create_transmission(text: str, volume: float) -> np.ndarray:
    """Собирает полный аудиосигнал: маркер + символы + маркер."""
    symbols = bits_to_symbols(text_to_bits(text))

    parts = [
        create_silence(0.5),
        create_tone(FREQ_MARKER, MARKER_DURATION, volume),  # старт
    ]

    for symbol in symbols:
        parts.append(create_tone(SYMBOL_FREQS[symbol], SYMBOL_DURATION, volume))

    parts.append(create_tone(FREQ_MARKER, MARKER_DURATION, volume))  # конец
    parts.append(create_silence(0.3))

    return np.concatenate(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Передатчик текста через звук (4-FSK)")
    parser.add_argument("--file", default=MESSAGE_FILE,
                        help=f"Файл с сообщением (по умолчанию {MESSAGE_FILE})")
    parser.add_argument("--volume", type=float, default=0.6,
                        help="Громкость от 0.0 до 1.0 (по умолчанию 0.6)")
    parser.add_argument("--device", type=int, default=None,
                        help="Номер динамика из devices.py")
    args = parser.parse_args()

    if sd is None:
        raise SystemExit("Ошибка: библиотека sounddevice не установлена (pip install sounddevice)")

    message_path = Path(args.file)
    if not message_path.exists():
        raise SystemExit(f"Ошибка: файл {message_path} не найден")

    text = message_path.read_text(encoding="utf-8")
    bits = text_to_bits(text)
    symbols = bits_to_symbols(bits)

    duration = len(symbols) * SYMBOL_DURATION + 2 * MARKER_DURATION + 0.8

    print(f"Сообщение: {text!r}")
    print(f"Битов: {len(bits)} | Символов: {len(symbols)} | Длительность: ~{duration:.1f} с")
    print("Сначала запустите receiver.py на другом компьютере!")
    print("Передача начнётся через 3 секунды...")

    for i in range(3, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    transmission = create_transmission(text, args.volume)

    print("Передаю...")
    sd.play(transmission, samplerate=SAMPLE_RATE, device=args.device, blocking=True)
    print("Передача завершена.")


if __name__ == "__main__":
    main()
