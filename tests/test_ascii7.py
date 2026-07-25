"""
test_ascii7.py — проверка 7-битной упаковки текста (кодировка ASCII-7).

Запуск: python tests/test_ascii7.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import protocol


def main() -> None:
    samples = [
        "Hello, World! (test) [brackets] {braces} <angle> #12 @a $5 ^ & * _ - + = / \\ | ~ ` ' \" ; : . , ?",
        "A" * 100,
        "".join(chr(c) for c in range(32, 127)),  # все печатные ASCII
        "multi\nline\ttext",
    ]
    for s in samples:
        packed = protocol.pack_ascii7(s)
        expected_len = (len(s) * 7 + 7) // 8
        assert len(packed) == expected_len, f"длина: {len(packed)} != {expected_len}"
        assert len(packed) < len(s.encode("utf-8")), "упаковка не короче UTF-8"
        out = protocol.unpack_ascii7(packed)
        assert out == s, f"не сошлось: {s!r} -> {out!r}"

    # не-ASCII текст должен отклоняться
    try:
        protocol.pack_ascii7("привет")
        raise SystemExit("ошибка: ожидали ValueError на не-ASCII")
    except ValueError:
        pass

    # флаг кодировки должен переноситься кадром
    text = samples[0]
    frame = protocol.build_frame("message.txt", protocol.pack_ascii7(text),
                                 is_text=True, is_ascii7=True)
    result = protocol.parse_frame(frame)
    assert result["is_text"] and result["is_ascii7"], "флаги кадра потерялись"
    assert protocol.unpack_ascii7(result["payload"]) == text, "текст после кадра не сошёлся"

    print(f"ASCII7 OK: {len(samples)} строк упакованы по 7 бит и распакованы без потерь,"
          " кадр переносит флаг кодировки")


if __name__ == "__main__":
    main()
