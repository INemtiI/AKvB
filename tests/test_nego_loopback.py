"""
test_nego_loopback.py — проверка авто-согласования параметров без звука.

Для каждого режима:
  1. Случайный файл -> кадр -> эфир со служебной посылкой (phy.build_transmission).
  2. Шум + тишина по краям (имитация микрофона).
  3. phy.decode_auto без подсказок должен сам определить режим и восстановить файл.
Плюс проверка совместимости со старым форматом без служебной посылки.

Запуск: python test_nego_loopback.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import phy
import protocol
from file_sender import frame_to_signal

CASES = [
    ("16-FSK ultra", phy.make_params(16, 60, "ultra")),
    ("2-FSK медленный", phy.make_params(2, 200, "standard")),
    ("4-FSK базовый", phy.make_params(4, 100, "standard")),
    ("4-FSK верхний", phy.make_params(4, 80, "high")),
    ("8-FSK быстрый", phy.make_params(8, 60, "wide")),
]


def with_noise(signal: np.ndarray, rng) -> np.ndarray:
    return np.concatenate([
        np.zeros(24000, dtype=np.float32),
        signal + 0.02 * rng.standard_normal(len(signal)).astype(np.float32),
        np.zeros(24000, dtype=np.float32),
    ])


def with_drift(signal: np.ndarray, factor: float) -> np.ndarray:
    """Имитирует расхождение часов звуковых карт: растягивает запись в factor раз."""
    n = int(len(signal) * factor)
    x_new = np.linspace(0, len(signal) - 1, n)
    return np.interp(x_new, np.arange(len(signal)), signal).astype(np.float32)


def main() -> None:
    rng = np.random.default_rng(7)
    payload = rng.integers(0, 256, size=120, dtype=np.uint8).tobytes()
    frame = protocol.build_frame("тест.bin", payload)

    for name, p in CASES:
        errors, _ = phy.validate(p)
        assert not errors, f"{name}: {errors}"
        signal = phy.build_transmission(frame, p, volume=0.5)
        result = phy.decode_auto(with_noise(signal, rng))

        assert result["error"] is None, f"{name}: {result['error']}"
        assert not result["legacy"], f"{name}: служебная посылка не распознана"
        assert result["params"] == p, f"{name}: параметры искажены: {result['params']}"

        parsed = protocol.parse_frame(protocol.bits_to_bytes(result["bits"]))
        assert parsed["payload"] == payload, f"{name}: данные не совпали"
        assert parsed["sha_ok"], f"{name}: SHA-256 не совпал"
        print(f"OK  {name}: {p.describe()} — режим опознан, файл восстановлен, SHA-256 сошёлся")

    # Совместимость со старым форматом (без служебной посылки)
    legacy_signal = frame_to_signal(frame, volume=0.5)
    result = phy.decode_auto(with_noise(legacy_signal, rng))
    assert result["legacy"], "старый формат не опознан как legacy"
    parsed = protocol.parse_frame(protocol.bits_to_bytes(result["bits"]))
    assert parsed["sha_ok"], "legacy: SHA-256 не совпал"
    print("OK  старый формат без служебной посылки принимается по-прежнему")

    # Дрейф часов: запись "уплыла" на ±0.25% (разные кварцы передатчика/приёмника).
    # Без следящего декодера такой кадр (~70 с) разваливается после первых ~10 с.
    p_drift = phy.make_params(4, 100, "standard")
    drift_signal = phy.build_transmission(frame, p_drift, volume=0.5)
    for factor in (0.9975, 1.0025):
        drifted = with_noise(with_drift(drift_signal, factor), rng)
        result = phy.decode_auto(drifted)
        assert result["error"] is None, f"дрейф {factor}: {result['error']}"
        assert not result["legacy"], f"дрейф {factor}: служебная посылка не распознана"
        parsed = protocol.parse_frame(protocol.bits_to_bytes(result["bits"]))
        assert parsed["payload"] == payload, f"дрейф {factor}: данные не совпали"
        assert parsed["sha_ok"], f"дрейф {factor}: SHA-256 не совпал"
        print(f"OK  дрейф часов {(factor - 1) * 100:+.2f}%: файл восстановлен, SHA-256 сошёлся")

    print("\nNEGO LOOPBACK OK: все режимы согласованы и декодированы автоматически!")


if __name__ == "__main__":
    main()
