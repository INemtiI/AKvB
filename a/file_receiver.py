"""
file_receiver.py — приём файла через звук с авто-согласованием параметров.

НИЧЕГО НАСТРАИВАТЬ НЕ НУЖНО: передатчик в начале эфира объявляет режим
(2/4/8-FSK, мс/символ, частоты) служебной посылкой в базовом режиме,
приёмник читает её и переключается сам. Старые передатчики без служебной
посылки тоже принимаются (базовый режим).

Запуск (ЗАПУСКАТЬ ДО file_sender.py!):
    python file_receiver.py --duration 60
"""

import argparse
from pathlib import Path

import phy
import protocol
from receiver import record_audio, save_recording


def report(result: dict, output_dir: Path) -> None:
    """Печатает отчёт о приёме и сохраняет файл."""
    print(f"\nИмя файла: {result['filename']}")
    print(f"Принято байт: {len(result['payload'])} из {result['payload_len_expected']}")

    total = result["total_blocks"]
    bad = result["bad_blocks"]
    print(f"Блоки: {total - len(bad)}/{total} без ошибок", end="")
    print(f", повреждены: {bad}" if bad else "")

    if result["truncated"]:
        print("ВНИМАНИЕ: передача оборвалась раньше конца — увеличьте --duration.")

    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / result["filename"]
    out_path.write_bytes(result["payload"])
    print(f"Файл сохранён: {out_path.resolve()}")

    if result["is_text"]:
        print(f"Текст: {result['payload'].decode('utf-8', errors='replace')!r}")

    if result["sha_ok"]:
        print("\n✅ ЦЕЛОСТНОСТЬ ПОДТВЕРЖДЕНА: SHA-256 совпал, файл восстановлен байт-в-байт.")
    else:
        print("\n❌ ОШИБКА ЦЕЛОСТНОСТИ: SHA-256 не совпал. Повторите передачу"
              " (уменьшите расстояние/шум или поднимите громкость до 60-70%).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Приём файла через звук (авто-согласование режима)")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="Длительность записи в секундах (по умолчанию 60);"
                             " file_sender подскажет нужное значение")
    parser.add_argument("--device", type=int, default=None,
                        help="Номер микрофона из devices.py")
    parser.add_argument("--wav", default="recording.wav",
                        help="Файл для сохранения записи (отладка)")
    parser.add_argument("--outdir", default="received",
                        help="Папка для принятых файлов (по умолчанию received/)")
    args = parser.parse_args()

    signal = record_audio(args.duration, args.device)
    save_recording(signal, Path(args.wav))

    print("Декодирую...")
    result = phy.decode_auto(signal)

    if result["error"]:
        print(f"\nFAILED: {result['error']}.")
        print("Проверьте громкость, расстояние и запускайте file_receiver ДО file_sender.")
        return

    if result["legacy"]:
        print("Служебная посылка не обнаружена — считаю, что передатчик старого формата"
              f" ({phy.CANONICAL.describe()}).")
    else:
        print(f"Передатчик объявил режим: {result['params'].describe()}")

    bits = result["bits"]
    if not bits:
        print("\nFAILED: данные не приняты.")
        return

    print(f"Принято битов: {len(bits)} ({len(bits) // 8} байт)")

    try:
        parsed = protocol.parse_frame(protocol.bits_to_bytes(bits))
    except protocol.FrameError as error:
        print(f"\nFAILED: {error}")
        print("Если передавался простой текст через sender.py — используйте receiver.py.")
        return

    report(parsed, Path(args.outdir))


if __name__ == "__main__":
    main()
