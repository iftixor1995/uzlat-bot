# uzlat-bot — Yangi o'zbek alifbosi (2026) konvertori

Telegram bot: kirill yoki eski lotin (1995) yozuvidagi matn va fayllarni
2026-yilgi yangi lotin alifbosiga (O'RQ-1184: 28 harf + tutuq belgisi, Ö Ğ Ş Ç) o'giradi.

| Kirish | Chiqish |
|---|---|
| Oddiy xabar (matn) | Matn (4096 belgidan uzun bo'lsa — .txt fayl) |
| `.txt` | `.txt` |
| `.docx` | `.docx` — formatlash (shrift, qalin/kursiv, jadval, sarlavha, kolontitul, izoh) to'liq saqlanadi |
| `.pdf` | `.pdf` + `.docx` — PDF **joyida** o'giriladi (rasm, tartib, shrift, rang saqlanadi; Times → Liberation Serif, Arial → Liberation Sans). Word nusxasi `pdf2docx` bilan rasmlar va jadvallar saqlangan holda yasalib, keyin o'giriladi. Skan PDF ishlamaydi |

## Ishga tushirish

### Docker (tavsiya)
```bash
cp .env.example .env        # BOT_TOKEN ni yozing
docker compose up -d --build
docker compose logs -f
```

### Lokal
```bash
pip install -r requirements.txt
BOT_TOKEN=... python -m bot.main
```

### Vercel (tavsiya — tekin, webhook rejimi)
1. vercel.com → Add New → Project → shu GitHub repo'ni import qiling (Framework: Other).
2. Environment Variables: `BOT_TOKEN`, `WEBHOOK_SECRET` (istalgan tasodifiy satr).
3. (ixtiyoriy) Storage → Upstash Redis ulang — tanlangan rejim eslab qolinadi va takroriy update'lar tashlanadi.
4. Deploy → saytning asosiy manzilini brauzerda oching (`https://<loyiha>.vercel.app/`) — webhook o'zi ro'yxatdan o'tadi, `{"ok": true, ...}` qaytadi.
Kirish nuqtasi: `api/index.py` (ASGI), sozlamalar: `vercel.json`. Polling uchun `python -m bot.main` ham ishlayveradi.

### HOKAS (hokas.net) va shunga o'xshash Python hostinglar
Docker shart emas. Loyiha papkasini yuklang, so'ng:
- Start command: `python -m bot.main`  (yoki `bash run.sh`)
- Environment: `BOT_TOKEN=...`
- Python 3.10+ ; `requirements.txt` avtomatik o'rnatiladi
PDF shrifti loyiha ichida (`bot/fonts/`), tizim paketi kerak emas.

### Railway / Render / Fly.io
Repo'ni ulang, `BOT_TOKEN` env o'zgaruvchisini qo'shing — Dockerfile avtomatik aniqlanadi.
Hech qanday port/webhook kerak emas (long polling).

## Rejimlar (pastki tugmalar)

| Tugma | Nima qiladi |
|---|---|
| **Кирилл → Özbek** | faqat kirill harflari o'giriladi, lotincha matn tegilmaydi |
| **Oʻzbek → Özbek** | faqat eski lotin (oʻ gʻ sh ch, apostroflar) o'giriladi, kirill tegilmaydi |
| **Avto (aralash)** | ikkalasi ham (standart) |

Rejim har bir chat uchun xotirada saqlanadi (bot qayta ishga tushsa — Avto).

## Transliteratsiya qoidalari (`bot/translit.py`)

Kirill → yangi lotin:
- `е` → so'z boshida, unlidan keyin, `ъ`/`ь` dan keyin **ye**, undoshdan keyin **e** (ер→yer, поезд→poyezd, съезд→syezd, тегирмон→tegirmon)
- `ц` → unli yoki `ь` dan keyin **ts**, boshqa joyda **s** (лицей→litsey, акция→aksiya, цирк→sirk)
- `ъ` → tutuq belgisi **ʼ** (U+02BC); `е` dan oldin tushib qoladi (маъно→maʼno, съезд→syezd)
- `ь` → tushadi; `о` dan oldin **y** (альбом→albom, бульон→bulyon)
- `ш ч ғ ў` → **ş ç ğ ö**; `ҳ`→h, `х`→x, `қ`→q, `ж`→j, `ё ю я`→yo yu ya

Eski lotin → yangi lotin:
- `oʻ gʻ` (har qanday apostrof: ʻ ʼ ' ‘ ’ `) → **ö ğ**
- `sh ch` → **ş ç**; `s'h` → **sh** (Is'hoq→Ishoq)
- `ma'no`, `ta’lim` → apostrof **ʼ** ga normallashtiriladi

Katta harf: `Ёш`→`Yoş`, `ЁШ`→`YOŞ`, `SHAHAR`→`ŞAHAR`.

Qo'shma/qisqartma so'zlar — `bot/compounds.json`: `е` undoshdan keyin kelsa-yu,
oldi `prefixes` bo'laklaridan tuzilgan va `е` dan boshlangan qism `e_roots` o'zaklaridan biri bo'lsa — **ye**
(Ўз+дав+ер+лойиҳа → Özdavyerloyiha, lekin кетди → ketdi). Yangi tashkilot bo'laklarini shu faylga qo'shing.

Istisno so'zlar — `bot/exceptions.json` (сентябрь→sentabr, октябрь→oktabr ...).
Yangi so'z qo'shish uchun shu faylga yozib, botni qayta ishga tushiring.

> Rasmiy yangi imlo qoidalari va transliteratsiya talabi hali e'lon qilinmagan
> (qonun 2026-yil 21-oktabrda qabul qilindi, 6 oydan keyin kuchga kiradi).
> Chiqqanda `translit.py` dagi 4–5 ta kontekst qoidasini moslash kifoya.

## /start statistikasi (Google Sheet)
`apps_script/Code.gs` ni sheet'ning Extensions → Apps Script'iga qo'ying, `SECRET` ni almashtiring,
Deploy → Web app (Execute as: Me, Access: Anyone). Vercel'ga `SHEETS_WEBHOOK_URL` (…/exec) va
`SHEETS_WEBHOOK_SECRET` qo'shing. Sheet o'zi private qoladi.

## Test
```bash
pip install pytest
python -m pytest tests -q
```

## Tuzilma
```
bot/translit.py     — alifbo qoidalari (char-by-char mapping, formatlashni saqlash uchun)
bot/documents.py    — txt / docx / pdf konvertorlar
bot/pdf_inplace.py  — PDF'ni joyida o'girish (PyMuPDF)
bot/main.py         — aiogram 3 bot (handlers + polling)
bot/storage.py      — rejim xotirasi (Upstash Redis yoki RAM)
api/index.py        — Vercel webhook kirish nuqtasi
bot/exceptions.json — istisno so'zlar lug'ati
bot/compounds.json  — qisqartma bo'laklari va е-o'zaklar
tests/              — 56 ta test
```
