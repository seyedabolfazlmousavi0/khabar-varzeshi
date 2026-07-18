# خبرورزشی — ربات سردبیری و اتوماسیون خبر

سیستم خودکار جمع‌آوری، بازنویسی، بازبینی و انتشار اخبار ورزشی برای **خبرورزشی** (`Khabar Varzeshi`).

خبرها از RSS انگلیسی خوانده می‌شوند، با **Google Gemini** به فارسی بازنویسی می‌شوند، از نظر شباهت معنایی با مطالب اخیر سایت فیلتر می‌شوند، و از طریق **ربات تلگرام** توسط ادمین بررسی و به کانال تلگرام و/یا سایت خبرورزشی منتشر می‌شوند.

---

## خلاصهٔ جریان کار

```
RSS منابع فعال
        │
        ▼
┌───────────────────┐
│  fetch_news       │  هر ۱ ساعت توسط run_worker (حداکثر ۱۰ خبر، فاصله ≥۵ دقیقه)
│  • نرمال‌سازی URL │
│  • حذف تکراری URL │  URL ذخیره‌شده هرگز دوباره به Gemini نمی‌رود
│  • semantic dedup │  مقایسه با RSS ۲۴ ساعت اخیر سایت
│  • scrape صفحه    │
│  • بازنویسی Gemini│  → site_title / site_lead / site_body / telegram_text
└─────────┬─────────┘
          │ NewsArticle (status=pending)
          ▼
┌───────────────────┐
│  run_worker       │  نوتیفیکیشن به ادمین‌ها در تلگرام
└─────────┬─────────┘
          ▼
┌───────────────────┐
│  run_bot          │  بازبینی دستی در تلگرام
│  تایید → کانال    │
│  رد / ویرایش متن  │
│  افزودن لینک سایت │
│  انتشار روی سایت  │  Selenium → newsroom.khabarvarzeshi.com
└───────────────────┘
```

برای کار کامل سیستم معمولاً **دو پروسه** هم‌زمان لازم است:

| پروسه | دستور | نقش |
|--------|--------|------|
| Worker | `python manage.py run_worker` | هر ۱ ساعت `fetch_news` + اطلاع به ادمین |
| Bot | `python manage.py run_bot` | ربات سردبیری (long polling) — همیشه روشن در طول روز |

---

## پیش‌نیازها

