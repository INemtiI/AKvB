// Автотест JS-логики из audiomodem.html без браузера (node test_webapp.js).
// Извлекает <script> из html и прогоняет loopback на 44100 Гц (частота телефонов).
const fs = require("fs");
const crypto = require("crypto");
const zlib = require("zlib");

// можно указать файл аргументом: node test_webapp.js index.html
const target = process.argv[2] || "index.html";
const html = fs.readFileSync(__dirname + "/../" + target, "utf8"); // index.html лежит в корне репозитория (GitHub Pages)
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

// 4. Авто-согласование параметров (если тестируем новый index.html)
if (typeof buildNegoSignal === "function") {
  for (const p of [makeParams(16, 60, 1400, 260, 6100), makeParams(8, 60, 1200, 380, 4300), makeParams(2, 200, 1500, 400, 3500)]) {
    const nego = buildNegoSignal(frame, sampleRate, 0.5, p);
    const noisy = new Float32Array(lead + nego.length + lead);
    for (let i = 0; i < noisy.length; i++) noisy[i] = 0.02 * rand();
    for (let i = 0; i < nego.length; i++) noisy[lead + i] += nego[i];

    const res = decodeAuto(noisy, sampleRate);
    if (res.error) throw new Error(`NEGO ${p.tones}-FSK: ${res.error}`);
    if (res.legacy) throw new Error(`NEGO ${p.tones}-FSK: служебная посылка не распознана`);
    if (res.params.tones !== p.tones || res.params.symbolMs !== p.symbolMs
        || res.params.base !== p.base || res.params.step !== p.step || res.params.marker !== p.marker)
      throw new Error(`NEGO ${p.tones}-FSK: параметры искажены`);
    const r2 = parseFrame(bitsToBytes(res.bits));
    if (!r2.shaOk) throw new Error(`NEGO ${p.tones}-FSK: SHA-256 не совпал`);
    console.log(`NEGO OK: ${p.tones}-FSK · ${p.symbolMs} мс · ${p.base}+${p.step}·k Гц · маркер ${p.marker} — режим опознан, файл восстановлен`);
  }

  // старый формат без служебной посылки должен опознаваться как legacy
  const legacyRes = decodeAuto(signal, sampleRate);
  if (!legacyRes.legacy) throw new Error("старый формат не опознан как legacy");
  if (!parseFrame(bitsToBytes(legacyRes.bits)).shaOk) throw new Error("legacy: SHA-256 не совпал");
  console.log("NEGO OK: старый формат без служебной посылки принимается по-прежнему");

  // 5. Дрейф часов: запись растянута на ±0.25% (разные кварцы устройств) —
  // следящий декодер должен удержать сетку на длинном кадре
  const pDrift = makeParams(4, 100, 1500, 400, 3500);
  const negoDrift = buildNegoSignal(frame, sampleRate, 0.5, pDrift);
  for (const factor of [0.9975, 1.0025]) {
    const n = Math.round(negoDrift.length * factor);
    const stretched = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const x = (i * (negoDrift.length - 1)) / (n - 1);
      const i0 = Math.floor(x);
      const i1 = Math.min(i0 + 1, negoDrift.length - 1);
      stretched[i] = negoDrift[i0] + (x - i0) * (negoDrift[i1] - negoDrift[i0]);
    }
    const noisy = new Float32Array(lead + n + lead);
    for (let i = 0; i < noisy.length; i++) noisy[i] = 0.02 * rand();
    for (let i = 0; i < n; i++) noisy[lead + i] += stretched[i];

    const res = decodeAuto(noisy, sampleRate);
    if (res.error) throw new Error(`ДРЕЙФ ${factor}: ${res.error}`);
    if (res.legacy) throw new Error(`ДРЕЙФ ${factor}: служебная посылка не распознана`);
    const rd = parseFrame(bitsToBytes(res.bits));
    if (!rd.shaOk) throw new Error(`ДРЕЙФ ${factor}: SHA-256 не совпал`);
    console.log(`ДРЕЙФ OK: ${((factor - 1) * 100).toFixed(2)}% — файл восстановлен, SHA-256 подтверждён`);
  }
}

// 6. Кодировка ASCII-7: упаковка по 7 бит + флаг кодировки в кадре
if (typeof packAscii7 === "function") {
  const a7Text = "Hello (World)! [test] {42} #symbols: <>&*+=_/\\|~^%$@'\";:.,?-";
  const a7Packed = packAscii7(a7Text);
  if (a7Packed.length !== Math.ceil(a7Text.length * 7 / 8)) throw new Error("ASCII7: неверная длина упаковки");
  if (unpackAscii7(a7Packed) !== a7Text) throw new Error("ASCII7: распаковка не сошлась");
  const a7Frame = buildFrame("message.txt", a7Packed, true, true);
  const a7Res = parseFrame(a7Frame);
  if (!a7Res.isText || !a7Res.isAscii7) throw new Error("ASCII7: флаги кадра потерялись");
  if (unpackAscii7(a7Res.payload) !== a7Text) throw new Error("ASCII7: текст после кадра не сошёлся");
  console.log("ASCII7 OK: 7-битная упаковка обратима, кадр переносит флаг кодировки");
}
