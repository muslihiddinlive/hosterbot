# 🤖 PythonHosterRobot — Telegram orqali boshqariladigan Python Bot Hosting platformasi

Demo: [@PythonHosterRobot](https://t.me/PythonHosterRobot)

**PythonHosterRobot** — bu foydalanuvchilarga o'z Python botlarini (Telegram bot, Flask/aiohttp servis, oddiy skript — istalgan turdagi) hech qanday terminal, SSH yoki hosting-panel bilan ishlashga hojat qoldirmasdan, **faqat Telegram interfeysi orqali** deploy qilish, boshqarish, monitoring qilish va zarur bo'lsa AI yordamida tuzatish imkonini beruvchi to'liq mini-PaaS (Platform-as-a-Service) tizimi.

Server sifatida istalgan **doimiy jarayon (persistent process) ishga tushira oladigan** hosting mos keladi — masalan Oracle Cloud Always Free VM, VPS, Railway, Render va shunga o'xshashlar. Yagona shart: tashqi HTTPS manzil (webhook uchun) va Python 3.12 muhiti.

---

## 📋 Mundarija

- [Asosiy imkoniyatlar](#-asosiy-imkoniyatlar)
- [Foydalanuvchi funksiyalari](#-foydalanuvchi-funksiyalari)
- [Bot deploy qilish](#-bot-deploy-qilish)
- [Bot boshqaruvi](#-bot-boshqaruvi)
- [AI yordamchi](#-ai-yordamchi)
- [Telegram Stars orqali monetizatsiya](#-telegram-stars-orqali-monetizatsiya)
- [Admin panel](#-admin-panel)
- [Ma'lumotlar saqlanishi va backup](#-malumotlar-saqlanishi-va-backup)
- [Xavfsizlik](#-xavfsizlik)
- [O'rnatish](#-ornatish)
- [Kerakli ENV o'zgaruvchilari](#-kerakli-env-ozgaruvchilari)
- [Arxitektura](#-arxitektura)
- [Testlar](#-testlar)

---

## ✨ Asosiy imkoniyatlar

- 📤 **Ikki xil deploy usuli**: `.py`/`.zip` fayl yuklash YOKI ochiq (public) GitHub repo linki
- 🔁 **GitHub'dan avtomatik qayta deploy** — `git push` qilinganda bot o'zi qayta build bo'lib qayta ishga tushadi (webhook orqali, OAuth talab qilinmaydi)
- 🌐 **Webhook proxy** — bot o'z ichida webhook-server (Flask/aiohttp) kodiga ega bo'lsa ham, platformaning umumiy porti orqali ishlay oladi
- 🛠 **To'liq tahrirlash**: kod, `requirements.txt`, ENV o'zgaruvchilar, nomi — hech biri qayta deploydan boshlashni talab qilmaydi
- 🩺 **Crash-diagnostika**: qulagan botning logi, kodi va requirements'i asosida oddiy tilda tashxis (AI yordamida)
- 💬 **Erkin AI suhbat** — bot kodini o'qib savolga javob beradi, xohlasa function-calling orqali kodni to'g'ridan-to'g'ri tuzatishni ham taklif qiladi (foydalanuvchi tasdiqlagandan keyingina qo'llanadi)
- 💳 **Telegram Stars monetizatsiyasi** — admin tasdig'isiz, o'z-o'ziga xizmat ko'rsatuvchi (self-service) hosting
- 💾 **Har bir botning shaxsiy "diski"** — botning o'zi runtime'da yaratadigan fayllar (baza, keshlar, media) alohida backup qilinadi, server qayta ishga tushsa ham yo'qolmaydi
- 📊 **To'liq admin panel** — foydalanuvchilar, botlar, statistika, xabar yuborish, moliyaviy nazorat
- 🔐 **Shifrlangan maxfiy ma'lumotlar** — bot tokenlari va ENV qiymatlari bazada shifrlangan holda saqlanadi
- 🌍 **Ko'p tilli tayyor infratuzilma** — matnlar markazlashtirilgan, boshqa tillarga tarjima qilish oson

---

## 👤 Foydalanuvchi funksiyalari

### Ro'yxatdan o'tish va admin tasdig'i
- `/start` — birinchi murojaatda so'rov adminlarga yuboriladi, admin **tasdiqlash/rad etish** qaror qiladi
- Tasdiqlanganda foydalanuvchiga **individual bot limiti** (nechta bot deploy qila olishi) beriladi
- **📩 Adminga habar berish** — istalgan vaqt matn, rasm, video, GIF, ovozli xabar, sticker yoki fayl yuborib to'g'ridan-to'g'ri admin bilan bog'lanish; admin javobi ham xuddi shunday formatda (media saqlanib) qaytadi
- **⛔️ Bekor qilish** — istalgan qadamda, istalgan jarayonni (kod yuklash, ENV kiritish va h.k.) darhol to'xtatish uchun universal tugma

### Yordam bo'limi
- **❓ Yordam** tugmasi yoki `/help` buyrug'i — tez-tez so'raladigan savollarga (deploy qanday qilinadi, ENV qayerga kiritiladi, bot qulasa nima qilish kerak, limit qancha, Stars qanday ishlaydi, resurs cheklovlari) tayyor javoblar — adminga bir xil savollar bilan murojaat kamayadi

---

## 🚀 Bot deploy qilish

### 1) Fayl yuklash orqali
1. **➕ Bot qo'shish** → `.py` fayl yoki `.zip` arxiv yuborish
2. Kerak bo'lsa `requirements.txt` yuborish (yo'q bo'lsa o'tkazib yuborish mumkin)
3. Build buyrug'i — **avtomatik** aniqlash (tavsiya) yoki qo'lda kiritish
4. Ishga tushirish buyrug'i (`start_cmd`) — qaysi faylni ishga tushirish kerakligi
5. ENV o'zgaruvchilar (`KEY=VALUE`) — istalgancha qo'shish mumkin, hammasi **shifrlab** saqlanadi
6. **✅ Tugatish va deploy qilish** — bot avtomatik build qilinib, ishga tushiriladi

### 2) GitHub repo orqali
- **🐙 GitHub repo orqali deploy qilish** — foydalanuvchi ochiq (public) repo linkini yuboradi (masalan `https://github.com/owner/repo`), **hech qanday token talab qilinmaydi**
- Repo `codeload.github.com` orqali ZIP sifatida yuklab olinadi (avval `main`, keyin `master` branch sinaladi)
- Keyingi barcha qadamlar (requirements, ENV va h.k.) fayl-yuklash oqimi bilan bir xil

### GitHub'dan avtomatik qayta deploy (CI/CD'ga o'xshash)
- Har bir GitHub orqali deploy qilingan bot uchun noyob `webhook_secret` generatsiya qilinadi
- Foydalanuvchiga webhook manzili beriladi: `{WEBHOOK_BASE_URL}/gh-webhook/{bot_id}/{webhook_secret}`
- Bu manzil GitHub repo → **Settings → Webhooks → Add webhook**ga qo'lda qo'shiladi (Content type: `application/json`, faqat `push` event)
- Endi har safar `git push` qilinganda, bot **avtomatik qayta yuklanadi, qayta build bo'ladi va qayta ishga tushadi** — Render/Vercel kabi platformalarning auto-deploy funksiyasiga o'xshash, lekin **OAuth talab qilmasdan**, oddiy webhook orqali
- Xavfsizlik: `bot_id`+`secret` mos kelmasa **404** qaytadi (bot mavjudligi tashqariga sizib chiqmasligi uchun), GitHub'ning test `ping` so'rovi alohida ushlanadi, `push`dan boshqa event turlari e'tiborsiz qoldiriladi

### Webhook proxy (o'z webhook-serveriga ega botlar uchun)
Agar deploy qilinayotgan bot o'zining ichida webhook-server kodiga ega bo'lsa (masalan Flask yoki aiohttp bilan yozilgan), **🌐 Webhook proxy** yoqilganda bu botga platformaning umumiy tashqi manzili va porti avtomatik beriladi — bot o'z servis manzilidan chinakam webhook rejimida foydalana oladi, alohida domen/port ochish shart emas.

---

## 🎛 Bot boshqaruvi

Har bir deploy qilingan bot uchun **"🤖 Mening botlarim"** bo'limida:

| Funksiya | Tavsif |
|---|---|
| ▶️ Ishga tushirish / ⏹ To'xtatish | Bir bosishda boshqarish |
| 🔁 Qayta build qilib urinish | Faqat **crashed** holatda — build bosqichini ham qayta bajaradi |
| 🗑 O'chirish | Tasdiqlash bilan, butunlay o'chiradi |
| ℹ️ Bot haqida | Kod, oxirgi log, ENV ro'yxatini ko'rish |
| 📡 Jonli log | Real vaqtda ishlab turgan bot logini kuzatish |
| 💾 Resurs | Botning joriy RAM sarfi |
| ✏️ Nomini o'zgartirish | Faqat ko'rsatiladigan nom o'zgaradi, qayta build kerak emas |
| 🔄 Egasini almashtirish (transfer) | Botni boshqa foydalanuvchiga topshirish, ikki tomonlama tasdiqlash bilan |
| 🔴 Hammasini to'xtatish / 🟢 Hammasini ishga tushirish | Ro'yxatdagi barcha botlarni bir amal bilan boshqarish, har biri uchun natija alohida ko'rsatiladi |

### 🛠 Botni tahrirlash va crash-fix
Har qanday holatdagi (running/stopped/crashed) bot uchun:
- **📄 Kodni almashtirish** — yangi fayl yoki yangi GitHub link yuborish; eski kod faqat yangisi **muvaffaqiyatli qabul qilingandan keyin** almashtiriladi
- **📋 requirements.txt almashtirish**
- **🔑 ENV tahrirlash** — mavjud kalitni yangilaydi (yangi ENV kalit qo'shish emas)

O'zgarish saqlangach bot **avtomatik qayta build qilinib** ishga tushiriladi. Agar bot tahrirlash paytida ishlab turgan bo'lsa, avval xavfsiz to'xtatiladi (bitta token bilan ikki jarayon parallel ishlab ketmasligi uchun).

---

## 🤖 AI yordamchi

### Crash-diagnostika (🤖 AI yordam)
Bot **crashed** holatda bo'lsa, AI'ga uchta manba beriladi: oxirgi log, asosiy `.py` fayl, `requirements.txt`. AI oddiy tilda tashxis va tuzatish tavsiyasini beradi — va **hech qachon** "terminalni oching", "pip install qiling" kabi foydalanuvchi bajara olmaydigan maslahat bermaydi (chunki foydalanuvchining faqat Telegram orqali kirish huquqi bor), buning o'rniga botdagi haqiqiy tugmalarni ("requirements.txt almashtirish", "Kodni almashtirish" va h.k.) tavsiya qiladi. AI shuningdek `requirements.txt`ni log bilan solishtirib, kutubxona **allaqachon bor bo'lsa**, uni noto'g'ri "yo'q" deb tashxis qo'ymaslik uchun tekshiradi.

### Erkin AI suhbat
Foydalanuvchi hech qanday menyu/buyruq bilan mos kelmagan matn yozsa, bot "AI'ga yozyapsizmi?" deb so'raydi. Tasdiqlansa:
- AI bot kodi va `requirements.txt` asosida savollarga javob beradi
- Agar aniq tuzatish kerak bo'lsa, AI **function-calling** orqali taklif kiritadi — lekin foydalanuvchi **alohida tasdiqlamaguncha** ("✅ Ha, tuzat") fayl **hech qachon** o'zgartirilmaydi. Bu qoida system-prompt darajasida ham mustahkamlangan.
- Chiqish uchun istalgan vaqt umumiy "⛔️ Bekor qilish" tugmasi ishlaydi

### Ko'p provayderli AI infratuzilmasi
Superadmin istalgan sondagi OpenAI-compatible endpoint qo'sha oladi (masalan turli ochiq AI xizmatlari) — har birining o'z kunlik so'rov limiti va ustuvorlik darajasi bor. Bitta provayder kunlik limitga yetsa, tizim avtomatik keyingi ustuvorlikdagi provayderga o'tadi — bitta hisobning cheklovi butun platformani to'xtatib qo'ymaydi.

### VIP (bepul AI)
Admin/superadmin uchun barcha AI funksiyalari (**crash-tashxis, erkin suhbat, tuzatish qo'llash**) — **bepul**: balans tekshiruvi umuman chaqirilmaydi, tugmalarda narx o'rniga "bepul" ko'rsatiladi.

---

## 💳 Telegram Stars orqali monetizatsiya

- **💳 Hisob** bo'limi — Stars bilan to'ldirish, tarix ko'rish
- **Self-service hosting** — admin tasdig'ini kutmasdan, foydalanuvchi Stars to'lab o'zi darhol bot host qila oladi (masalan: har 3⭐ = 24 soat hosting, sozlanadi)
- Muddat tugaganda bot **avtomatik to'xtaydi**, "💳 Hisob" orqali istalgan vaqt uzaytiriladi
- **AI xizmatlar narxlash** — crash-diagnostika va erkin suhbat har biri alohida narxlanadi (superadmin panelidan sozlanadi, masalan default 1⭐/savol, 5⭐/tashxis)
- Admin/superadmin uchun **real balans**, **o'ziga sovg'a/withdraw** funksiyalari

---

## 🛡 Admin panel

### Foydalanuvchilar
- Yangi so'rovlarni **tasdiqlash/rad etish**, individual limit va muddat belgilash
- **🔍 Qidirish**: ID/username bo'yicha, yoki **harflab qidirish** — inline QWERTY klaviatura orqali, matn kiritmasdan, har bosishda natija yangilanadi (mobil qurilmada juda qulay)
- To'liq foydalanuvchi profili: botlar holati, tili, deploy sanasi, joriy RAM, Stars muddati, limit holati
- Foydalanuvchini **bloklash** — botlarini xavfsiz to'xtatadi, vaqtinchalik yoki doimiy, audit xabari adminlarga yuboriladi

### Botlar va statistika
- **📊 Umumiy statistika**: foydalanuvchilar (jami/tasdiqlangan/kutayotgan/bloklangan), botlar (jami/ishlayotgan/qulagan), bugungi va so'nggi 7 kunlik deploy soni, umr bo'yi to'langan Stars, bugungi AI so'rovlari, eng ko'p RAM yeyotgan top-5 bot (real vaqtda)

### Muloqot
- **Broadcast** — barcha foydalanuvchilarga matn yoki media (rasm/video/GIF) bir vaqtda yuborish, oldindan ko'rish bilan
- Admin nomidan yoki shaxsiy nomidan xabar yuborish tanlovi

### Sozlamalar
- Stars narxi/muddati, minimal withdraw miqdori, blok muddati, AI narxlari, Data Storage guruhi — barchasi botdan chiqmasdan sozlanadi

---

## 💾 Ma'lumotlar saqlanishi va backup

- **Asosiy baza** (foydalanuvchilar, botlar, ENV) xotirada ishlaydi va har muhim o'zgarishdan keyin (tasdiqlash, deploy, start/stop, to'lov) Telegram guruhga **document sifatida backup va pin** qilinadi. Server qayta ishga tushganda eng so'nggi pin qilingan backup avtomatik tiklanadi — **hech qanday foydalanuvchi yoki bot ma'lumoti yo'qolmaydi**, hatto serverning diski har safar tozalanadigan (ephemeral) bo'lsa ham
- **Har bir bot uchun alohida "disk"** — botning o'zi runtime'da yaratgan har qanday fayl (baza, JSON/txt, yuklab olingan media) davriy ravishda zip qilinib backup qilinadi. Agar Data Storage guruhi Telegram "Topics" rejimida bo'lsa, har bot uchun **alohida mavzu (topic)** avtomatik ochiladi — barcha backup tartibli joylashadi
- Agar eng so'nggi backup yaroqsiz chiqsa, foydalanuvchi **bir qadam oldingi** snapshotni qo'lda olishi mumkin

---

## 🔒 Xavfsizlik

- Bot tokenlari va ENV qiymatlari bazada **shifrlangan** holda saqlanadi (`cryptography.Fernet`)
- Har bir hosted bot uchun **resurs monitoring** — jismoniy RAM byudjeti oldindan hisoblanadi, yangi bot shu byudjetdan oshsa oldindan rad etiladi (bitta og'ir bot boshqalarni ag'darib yubormasligi uchun)
- **Ogohlantirish**: foydalanuvchi kodi subprocess + RLIMIT orqali ishga tushiriladi — bu **to'liq izolyatsiya (sandbox) emas**. Fayl tizimi va tashqi tarmoqqa kirish cheklanmagan. Bu daraja faqat kichik va nazorat qilinadigan miqyos (qo'lda tasdiqlangan foydalanuvchilar, cheklangan bot soni) uchun oqilona murosa hisoblanadi
- Ishonchsiz kod ulushi yoki foydalanuvchilar soni oshsa, quyidagilar tavsiya etiladi: Docker konteynerlar (`--memory`, `--cpus`, tarmoq izolyatsiyasi), Firecracker micro-VM'lar, yoki ishga tushirishdan oldin statik xavfsizlik tahlili (masalan `bandit`)
- Kod ichida oddiy xavfli patternlarni (`os.system`, `rm -rf` va h.k.) belgilaydigan statik tekshiruv bor — bu **blocklist emas**, faqat ogohlantirish; yuklangan kodni har doim imkon qadar ko'rib chiqish tavsiya etiladi

---

## ⚙️ O'rnatish

```bash
pip install -r requirements.txt
```

Bot **webhook** rejimida ishlaydi (aiohttp web-server orqali) — shuning uchun serverga qo'yishda **doimiy ishlaydigan (persistent) jarayon** sifatida ishga tushirish kerak (serverless funksiyalar mos emas):

```bash
python main.py
```

- Build buyrug'i: `pip install -r requirements.txt`
- Start buyrug'i: `python main.py`
- Server tashqariga chiqadigan **HTTPS manzil** va `PORT` o'zgaruvchisini ta'minlashi kerak
- Agar hosting-provideringiz uzoq faolsizlikdan keyin jarayonni to'xtatib qo'yadigan bo'lsa (ba'zi bepul PaaS'larda uchraydi), UptimeRobot kabi xizmat orqali `/` endpointga davriy ping yuborish tavsiya etiladi (bot shu route'ni health-check sifatida qaytaradi) — Oracle Cloud yoki o'z VPS'ingiz kabi doimiy ishlaydigan serverlarda bu qadam **shart emas**

### Python versiyasi
Repo ildizidagi `.python-version` fayli qaysi Python versiyasi ishlatilishini bildiradi (hozir `3.12`) — bu muhim, chunki ba'zi kutubxonalar (masalan `aiogram`) eng yangi Python versiyasiga hali moslashmagan bo'lishi mumkin. `.python-version` faylini birinchi marta qo'shgan/o'zgartirgandan keyin build keshini tozalab qayta deploy qilish tavsiya etiladi.

---

## 🔧 Kerakli ENV o'zgaruvchilari

| Nomi | Tavsif | Misol |
|---|---|---|
| `BOT_TOKEN` | Asosiy (manager) bot tokeni | `123456:AAExxx` |
| `ADMIN_IDS` | Vergul bilan admin telegram_id'lar | `111111,222222` |
| `SUPERADMIN_IDS` | Vergul bilan superadmin telegram_id'lar | `999999` |
| `STORAGE_GROUP_ID` | Kod fayllari va baza backup qilinadigan guruh ID | `-1001234567890` |
| `WEBHOOK_BASE_URL` | Serveringizning tashqi HTTPS manzili (agar hosting avtomatik bermasa) | `https://sizning-domen.com` |
| `PORT` | Server tinglaydigan port (odatda hosting o'zi beradi) | `10000` |
| `MAX_BOTS_PER_USER` | (ixtiyoriy, default 3) foydalanuvchi boshiga bot limiti | `3` |
| `BOT_MEMORY_LIMIT_MB` | (ixtiyoriy, default 2048) har bot uchun ruxsat etilgan **virtual** manzil maydoni (haqiqiy RAM emas) | `2048` |
| `BOT_CPU_TIME_LIMIT_SEC` | (ixtiyoriy, default 0=cheklanmagan) har bot uchun CPU vaqt chegarasi | `600` |
| `TOTAL_RAM_BUDGET_MB` | (ixtiyoriy, default 420) platforma + barcha botlar uchun umumiy **haqiqiy** RAM byudjeti — serveringiz jismoniy RAM hajmiga qarab sozlang | `420` |
| `ENCRYPTION_KEY` | (tavsiya etiladi) tokenlar/ENV'ni bazada shifrlash kaliti. Generatsiya: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | `gAAAAA...` |
| `STARS_PER_UNIT` / `SECONDS_PER_UNIT` | Self-service hosting narxi (default: 3⭐ = 24 soat) | `3` / `86400` |
| `MIN_WITHDRAW_STARS` | Minimal withdraw miqdori | `15` |

> `STORAGE_GROUP_ID` — botni shu guruhga admin qilib qo'shing, u yerga foydalanuvchilarning yuklagan fayllari va baza backup'lari yuboriladi.

---

## 🏗 Arxitektura

```
main.py                    — dispatcher, router'lar, webhook server, GitHub webhook handler
config.py                  — barcha sozlamalar ENV orqali
database.py                — SQLite (xotirada), users/bots/bot_envs/stars_ledger/ai_providers
keyboards.py                — barcha inline/reply klaviaturalar
states.py                   — FSM holatlari

handlers/
 ├─ start.py               — /start, admin tasdig'i, adminga murojaat
 ├─ user_menu.py           — asosiy menyu, "Mening botlarim" ro'yxati, bulk start/stop
 ├─ add_bot.py             — fayl/GitHub yuklash → build → ENV → deploy oqimi
 ├─ github_deploy.py       — GitHub public repo orqali deploy
 ├─ bot_actions.py         — start/stop/delete/info/log/resurs/transfer/tahrirlash/crash-fix
 ├─ admin_review.py        — so'rov tasdiqlash/rad etish/javob
 ├─ admin_panel.py         — foydalanuvchilar, statistika, broadcast, sozlamalar
 ├─ ai_chat.py             — erkin AI suhbat va AI orqali tuzatish
 ├─ stars.py               — to'lov, balans, uzaytirish, tarix
 └─ help_faq.py            — /help, FAQ

services/
 ├─ deploy_manager.py      — subprocess orqali ishga tushirish, RAM/CPU limitlari
 ├─ github_deploy.py       — repo ZIP yuklab olish
 ├─ file_utils.py          — zip/py fayllarni aniqlash va joylashtirish
 ├─ ai_client.py           — AI provayderlar bilan ishlash, function-calling
 ├─ resource_monitor.py    — jami RAM byudjetini kuzatish (OOM oldini olish)
 ├─ backup.py              — asosiy baza backup/restore (Telegram orqali)
 ├─ data_backup.py         — har bot uchun alohida ma'lumot backup'i
 ├─ crypto_utils.py        — tokenlarni shifrlash/deshifrlash
 └─ deploy_log.py          — deploy jarayoni loglari
```

---

## 🧪 Testlar

```bash
pip install -r requirements.txt --break-system-packages
pip install pytest pytest-asyncio --break-system-packages
pytest tests/ -v
```

`pytest-asyncio` faqat testlar uchun kerak (production `requirements.txt`da yo'q) — `services/ai_client.py`dagi async fallback logikasini sinash uchun ishlatiladi.

---

## 📄 Litsenziya

Loyiha egasi tomonidan belgilanadi.