- Python 3.11+ (پیشنهادی)
- حساب [Google AI Studio](https://aistudio.google.com/app/apikey) برای `GEMINI_API_KEY`
- ربات تلگرام از [@BotFather](https://t.me/BotFather)
- شناسهٔ عددی ادمین(ها) از [@userinfobot](https://t.me/userinfobot)
- کانال عمومی تلگرام که ربات در آن **ادمین** باشد و اجازهٔ Post Messages داشته باشد
- (اختیاری، برای انتشار روی سایت) حساب Newsroom + Chrome/Chromium برای Selenium

---

## نصب و راه‌اندازی

```bash
# کلون و ورود به پروژه
cd khabar_varzeshi

# محیط مجازی
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# وابستگی‌ها
pip install -r requirements.txt

# فایل محیط
copy .env.example .env   # Windows
# یا: cp .env.example .env

# مایگریشن دیتابیس
python manage.py migrate

# ساخت کاربر ادمین Django (برای پنل /admin)
python manage.py createsuperuser
```

سپس مقادیر واقعی را در `.env` پر کنید (جزئیات در بخش متغیرهای محیطی).

در پنل ادمین Django (`/admin`) حداقل یک **RssSource** فعال بسازید (`is_active=True`).

اجرای سیستم:

```bash
# ترمینال ۱ — جمع‌آوری دوره‌ای
python manage.py run_worker

# ترمینال ۲ — ربات تلگرام
python manage.py run_bot
```

یک‌بار دستی هم می‌توانید ingestion را اجرا کنید:

```bash
python manage.py fetch_news
```

---

## متغیرهای محیطی (`.env`)

فایل نمونه: `.env.example` — هرگز `.env` واقعی را commit نکنید.

### ضروری

| متغیر | توضیح |
|--------|--------|
| `GEMINI_API_KEY` | کلید API جمینای |
| `TELEGRAM_BOT_TOKEN` | توکن ربات از BotFather |
| `ALLOWED_ADMIN_IDS` | شناسه‌های عددی ادمین، جدا با کاما (مثلاً `123,456`) |
| `TELEGRAM_PUBLIC_CHANNEL_ID` | کانال عمومی (`@username` یا id عددی مثل `-100...`) |

اگر `ALLOWED_ADMIN_IDS` خالی باشد، از `TELEGRAM_ADMIN_CHAT_ID` به‌عنوان fallback تک‌ادمین استفاده می‌شود.

### اختیاری — Gemini

| متغیر | پیش‌فرض | توضیح |
|--------|----------|--------|
| `GEMINI_MODEL` | `models/gemini-2.5-flash-lite` | مدل بازنویسی متن |

### انتشار روی سایت (Newsroom)

| متغیر | پیش‌فرض | توضیح |
|--------|----------|--------|
| `NEWSROOM_USERNAME` | — | نام کاربری داشبورد |
| `NEWSROOM_PASSWORD` | — | رمز عبور |
| `NEWSROOM_LOGIN_URL` | `https://newsroom.khabarvarzeshi.com/login/login.xhtml` | صفحهٔ لاگین |
| `NEWSROOM_CREATE_URL` | `https://newsroom.khabarvarzeshi.com/news.xhtml` | صفحهٔ ایجاد خبر |
| `NEWSROOM_HEADLESS` | `1` | اجرای بدون پنجرهٔ مرورگر |
| `SELENIUM_WAIT_TIMEOUT` | `25` | ثانیه |
| `SELENIUM_LOGIN_WAIT_TIMEOUT` | `30` | ثانیه |
| `SELENIUM_CKEDITOR_WAIT_TIMEOUT` | `45` | ثانیه |

### حذف تکراری معنایی (Semantic Dedup)

به‌صورت پیش‌فرض فعال است و از همان `GEMINI_API_KEY` برای embedding استفاده می‌کند.

| متغیر | پیش‌فرض | توضیح |
|--------|----------|--------|
| `SEMANTIC_DEDUP_ENABLED` | `1` | روشن/خاموش |
| `SEMANTIC_DEDUP_BASELINE_RSS` | `https://www.khabarvarzeshi.com/rss` | RSS مرجع سایت |
| `SEMANTIC_DEDUP_EMBEDDING_MODEL` | `gemini-embedding-001` | مدل embedding |
| `SEMANTIC_DEDUP_THRESHOLD` | `0.80` | آستانهٔ شباهت کسینوسی |
| `SEMANTIC_DEDUP_DIMENSIONS` | `768` | ابعاد بردار |
| `SEMANTIC_DEDUP_LOOKBACK_HOURS` | `24` | بازهٔ زمانی baseline |
| `SEMANTIC_DEDUP_FAIL_OPEN` | `1` | در خطای embedding، ingestion ادامه یابد |
| `SEMANTIC_DEDUP_REQUEST_INTERVAL` | `1.2` | فاصلهٔ درخواست‌ها (ثانیه) |
| `SEMANTIC_DEDUP_BATCH_SIZE` | `8` | اندازهٔ بچ |
| `SEMANTIC_DEDUP_BATCH_PAUSE` | `2.0` | مکث بین بچ‌ها |
| `SEMANTIC_DEDUP_MAX_RETRIES` | `5` | تلاش مجدد روی 429 |
| `SEMANTIC_DEDUP_RETRY_BASE_DELAY` | `12` | تأخیر پایهٔ retry |
| `SEMANTIC_DEDUP_RETRY_MAX_DELAY` | `60` | سقف تأخیر retry |

---

## دستورات مدیریتی (Management Commands)

| دستور | توضیح |
|--------|--------|
| `python manage.py run_bot` | اجرای ربات سردبیری (aiogram، long polling). در خطای شبکه بعد از ۱۰ ثانیه دوباره شروع می‌شود. |
| `python manage.py run_worker` | هر ۱ ساعت `fetch_news`؛ حداکثر ۱۰ بازنویسی Gemini در هر چرخه با فاصلهٔ ≥۵ دقیقه؛ URLهای ذخیره‌شده دوباره بازنویسی نمی‌شوند. اگر خبر جدید pending شود به ادمین‌ها پیام می‌دهد. |
| `python manage.py fetch_news` | یک‌بار: خواندن RSS فعال‌ها، dedup، scrape، Gemini (سقف ۱۰ + فاصله ۵ دقیقه)، ذخیرهٔ `pending`. |
| `python manage.py remove_duplicate_articles` | حذف ردیف‌های تکراری بر اساس URL نرمال‌شده (قدیمی‌ترین نگه داشته می‌شود). |
| `python manage.py remove_duplicate_articles --dry-run` | فقط گزارش تکراری‌ها بدون حذف. |
| `python manage.py runserver` | پنل Django Admin برای مدیریت منابع و اخبار. |

---

## ربات تلگرام — قابلیت‌ها

ربات با **aiogram 3** نوشته شده، از FSM برای ویرایش/لینک استفاده می‌کند، و دسترسی ادمین فقط برای `ALLOWED_ADMIN_IDS` است.

### دستورات و منو

| ورودی | دسترسی | کار |
|--------|---------|------|
| `/start` ، `/help` | همه | خوش‌آمد؛ برای ادمین راهنمای دکمهٔ بررسی |
| دکمهٔ **بررسی آخرین اخبار 📋** یا `/check_pending` | فقط ادمین | نمایش تا ۲۰ خبر `pending` (جدیدترین‌ها) |
| `/cancel` | ادمین | لغو ویرایش متن یا افزودن لینک در حال انجام |

کاربر غیرادمین با زدن دکمهٔ بررسی پیام «فقط ادمین‌ها دسترسی دارند» می‌گیرد.

### دکمه‌های اینلاین هر خبر

روی پیش‌نمایش هر مقاله:

| دکمه | عمل |
|------|------|
| ✅ تایید و ارسال | ارسال `telegram_text` (+ تصویر در صورت وجود) به کانال عمومی و تغییر وضعیت به `published` |
| ❌ رد کردن | وضعیت → `rejected` و حذف دکمه‌ها |
| ✏️ ویرایش متن | ورود به FSM؛ فقط بدنهٔ پست قابل ویرایش است؛ لینک و فوتر حفظ می‌شوند |
| 🔗 افزودن لینک | دریافت URL و درج لینک HTML با متن ثابت «در سایت خبرورزشی بخوانید» |
| انتشار خبر در سایت | انتشار پس‌زمینه‌ای با Selenium روی Newsroom (تیتر/لید/متن سایت الزامی) |

بعد از تایید کانال، دکمه به «✅ ارسال شد به کانال» تغییر ظاهر می‌دهد. بعد از موفقیت سایت: «✅ منتشر شد در سایت». حین انتشار سایت: «⏳ در حال انتشار...».

### پیش‌نمایش ادمین

هر پیام بازبینی شامل این‌هاست:

- تیتر سایت (`site_title`)
- لید (`site_lead`)
- متن سایت (`site_body`) — در صورت طولانی بودن کوتاه می‌شود
- متن تلگرام (`telegram_text`) با لینک HTML در صورت وجود
- تصویر (`image_url`) در صورت موجود بودن؛ اگر تلگرام عکس را نپذیرد، به متن‌ alone fallback می‌شود

محدودیت کپشن عکس تلگرام (۱۰۲۴ کاراکتر) و پیام متنی (۴۰۹۶) رعایت می‌شود.

### ساختار متن تلگرام

`telegram_text` از سه بخش تشکیل می‌شود:

1. **بدنه** — متن اصلی پست (قابل ویرایش)
2. **لینک اختیاری** — `<a href="...">در سایت خبرورزشی بخوانید</a>`
3. **فوتر** — معمولاً `@KhabarVarzeshi`

Gemini در ingestion متن را طوری می‌سازد که با `@KhabarVarzeshi` تمام شود.

---

## جمع‌آوری و بازنویسی (`fetch_news`)

محدودیت‌های هر چرخه (توسط `run_worker` هر ساعت یک‌بار):

| قانون | مقدار |
|--------|--------|
| فاصلهٔ اجرای Worker | هر ۱ ساعت (ربات تلگرام جدا و همیشه روشن است) |
| حداکثر درخواست Gemini در هر چرخه | ۱۰ |
| حداقل فاصله بین دو درخواست Gemini | ۵ دقیقه |
| بازنویسی دوبارهٔ همان خبر | ممنوع — اگر `original_url` در DB باشد (هر وضعیتی)، به Gemini نمی‌رود |

برای هر `RssSource` فعال:

1. پارس RSS با `feedparser`
2. نرمال‌سازی URL (`normalize_article_url`) و رد کردن URL تکراری در DB
3. **Semantic dedup** قبل از scrape/Gemini: embedding عنوان+توضیح در برابر corpus ۲۴ ساعتهٔ RSS سایت؛ اگر شباهت ≥ آستانه باشد رد می‌شود
4. scrape صفحهٔ مقاله (fallback به محتوای RSS)
5. استخراج تصویر از media RSS / enclosure / HTML و …
6. فراخوانی Gemini با پرامپت سردبیر رسمی ورزشی (فارسی حرفه‌ای، بدون اغراق کلیک‌بیتی)
7. ذخیرهٔ JSON خروجی به‌عنوان `NewsArticle` با `status=pending`

خروجی اجباری Gemini:

- `site_title` — تیتر SEO فارسی
- `site_lead` — لید ۲–۳ جمله‌ای
- `site_body` — بدنهٔ HTML فقط با `<h2>` و `<p>`
- `telegram_text` — پست مستقل کانال (حدود ۴۰–۱۲۰ کلمه)

---

## حذف تکراری معنایی

ماژول `core/semantic_dedup/`:

- RSS سایت خبرورزشی را به‌عنوان baseline می‌گیرد
- embeddingها در مدل `BaselineArticleEmbedding` کش می‌شوند
- مقایسه با cosine similarity
- اگر سرویس embedding از کار بیفتد، با `SEMANTIC_DEDUP_FAIL_OPEN=1` ingestion متوقف نمی‌شود

---

## انتشار روی سایت

با دکمهٔ «انتشار خبر در سایت»:

1. اعتبارسنجی `site_title` / `site_lead` / `site_body`
2. جاب پس‌زمینه (`asyncio.create_task`) تا event loop ربات بلاک نشود
3. دانلود تصویر (در صورت وجود)
4. لاگین Selenium به Newsroom و پر کردن فرم خبر
5. به‌روزرسانی پیام بازبینی با موفقیت یا خطا

وضعیت «منتشر شد در سایت» در حافظهٔ پروسهٔ ربات نگه داشته می‌شود (با ری‌استارت ربات ریست می‌شود؛ وضعیت کانال از فیلد `status` در DB می‌آید).

---

## مدل‌های داده

### `RssSource`

| فیلد | توضیح |
|------|--------|
| `name` | نام منبع |
| `url` | آدرس RSS (یکتا) |
| `category` | دسته‌بندی اختیاری |
| `is_active` | فقط منابع فعال در `fetch_news` خوانده می‌شوند |

### `NewsArticle`

| فیلد | توضیح |
|------|--------|
| `source` | FK به RssSource |
| `original_title` / `original_url` | عنوان و لینک اصلی (URL یکتا، نرمال‌شده) |
| `image_url` | تصویر خبر |
| `site_title` / `site_lead` / `site_body` | محتوای بازنویسی‌شده برای سایت |
| `telegram_text` | پست کانال |
| `status` | `pending` \| `published` \| `rejected` |
| `created_at` | زمان ایجاد |

### `BaselineArticleEmbedding`

کش embedding اخبار RSS سایت برای dedup معنایی (guid، url، title، embedding، …).

---

## ساختار پروژه

```
khabar_varzeshi/
├── manage.py
├── requirements.txt
├── .env.example
├── khabar_varzeshi/          # تنظیمات Django
│   ├── settings.py           # SQLite پیش‌فرض: db.sqlite3
│   ├── urls.py
│   └── ...
└── core/
    ├── models.py
    ├── admin.py
    ├── article_scraper.py
    ├── url_utils.py
    ├── bot/                  # ربات سردبیری (aiogram)
    │   ├── app.py            # factory + polling
    │   ├── config.py
    │   ├── auth.py           # AdminFilter
    │   ├── keyboards.py
    │   ├── services.py       # DB + ارسال/ویرایش پیام
    │   ├── site_publish.py   # جاب انتشار سایت
    │   ├── states.py         # FSM
    │   ├── text_compose.py   # پارس/ترکیب telegram_text
    │   └── handlers/
    │       ├── common.py     # start / help / cancel
    │       ├── check_pending.py
    │       ├── review.py     # approve / reject / edit / link / site
    │       └── fsm.py
    ├── newsroom/             # Selenium → Newsroom
    │   ├── automation.py
    │   ├── publisher.py
    │   ├── config.py
    │   └── exceptions.py
    ├── semantic_dedup/       # فیلتر شباهت معنایی
    │   ├── filter.py
    │   ├── baseline.py
    │   ├── embeddings.py
    │   └── ...
    └── management/commands/
        ├── run_bot.py
        ├── run_worker.py
        ├── fetch_news.py
        └── remove_duplicate_articles.py
```

---

## نکات عملیاتی

- **IPv4 اجباری:** در `run_bot` / `run_worker` / `fetch_news` سوکت طوری پچ شده که فقط IPv4 استفاده شود (جلوگیری از تایم‌اوت IPv6 روی برخی شبکه‌ها).
- **دیتابیس:** پیش‌فرض SQLite (`db.sqlite3`). برای production می‌توانید در `settings.py` به PostgreSQL و غیره تغییر دهید.
- **دو پروسه:** Worker فقط ingestion و نوتیف می‌کند (هر ساعت یک‌بار)؛ بازبینی فقط با `run_bot` است که باید کل روز روشن بماند. دکمهٔ «بررسی آخرین اخبار» ingestion را دوباره اجرا نمی‌کند.
- **کروم برای سایت:** برای انتشار Newsroom باید Chrome/Chromium در دسترس باشد (`webdriver-manager` درایور را مدیریت می‌کند).
- **امنیت:** توکن‌ها و پسوردها فقط در `.env`؛ `ALLOWED_ADMIN_IDS` را محدود نگه دارید.

---

## پنل Django Admin

```bash
python manage.py runserver
```

آدرس معمول: `http://127.0.0.1:8000/admin/`

از آنجا می‌توانید منابع RSS را فعال/غیرفعال کنید و وضعیت مقالات، تصاویر و متن‌های بازنویسی‌شده را ببینید یا ویرایش کنید.

---

## عیب‌یابی سریع

| مشکل | بررسی |
|------|--------|
| ربات بالا نمی‌آید | `TELEGRAM_BOT_TOKEN` و `ALLOWED_ADMIN_IDS` / کانال در `.env` |
| `fetch_news` خطا می‌دهد | `GEMINI_API_KEY` و فعال بودن حداقل یک RssSource |
| خبر جدید نمی‌آید | Worker در حال اجراست؟ منبع RSS درست است؟ semantic dedup همه را رد نمی‌کند؟ |
| ارسال به کانال شکست می‌خورد | ربات ادمین کانال است؟ `TELEGRAM_PUBLIC_CHANNEL_ID` درست است؟ |
| انتشار سایت خطا می‌دهد | `NEWSROOM_*`، دسترسی شبکه، و Selenium/Chrome |
| دکمهٔ قدیمی کیبورد | `/start` بزنید تا منوی جدید بیاید |

---

## لایسنس / مالکیت

پروژهٔ داخلی اتوماسیون خبر برای **خبرورزشی**. استفاده و استقرار طبق سیاست تیم تحریریه.
