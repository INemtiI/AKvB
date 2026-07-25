"""
protocol.py — кадровый (канальный) уровень аудиомодема.

Физический уровень (4-FSK, 100 мс/символ) живёт в sender.py/receiver.py
и НЕ меняется. Этот модуль только упаковывает/распаковывает байты.

ФОРМАТ КАДРА (все числа big-endian):

  ЗАГОЛОВОК:
    magic          4 байта   b"AM01" — опознавательный маркер кадра
    flags          1 байт    bit0: 1 = payload это UTF-8 текст, 0 = бинарный файл
    filename_len   1 байт    длина имени файла в байтах (0..255)
    payload_len    4 байта   размер данных в байтах
    sha256        32 байта   хеш всего payload (проверка целостности файла)
    filename       N байт   имя файла в UTF-8
    header_crc32   4 байта   CRC32 всего заголовка выше

  ДАННЫЕ — блоками по BLOCK_SIZE байт (последний может быть короче):
    data           <=64 байт
    block_crc32    4 байта   CRC32 этого блока — локализует ошибки:
                              видно, КАКОЙ именно участок файла побился

Зачем два уровня проверки:
  - CRC32 блоков — быстрая локализация ошибок при приёме;
  - SHA-256 файла — криптографически стойкое подтверждение, что файл
    восстановлен байт-в-байт (требование задания).
"""

import hashlib
import zlib

MAGIC = b"AM01"
BLOCK_SIZE = 64
FLAG_TEXT = 0x01


def bytes_to_bits(data: bytes) -> list[int]:
    """Байты -> список битов (старший бит первым)."""
    bits: list[int] = []
    for byte in data:
        for position in range(7, -1, -1):
            bits.append((byte >> position) & 1)
    return bits


def bits_to_bytes(bits: list[int]) -> bytes:
    """Список битов (старший первым) -> байты (неполный хвост отбрасывается)."""
    usable = len(bits) - (len(bits) % 8)
    data = bytearray()
    for i in range(0, usable, 8):
        byte = 0
        for bit in bits[i:i + 8]:
            byte = (byte << 1) | bit
        data.append(byte)
    return bytes(data)


def build_frame(filename: str, payload: bytes, is_text: bool = False) -> bytes:
    """Собирает кадр: заголовок + блоки с CRC32."""
    name_bytes = filename.encode("utf-8")
    if len(name_bytes) > 255:
        raise ValueError("Слишком длинное имя файла (максимум 255 байт в UTF-8)")

    flags = FLAG_TEXT if is_text else 0
    header = (
        MAGIC
        + bytes([flags, len(name_bytes)])
        + len(payload).to_bytes(4, "big")
        + hashlib.sha256(payload).digest()
        + name_bytes
    )
    header += zlib.crc32(header).to_bytes(4, "big")

    body = bytearray()
    for i in range(0, len(payload), BLOCK_SIZE):
        block = payload[i:i + BLOCK_SIZE]
        body += block
        body += zlib.crc32(block).to_bytes(4, "big")

    return header + bytes(body)


def frame_overhead(filename: str, payload_len: int) -> int:
    """Сколько служебных байт добавит кадр (для оценки времени передачи)."""
    name_len = len(filename.encode("utf-8"))
    header = 4 + 1 + 1 + 4 + 32 + name_len + 4
    block_count = (payload_len + BLOCK_SIZE - 1) // BLOCK_SIZE
    return header + block_count * 4


class FrameError(Exception):
    """Кадр не распознан или безнадёжно повреждён."""


def parse_frame(data: bytes) -> dict:
    """Разбирает принятые байты.

    Возвращает словарь:
      filename      имя файла
      is_text       был ли payload текстом
      payload       восстановленные данные (байты)
      total_blocks  сколько блоков ожидалось
      bad_blocks    список номеров блоков с ошибкой CRC32 (с нуля)
      truncated     оборвалась ли передача раньше конца
      sha_ok        совпал ли SHA-256 (главный вердикт целостности)
    """
    magic_at = data.find(MAGIC)
    if magic_at < 0:
        raise FrameError("Магическая последовательность AM01 не найдена — это не файловый кадр")
    data = data[magic_at:]

    fixed = 4 + 1 + 1 + 4 + 32
    if len(data) < fixed:
        raise FrameError("Кадр оборван внутри заголовка")

    flags = data[4]
    name_len = data[5]
    payload_len = int.from_bytes(data[6:10], "big")
    sha_expected = data[10:42]

    header_end = fixed + name_len
    if len(data) < header_end + 4:
        raise FrameError("Кадр оборван внутри имени файла")

    filename = data[fixed:header_end].decode("utf-8", errors="replace")
    header_crc = int.from_bytes(data[header_end:header_end + 4], "big")
    if zlib.crc32(data[:header_end]) != header_crc:
        raise FrameError("CRC32 заголовка не сошёлся — заголовок повреждён, разбор невозможен")

    total_blocks = (payload_len + BLOCK_SIZE - 1) // BLOCK_SIZE
    payload = bytearray()
    bad_blocks: list[int] = []
    truncated = False

    offset = header_end + 4
    for block_index in range(total_blocks):
        block_len = min(BLOCK_SIZE, payload_len - block_index * BLOCK_SIZE)
        end = offset + block_len + 4
        if len(data) < end:
            truncated = True
            available = max(0, len(data) - offset - 4)
            payload += data[offset:offset + max(0, min(block_len, available))]
            bad_blocks.extend(range(block_index, total_blocks))
            break

        block = data[offset:offset + block_len]
        crc_expected = int.from_bytes(data[offset + block_len:end], "big")
        if zlib.crc32(block) != crc_expected:
            bad_blocks.append(block_index)
        payload += block
        offset = end

    sha_ok = (
        not truncated
        and len(payload) == payload_len
        and hashlib.sha256(bytes(payload)).digest() == sha_expected
    )

    return {
        "filename": filename,
        "is_text": bool(flags & FLAG_TEXT),
        "payload": bytes(payload),
        "payload_len_expected": payload_len,
        "total_blocks": total_blocks,
        "bad_blocks": bad_blocks,
        "truncated": truncated,
        "sha_ok": sha_ok,
    }
