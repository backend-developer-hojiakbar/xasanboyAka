# Avto Xabar Bot

Bu foydalanuvchilarga guruhlarga avtomatik xabarlar yuborish imkonini beruvchi keng qamrovli Telegram botidir. Bot obuna boshqaruvi, to'lov tekshiruvi, akkaunt ulanishi va xabar rejalashtirish funksiyalarini o'z ichiga oladi.

## Xususiyatlar

### Foydalanuvchi Xususiyatlari:
- **Obuna Boshqaruvi**: Foydalanuvchilar 1-oylik kirish huquqi uchun obuna bo'lishadi
- **To'lov Tizimi**: Chekni tekshirish bilan integratsiyalangan to'lov tizimi
- **Akkaunt Ulanishi**: Telegram akkauntlarini ulash va tekshirish
- **Xabar Rejalashtirish**: Maxsus vaqtlarda yuboriladigan xabarlarni rejalashtirish
- **Guruh Boshqaruvi**: Barcha yoki tanlangan guruhlarga xabar yuborish
- **Video Qo'llanma**: Ichki o'quv qo'llanma va ko'rsatmalar

### Admin Xususiyatlari:
- **Foydalanuvchi Boshqaruvi**: Barcha foydalanuvchilarni ko'rish va boshqarish
- **To'lov Ko'rib Chiqish**: To'lov cheklarini tasdiqlash/bekor qilish
- **Obuna Boshqaruvi**: Foydalanuvchi obunalarini qayta o'rnatish yoki deaktivatsiya qilish
- **Statistika**: Bot foydalanish statistikasini ko'rish
- **Foydalanuvchi Qidirish**: Foydalanuvchilarni ID yoki foydalanuvchi nomi bo'yicha qidirish

### Texnik Xususiyatlar:
- **Ma'lumotlar Bazasi Boshqaruvi**: SQLAlchemy ORM bilan SQLite ma'lumotlar bazasi
- **Xabar Rejalashtiruvchi**: Rejalashtirishga asoslangan avtomatik xabar yuborish
- **Xato Boshqaruvi**: Keng qamrovli xato boshqaruvi va jurnalga yozish
- **Muhit Konfiguratsiyasi**: Muhit o'zgaruvchilari orqali sozlanadigan

## O'rnatish

### Talablama
- Python 3.8 yoki undan yuqori versiya
- pip (Python paket menejeri)

### Qadamlar

1. **Loyihaning fayllarini klonlang yoki yuklab oling**

2. **Kutubxonalarni o'rnating:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Muhit o'zgaruvchilarini sozlang:**
   `.env` faylini tahrirlab, o'zingizning sozlamalaringizni kiriting:
   ```env
   # Telegram Bot Konfiguratsiyasi
   BOT_TOKEN=sizning_telegram_bot_tokeningiz
   ADMIN_ID=sizning_telegram_foydalanuvchi_id_raqamingiz

   # Ma'lumotlar Bazasi Konfiguratsiyasi
   DATABASE_URL=sqlite:///bot_database.db

   # To'lov Konfiguratsiyasi
   CARD_NUMBER=1234 5678 9012 3456
   CARD_HOLDER=Sizning Ismingiz

   # Xabar Sozlamalari
   MIN_SCHEDULE_TIME=5
   ```

4. **Telegram Bot Token Oling:**
   - Telegramda @BotFather ga murojaat qiling
   - Yangi bot yaratish uchun `/newbot` buyrug'ini yozing
   - Tokenni nusxalab, `.env` fayliga joylashtiring

5. **Telegram Foydalanuvchi ID Oling:**
   - Telegramda @userinfobot ga murojaat qiling
   - Sizga foydalanuvchi ID raqamingizni ko'rsatadi, nusxalang

6. **Botni ishga tushiring:**
   ```bash
   python main.py
   ```

## Foydalanish

### Foydalanuvchilar Uchun:

1. **Botni Ishga Tushiring:**
   - Boshlash uchun `/start` yozing
   - Agar hali obuna bo'lmagan bo'lsangiz, obuna jarayonini bajaring

2. **Botga Obuna Bo'ling:**
   - "Ko'rsatmalarni O'qish" tugmasini bosing
   - "Karta Tafsilotlarini Ko'rish" tugmasini bosing
   - To'lov qiling va "To'lov Qildim" yordamida chek yuboring
   - Admin tasdiqlashini kuting (odatda 1 soat ichida)

3. **Akkauntingizni Qo'shing:**
   - Obuna tasdiqlanganidan keyin "Akkaunt Qo'shish" tugmasini bosing
   - Telefon raqamingizni xalqaro formatda kiriting (+1234567890)
   - Telegramda kelgan tekshiruv kodini (format: 123.456) kiriting

4. **Xabarlarni Rejalashtiring:**
   - "Xabarni Rejalashtirish" tugmasini bosing
   - Xabar matnini kiriting
   - Rejalashtirish vaqtini belgilang (minimal 5 daqiqa)
   - Maqsadli guruhlarni tanlang (barchasi yoki tanlanganlar)
   - Xabar rejalashtirilgan vaqtda avtomatik tarzda yuboriladi

