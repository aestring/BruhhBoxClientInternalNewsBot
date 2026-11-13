"""
BruhhBox News Bot
Reads messages from one Discord channel and serves them as a news page.

  Title       -> first line of the message (or embed title)
  Description -> the rest of the message (or embed description)
  Thumbnail   -> first image attachment (or embed image)
  Date        -> when the message was posted
  Link        -> first link found in the message (or embed url)
"""
import asyncio
import datetime
import html
import os
import re
from pathlib import Path

import discord
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["NEWS_CHANNEL_ID"])
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
NEWS_LIMIT = int(os.getenv("NEWS_LIMIT", "50"))

MEDIA_DIR = Path(__file__).parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)

CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
TIMESTAMP_TAG = re.compile(r"<t:(\d+)(?::[a-zA-Z])?>")
MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
BARE_URL = re.compile(r"https?://[^\s<>]+")


def fmt_date(dt: datetime.datetime) -> str:
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def clean_text(text: str) -> str:
    text = CUSTOM_EMOJI.sub("", text)
    text = TIMESTAMP_TAG.sub(
        lambda m: fmt_date(datetime.datetime.fromtimestamp(int(m.group(1)), datetime.timezone.utc)),
        text,
    )
    return text.strip()


def to_html(text: str) -> str:
    """Escape text and support **bold** + line breaks."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"\n{2,}", "\n\n", out)
    return out.replace("\n", "<br>")


async def parse_message(message: discord.Message) -> dict | None:
    embed = message.embeds[0] if message.embeds else None

    # ---- title + description ----
    if embed and embed.title:
        title = embed.title
        body = embed.description or ""
    else:
        text = clean_text(message.clean_content)
        lines = text.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            return None
        title = re.sub(r"^[#>\s]+", "", lines[0]).replace("*", "").replace("_", "").strip()
        body = "\n".join(lines[1:])

    # ---- link ----
    link, link_label = "", ""
    m = MD_LINK.search(body)
    if m:
        link_label, link = m.group(1), m.group(2)
        body = body.replace(m.group(0), "")
    else:
        m = BARE_URL.search(body)
        if m:
            link = m.group(0)
            body = body.replace(link, "")
    if not link and embed and embed.url:
        link = embed.url
    body = clean_text(body)

    # ---- thumbnail (download it; Discord CDN links expire) ----
    thumbnail = ""
    for att in message.attachments:
        if (att.content_type or "").startswith("image/"):
            ext = Path(att.filename).suffix or ".png"
            filename = f"{message.id}_{att.id}{ext}"
            path = MEDIA_DIR / filename
            if not path.exists():
                await att.save(path)
            thumbnail = f"/media/{filename}"
            break
    if not thumbnail and embed:
        if embed.image and embed.image.url:
            thumbnail = embed.image.url
        elif embed.thumbnail and embed.thumbnail.url:
            thumbnail = embed.thumbnail.url

    return {
        "id": message.id,
        "timestamp": message.created_at.isoformat(),
        "date": fmt_date(message.created_at),
        "title": title,
        "description": body,
        "thumbnail": thumbnail,
        "link": link,
        "link_label": link_label,
    }


# ---------------------------------------------------------------- web UI

CSS = """
body{margin:0;padding:32px 16px;background:#0f1115;color:#e6e6e6;font-family:system-ui,Segoe UI,Roboto,sans-serif}
h1{text-align:center;margin:0 0 28px}
.feed{max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:20px}
.news-item{background:#181b22;border:1px solid #262a33;border-radius:12px;padding:20px;overflow:hidden}
.thumbnail{display:block;width:100%;max-height:320px;object-fit:cover;border-radius:8px;margin-bottom:14px}
.date{display:inline-block;background:#5865f2;color:#fff;font-size:12px;padding:3px 10px;border-radius:999px}
.title{margin:10px 0 8px;font-size:22px}
.description{margin:0 0 12px;line-height:1.55;color:#c7c9d1}
.read-more{color:#8ea1ff;text-decoration:none;font-weight:600}
.read-more:hover{text-decoration:underline}
.empty{text-align:center;color:#888}
"""


def render_item(n: int, item: dict) -> str:
    parts = [f"<!-- {n} Page News -->", '    <div class="news-item">']
    if item["thumbnail"]:
        parts.append(
            f'        <img class="thumbnail" src="{html.escape(item["thumbnail"], quote=True)}" '
            f'alt="{html.escape(item["title"], quote=True)}" />'
        )
    parts.append(f'        <span class="date">{html.escape(item["date"])}</span>')
    parts.append(f'        <h2 class="title">{html.escape(item["title"])}</h2>')
    parts.append(f'        <p class="description">{to_html(item["description"])}</p>')
    if item["link"]:
        label = html.escape(item["link_label"] or "Read more")
        parts.append(
            f'        <a class="read-more" href="{html.escape(item["link"], quote=True)}" '
            f'target="_blank" rel="noopener">{label} &rarr;</a>'
        )
    parts.append("    </div>")
    return "\n".join(parts)


def render_page(items: list[dict]) -> str:
    body = (
        "\n".join(render_item(i, it) for i, it in enumerate(items, 1))
        if items
        else '<p class="empty">No news yet.</p>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="30">
    <title>BruhhBox News Feed</title>
    <style>{CSS}</style>
</head>
<body>
    <h1>BruhhBox News Feed</h1>
    <div class="feed">
{body}
    </div>
</body>
</html>"""


# ------------------------------------------------------------------- bot

class NewsBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # also enable in the Developer Portal
        super().__init__(intents=intents)
        self.news: dict[int, dict] = {}

    def sorted_news(self) -> list[dict]:
        return sorted(self.news.values(), key=lambda i: i["timestamp"], reverse=True)[:NEWS_LIMIT]

    async def setup_hook(self):
        app = web.Application()
        app.router.add_get("/", self.handle_index)
        app.router.add_get("/api/news", self.handle_api)
        app.router.add_static("/media", MEDIA_DIR)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, WEB_HOST, WEB_PORT).start()
        print(f"Web UI running at http://localhost:{WEB_PORT}")

    async def handle_index(self, request):
        return web.Response(text=render_page(self.sorted_news()), content_type="text/html")

    async def handle_api(self, request):
        return web.json_response(self.sorted_news())

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)
        async for msg in channel.history(limit=NEWS_LIMIT):
            item = await parse_message(msg)
            if item:
                self.news[msg.id] = item
        print(f"Loaded {len(self.news)} news items")

    async def on_message(self, message: discord.Message):
        if message.channel.id != CHANNEL_ID:
            return
        item = await parse_message(message)
        if item:
            self.news[message.id] = item

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if payload.channel_id != CHANNEL_ID:
            return
        channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)
        try:
            msg = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        item = await parse_message(msg)
        if item:
            self.news[msg.id] = item
        else:
            self.news.pop(msg.id, None)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.channel_id == CHANNEL_ID:
            self.news.pop(payload.message_id, None)


if __name__ == "__main__":
    NewsBot().run(TOKEN)
