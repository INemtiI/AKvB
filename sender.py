"""
sender.py — передатчик тестового сигнала.

Запускается на первом компьютере. Воспроизводит через динамик:
  1) три коротких сигнала 1000 Гц (стартовая последовательность);
  2) основной тон 2000 Гц длительностью 3 секунды;
  3) завершающий сигнал 1000 Гц.

Запуск:
    python sender.py
    python sender.py --volume 0.8
    python sender.py --device 2      # номер динамика из devices.py
"""

import argparse
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 48000


def create_tone(frequency: float, duration: float, volume: float) -> np.ndarray:
    """Создаёт синусоидальный тон с плавным началом и концом."""
    sample_count = int(duration * SAMPLE_RATE)
    t = np.arange(sample_count) / SAMPLE_RATE

    signal = volume * np.sin(2 * np.pi * frequency * t)

    # Плавное нарастание и затухание (20 мс), чтобы не было щелчков.
    fade_samples = min(int(0.02 * SAMPLE_RATE), sample_count // 4)
    if fade_samples > 0:
        signal[:fade_samples] *= np.linspace(0, 1, fade_samples)
        signal[-fade_samples:] *= np.linspace(1, 0, fade_samples)

    return signal.astype(np.float32)


def create_silence(duration: float) -> np.ndarray:
    return np.zeros(int(duration * SAMPLE_RATE), dtype=np.float32)


def create_transmission(volume: float) -> np.ndarray:
    parts = []

    # 1. Тишина перед началом.
    parts.append(create_silence(0.5))

    # 2. Стартовая последовательность: три коротких сигнала 1000 Гц.
    for _ in range(3):
        parts.append(create_tone(1000, 0.25, volume))
        parts.append(create_silence(0.15))

    # 3. Основной тестовый сигнал: 2000 Гц, 3 секунды.
    parts.append(create_tone(2000, 3.0, volume))

    # 4. Завершающий сигнал.
    parts.append(create_silence(0.3))
    parts.append(create_tone(1000, 0.5, volume))

    return np.concatenate(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Передатчик тестового сигнала")
    parser.add_argument("--volume", type=float, default=0.5,
                        help="Громкость от 0.0 до 1.0 (по умолчанию 0.5)")
    parser.add_argument("--device", type=int, default=None,
                        help="Номер динамика из devices.py")
    args = parser.parse_args()

    if not 0.0 <= args.volume <= 1.0:
        raise SystemExit("Ошибка: громкость должна быть от 0.0 до 1.0")

    transmission = create_transmission(args.volume)

    print("Сначала запустите receiver.py на другом компьютере!")
    print("Передача начнётся через 3 секунды...")

    for i in range(3, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    print("Передаю сигнал...")
    sd.play(transmission, samplerate=SAMPLE_RATE, device=args.device, blocking=True)
    print("Передача завершена.")


if __name__ == "__main__":
    main()
