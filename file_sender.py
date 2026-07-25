"""
file_sender.py — передача ЛЮБОГО файла через звук.

Физический уровень тот же, что у sender.py (4-FSK, 100 мс/символ) —
код переиспользуется импортом, ничего не меняется.
Сверху добавляется кадр из protocol.py: имя файла, размер, SHA-256,
данные блоками с CRC32.

Запуск:
    python file_sender.py --file photo_small.jpg
    python file_sender.py --file notes.txt --volume 0.8
"""

import argparse
import time
from pathlib import Path

import numpy as np

import protocol
from sender import (
    SAMPLE_RATE,
    SYMBOL_DURATION,
    SYMBOL_FREQS,
    FREQ_MARKER,
    MARKER_DURATION,
    create_tone,
    create_silence,
    bits_to_symbols,
)

try:
    import sounddevice as sd
except ImportError:
    sd = None


def frame_to_signal(frame: bytes, volume: float) -> np.ndarray:
    """Байты кадра -> аудиосигнал (маркер + символы + маркер)."""
    symbols = bits_to_symbols(protocol.bytes_to_bits(frame))

    parts = [
        create_silence(0.5),
        create_tone(FREQ_MARKER, MARKER_DURATION, volume),
    ]
    for symbol in symbols:
        parts.append(create_tone(SYMBOL_FREQS[symbol], SYMBOL_DURATION, volume))
    parts.append(create_tone(FREQ_MARKER, MARKER_DURATION, volume))
    parts.append(create_silence(0.3))

    return np.concatenate(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Передача файла через звук (4-FSK + кадр)")
    parser.add_argument("--file", required=True, help="Путь к передаваемому файлу")
    parser.add_argument("--volume", type=float, default=0.6,
                        help="Громкость от 0.0 до 1.0 (по умолчанию 0.6)")
    parser.add_argument("--device", type=int, default=None,
                        help="Номер динамика из devices.py")
    parser.add_argument("--text", action="store_true",
                        help="Пометить содержимое как текст (приёмник покажет его на экране)")
    args = parser.parse_args()

    if sd is None:
        raise SystemExit("Ошибка: библиотека sounddevice не установлена (pip install sounddevice)")

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Ошибка: файл {path} не найден")

    payload = path.read_bytes()
    frame = protocol.build_frame(path.name, payload, is_text=args.text)

    bit_count = len(frame) * 8
    duration = bit_count / 2 * SYMBOL_DURATION + 2 * MARKER_DURATION + 0.8

    print(f"Файл: {path.name} ({len(payload)} байт)")
    print(f"Кадр с заголовком и CRC: {len(frame)} байт ({bit_count} бит)")
    print(f"Ожидаемое время передачи: ~{duration:.0f} с (скорость 20 бит/с)")
    print("Сначала запустите file_receiver.py на другом устройстве"
          f" (--duration не меньше {int(duration) + 5})!")
    print("Передача начнётся через 3 секунды...")

    for i in range(3, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    transmission = frame_to_signal(frame, args.volume)

    print("Передаю...")
    started = time.time()
    sd.play(transmission, samplerate=SAMPLE_RATE, device=args.device, blocking=True)
    print(f"Передача завершена за {time.time() - started:.1f} с.")


if __name__ == "__main__":
    main()
