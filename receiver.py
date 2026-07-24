"""
receiver.py — приёмник текстового сообщения через звук (BFSK).

Как работает:
  1. Записывает звук с микрофона (по умолчанию 20 секунд).
  2. Сохраняет запись в recording.wav (можно прослушать для отладки).
  3. Находит стартовый маркер 3500 Гц.
  4. Читает биты каждые 100 мс: 1000 Гц = 0, 2000 Гц = 1.
     Анализируются только центральные 50 мс каждого бита —
     края (по 25 мс) отбрасываются из-за реверберации и погрешности
     синхронизации.
  5. Останавливается на конечном маркере 3500 Гц.
  6. Переводит биты в текст (UTF-8) и записывает в received_message.txt.

Запуск (ЗАПУСКАТЬ ДО sender.py!):
    python receiver.py
    python receiver.py --duration 30 --device 1
"""

import argparse
import wave
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

# ==== Параметры протокола (должны совпадать с sender.py!) ====
SAMPLE_RATE = 48000
BIT_DURATION = 0.1        # 100 мс на бит
FREQ_ZERO = 1000          # бит 0
FREQ_ONE = 2000           # бит 1
FREQ_MARKER = 3500        # маркер (НЕ 3000: это гармоника 1000 Гц!)
MARKER_DURATION = 0.5

OUTPUT_FILE = "received_message.txt"


def record_audio(duration: float, device) -> np.ndarray:
    if sd is None:
        raise SystemExit("Ошибка: библиотека sounddevice не установлена (pip install sounddevice)")

    print(f"Запись началась ({duration:.0f} секунд).")
    print("Теперь запустите sender.py на другом компьютере!")

    recording = sd.rec(
        frames=int(duration * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype=np.float32,
        device=device,
    )
    sd.wait()

    print("Запись завершена.")
    return recording[:, 0]


def save_recording(signal: np.ndarray, path: Path) -> None:
    pcm = (np.clip(signal, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm.tobytes())
    print(f"Запись сохранена: {path.resolve()}")


def tone_energy(segment: np.ndarray, frequency: float) -> float:
    """Энергия сигнала на заданной частоте (метод Гёрцеля через numpy)."""
    t = np.arange(len(segment)) / SAMPLE_RATE
    reference = np.exp(-2j * np.pi * frequency * t)
    return float(abs(np.dot(segment, reference))) / len(segment)


def classify_segment(segment: np.ndarray):
    """Возвращает (частота-победитель, энергии по всем трём частотам)."""
    energies = {
        FREQ_ZERO: tone_energy(segment, FREQ_ZERO),
        FREQ_ONE: tone_energy(segment, FREQ_ONE),
        FREQ_MARKER: tone_energy(segment, FREQ_MARKER),
    }
    winner = max(energies, key=energies.get)
    return winner, energies


def find_data_start(signal: np.ndarray):
    """Ищет конец стартового маркера 3500 Гц. Возвращает номер сэмпла,
    с которого начинаются биты данных, или None, если маркер не найден."""
    hop = int(0.01 * SAMPLE_RATE)   # шаг 10 мс
    win = int(0.05 * SAMPLE_RATE)   # окно 50 мс

    # Маркер должен уверенно держаться минимум 0.25 с (25 окон подряд).
    required_run = 25

    run_length = 0
    for index in range(0, (len(signal) - win) // hop):
        start = index * hop
        segment = signal[start:start + win]
        rms = float(np.sqrt(np.mean(segment ** 2)))

        is_marker = False
        if rms > 1e-4:
            winner, energies = classify_segment(segment)
            others = max(energies[FREQ_ZERO], energies[FREQ_ONE])
            is_marker = (
                winner == FREQ_MARKER
                and energies[FREQ_MARKER] > 2 * others
            )

        if is_marker:
            run_length += 1
        else:
            if run_length >= required_run:
                # Маркер только что закончился. Граница перехода —
                # примерно на пол-окна раньше начала этого окна.
                return index * hop + win // 2 - hop // 2
            run_length = 0

    return None


def decode_signal(signal: np.ndarray):
    """Декодирует запись: возвращает (текст, список битов)."""
    data_start = find_data_start(signal)
    if data_start is None:
        return None, []

    bit_samples = int(BIT_DURATION * SAMPLE_RATE)
    guard = int(0.025 * SAMPLE_RATE)  # отбрасываем по 25 мс с каждого края бита

    bits: list[int] = []
    position = data_start

    while position + bit_samples <= len(signal):
        segment = signal[position + guard:position + bit_samples - guard]
        rms = float(np.sqrt(np.mean(segment ** 2)))

        if rms < 2e-4:
            break  # тишина — передача прервалась

        winner, _ = classify_segment(segment)

        if winner == FREQ_MARKER:
            break  # конечный маркер — сообщение закончилось

        bits.append(1 if winner == FREQ_ONE else 0)
        position += bit_samples

    return bits_to_text(bits), bits


def bits_to_text(bits: list[int]) -> str:
    """Список битов (старший первым) -> байты -> текст UTF-8."""
    usable_length = len(bits) - (len(bits) % 8)
    data = bytearray()

    for i in range(0, usable_length, 8):
        byte = 0
        for bit in bits[i:i + 8]:
            byte = (byte << 1) | bit
        data.append(byte)

    return data.decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(description="Приёмник текста через звук (BFSK)")
    parser.add_argument("--duration", type=float, default=20.0,
                        help="Длительность записи в секундах (по умолчанию 20)")
    parser.add_argument("--device", type=int, default=None,
                        help="Номер микрофона из devices.py")
    parser.add_argument("--wav", default="recording.wav",
                        help="Файл для сохранения записи")
    parser.add_argument("--output", default=OUTPUT_FILE,
                        help=f"Файл для принятого сообщения (по умолчанию {OUTPUT_FILE})")
    args = parser.parse_args()

    signal = record_audio(args.duration, args.device)
    save_recording(signal, Path(args.wav))

    print("Декодирую...")
    text, bits = decode_signal(signal)

    if text is None:
        print("\nFAILED: стартовый маркер 3500 Гц не найден.")
        print("Проверьте громкость, расстояние и запускайте receiver ДО sender.")
        return

    print(f"\nПринято битов: {len(bits)} ({len(bits) // 8} байт)")
    print(f"Сообщение: {text!r}")

    output_path = Path(args.output)
    output_path.write_text(text, encoding="utf-8")
    print(f"Сообщение записано в файл: {output_path.resolve()}")


if __name__ == "__main__":
    main()
