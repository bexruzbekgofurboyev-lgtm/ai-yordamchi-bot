# AI Yordamchi va Vazifa Rejalashtiruvchi Telegram Bot

Gemini API asosida ishlaydigan shaxsiy yordamchi va vazifalarni rejalashtiruvchi bot.

## ⚠️ Xavfsizlik haqida eslatma

API kalitlaringizni (Telegram token va Gemini kalit) hech qachon boshqalar bilan
(shu jumladan AI chat suhbatlarida) baham ko'rmang. Ularni faqat quyida
ko'rsatilgandek muhit o'zgaruvchilari (environment variables) sifatida saqlang.

## 1-qadam: Telegram bot yaratish

1. Telegram'da **@BotFather** ni toping va unga yozing.
2. `/newbot` buyrug'ini yuboring.
3. Bot uchun nom va username bering (username `bot` bilan tugashi kerak, masalan `MeningYordamchimBot`).
4. BotFather sizga **token** beradi — bu shunday ko'rinishda bo'ladi:
   `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
5. Bu tokenni saqlab qo'ying.

## 2-qadam: Gemini API kalitini olish

Siz buni allaqachon qilgansiz — [Google AI Studio](https://aistudio.google.com/apikey)
orqali bepul olingan kalitingiz bo'lishi kerak.

## 3-qadam: Kompyuterda tayyorlash

Python 3.9+ o'rnatilgan bo'lishi kerak. Terminalda quyidagilarni bajaring:

```bash
# Kerakli kutubxonalarni o'rnatish
pip install python-telegram-bot requests
```

## 4-qadam: Kalitlarni sozlash

**Windows (PowerShell):**
```powershell
$env:TELEGRAM_BOT_TOKEN="sizning_telegram_tokeningiz"
$env:GEMINI_API_KEY="sizning_gemini_kalitingiz"
```

**macOS / Linux:**
```bash
export TELEGRAM_BOT_TOKEN="sizning_telegram_tokeningiz"
export GEMINI_API_KEY="sizning_gemini_kalitingiz"
```

> 💡 Har safar terminalni yopganda bu qiymatlar o'chib ketadi. Doimiy ishlatish
> uchun ularni `.env` faylga yozib, yoki serverning muhit sozlamalariga
> qo'shishingiz mumkin.

## 5-qadam: Botni ishga tushirish

```bash
python bot.py
```

Terminalda "Bot ishga tushmoqda..." degan xabarni ko'rsangiz — tayyor!
Endi Telegram'da botingizga o'ting va `/start` bosing.

## Buyruqlar ro'yxati

| Buyruq | Vazifasi |
|---|---|
| `/start` | Botni boshlash, yo'riqnoma ko'rsatish |
| `/task <matn>` | Yangi vazifa qo'shish |
| `/plan <matn>` | Katta maqsadni AI yordamida kichik bosqichlarga bo'lib qo'shish |
| `/list` | Barcha vazifalarni ko'rish (bajarildi/o'chirish tugmalari bilan) |
| `/clear` | Bajarilgan vazifalarni tozalash |
| Oddiy xabar | AI yordamchi bilan erkin suhbat |

## Botni doimiy (24/7) ishlatish

Kompyuteringizni doimiy yoqib qo'ymaslik uchun botni bepul yoki arzon serverda
joylashtirishingiz mumkin:

- **Railway.app** — bepul tarif bor, GitHub orqali oson deploy qilinadi
- **Render.com** — "Background Worker" sifatida ishga tushiriladi
- **PythonAnywhere** — bepul tarifda ham ishlaydi
- Shaxsiy **VPS** (masalan, Contabo, Hetzner) — to'liq nazorat uchun

Bu platformalarning barchasida environment variables (muhit o'zgaruvchilari)
sozlamalar bo'limida qo'shiladi — kod ichiga API kalitlarni yozish shart emas.

## Ma'lumotlar qayerda saqlanadi?

Barcha vazifalar bot ishlayotgan papkadagi `tasks_data.json` faylida saqlanadi.
Har bir foydalanuvchining vazifalari alohida, Telegram ID bo'yicha ajratiladi.

## Muammo yuzaga kelsa

- **"TELEGRAM_BOT_TOKEN topilmadi"** — muhit o'zgaruvchisini to'g'ri sozlaganingizni tekshiring.
- **AI javob bermayapti** — Gemini API kalitingiz to'g'riligini va limitingiz
  tugamaganini tekshiring ([Google AI Studio](https://aistudio.google.com/apikey)).
- **Bot umuman javob bermayapti** — `python bot.py` terminalda xatolik
  chiqarayotganini tekshiring.