"""
test_loopback.py — проверка модема БЕЗ микрофона и динамика.

Генерирует сигнал функциями sender.py, добавляет шум и декодирует
функциями receiver.py. Удобно для отладки протокола без двух компьютеров.

Запуск:
    python test_loopback.py
"""

import numpy as np

from sender import create_transmission
from receiver import decode_signal


def main() -> None:
    text = open("message.txt", encoding="utf-8").read()
    print(f"Исходное сообщение: {text!r}")

    signal = create_transmission(text, volume=0.5)

    # Имитируем реальные условия: шум + тишина до и после.
    rng = np.random.default_rng(42)
    noisy = signal + 0.02 * rng.standard_normal(len(signal)).astype(np.float32)
    padding = np.zeros(24000, dtype=np.float32)
    recording = np.concatenate([padding, noisy, padding])

    decoded_text, bits = decode_signal(recording)

    print(f"Принято битов: {len(bits)}")
    print(f"Декодировано: {decoded_text!r}")

    if decoded_text == text:
        print("LOOPBACK OK: сообщение восстановлено без ошибок!")
    else:
        print("LOOPBACK FAILED: сообщения не совпадают.")


if __name__ == "__main__":
    main()
