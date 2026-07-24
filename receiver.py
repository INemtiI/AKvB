"""
receiver.py — приёмник тестового сигнала.

Запускается на втором компьютере ПЕРЕД запуском sender.py.
Записывает звук с микрофона, сохраняет его в recording.wav
и проверяет, слышен ли основной тон 2000 Гц.

Запуск:
    python receiver.py
    python receiver.py --duration 15
    python receiver.py --device 1     # номер микрофона из devices.py
"""

import argparse
from pathlib import Path

import numpy as np
import sounddevice as sd
from scipy.io import wavfile

SAMPLE_RATE = 48000
EXPECTED_FREQUENCY = 2000.0   # Ожидаемый основной сигнал, Гц
FREQUENCY_TOLERANCE = 100.0   # Допустимое отклонение, Гц


def record_audio(duration: float, device):
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
    wavfile.write(path, SAMPLE_RATE, pcm)
    print(f"Запись сохранена: {path.resolve()}")


def find_dominant_frequency(signal: np.ndarray):
    """Возвращает доминирующую частоту (Гц) и выраженность её пика."""
    # Убираем постоянную составляющую.
    signal = signal - np.mean(signal)

    # Окно Ханна уменьшает "размазывание" спектра.
    windowed = signal * np.hanning(len(signal))

    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(windowed), d=1.0 / SAMPLE_RATE)

    # Анализируем только диапазон 500–5000 Гц.
    mask = (freqs >= 500) & (freqs <= 5000)
    spectrum = spectrum[mask]
    freqs = freqs[mask]

    peak_index = int(np.argmax(spectrum))
    dominant_frequency = float(freqs[peak_index])

    # Насколько пик сильнее среднего уровня спектра.
    peak_ratio = float(spectrum[peak_index] / (np.mean(spectrum) + 1e-12))

    return dominant_frequency, peak_ratio


def main() -> None:
    parser = argparse.ArgumentParser(description="Приёмник тестового сигнала")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Длительность записи в секундах (по умолчанию 10)")
    parser.add_argument("--device", type=int, default=None,
                        help="Номер микрофона из devices.py")
    parser.add_argument("--output", default="recording.wav",
                        help="Файл для сохранения записи")
    args = parser.parse_args()

    signal = record_audio(args.duration, args.device)
    save_recording(signal, Path(args.output))

    rms = float(np.sqrt(np.mean(signal ** 2)))
    frequency, peak_ratio = find_dominant_frequency(signal)

    print("\nРезультаты анализа:")
    print(f"Уровень сигнала (RMS):  {rms:.6f}")
    print(f"Доминирующая частота:   {frequency:.1f} Гц")
    print(f"Выраженность пика:      {peak_ratio:.1f}x")

    detected = (
        abs(frequency - EXPECTED_FREQUENCY) <= FREQUENCY_TOLERANCE
        and peak_ratio >= 10.0
        and rms >= 0.001
    )

    if detected:
        print("\nSUCCESS: сигнал 2000 Гц принят!")
    else:
        print("\nFAILED: сигнал не обнаружен.")
        print("Проверьте громкость, расстояние и выбранные устройства.")


if __name__ == "__main__":
    main()
