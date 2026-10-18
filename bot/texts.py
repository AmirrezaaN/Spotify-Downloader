"""Persian bot messages (HTML parse mode)."""
from __future__ import annotations

from html import escape

from core.models import Collection, format_duration

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(value) -> str:
    return str(value).translate(FA_DIGITS)


def start(app_name: str, first_name: str) -> str:
    return (
        f"سلام {escape(first_name or '')} 👋\n"
        f"به <b>{escape(app_name)}</b> خوش اومدی 🎧\n\n"
        "لینک هر <b>ترک</b>، <b>آلبوم</b> یا <b>پلی‌لیست</b> اسپاتیفای رو بفرست؛ "
        "کیفیت رو انتخاب کن و فایل کامل رو تحویل بگیر.\n\n"
        "✨ <b>داخل هر فایل:</b>\n"
        "🖼 کاور باکیفیت\n"
        "🎵 اسم ترک، خواننده و آلبوم\n"
        "🔢 شماره ترک و سال انتشار\n"
        "📝 متن آهنگ (هم‌گام، اگه موجود باشه)\n\n"
        "مثال:\n<code>https://open.spotify.com/track/...</code>"
    )


HELP = (
    "📖 <b>راهنما</b>\n\n"
    "۱. توی اسپاتیفای روی ترک، آلبوم یا پلی‌لیست بزن ← <b>Share</b> ← <b>Copy link</b>\n"
    "۲. لینک رو همین‌جا بفرست\n"
    "۳. کیفیت رو انتخاب کن\n\n"
    "🎚 <b>کیفیت‌ها</b>\n"
    "🎧 <b>MP3 · 128kbps</b>: حجم کم، مناسب گوشی\n"
    "💎 <b>بهترین کیفیت موجود</b>: بالاترین کیفیت واقعی منبع، با MP3 یا فرمت اصلی بدون تبدیل\n\n"
    "ℹ️ کیفیت واقعی منبع همیشه زیر هر فایل نوشته میشه. فایل با بیت‌ریت بالاتر از منبع جعل نمی‌کنیم.\n"
    "⚠️ از هر پلی‌لیست حداکثر ۱۰۰ ترک اول دریافت میشه."
)

NOT_A_LINK = "🔗 لینک اسپاتیفای بفرست. مثلاً:\n<code>https://open.spotify.com/track/...</code>"
CHECKING = "🔍 در حال بررسی لینک..."
EXPIRED = "این درخواست منقضی شده؛ لینک رو دوباره بفرست."
NOT_YOURS = "این دکمه مال درخواست شخص دیگه‌ایه."
BUSY = "⏳ یه دانلود دیگه در جریانه. صبر کن تموم بشه یا متوقفش کن."
STARTED = "شروع شد ✅"
STOPPING = "⏹ بعد از ترک فعلی متوقف میشه."
CANCELLED = "❌ لغو شد."
CHOOSE_QUALITY = "👇 کیفیت مورد نظرت رو انتخاب کن:"
CHOOSE_FORMAT = (
    "👇 فرمت فایل؟\n"
    "🎵 <b>MP3</b>: روی همه پلیرها اجرا میشه\n"
    "🎼 <b>فرمت اصلی</b>: همون فایل منبع، بدون هیچ تبدیلی (Opus/M4A)"
)


def collection_caption(c: Collection, footer: str | None = CHOOSE_QUALITY) -> str:
    if c.kind == "track":
        t = c.tracks[0]
        lines = [f"🎵 <b>{escape(t.title)}</b>", f"👤 {escape(t.artist)}"]
        if t.album:
            lines.append(f"💿 {escape(t.album)}" + (f" · {fa(t.year)}" if t.year else ""))
        lines.append(f"⏱ {fa(format_duration(t.duration_s))}" + (" · 🅴" if t.explicit else ""))
    else:
        icon, kind = ("💿", "آلبوم") if c.kind == "album" else ("📃", "پلی‌لیست")
        lines = [f"{icon} <b>{escape(c.title)}</b>", f"👤 {escape(c.subtitle)}"]
        lines.append(f"🎶 {kind} · {fa(len(c.tracks))} ترک · ⏱ {fa(format_duration(c.duration_s))}")
        if c.truncated:
            lines.append(f"⚠️ این پلی‌لیست {fa(c.total)} ترک داره؛ فقط {fa(len(c.tracks))} ترک اول دریافت میشه.")
    if footer:
        lines += ["", footer]
    return "\n".join(lines)


def _bar(done: int, total: int, width: int = 10) -> str:
    filled = round(width * done / total) if total else width
    return "▰" * filled + "▱" * (width - filled)


def progress(c: Collection, index: int, total: int, title: str, quality_label: str) -> str:
    done = index - 1
    pct = round(100 * done / total) if total else 0
    return collection_caption(c, footer=None) + (
        "\n━━━━━━━━━━━━━━\n"
        f"⏳ در حال دانلود {fa(index)} از {fa(total)}\n"
        f"{_bar(done, total)} {fa(pct)}٪\n"
        f"🎵 {escape(title)}\n"
        f"🎚 {escape(quality_label)}"
    )


def single_progress(c: Collection, quality_label: str) -> str:
    return collection_caption(c, footer=None) + (
        f"\n━━━━━━━━━━━━━━\n⏳ در حال آماده‌سازی فایل…\n🎚 {escape(quality_label)}"
    )


def finished(c: Collection, sent: int, total: int, failed: list[tuple[str, str]], stopped: bool, quality_label: str) -> str:
    lines = [collection_caption(c, footer=None), "━━━━━━━━━━━━━━"]
    if total == 1 and sent == 1:
        lines.append(f"✅ ارسال شد · 🎚 {escape(quality_label)}")
    else:
        head = "⏹ متوقف شد." if stopped else "✅ تموم شد!"
        lines.append(f"{head} {fa(sent)} از {fa(total)} ترک ارسال شد · 🎚 {escape(quality_label)}")
    if failed:
        lines.append("")
        lines.append("⚠️ این‌ها دریافت نشدن:" if total > 1 else "⚠️ دریافت نشد:")
        for title, reason in failed[:8]:
            lines.append(f"• {escape(title)}" + (f" ({escape(reason)})" if total == 1 else ""))
        if len(failed) > 8:
            lines.append(f"… و {fa(len(failed) - 8)} ترک دیگه")
    text = "\n".join(lines)
    return text[:1020] + "…" if len(text) > 1024 else text


def file_caption(info: dict, bot_username: str | None) -> str:
    t = info.get("track") or {}
    lines = [f"🎵 <b>{escape(t.get('title', ''))}</b>", f"👤 {escape(t.get('artist', ''))}"]
    if t.get("album"):
        lines.append(f"💿 {escape(t['album'])}")
    lines.append(f"🎚 {escape(info.get('label', ''))}" + (" · 📝 متن" if info.get("lyrics") else ""))
    lines.append(f"🔎 منبع: {escape(info.get('source', ''))}")
    if bot_username:
        lines.append(f"🤖 @{bot_username}")
    return "\n".join(lines)


def too_big(title: str, size_mb: float, limit_mb: int) -> str:
    return f"📦 «{escape(title)}» {fa(round(size_mb, 1))} مگابایته و از سقف آپلود تلگرام ({fa(limit_mb)} مگ) بیشتره."
