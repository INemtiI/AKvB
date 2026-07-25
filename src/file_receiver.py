"""
file_receiver.py — приём файла через звук с авто-согласованием параметров.

НИЧЕГО НАСТРАИВАТЬ НЕ НУЖНО: передатчик в начале эфира объявляет режим
(2/4/8-FSK, мс/символ, частоты) служебной посылкой в базовом режиме,
приёмник читает её и переключается сам. Старые передатчики без служебной
посылки тоже принимаются (базовый режим).

Запись останавливается САМА, как только сообщение принято целиком
(пришёл конечный маркер); --duration — лишь верхний предел ожидания.

Запуск (ЗАПУСКАТЬ ДО file_sender.py!):
    python file_receiver.py --duration 60
"""

import argparse
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

import phy
import protocol
from receiver import save_recording


def frame_complete(signal: np.ndarray) -> bool:
    """True, если в записи уже есть целый кадр (пришёл конечный маркер)."""
    result = phy.decode_auto(signal)
    if result["error"] or not result["bits"]:
        return False
    try:
        parsed = protocol.parse_frame(protocol.bits_to_bytes(result["bits"]))
    except protocol.FrameError:
        return False  # заголовок ещё не дошёл — продолжаем запись
    if parsed["truncated"]:
        return False
    if parsed["sha_ok"]:
        return True  # файл уже цел — блоки чётности можно не ждать
    return not parsed.get("fec_pending", False)  # ждём XOR-чётность для починки


def record_until_complete(max_duration: float, device) -> np.ndarray:
    """Пишет микрофон и сама останавливается, как только кадр принят целиком.

    Раз в секунду пробуем декодировать накопленную запись: если кадр
    разобран и не оборван — конечный маркер пришёл, ждать больше нечего.
    """
    if sd is None:
        raise SystemExit("Ошибка: библиотека sounddevice не установлена (pip install sounddevice)")

    chunk = int(phy.SAMPLE_RATE * 0.25)
    frames = []
    recorded = 0.0
    next_check = 3.0  # раньше 3 секунд кадр физически не успеет прийти

    print(f"Запись пошла (максимум {max_duration:.0f} с) — запускайте file_sender.py!")
    print("Остановлюсь сама, как только сообщение будет принято целиком.")

    with sd.InputStream(samplerate=phy.SAMPLE_RATE, channels=1,
                        dtype="float32", device=device, latency="high") as stream:
        while recorded < max_duration:
            data, _ = stream.read(chunk)
            frames.append(data[:, 0].copy())
            recorded += len(data) / phy.SAMPLE_RATE
            if recorded >= next_check:
                next_check = recorded + 1.0  # проверяем раз в секунду
                signal = np.concatenate(frames)
                if frame_complete(signal):
                    print(f"Конечный маркер: сообщение принято целиком на {recorded:.1f} с"
                          " — останавливаю запись.")
                    return signal

    print("Достигнут максимум длительности записи (конечный маркер не замечен).")
    return np.concatenate(frames)


def report(result: dict, output_dir: Path) -> None:
    """Печатает отчёт о приёме и сохраняет файл."""
    print(f"\nИмя файла: {result['filename']}")
    print(f"Принято байт: {len(result['payload'])} из {result['payload_len_expected']}")

    total = result["total_blocks"]
    bad = result["bad_blocks"]
    print(f"Блоки: {total - len(bad)}/{total} без ошибок", end="")
    print(f", повреждены: {bad}" if bad else "")

    if result.get("recovered_blocks"):
        print(f"FEC: блоки {result['recovered_blocks']} восстановлены XOR-чётностью")
    if result["truncated"]:
        print("ВНИМАНИЕ: передача оборвалась раньше конца — увеличьте --duration.")

    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / result["filename"]
    out_path.write_bytes(result["payload"])
    print(f"Файл сохранён: {out_path.resolve()}")

    if result["is_text"]:
        if result.get("is_ascii7"):
            print(f"Текст (ASCII-7): {protocol.unpack_ascii7(result['payload'])!r}")
        else:
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
                        help="Максимум записи в секундах (по умолчанию 60);"
                             " запись остановится сама после конечного маркера")
    parser.add_argument("--device", type=int, default=None,
                        help="Номер микрофона из devices.py")
    parser.add_argument("--wav", default="recording.wav",
                        help="Файл для сохранения записи (отладка)")
    parser.add_argument("--outdir", default="received",
                        help="Папка для принятых файлов (по умолчанию received/)")
    args = parser.parse_args()

    signal = record_until_complete(args.duration, args.device)
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
