"""
file_sender.py — передача ЛЮБОГО файла через звук с настраиваемыми параметрами.

Физический уровень — phy.py: 2/4/8-FSK, любая длительность символа,
пресеты частот. Параметры выбираются ТОЛЬКО здесь: передатчик сам объявляет
их приёмнику служебной посылкой в базовом режиме — на приёме ничего
настраивать не нужно.

Примеры:
    python file_sender.py --file notes.txt                        # 4-FSK, 100 мс (проверено)
    python file_sender.py --file photo.jpg --mode 8 --preset wide --symbol-ms 60
    python file_sender.py --file a.bin --mode 2 --symbol-ms 200   # максимальная надёжность
    python file_sender.py --file a.bin --base 1600 --step 450 --marker 4000  # свои частоты
    python file_sender.py --file a.bin --mode 16 --bands 6 --symbol-ms 50    # ТУРБО: ~480 бит/с
"""

import argparse
import time
from pathlib import Path

import numpy as np

import phy
import protocol

try:
    import sounddevice as sd
except ImportError:
    sd = None


def frame_to_signal(frame: bytes, volume: float) -> np.ndarray:
    """Старый формат без служебной посылки (оставлен для совместимости и тестов)."""
    p = phy.CANONICAL
    return np.concatenate([
        phy.create_silence(0.5),
        phy.create_tone(p.marker, phy.MARKER_DURATION, volume),
        phy.modulate(frame, p, volume),
        phy.create_tone(p.marker, phy.MARKER_DURATION, volume),
        phy.create_silence(0.3),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Передача файла через звук (2/4/8/16-FSK, ТУРБО-полосы + авто-согласование)")
    parser.add_argument("--file", required=True, help="Путь к передаваемому файлу")
    parser.add_argument("--volume", type=float, default=0.6,
                        help="Громкость от 0.0 до 1.0 (по умолчанию 0.6)")
    parser.add_argument("--device", type=int, default=None,
                        help="Номер динамика из devices.py")
    parser.add_argument("--text", action="store_true",
                        help="Пометить содержимое как текст (приёмник покажет его на экране)")
    parser.add_argument("--mode", type=int, choices=(2, 4, 8, 16), default=4,
                        help="Число тонов FSK: 2 (надёжнее) / 4 (проверено) / 8 (быстрее) / 16 (максимум)")
    parser.add_argument("--symbol-ms", type=int, default=100,
                        help="Длительность символа в мс (по умолчанию 100)")
    parser.add_argument("--preset", choices=sorted(phy.PRESETS), default="standard",
                        help="Пресет частот: standard (2/4-FSK), wide (под 8-FSK), high (верхний диапазон), ultra (под 16-FSK)")
    parser.add_argument("--encoding", choices=("utf8", "ascii7"), default="utf8",
                        help="Кодировка для --text: ascii7 упаковывает латиницу/цифры/знаки по 7 бит (на 12.5 процента короче)")
    parser.add_argument("--base", type=int, default=None, help="Своя частота первого тона, Гц")
    parser.add_argument("--step", type=int, default=None, help="Свой шаг между тонами, Гц")
    parser.add_argument("--marker", type=int, default=None, help="Своя частота маркера, Гц")
    parser.add_argument("--bands", type=int, choices=(1, 2, 3, 4, 5, 6), default=1,
                        help="ТУРБО: число параллельных частотных полос (1 = обычный режим;"
                             " 4-6 полос по 16 тонов дают 320-480 бит/с)")
    args = parser.parse_args()

    if sd is None:
        raise SystemExit("Ошибка: библиотека sounddevice не установлена (pip install sounddevice)")

    # 8-FSK не влезает в standard (тон №5 = маркер 3500) — подсказываем wide
    preset = args.preset
    if args.mode == 8 and preset == "standard" and args.base is None and args.step is None:
        print("Для 8-FSK пресет standard не подходит — автоматически беру wide.")
        preset = "wide"
    # 16 тонов помещаются только в сетку ultra
    if args.mode == 16 and preset != "ultra" and args.base is None and args.step is None:
        print("Для 16-FSK подходит только пресет ultra — автоматически беру ultra.")
        preset = "ultra"
    # ТУРБО: полосам нужна сетка turbo (маркер 1000 Гц ниже всех тонов)
    if args.bands > 1 and preset != "turbo" and args.base is None and args.step is None:
        print(f"ТУРБО ({args.bands} полос) — автоматически беру пресет turbo.")
        preset = "turbo"

    p = phy.make_params(args.mode, args.symbol_ms, preset,
                        base=args.base, step=args.step, marker=args.marker,
                        bands=args.bands)
    errors, warnings = phy.validate(p)
    for warning in warnings:
        print(f"⚠  {warning}")
    if errors:
        raise SystemExit("Недопустимые параметры:\n  - " + "\n  - ".join(errors))

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Ошибка: файл {path} не найден")

    payload = path.read_bytes()
    is_ascii7 = False
    if args.text and args.encoding == "ascii7":
        try:
            packed = protocol.pack_ascii7(payload.decode("utf-8"))
            print(f"ASCII-7: {len(payload)} Б → {len(packed)} Б")
            payload = packed
            is_ascii7 = True
        except (UnicodeDecodeError, ValueError) as exc:
            print(f"⚠  ASCII-7 не подходит ({exc}) — отправляю как UTF-8")
    frame = protocol.build_frame(path.name, payload, is_text=args.text, is_ascii7=is_ascii7,
                                 fec=True)  # XOR-чётность: приёмник чинит одиночные битые блоки
    duration = phy.transmission_duration(len(frame), p)

    print(f"Файл: {path.name} ({len(payload)} байт)")
    print(f"Кадр с заголовком и CRC: {len(frame)} байт")
    print(f"Режим: {p.describe()}")
    print("Приёмник узнает параметры сам из служебной посылки — настраивать его не нужно.")
    print(f"Ожидаемое время передачи: ~{duration:.0f} с")
    print("Сначала запустите file_receiver.py на другом устройстве"
          f" (--duration не меньше {int(duration) + 5})!")
    print("Передача начнётся через 3 секунды...")

    for i in range(3, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    transmission = phy.build_transmission(frame, p, args.volume)

    print("Передаю...")
    started = time.time()
    sd.play(transmission, samplerate=phy.SAMPLE_RATE, device=args.device, blocking=True)
    print(f"Передача завершена за {time.time() - started:.1f} с.")


if __name__ == "__main__":
    main()
