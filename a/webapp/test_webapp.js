// Автотест JS-логики из audiomodem.html без браузера (node test_webapp.js).
// Извлекает <script> из html и прогоняет loopback на 44100 Гц (частота телефонов).
const fs = require("fs");
const crypto = require("crypto");
const zlib = require("zlib");

const html = fs.readFileSync(__dirname + "/audiomodem.html", "utf8");
// Убираем "use strict", чтобы функции из eval были видны в тесте
const script = html.match(/<script>([\s\S]*)<\/script>/)[1].replace('"use strict";', '');

global.window = {};
eval(script);

// 1. Кросс-проверка SHA-256 и CRC32 с эталонными реализациями Node
const sample = new Uint8Array(999).map((_, i) => (i * 37 + 11) & 0xff);
const shaRef = crypto.createHash("sha256").update(sample).digest();
const shaOurs = Buffer.from(sha256(sample));
if (!shaRef.equals(shaOurs)) throw new Error("SHA-256 НЕ совпал с эталоном!");
if (zlib.crc32(Buffer.from(sample)) !== crc32(sample)) throw new Error("CRC32 НЕ совпал с эталоном!");
console.log("SHA-256 и CRC32 совпадают с эталонными реализациями Node");

// 2. Полный loopback: бинарный файл -> сигнал 44100 Гц + шум -> декодер -> кадр
const sampleRate = 44100;
const payload = new Uint8Array(200);
for (let i = 0; i < payload.length; i++) payload[i] = (i * 73 + 5) & 0xff;

const frame = buildFrame("тест.bin", payload, false);
const clean = buildSignal(frame, sampleRate, 0.5);

const lead = Math.round(0.5 * sampleRate);
const signal = new Float32Array(lead + clean.length + lead);
let seed = 7;
const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff - 0.5; };
for (let i = 0; i < signal.length; i++) signal[i] = 0.02 * rand();
for (let i = 0; i < clean.length; i++) signal[lead + i] += clean[i];

const bits = decodeSignal(signal, sampleRate);
if (!bits) throw new Error("Маркер не найден!");
const result = parseFrame(bitsToBytes(bits));
console.log(`Файл: ${result.filename} | байт: ${result.payload.length} | плохих блоков: ${result.badBlocks.length}`);
if (result.filename !== "тест.bin") throw new Error("Имя файла не совпало");
if (!result.shaOk) throw new Error("SHA-256 не совпал");
for (let i = 0; i < payload.length; i++)
  if (result.payload[i] !== payload[i]) throw new Error("Данные не совпали");

// 3. Совместимость с Python: кадр из JS должен бит-в-бит совпадать с protocol.py
fs.writeFileSync(__dirname + "/frame_from_js.bin", Buffer.from(frame));
console.log("WEBAPP LOOPBACK OK: файл восстановлен на 44100 Гц, SHA-256 подтверждён!");
