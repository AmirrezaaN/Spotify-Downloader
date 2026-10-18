"""Telegram bot: send a Spotify link, pick a quality, get tagged audio files back."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field

from telegram import (
    BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters, Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from core import spotify
from core.cache import cache
from core.config import settings
from core.models import Collection, Track
from core.pipeline import DownloadError, check_environment, download, get_cover, lookup, make_thumbnail
from core.qualities import GROUPS, QUALITIES, available_groups

from . import texts

log = logging.getLogger("bot")

REQUEST_TTL = 60 * 60
PROGRESS_EVERY = 2.5  # seconds between progress edits (Telegram rate limits edits)


@dataclass
class Request:
    id: str
    user_id: int
    chat_id: int
    collection: Collection
    card: Message | None = None
    created: float = field(default_factory=time.time)
    running: bool = False
    stop: bool = False


def _requests(app: Application) -> dict[str, Request]:
    return app.bot_data.setdefault("requests", {})


def _active_users(app: Application) -> set[int]:
    return app.bot_data.setdefault("active", set())


def _semaphore(app: Application) -> asyncio.Semaphore:
    return app.bot_data["semaphore"]


# ---------- keyboards ----------

def quality_keyboard(rid: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"{GROUPS[g]['emoji']} {GROUPS[g]['label']}", callback_data=f"q:{rid}:{g}")
        for g in available_groups()
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("❌ لغو", callback_data=f"x:{rid}")])
    return InlineKeyboardMarkup(rows)


def format_keyboard(rid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 MP3", callback_data=f"f:{rid}:best_mp3"),
            InlineKeyboardButton("🎼 فرمت اصلی", callback_data=f"f:{rid}:best_orig"),
        ],
        [InlineKeyboardButton("🔙 برگشت", callback_data=f"b:{rid}")],
    ])


def stop_keyboard(rid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏹ توقف", callback_data=f"s:{rid}")]])


# ---------- helpers ----------

async def _edit_card(req: Request, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    if not req.card:
        return
    try:
        if req.card.photo:
            await req.card.edit_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=markup)
        else:
            await req.card.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except RetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.debug("edit failed: %s", exc)


async def _send_file(app: Application, chat_id: int, info: dict, file, filename: str | None, thumb, reply_to: int | None):
    t = info.get("track") or {}
    caption = texts.file_caption(info, app.bot.username)
    reply = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    is_document = (filename or info.get("filename", "")).lower().endswith(".opus")
    for attempt in range(3):
        try:
            if is_document:
                msg = await app.bot.send_document(
                    chat_id, document=file, filename=filename, thumbnail=thumb, caption=caption,
                    parse_mode=ParseMode.HTML, reply_parameters=reply,
                )
                return msg.document.file_id
            msg = await app.bot.send_audio(
                chat_id, audio=file, filename=filename, title=t.get("title"), performer=t.get("artist"),
                duration=info.get("duration"), thumbnail=thumb, caption=caption,
                parse_mode=ParseMode.HTML, reply_parameters=reply,
            )
            return (msg.audio or msg.document).file_id
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            if hasattr(file, "seek"):
                file.seek(0)
    raise DownloadError("ارسال به تلگرام ناموفق بود.")


async def send_track(app: Application, chat_id: int, track: Track, quality: str, reply_to: int | None) -> None:
    # A track that was sent before is forwarded by file_id: instant, nothing is downloaded or uploaded.
    entry = lookup(track.id, quality)
    if entry and entry.tg_file_id:
        try:
            await _send_file(app, chat_id, {**entry.info, "filename": entry.filename}, entry.tg_file_id, None, None, reply_to)
            return
        except BadRequest as exc:
            log.info("cached file_id rejected, uploading again: %s", exc)

    async with _semaphore(app):
        await app.bot.send_chat_action(chat_id, ChatAction.UPLOAD_DOCUMENT)
        result = await asyncio.to_thread(download, track, quality)

    size_mb = result.entry.size / 1024 / 1024
    if size_mb > settings.telegram_upload_limit_mb:
        raise DownloadError(texts.too_big(result.track.title, size_mb, settings.telegram_upload_limit_mb))

    thumb = await asyncio.to_thread(lambda: make_thumbnail(get_cover(result.track)))
    with open(result.path, "rb") as fh:
        file_id = await _send_file(app, chat_id, result.info, fh, result.filename, thumb, reply_to)
    cache.set_tg_file_id(result.entry.key, file_id)


async def run_request(app: Application, req: Request, quality: str) -> None:
    c = req.collection
    label = QUALITIES[quality].label
    tracks = c.tracks
    total = len(tracks)
    sent, failed = 0, []
    last_edit = 0.0
    try:
        if total == 1:
            await _edit_card(req, texts.single_progress(c, label))
        for i, track in enumerate(tracks, 1):
            if req.stop:
                break
            if total > 1 and time.monotonic() - last_edit > PROGRESS_EVERY:
                last_edit = time.monotonic()
                await _edit_card(req, texts.progress(c, i, total, track.title, label), stop_keyboard(req.id))
            try:
                await send_track(app, req.chat_id, track, quality, req.card.message_id if total == 1 and req.card else None)
                sent += 1
            except DownloadError as exc:
                failed.append((f"{track.title} — {track.artist}", str(exc)))
            except TelegramError as exc:
                log.warning("telegram error for %s: %s", track.id, exc)
                failed.append((f"{track.title} — {track.artist}", "خطای تلگرام"))
            except Exception:
                log.exception("unexpected error for %s", track.id)
                failed.append((f"{track.title} — {track.artist}", "خطای غیرمنتظره"))
        await _edit_card(req, texts.finished(c, sent, total, failed, req.stop, label))
        log.info("request %s finished: %d/%d sent, %d failed", req.id, sent, total, len(failed))
    finally:
        req.running = False
        _active_users(app).discard(req.user_id)
        _requests(app).pop(req.id, None)


# ---------- handlers ----------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        texts.start(settings.app_name, update.effective_user.first_name), parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(texts.HELP, parse_mode=ParseMode.HTML)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    text = msg.text or msg.caption or ""
    if "spotify" not in text.lower():
        if update.effective_chat.type == "private":
            await msg.reply_text(texts.NOT_A_LINK, parse_mode=ParseMode.HTML)
        return

    status = await msg.reply_text(texts.CHECKING)
    try:
        collection = await asyncio.to_thread(spotify.resolve, text)
    except spotify.SpotifyError as exc:
        await status.edit_text(f"❌ {exc}")
        return
    except Exception:
        log.exception("resolve failed")
        await status.edit_text("❌ خطای غیرمنتظره موقع خوندن لینک. دوباره امتحان کن.")
        return

    _prune(ctx.application)
    req = Request(id=secrets.token_urlsafe(6), user_id=update.effective_user.id,
                  chat_id=update.effective_chat.id, collection=collection)
    _requests(ctx.application)[req.id] = req

    caption = texts.collection_caption(collection)
    markup = quality_keyboard(req.id)
    try:
        if collection.cover_url:
            req.card = await msg.reply_photo(collection.cover_url, caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
        else:
            raise BadRequest("no cover")
    except BadRequest:
        req.card = await msg.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=markup)
    try:
        await status.delete()
    except TelegramError:
        pass


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, rid, *rest = (query.data or "").split(":")
    req = _requests(ctx.application).get(rid)
    if not req:
        await query.answer(texts.EXPIRED, show_alert=True)
        return
    if query.from_user.id != req.user_id:
        await query.answer(texts.NOT_YOURS, show_alert=True)
        return

    if action == "s":
        req.stop = True
        await query.answer(texts.STOPPING)
        return
    if req.running:
        await query.answer()
        return
    if action == "x":
        _requests(ctx.application).pop(rid, None)
        await query.answer()
        await _edit_card(req, texts.collection_caption(req.collection, footer=texts.CANCELLED))
        return
    if action == "b":
        await query.answer()
        await _edit_card(req, texts.collection_caption(req.collection), quality_keyboard(rid))
        return
    if action == "q" and rest and rest[0] == "best":
        await query.answer()
        await _edit_card(req, texts.collection_caption(req.collection, footer=texts.CHOOSE_FORMAT), format_keyboard(rid))
        return

    quality = rest[0] if rest else ""
    if quality not in QUALITIES:
        await query.answer(texts.EXPIRED, show_alert=True)
        return
    active = _active_users(ctx.application)
    if req.user_id in active:
        await query.answer(texts.BUSY, show_alert=True)
        return
    active.add(req.user_id)
    req.running = True
    log.info("request %s: %s %s (%d tracks) as %s", req.id, req.collection.kind, req.collection.id,
             len(req.collection.tracks), quality)
    await query.answer(texts.STARTED)
    ctx.application.create_task(run_request(ctx.application, req, quality), update=update)


def _prune(app: Application) -> None:
    now = time.time()
    reqs = _requests(app)
    for rid in [r for r, v in reqs.items() if not v.running and now - v.created > REQUEST_TTL]:
        reqs.pop(rid, None)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("update failed: %s", ctx.error, exc_info=ctx.error)


async def _cleanup_loop() -> None:
    while True:
        removed = await asyncio.to_thread(cache.cleanup, settings.keep_files_days)
        if removed:
            log.info("cleanup: removed %d old files", removed)
        await asyncio.sleep(6 * 3600)


async def post_init(app: Application) -> None:
    app.bot_data["semaphore"] = asyncio.Semaphore(settings.max_concurrent)
    await app.bot.set_my_commands([
        BotCommand("start", "شروع"),
        BotCommand("help", "راهنما و کیفیت‌ها"),
    ])
    # The application isn't running yet inside post_init, so schedule on the loop directly.
    app.bot_data["cleanup_task"] = asyncio.get_running_loop().create_task(_cleanup_loop())
    log.info("bot @%s is running", app.bot.username)


def build_app() -> Application:
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is empty. Put your bot token in the .env file.")
    builder = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .concurrent_updates(True)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(300)
        .media_write_timeout(600)
        .post_init(post_init)
    )
    if settings.proxy:
        builder = builder.proxy(settings.proxy).get_updates_proxy(settings.proxy)
    if settings.telegram_api_url:
        url = settings.telegram_api_url.rstrip("/")
        builder = builder.base_url(f"{url}/bot").base_file_url(f"{url}/file/bot").local_mode(True)
    app = builder.build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for problem in check_environment():
        log.warning(problem)
    build_app().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
