"""ТУРБО-режим: параллельные полосы × 16-FSK + FEC (XOR-чётность).

Проверяет:
  - согласование и декодирование 4×16 и 6×16 полос @ 50 мс (320/480 бит/с)
  - устойчивость к дрейфу часов +0.2% в турбо-режиме
  - восстановление побитого блока XOR-чётностью
  - ожидание блоков чётности (fec_pending) при обрезанном кадре
"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import phy
import protocol


def with_noise(sig: np.ndarray, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    lead = np.zeros(24000, dtype=np.float32)
    out = np.concatenate([lead, sig, lead])
    return (out + rng.normal(0.0, 0.02, len(out))).astype(np.float32)


payload = bytes((i * 73 + 5) & 0xFF for i in range(400))
frame = protocol.build_frame("turbo.bin", payload, fec=True)

for bands in (4, 6):
    p = phy.make_params(16, 50, "turbo", bands=bands)
    errors, _ = phy.validate(p)
    assert not errors, errors
    res = phy.decode_auto(with_noise(phy.build_transmission(frame, p, volume=0.6)))
    assert res["error"] is None, res["error"]
    assert res["params"].bands == bands, res["params"]
    parsed = protocol.parse_frame(protocol.bits_to_bytes(res["bits"]))
    assert parsed["sha_ok"], (parsed["truncated"], parsed["bad_blocks"])
    print(f"ТУРБО OK: {bands}×16 тонов · 50 мс · {p.bitrate:.0f} бит/с — файл восстановлен, SHA-256 подтверждён")

# дрейф часов +0.2% (разные звуковые карты)
p4 = phy.make_params(16, 50, "turbo", bands=4)
sig = phy.build_transmission(frame, p4, volume=0.6)
n2 = int(len(sig) / 1.002)
drifted = np.interp(np.linspace(0, len(sig) - 1, n2), np.arange(len(sig)), sig).astype(np.float32)
res = phy.decode_auto(with_noise(drifted))
assert res["error"] is None, res["error"]
parsed = protocol.parse_frame(protocol.bits_to_bytes(res["bits"]))
assert parsed["sha_ok"], (parsed["truncated"], parsed["bad_blocks"])
print("ТУРБО ДРЕЙФ OK: +0.2% — файл восстановлен, SHA-256 подтверждён")

# FEC: портим один блок в байтах кадра — XOR-чётность обязана его починить
header_len = 4 + 1 + 1 + 4 + 32 + len("turbo.bin") + 4  # 55
corrupted = bytearray(frame)
corrupted[header_len + 2 * (protocol.BLOCK_SIZE + 4) + 10] ^= 0xFF  # блок №2
parsed = protocol.parse_frame(bytes(corrupted))
assert parsed["recovered_blocks"] == [2], parsed["recovered_blocks"]
assert parsed["sha_ok"] and not parsed["bad_blocks"], (parsed["sha_ok"], parsed["bad_blocks"])
print("FEC OK: побитый блок восстановлен XOR-чётностью, SHA-256 подтверждён")

# чётность ещё не дошла: кадр не должен считаться завершённым (авто-стоп ждёт)
cut = protocol.parse_frame(bytes(corrupted[:-20]))
assert cut["fec_pending"], "обрезанная чётность не помечена как ожидаемая"
print("FEC PENDING OK: приёмник ждёт блоки чётности, если данные пришли с ошибками")
