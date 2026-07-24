"""
sender.py — передатчик текстового сообщения через звук (BFSK).

Как работает:
  1. Читает текст из файла MESSAGE_FILE (по умолчанию message.txt).
  2. Переводит текст в биты (UTF-8, 8 бит на байт, старший бит первым).
  3. Воспроизводит:
       - стартовый маркер 3500 Гц (0.5 с) — по нему приёмник находит начало;
       - биты: 0 = 1000 Гц, 1 = 2000 Гц, каждый длительностью 50 мс;
       - конечный маркер 3500 Гц (0.5 с) — конец сообщения.

ПОЧЕМУ МАРКЕР 3500 ГЦ, А НЕ 3000:
  Динамики ноутбуков искажают сигнал и создают гармоники:
  тон 1000 Гц порождает призвуки на 2000 и 3000 Гц. Из-за этого приёмник
  принимал бит 0 за маркер конца и обрывал приём.
  3500 Гц не является гармоникой ни 1000, ни 2000 Гц.

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
SAMPLE_RATE = 48000       # частота дискретизации
BIT_DURATION = 0.05       # 50 мс на бит
FREQ_ZERO = 1000          # бит 0
FREQ_ONE = 2000           # бит 1
FREQ_MARKER = 3500        # маркер (НЕ 3000: это гармоника 1000 Гц!)
MARKER_DURATION = 0.5     # длительность маркера

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


def create_transmission(text: str, volume: float) -> np.ndarray:
    """Собирает полный аудиосигнал: маркер + биты + маркер."""
    bits = text_to_bits(text)

    parts = [
        create_silence(0.5),
        create_tone(FREQ_MARKER, MARKER_DURATION, volume),  # старт
    ]

    for bit in bits:
        frequency = FREQ_ONE if bit == 1 else FREQ_ZERO
        parts.append(create_tone(frequency, BIT_DURATION, volume))

    parts.append(create_tone(FREQ_MARKER, MARKER_DURATION, volume))  # конец
    parts.append(create_silence(0.3))

    return np.concatenate(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Передатчик текста через звук (BFSK)")
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

    duration = len(bits) * BIT_DURATION + 2 * MARKER_DURATION + 0.8

    print(f"Сообщение: {text!r}")
    print(f"Битов: {len(bits)} | Длительность передачи: ~{duration:.1f} с")
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
