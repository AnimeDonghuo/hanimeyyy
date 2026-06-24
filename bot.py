import os
import time
import hashlib
import re
import subprocess
import tempfile
import requests
import yt_dlp
import threading
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- CONFIG ---
API_ID = int(os.environ.get("API_ID", "26826540"))
API_HASH = os.environ.get("API_HASH", "32d454f51fc7b3b3c7d51c4f80f628b5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_token")
PORT = int(os.environ.get("PORT", 8080))

# --- KOYEB HEALTH CHECK ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running")

def run_health_server():
    HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever()

# --- HANIME LOGIC (STRICTLY FROM YOUR CODE) ---
BASE = "https://hanime.tv/api/v8"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
BASE_HEADERS = {"User-Agent": UA, "Referer": "https://hanime.tv/", "Origin": "https://hanime.tv", "Accept": "application/json", "Content-Type": "application/json", "X-Signature-Version": "web2"}
JS_PREAMBLE = "delete globalThis.process; var window = new Proxy({ top: { location: { origin: 'https://hanime.tv' } }, addEventListener: (e, cb) => {} }, { set(o, k, v) { if (k == 'ssignature' || k == 'stime') console.log(k, v); o[k] = v; return true; } }); globalThis.window = window;"

def make_headers(path: str):
    t = int(time.time()); sig = hashlib.sha1(f"{path}{t}".encode()).hexdigest()
    return {**BASE_HEADERS, "X-Time": str(t), "X-Signature": sig}

def get_hv_id(slug: str):
    try:
        r = requests.get(f"{BASE}/video", params={"id": slug}, headers=make_headers("/api/v8/video"))
        data = r.json()
        hv = data.get("hentai_video", data)
        return str(hv.get("id") or hv.get("hv_id"))
    except: return None

def generate_credentials():
    r = requests.get("https://hanime.tv/", headers={"User-Agent": UA})
    match = re.search(r'src="(https://hanime-cdn\.com/js/vendor\.[^"]+)"', r.text)
    if not match: return None, None
    vendor_js = requests.get(match.group(1), headers={"User-Agent": UA}).text
    script = JS_PREAMBLE + "\n" + vendor_js
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(script); tmp_path = f.name
    try:
        res = subprocess.run(["node", tmp_path], capture_output=True, text=True, timeout=15)
        creds = {l.split(" ")[0]: l.split(" ")[1].strip() for l in res.stdout.strip().split("\n") if " " in l}
        return creds.get("ssignature"), creds.get("stime")
    finally:
        if os.path.exists(tmp_path): os.unlink(tmp_path)

def get_streams(hv_id, sig, t):
    r = requests.get(f"{BASE}/guest/videos/{hv_id}/manifest", headers={**BASE_HEADERS, "X-Signature": sig, "X-Time": t})
    streams = []
    if r.status_code == 200:
        for server in r.json().get("videos_manifest", {}).get("servers", []):
            for s in server.get("streams", []):
                streams.append({"url": s['url'], "res": f"{s['height']}p", "height": s['height']})
    return sorted(streams, key=lambda x: x["height"], reverse=True)

# --- BOT HANDLERS ---
app = Client("hanime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start(c, m):
    await m.reply_text("👋 **Hanime Downloader**\n\n- Send `/search <name>`\n- Or paste a Hanime link directly!")

@app.on_message(filters.command("search"))
async def search(c, m):
    query = " ".join(m.command[1:])
    if not query: return await m.reply("Usage: `/search Naruto`")
    
    msg = await m.reply("🔍 Searching Hanime...")
    try:
        data = {"search_text": query, "tags": [], "brands": [], "blacklist": [], "order_by": "created_at_unix", "ordering": "desc", "page": 0}
        r = requests.post("https://search.htv-services.com/", json=data)
        hits = r.json().get("hits", [])
        if not hits: return await msg.edit("❌ No results found.")

        btn = []
        for h in hits[:8]:
            res = json.loads(h) if isinstance(h, str) else h
            btn.append([InlineKeyboardButton(res['name'], callback_data=f"slug_{res['slug']}")])
        await msg.edit("✅ Search Results:", reply_markup=InlineKeyboardMarkup(btn))
    except Exception as e: await msg.edit(f"❌ Search Error: {e}")

# Handle Direct Links
@app.on_message(filters.regex(r"hanime\.tv/videos/hentai/(.+)"))
async def link_handler(c, m):
    slug = m.matches[0].group(1).split('?')[0]
    await process_slug(c, m, slug)

async def process_slug(c, m, slug):
    wait = await m.reply("🔓 Fetching qualities...")
    hv_id = get_hv_id(slug)
    sig, t = generate_credentials()
    streams = get_streams(hv_id, sig, t)
    
    if not streams: return await wait.edit("❌ Failed to get streams.")
    
    btn = [[InlineKeyboardButton(f"🎬 {s['res']}", callback_data=f"q_{slug}_{s['height']}")] for s in streams]
    await wait.edit(f"📺 **Video:** `{slug}`\nSelect quality:", reply_markup=InlineKeyboardMarkup(btn))

@app.on_callback_query(filters.regex("^slug_"))
async def cb_slug(c, cb):
    slug = cb.data.split("_")[1]
    await process_slug(c, cb.message, slug)
    await cb.answer()

@app.on_callback_query(filters.regex("^q_"))
async def cb_quality(c, cb):
    _, slug, height = cb.data.split("_")
    # Show Download vs Link options
    btn = [
        [InlineKeyboardButton("📥 Download Video", callback_data=f"action_dl_{slug}_{height}")],
        [InlineKeyboardButton("🔗 Get M3U8 Link", callback_data=f"action_link_{slug}_{height}")]
    ]
    await cb.edit_message_text(f"Selected: **{height}p**\nWhat do you want to do?", reply_markup=InlineKeyboardMarkup(btn))

@app.on_callback_query(filters.regex("^action_"))
async def cb_action(c, cb):
    data = cb.data.split("_")
    action = data[1]
    slug = data[2]
    height = data[3]
    
    await cb.edit_message_text("⚙️ Processing...")
    
    hv_id = get_hv_id(slug)
    sig, t = generate_credentials()
    streams = get_streams(hv_id, sig, t)
    sel = next((s for s in streams if str(s['height']) == height), None)

    if not sel: return await cb.edit_message_text("❌ Link expired.")

    if action == "link":
        await cb.edit_message_text(f"✅ **M3U8 Link ({height}p):**\n\n`{sel['url']}`")
    
    elif action == "dl":
        await cb.edit_message_text("📥 Downloading... please wait.")
        file_path = f"{slug}_{height}p.mp4"
        ydl_opts = {
            'outtmpl': file_path, 
            'http_headers': {'Referer': 'https://hanime.tv/', 'Origin': 'https://hanime.tv'},
            'format': 'best', 'quiet': True
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([sel['url']])
            await cb.edit_message_text("📤 Uploading to Telegram...")
            await c.send_video(cb.message.chat.id, video=file_path, caption=f"✅ {slug} ({height}p)")
            os.remove(file_path)
        except Exception as e: await cb.edit_message_text(f"❌ Error: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    app.run()
