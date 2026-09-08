# Bot Hosting Platform (Telegram bot orqali)

Foydalanuvchilar Python bilan yozilgan botlarini shu bot orqali deploy qila oladigan tizim.

## O'rnatish

```bash
pip install -r requirements.txt
```

## Kerakli ENV o'zgaruvchilari (Render → Environment)

| Nomi | Tavsif | Misol |
|---|---|---|
| `BOT_TOKEN` | Asosiy (manager) bot tokeni | `123456:AAExxx` |
| `ADMIN_IDS` | Vergul bilan admin telegram_id lar | `111111,222222` |
| `SUPERADMIN_IDS` | Vergul bilan superadmin telegram_id lar | `999999` |
| `STORAGE_GROUP_ID` | Kod fayllari backup qilinadigan guruh ID | `-1001234567890` |
| `MAX_BOTS_PER_USER` | (ixtiyoriy, default 3) | `3` |
| `BOT_MEMORY_LIMIT_MB` | (ixtiyoriy, default 2048) har bir botga ruxsat etilgan **virtual manzil maydoni** (haqiqiy RAM sarfi emas — Render'ning o'zi fizik RAM tugaganda process'ni baribir o'chirib tashlaydi). `opencv`(ffmpeg kodekli)/`numpy`/`pandas` kabi kutubxonalar ko'p .so faylni map qilgani uchun past qiymat (100-512) ularni `"failed to map segment from shared object"` bilan buzib qo'yishi mumkin. | `2048` |
| `BOT_CPU_TIME_LIMIT_SEC` | (ixtiyoriy, default 0=cheklanmagan) har bir bot uchun CPU vaqti chegarasi | `600` |
| `ENCRYPTION_KEY` | (tavsiya etiladi) bot tokenlari/ENV qiymatlarini bazada shifrlash uchun kalit. Generatsiya: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | `gAAAAA...` |

