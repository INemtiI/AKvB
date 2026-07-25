"""
test_file_loopback.py — проверка файлового режима без звука (математический loopback).

Сценарий:
  1. Генерируется случайный бинарный файл (200 байт).
  2. Собирается кадр и аудиосигнал (как в file_sender.py).
  3. Добавляется шум и тишина по краям (имитация записи с микрофона).
  4. Сигнал декодируется (как в file_receiver.py) и сравнивается SHA-256.

Запуск: python test_file_loopback.py
"""

import hashlib

import numpy as np

import protocol
from file_sender import frame_to_signal
from receiver import decode_signal


def main() -> None:
    rng = np.random.default_rng(42)
    payload = rng.integers(0, 256, size=200, dtype=np.uint8).tobytes()
    filename = "тест.bin"

    frame = protocol.build_frame(filename, payload)
    signal = frame_to_signal(frame, volume=0.5)

    noisy = np.concatenate([
        np.zeros(24000, dtype=np.float32),
        signal + 0.02 * rng.standard_normal(len(signal)).astype(np.float32),
        np.zeros(24000, dtype=np.float32),
    ])

    _, bits = decode_signal(noisy)
    result = protocol.parse_frame(protocol.bits_to_bytes(bits))

    print(f"Файл: {result['filename']} | байт: {len(result['payload'])}"
          f" | блоков с ошибками: {len(result['bad_blocks'])}")

    assert result["filename"] == filename, "Имя файла не совпало"
    assert result["payload"] == payload, "Данные не совпали"
    assert result["sha_ok"], "SHA-256 не совпал"
    assert hashlib.sha256(result["payload"]).digest() == hashlib.sha256(payload).digest()

    print("FILE LOOPBACK OK: файл восстановлен байт-в-байт, SHA-256 подтверждён!")


if __name__ == "__main__":
    main()
