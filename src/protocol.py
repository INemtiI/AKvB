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
FLAG_ASCII7 = 0x02  # текст упакован по 7 бит на символ (только ASCII)
FLAG_FEC = 0x04     # после блоков данных идут блоки XOR-чётности (восстановление ошибок)
FEC_GROUP = 8       # блоков данных на один блок чётности (накладные ~12.5%)


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


def build_frame(filename: str, payload: bytes, is_text: bool = False,
                is_ascii7: bool = False, fec: bool = False) -> bytes:
    """Собирает кадр: заголовок + блоки с CRC32 (+ блоки XOR-чётности при fec=True)."""
    name_bytes = filename.encode("utf-8")
    if len(name_bytes) > 255:
        raise ValueError("Слишком длинное имя файла (максимум 255 байт в UTF-8)")

    has_fec = fec and len(payload) > 0
    flags = ((FLAG_TEXT if is_text else 0) | (FLAG_ASCII7 if is_ascii7 else 0)
             | (FLAG_FEC if has_fec else 0))
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

    if has_fec:
        # XOR-чётность: на каждые FEC_GROUP блоков данных — один блок чётности
        # (64 байта, короткий последний блок дополняется нулями).
        # Приёмник восстановит ЛЮБОЙ один побитый блок в группе.
        blocks = [payload[i:i + BLOCK_SIZE] for i in range(0, len(payload), BLOCK_SIZE)]
        for g in range(0, len(blocks), FEC_GROUP):
            parity = bytearray(BLOCK_SIZE)
            for block in blocks[g:g + FEC_GROUP]:
                for j, byte in enumerate(block):
                    parity[j] ^= byte
            body += parity
            body += zlib.crc32(bytes(parity)).to_bytes(4, "big")

    return header + bytes(body)


def frame_overhead(filename: str, payload_len: int, fec: bool = False) -> int:
    """Сколько служебных байт добавит кадр (для оценки времени передачи)."""
    name_len = len(filename.encode("utf-8"))
    header = 4 + 1 + 1 + 4 + 32 + name_len + 4
    block_count = (payload_len + BLOCK_SIZE - 1) // BLOCK_SIZE
    parity = (((block_count + FEC_GROUP - 1) // FEC_GROUP) * (BLOCK_SIZE + 4)
              if fec and payload_len else 0)
    return header + block_count * 4 + parity


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

    has_fec = bool(flags & FLAG_FEC)
    fec_pending = False
    recovered_blocks: list[int] = []
    if has_fec and not truncated and total_blocks:
        parity_count = (total_blocks + FEC_GROUP - 1) // FEC_GROUP
        parities = []
        for gi in range(parity_count):
            p_off = offset + gi * (BLOCK_SIZE + 4)
            p_end = p_off + BLOCK_SIZE + 4
            if len(data) < p_end:
                fec_pending = True  # чётность ещё не дошла (важно для авто-стопа)
                parities.append(None)
                continue
            pblock = data[p_off:p_off + BLOCK_SIZE]
            pcrc = int.from_bytes(data[p_off + BLOCK_SIZE:p_end], "big")
            parities.append(pblock if zlib.crc32(pblock) == pcrc else None)
        for gi, pblock in enumerate(parities):
            if pblock is None or not bad_blocks:
                continue
            group = range(gi * FEC_GROUP, min((gi + 1) * FEC_GROUP, total_blocks))
            bad_in_group = [b for b in group if b in bad_blocks]
            if len(bad_in_group) != 1:
                continue  # XOR-чётность чинит ровно один блок в группе
            bad = bad_in_group[0]
            rec = bytearray(pblock)
            for b in group:
                if b == bad:
                    continue
                for j, byte in enumerate(payload[b * BLOCK_SIZE:b * BLOCK_SIZE + BLOCK_SIZE]):
                    rec[j] ^= byte
            bad_len = min(BLOCK_SIZE, payload_len - bad * BLOCK_SIZE)
            payload[bad * BLOCK_SIZE:bad * BLOCK_SIZE + bad_len] = rec[:bad_len]
            bad_blocks.remove(bad)
            recovered_blocks.append(bad)

    sha_ok = (
        not truncated
        and len(payload) == payload_len
        and hashlib.sha256(bytes(payload)).digest() == sha_expected
    )

    return {
        "filename": filename,
        "is_text": bool(flags & FLAG_TEXT),
        "is_ascii7": bool(flags & FLAG_ASCII7),
        "payload": bytes(payload),
        "payload_len_expected": payload_len,
        "total_blocks": total_blocks,
        "bad_blocks": bad_blocks,
        "truncated": truncated,
        "fec": has_fec,
        "fec_pending": fec_pending,
        "recovered_blocks": recovered_blocks,
        "sha_ok": sha_ok,
    }


# ────────── ASCII-7: упаковка текста по 7 бит на символ ──────────

def pack_ascii7(text: str) -> bytes:
    """Упаковывает ASCII-текст (коды 0..127) по 7 бит — на 12.5% короче UTF-8.

    Покрывает латиницу, цифры, скобки и все стандартные знаки.
    На не-ASCII символе бросает ValueError.
    """
    acc = 0
    nbits = 0
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if code > 127:
            raise ValueError(f"не-ASCII символ: {ch!r}")
        acc = (acc << 7) | code
        nbits += 7
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
    if nbits:
        out.append((acc << (8 - nbits)) & 0xFF)
    return bytes(out)


def unpack_ascii7(data: bytes) -> str:
    """Обратная операция: 7-битный поток -> строка (хвостовые NUL отбрасываются)."""
    acc = 0
    nbits = 0
    chars = []
    for byte in data:
        acc = ((acc << 8) | byte) & 0x7FFF
        nbits += 8
        while nbits >= 7:
            nbits -= 7
            chars.append(chr((acc >> nbits) & 0x7F))
    return "".join(chars).rstrip("\x00")