`STORAGE_GROUP_ID` — botni shu guruhga admin qilib qo'shing, u yerga foydalanuvchilarning
yuklagan .py/.zip fayllari backup sifatida yuboriladi (Render diski redeploy'da tozalanadi,
shu sababli fayllarning asl nusxasi Telegram'da saqlanadi).

## Ishga tushirish

```bash
python main.py
```

Bot **webhook** rejimida ishlaydi (aiohttp web server orqali), shuning uchun Render'da
**Web Service** turini tanlang — bu **bepul** (Background Worker pullik).

- Build Command: `pip install -r requirements.txt`
- Start Command: `python main.py`
- Render avtomatik `PORT` va `RENDER_EXTERNAL_URL` beradi — qo'shimcha sozlash shart emas.
- Free Web Service 15 daqiqa faolsizlikdan keyin uxlab qoladi — shuning uchun
  UptimeRobot (yoki shunga o'xshash) orqali `/` endpointga 5-10 daqiqada bir marta
  ping yuboring (bot shu route'ni health-check sifatida qaytaradi).

## Arxitektura qisqacha

- `main.py` — dispatcher va router'larni yig'adi
- `handlers/start.py` — /start, adminga birinchi murojaat
- `handlers/admin_review.py` — admin tasdiqlash/rad etish/javob berish
- `handlers/user_menu.py` — asosiy menu, "Mening botlarim" ro'yxati
- `handlers/add_bot.py` — kod yuklash → build → start → ENV → deploy oqimi
- `handlers/bot_actions.py` — start/stop/delete/info (kod, log, env)
- `handlers/admin_panel.py` — barcha botlar/foydalanuvchilar, xabar yozish
- `handlers/help_faq.py` — foydalanuvchilar uchun "❓ Yordam" / `/help` bo'limi (tez-tez so'raladigan savollar)

### Botni tahrirlash (kod/requirements/ENV) va crash-fix

Har qanday holatdagi bot (running/stopped/crashed) uchun **"🛠 Botni tahrirlash"**
tugmasi bor (`bot_manage` sahifasida, `edit_bot_menu_kb`):
- **📄 Kodni almashtirish** / **📋 requirements.txt** — foydalanuvchi o'zi yangi fayl yuboradi,
  eski fayl faqat yangisi muvaffaqiyatli qabul qilingandan SO'NG almashtiriladi (`handlers/bot_actions.py`,
  `FixCode`/`FixRequirements` state'lari). `.py.txt` kengaytmasi (fayl-menejer xatosi) avtomatik `.py`ga tuzatiladi.
- **🔑 ENV tahrirlash** — faqat botda ENV mavjud bo'lsagina ko'rsatiladi (mavjud kalitni yangilaydi, yangi qo'shmaydi).

- **✏️ Nomini o'zgartirish** — faqat `display_name` o'zgaradi, qayta build kerak emas.

O'zgarish saqlangach bot avtomatik qayta build qilinib ishga tushiriladi (`_rebuild_and_start`).
Agar bot tahrirlash paytida **ishlab turgan** bo'lsa, avval xavfsiz to'xtatiladi — aks holda
eski va yangi jarayon parallel ishlab, bitta Telegram token bilan konflikt yaratib qo'yishi mumkin edi.

Faqat **crashed** holatda qo'shimcha ravishda:
- **🔁 Qayta build qilib urinish** — oddiy "Ishga tushirish"dan farqli, build bosqichini ham qayta bajaradi.
- **🤖 AI yordam** (Stars bilan to'lanadi, narxi superadmin panelidan sozlanadi, default 5⭐) — log va kod
  parchasini `services/ai_client.py` orqali sozlangan AI provayderlardan biriga yuborib, oddiy tilda
  tashxis va tuzatish tavsiyasi oladi.

Superadmin **"🤖 AI provayderlar"** bo'limidan istalgan sondagi OpenAI-compatible endpoint
(Cloudflare Workers AI, OpenRouter va h.k.) qo'sha oladi — har birining o'z kunlik so'rov
limiti va ustuvorlik darajasi bor. Bitta provayder kunlik limitga yetsa, tizim avtomatik
keyingi (ustuvorligi pastroq) provayderga o'tadi — bitta AI hisobining byudjeti butun
platformani to'xtatib qo'ymasligi uchun.

### Bulk start/stop va superadmin dashboard

"🤖 Mening botlarim" ro'yxatida, agar mos harakat ma'noli bo'lsa (kamida bitta running/
stopped bot bo'lsa), qo'shimcha **"🔴 Hammasini to'xtatish"** / **"🟢 Hammasini ishga
tushirish"** tugmalari ko'rinadi (`my_bots_list_kb`). Bular bitta-bitta boshqarish
tugmalarini (mavjud, o'zgarishsiz) ALMASHTIRMAYDI — shunchaki qo'shimcha tezkor variant.
Har bir bot uchun natija (muvaffaqiyatli/muvaffaqiyatsiz) alohida ko'rsatiladi.

Superadmin panelida **"📊 Umumiy statistika"** bo'limi bor (`db.get_dashboard_stats()`):
foydalanuvchilar (jami/tasdiqlangan/kutayotgan/ban), botlar (jami/ishlayotgan/qulagan),
bugungi va oxirgi 7 kunlik deploy soni, umr bo'yi to'langan Stars, bugungi AI so'rovlari
soni, va real vaqtda hisoblangan eng ko'p RAM yeyotgan top-5 bot.

### Foydalanuvchi qidirish (ID yoki harflab)

Admin panelidagi "👥 Foydalanuvchilar" bo'limida "🔍 Qidirish" ikkita usulni taklif qiladi:
- **🔢 ID orqali** — mavjud oqim, Telegram ID yoki to'liq `@username` matn sifatida yuboriladi.
- **🔤 Harflab qidirish** — inline QWERTY klaviatura (`admin_search_qwerty_kb`): admin
  harflarni bittalab bosadi, har bosishda so'rov o'sadi va mos foydalanuvchilar (username
  yoki ism boshi bo'yicha, `db.search_users_by_prefix`) darhol yuqorida ko'rinadi — matn
  kiritishga umuman hojat yo'q, bu ayniqsa mobil qurilmada qulay.

- `services/deploy_manager.py` — subprocess orqali botlarni ishga tushirish, RAM limiti
- `services/file_utils.py` — zip/py fayllarni aniqlash va joylashtirish
- `database.py` — SQLite (users, bots, bot_envs)

## Python versiyasi

Repo ildizidagi `.python-version` fayli Render'ga qaysi Python versiyasini ishlatishni
aytadi (hozir `3.12`). Bu **muhim**: Render default'da eng yangi Python'ni (masalan 3.14)
ishlatishi mumkin, lekin ko'plab mashhur kutubxonalar (masalan `python-telegram-bot`)
yangi Python versiyasi chiqqanda hali ulgurmagan bo'lishi mumkin — natijada
`RuntimeError: There is no current event loop` kabi xatolar chiqadi. `3.12` — keng
qo'llab-quvvatlanadigan barqaror versiya.

`.python-version` faylini birinchi marta qo'shgandan/o'zgartirgandan keyin Render'da
**albatta build cache'ni tozalab qayta deploy qiling** ("Manual Deploy" → "Clear build
cache & deploy"), aks holda eski Python muhiti qolib ketishi mumkin.

## Ma'lumotlar bazasi backup (avtomatik)

Render diski ephemeral bo'lgani uchun `platform.db` (users, bots, envs) har safar
muhim o'zgarish bo'lganda (user tasdiqlanganda/rad etilganda, bot deploy/start/stop/
o'chirilganda) `STORAGE_GROUP_ID` guruhiga document sifatida yuboriladi va **pin**
qilinadi. Service qayta ishga tushganda (`on_startup`), eng so'nggi pin qilingan
backup avtomatik yuklab olinib, `platform.db` shu asosda tiklanadi — shuning uchun
redeploy/restart'da foydalanuvchilar va deploy qilingan botlar ro'yxati **yo'qolmaydi**.

Kod: `services/backup.py` (`backup_database()` / `restore_database()`).

## MUHIM: xavfsizlik chegaralari


Bu tizim foydalanuvchi kodini **subprocess + RLIMIT** orqali ishga tushiradi — bu
**to'liq sandbox emas**. Fayl tizimi va tashqi tarmoqqa kirish cheklanmagan. Bu daraja
faqat kichik va nazorat qilinadigan miqyos uchun (masalan, `MAX_BOTS_PER_USER=3`,
foydalanuvchilar admin tomonidan qo'lda tasdiqlanadi) oqilona murosa hisoblanadi.

Agar keyinchalik foydalanuvchilar soni yoki ishonchsiz kod ulushi oshsa, quyidagilarni
ko'rib chiqing:
- Docker container'lar (`--memory`, `--cpus`, `--network=none` yoki maxsus tarmoq)
- Fly.io Firecracker micro-VM'lar
- Kodni ishga tushirishdan oldin kengroq statik tahlil (masalan, `bandit` python uchun)

`services/deploy_manager.py` ichidagi `static_scan()` funksiyasi faqat bir nechta aniq
xavfli patternni (`os.system`, `rm -rf` va h.k.) belgilaydi — bu blocklist emas, faqat
ogohlantirish. Har doim yuklangan kodni imkon qadar ko'rib chiqing.

## Testlar

```bash
pip install -r requirements.txt --break-system-packages
pip install pytest pytest-asyncio --break-system-packages
pytest tests/ -v
```

`pytest-asyncio` faqat testlar uchun kerak (production `requirements.txt`da yo'q) —
`services/ai_client.py`dagi async fallback logikasini sinovdan o'tkazish uchun ishlatiladi.
