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
- **🤖 AI yordam** (Stars bilan to'lanadi, narxi superadmin panelidan sozlanadi, default 5⭐) — AI'ga
  uchta manba birdan beriladi: oxirgi **log**, asosiy **.py fayl** (`start_cmd`da ko'rsatilgan) va
  **requirements.txt** tarkibi (agar bo'lsa). Shu asosda oddiy tilda tashxis va tuzatish tavsiyasi oladi.

**MUHIM**: `build_crash_diagnosis_prompt()`dagi system prompt AI'ga aniq aytadiki, foydalanuvchi
FAQAT Telegram orqali ishlaydi — hech qanday terminal/kompyuter kirish huquqi yo'q. Shu sabab AI
"pip install qiling", "terminalni oching" kabi bajarib bo'lmaydigan maslahatlar bermaydi, faqat
haqiqiy botdagi tugmalarni ("📋 requirements.txt almashtirish", "📄 Kodni almashtirish", "🔑 ENV
tahrirlash") tavsiya qiladi. Shuningdek, prompt AI'ga requirements.txt'ni log bilan solishtirib
tekshirishni buyuradi — agar "ModuleNotFoundError" chiqsa-yu, o'sha kutubxona requirements.txt'da
ALLAQACHON bor bo'lsa, AI buni "kutubxona yo'q" deb noto'g'ri tashxis qo'ymasligi kerak (masalan
nom xato yozilgan yoki versiya nomuvofiqligi bo'lishi mumkin).

Superadmin **"🤖 AI provayderlar"** bo'limidan istalgan sondagi OpenAI-compatible endpoint
(Cloudflare Workers AI, OpenRouter va h.k.) qo'sha oladi — har birining o'z kunlik so'rov
limiti va ustuvorlik darajasi bor. Bitta provayder kunlik limitga yetsa, tizim avtomatik
keyingi (ustuvorligi pastroq) provayderga o'tadi — bitta AI hisobining byudjeti butun
platformani to'xtatib qo'ymasligi uchun.

**VIP (admin/superadmin) — AI bepul**: agar AI'ni ishlatayotgan odam (`is_admin()`)
bo'lsa — crash-tashxis (`cb_ai_help`), erkin suhbat (`handle_ai_chat_message`), va AI
tahrirlashni qo'llash (`cb_ai_chat_apply_edit`) uchun narx `0` bo'ladi: balans
tekshiruvi/yechish/backup umuman chaqirilmaydi, tugma matnlarida narx o'rniga "bepul"
ko'rsatiladi. **DIQQAT**: qaysi `bot_row`/`message`/`callback` orqali kim VIP ekanligi
aniqlanishini tekshirish kerak — `_rebuild_and_start` kabi bir nechta joydan (jumladan
`ai_chat.py`dan `callback.message` orqali) chaqiriladigan funksiyalarda `message.from_user.id`
noto'g'ri bo'lishi mumkin (botning o'zi bo'lib chiqishi mumkin), shu sabab u yerda
`bot_row["owner_id"]` orqali tekshiriladi, boshqa joylarda esa to'g'ridan-to'g'ri
so'rovchi (`callback.from_user.id` / `message.from_user.id`) tekshiriladi.

### GitHub repo orqali deploy va avtomatik qayta deploy (webhook)

"➕ Bot qo'shish" bosqichida fayl yuklashdan tashqari **"🐙 GitHub repo orqali
deploy qilish"** varianti ham bor (`handlers/github_deploy.py`). Foydalanuvchi
**public** repo linkini yuboradi (masalan `https://github.com/owner/repo`) —
token talab qilinmaydi. Bot `services/github_deploy.py: download_repo_zip()`
orqali repo'ni `codeload.github.com` orqali ZIP sifatida yuklab oladi (avval
`main`, keyin `master` branch sinaladi, aniq branch berilmasa), so'ng
`handlers/add_bot.py: _process_downloaded_code()` — fayl-yuklash oqimi bilan
BIR XIL funksiya — orqali davom etadi (kod ikki marta yozilmasin uchun).

**Avtomatik qayta deploy (Render'ning auto-deploy'iga o'xshab, lekin OAuth'siz,
oddiy GitHub webhook orqali)**: har bir GitHub orqali deploy qilingan bot uchun
noyob `webhook_secret` generatsiya qilinadi (`database.py`: `bots.webhook_secret`
ustuni). Deploy tugagach, foydalanuvchiga webhook URL ko'rsatiladi:
`{WEBHOOK_BASE_URL}/gh-webhook/{bot_id}/{webhook_secret}` — buni GitHub repo
**Settings → Webhooks → Add webhook**ga qo'lda qo'shishi kerak (Content type:
`application/json`, faqat `push` event).

`main.py`: `github_webhook_handler` — `POST /gh-webhook/{bot_id}/{secret}`:
- `bot_id`+`secret` mos kelmasa **404** (bot mavjudligi sizib chiqmasin uchun,
  403 emas)
- GitHub `ping` event'ini alohida ushlab, deploy qilmasdan `200 pong` qaytaradi
  (GitHub webhook birinchi qo'shilganda avtomatik test so'rov yuboradi)
- `push` bo'lmagan boshqa event turlarini e'tiborsiz qoldiradi (`200 ignored`)
- Repo'ni qayta yuklab, eski kod papkasini yangisi bilan almashtiradi, so'ng
  `handlers/bot_actions.py: _rebuild_and_start()` — qayta build+start uchun
  ISHLAB TURGAN botlarda ham ishlatiladigan xuddi shu funksiya — orqali
  qayta ishga tushiradi. Bot egasiga Telegram orqali natija haqida xabar
  beriladi (`_WebhookNotifyMessage` — `_rebuild_and_start`ning `.answer()`
  chaqiruvini `bot.send_message()`ga proksi qiluvchi minimal wrapper, chunki
  webhook HTTP kontekstida haqiqiy Telegram `Message` obyekti yo'q).

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

Foydalanuvchi ustiga bosilganda (ro'yxatdanmi, ID qidiruvidanmi, QWERTY qidiruvidanmi —
farqi yo'q, hammasi bir xil `_build_user_view` funksiyasini chaqiradi) to'liq profil
ko'rsatiladi: har bir botning holati, tili, deploy sanasi, joriy RAM sarfi (ishlab
turgan bo'lsa), Stars orqali hostlangan bo'lsa muddat, va limit qatori ("2/5 bot" —
individual yoki global limit ekanligi bilan).

Bot sarlavhasi (agar botning o'z Telegram username'i bo'lsa) `services.deploy_manager.bot_link_html()`
orqali `t.me/username` ga bosiladigan link qilib ko'rsatiladi — admin/superadmin uchun
(bot boshqarish, "Bot haqida", foydalanuvchi profili) HAM, botning egasi uchun ("Mening
botlarim" ichida bot boshqarish sahifasi) HAM bir xil ishlaydi. `<pre>` bloklari ichida
(masalan crash log xabarlari, `format_log_block`) link ishlatilmaydi — Telegram u yerda
`<a>` teglarni render qilmaydi, faqat oddiy matn ko'rsatadi.

### Erkin AI suhbat (fallback) va AI orqali tahrirlash

`handlers/ai_chat.py` — foydalanuvchi hech qanday menyu tugmasi/buyruq bilan mos
kelmaydigan erkin matn yozganda ("hech qanday state'da bo'lmasa"), bot "🤖 AI'ga
yozyapsizmi?" deb so'raydi (Ha/Yo'q). **MUHIM**: bu router `main.py`da ENG OXIRIDA
ro'yxatdan o'tadi — aks holda barcha boshqa reply-tugma va state handler'larini
"yutib" qo'yardi.

"Ha" bosilsa (kerak bo'lsa bot tanlanadi, agar bir nechtasi bo'lsa) — erkin AI
suhbat rejimi boshlanadi (`AIChat.chatting` state):
- Har bir savol **1⭐️** turadi (default, `db.get_ai_chat_price_stars()`, superadmin
  panelidan sozlanadi) — AI kod va requirements.txt ni o'qib tushuntiradi.
- Agar AI aniq bir tuzatish taklif qilsa (function-calling, `EDIT_FILE_TOOL` orqali —
  `services/ai_client.py`), foydalanuvchiga ALOHIDA tasdiqlash so'raladi
  ("✅ Ha, tuzat" / "❌ Yo'q"). **FAQAT tasdiqlangandan keyin** haqiqiy fayl o'zgaradi,
  qo'shimcha `db.get_ai_help_price_stars()` narxi bilan (crash-tashxis bilan bir xil).
  AI hech qachon so'ralmasdan yoki tasdiqlanmasdan faylni o'zgartira olmaydi — bu
  qat'iy qoida system prompt darajasida ham mustahkamlangan (`build_free_chat_system_prompt`).
- Chiqish: mavjud umumiy "⛔️ Bekor qilish" tugmasi (`handlers/start.py: cancel_any`) —
  bu handler state'dan mustaqil ishlaydi va `main.py`da eng birinchi ro'yxatdan o'tadi,
  shu sabab suhbat davomida ham har doim ishlaydi.

**KRITIK BUG FIX (Stars balans)**: avval bir nechta joyda (`cb_ai_help`, `stars.py`dagi
to'lov va bot-uzaytirish oqimlari, admin gift) Stars balansi o'zgartirilgandan keyin
`backup_database()` chaqirilmasdi. Render Free Tier diski ephemeral bo'lgani uchun,
agar server backup'dan oldin qayta ko'tarilsa, `restore_database()` eski (Stars hali
yechilmagan) backup'ni tiklab qo'yardi — foydalanuvchi Stars sarflab/to'lab, baribir
balansi o'zgarmagan holatga tushib qolardi. Endi **har bir** balans o'zgarishidan keyin
darhol backup qilinadi.

### Adminga/foydalanuvchiga rasm, video, GIF va fayl yuborish

Uchta oqim ham (avval faqat matn qabul qilardi) endi istalgan turdagi xabarni
(rasm, video, GIF/animation, hujjat, ovozli xabar, sticker, audio) `message.copy_to()`
yoki `bot.copy_message()` orqali formatini saqlab yuboradi:
- **Foydalanuvchi → admin** (`handlers/start.py: forward_to_admin`, "📩 Adminga habar berish")
- **Admin → foydalanuvchi** (`handlers/admin_review.py: send_reply_to_user` — murojaatga javob,
  `handlers/admin_panel.py: _send_admin_msg` — to'g'ridan-to'g'ri xabar)
- **Broadcast** (`handlers/admin_panel.py: cb_admin_broadcast_confirm`) — barcha
  foydalanuvchilarga bir xil media yuboriladi, oldindan ko'rish ham media bilan ko'rsatiladi

Superadmin uchun "kim nomidan yuborilsin" (Ega/Admin) qadami bo'lgan oqimlarda
(`_send_admin_msg`, broadcast), asl media xabar `chat_id`+`message_id` sifatida
`state`da saqlanadi — chunki keyingi qadamda (`callback.message` orqali) asl xabarga
to'g'ridan-to'g'ri kirish imkoni yo'qoladi, shu sabab `bot.copy_message()` bilan qayta
nusxalanadi.

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