5. **Xabarlarni Darhol Yuboring:**
   - "Xabar Yuborish" tugmasini bosing
   - "Barcha Guruhlar" yoki "Tanlangan Guruhlar"ni tanlang
   - Xabar darhol yuboriladi

### Admin Uchun:

1. **Admin Panelga Kirish:**
   - Bosh menyuni ochish uchun `/start` yozing
   - Admin funksiyalari avtomatik ravishda mavjud bo'ladi

2. **To'lovlarni Ko'rib Chiqing:**
   - Admin panelida "To'lovlarni Ko'rib Chiqish" tugmasini bosing
   - Kutilayotgan to'lov cheklarini ko'ring
   - To'lovlarni tasdiqlang yoki bekor qiling

3. **Foydalanuvchilarni Boshqaring:**
   - Foydalanuvchi ro'yxatini va tafsilotlarini ko'ring
   - Foydalanuvchilarni ID yoki foydalanuvchi nomi bo'yicha qidiring
   - Obunalarni qayta o'rnating yoki foydalanuvchilarni deaktivatsiya qiling

4. **Statistikani Ko'ring:**
   - Jami foydalanuvchilar, faol foydalanuvchilar va to'lov statistikasini ko'ring

## Ma'lumotlar Bazasi Struktura

Bot SQLAlchemy ORM bilan SQLite ma'lumotlar bazasidan foydalanadi:

- **users**: Foydalanuvchi ma'lumotlarini va obuna holatini saqlaydi
- **payments**: To'lov cheklarini va tasdiqlash holatini kuzatadi
- **scheduled_messages**: Rejalashtirilgan xabarlarni va ularning tafsilotlarini saqlaydi
- **user_groups**: Foydalanuvchi guruhlari bilan bog'lanishlarni kuzatadi
- **bot_settings**: Bot konfiguratsiya sozlamalarini saqlaydi

## Konfiguratsiya Variantlari

### Muhit O'zgaruvchilari:

- `BOT_TOKEN`: Sizning Telegram bot tokeningiz (majburiy)
- `ADMIN_ID`: Sizning Telegram foydalanuvchi ID raqamingiz (majburiy)
- `DATABASE_URL`: Ma'lumotlar bazasi ulanish satringiz (standart: sqlite:///bot_database.db)
- `CARD_NUMBER`: Obunalar uchun to'lov kartasi raqami (standart: 1234 5678 9012 3456)
- `CARD_HOLDER`: Karta egasi nomi (standart: Sizning Ismingiz)
- `MIN_SCHEDULE_TIME`: Minimal rejalashtirish vaqti daqiqada (standart: 5)

## Jurnalga Yozish

Bot barcha faoliyatni quyidagilarga yozadi:
- Konsol chiqishi
- `logs/bot.log` fayliga

Jurnal darajalari quyidagilarni o'z ichiga oladi:
- INFO: Umumiy axborot
- WARNING: Ogohlantirishlar va ehtimoliy muammolar
- ERROR: Xatolar va istisnolar

## Xato Boshqaruvi

Bot keng qamrovli xato boshqaruvidan foydalanadi:
- Ma'lumotlar bazasi ulanish xatolari
- Telegram API xatolari
- Foydalanuvchi kiritish tekshiruvi
- To'lov qayta ishlash xatolari
- Xabar rejalashtirish muvaffaqiyatlari

## Xavfsizlik Xususiyatlari

- Admin kirish huquqi boshqaruvi
- Foydalanuvchi autentifikatsiyasi va tekshiruvi
- To'lov chekini tekshirish
- Obuna muddati tugashini tekshirish
- Kirish ma'lumotlarini tozalash

## Muammo Hal Qilish

### Ko'pinap Uchraydigan Muammolar:

1. **Bot javob bermaydi:**
   - BOT_TOKEN to'g'ri ekanligini tekshiring
   - Bot ishlayotganligini tekshiring
   - Jurnallarda xatolarni tekshiring

2. **To'lov qayta ishlamaydi:**
   - ADMIN_ID to'g'ri ekanligini tekshiring
   - Admin paneli ishlayotganligini tekshiring
   - To'lov jurnallarini tekshiring

3. **Xabarlar yuborilmaydi:**
   - Foydalanuvchining faol obunasi borligini tekshiring
   - Akkaunt tekshirilganligini tekshiring
   - Guruhlar to'g'ri sozlanganligini tekshiring

4. **Ma'lumotlar bazasi xatolari:**
   - Loyiha jildida yozish huquqlariga ega ekanligingizni tekshiring
   - Ma'lumotlar bazasi fayliga kirish mumkinligini tekshiring
   - Ma'lumotlar bazasi ulanish sozlamalarini tekshiring

## Qo'llab-quvvatlash

Yordam va savollar uchun:
- `logs/bot.log` jurnalini ko'rib chiqing
- Qo'llanmani o'qing
- Bot administratori bilan bog'laning

## Litsenziya

Ushbu loyiha ta'lim va shaxsiy foydalanish uchundir. Ushbu botdan foydalanayotganda Telegramning xizmat ko'rsatish shartlariga va qo'llaniladigan qonunlarga rioya qiling.