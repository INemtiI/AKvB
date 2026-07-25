"""
test_autostop.py — проверка автостопа записи по конечному маркеру.

Полная запись должна опознаваться как завершённая, оборванная
посередине или чистый шум — нет (иначе запись остановится раньше времени).

Запуск: python tests/test_autostop.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import phy
import protocol
from file_receiver import frame_complete


def main() -> None:
    rng = np.random.default_rng(3)
    payload = rng.integers(0, 256, size=150, dtype=np.uint8).tobytes()
    frame = protocol.build_frame("тест.bin", payload)
    params = phy.make_params(8, 60, "wide")

    signal = phy.build_transmission(frame, params, volume=0.5)
    noisy = np.concatenate([
        np.zeros(24000, dtype=np.float32),
        signal + 0.02 * rng.standard_normal(len(signal)).astype(np.float32),
    ])

    assert frame_complete(noisy), "полная запись должна считаться завершённой"

    for cut in (0.35, 0.6, 0.85):
        part = noisy[:int(len(noisy) * cut)]
        assert not frame_complete(part), (
            f"обрыв на {cut:.0%}: запись не должна считаться завершённой")

    quiet = 0.02 * rng.standard_normal(2 * phy.SAMPLE_RATE).astype(np.float32)
    assert not frame_complete(quiet), "шум не должен считаться завершённым кадром"

    print("AUTOSTOP OK: конец передачи детектируется, обрыв и шум не путаются с концом")


if __name__ == "__main__":
    main()
