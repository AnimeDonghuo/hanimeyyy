import os
import time
import hashlib
import re
import subprocess
import tempfile
import requests
import yt_dlp
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "26826540"))
API_HASH = os.environ.get("API_HASH", "32d454f51fc7b3b3c7d51c4f80f628b5")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_token")
PORT = int(os.environ.get("PORT", 8080)) # Koyeb uses this

# --- DUMMY SERVER FOR KOYEB HEALTH CHECK ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running")

def run_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    server.serve_forever()

# --- YOUR LOGIC (UNCHANGED) ---
BASE = "https://hanime.tv/api/v8"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
BASE_HEADERS = {"User-Agent": UA, "Referer": "https://hanime.tv/", "Origin": "https://hanime.tv", "Accept": "application/json", "Content-Type": "application/json", "X-Signature-Version": "web2"}
JS_PREAMBLE = "delete globalThis.process; var window = new Proxy({ top: { location: { origin: 'https://hanime.tv' } }, addEventListener: (e, cb) => {} }, { set(o, k, v) { if (k == 'ssignature' || k == 'stime') console.log(k, v); o[k] = v; return true; } }); globalThis.window = window;"
_vendor_script_cache = None

def make_headers(path: str):
    t = int(time.time()); sig = hashlib.sha1(f"{path}{t}".encode()).hexdigest()
    return {**BASE_HEADERS, "X-Time": str(t), "X-Signature": sig}

def get_hv_id(slug: str):
    r = requests.get(f"{BASE}/video", params={"id": slug}, headers=make_headers("/api/v8/video"))
    if r.status_code == 200:
        data = r.json()
        hv = data.get("hentai_video", data)
        return str(hv.get("id") or hv.get("hv_id"))
    return None

def get_vendor_script():
    global _vendor_script_cache
    if _vendor_script_cache: return _vendor_script_cache
    r = requests.get("https://hanime.tv/", headers={"User-Agent": UA, "Accept": "text/html"})
    match = re.search(r'src="(https://hanime-cdn\.com/js/vendor\.[^"]+)"', r.text)
    if not match: return None
    r2 = requests.get(match.group(1), headers={"User-Agent": UA, "Referer": "https://hanime.tv/"})
    _vendor_script_cache = r2.text
    return _vendor_script_cache

def generate_credentials():
    vendor_js = get_vendor_script()
    if not vendor_js: return None, None
    script = JS_PREAMBLE + "\n" + vendor_js
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
        f.write(script); tmp_path = f.name
    try:
        result = subprocess.run(["node", tmp_path], capture_output=True, text=True, timeout=15)
        creds = {l.split(" ")[0]: l.split(" ")[1].strip() for l in result.stdout.strip().split("\n") if " " in l}
        return creds.get("ssignature"), creds.get("stime")
    finally:
        if os.path.exists(tmp_path): os.unlink(tmp_path)

def get_streams(hv_id, sig, t):
    r = requests.get(f"https://hanime.tv/api/v8/guest/videos/{hv_id}/manifest", headers={**BASE_HEADERS, "X-Signature": sig, "X-Time": t})
    if r.status_code != 200: return []
    streams = []
    try:
        for server in r.json().get("videos_manifest", {}).get("servers", []):
            for s in server.get("streams", []):
                streams.append({"url": s['url'], "res": f"{s['height']}p", "height": s['height']})
    except: pass
    return sorted(streams, key=lambda x: x["height"], reverse=True)

# --- BOT INTERFACE ---
app = Client("hanime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start(c, m):
    await m.reply_text("👋 **Hanime Downloader Bot**\n\nCommands:\n/search <name>\n/help")

@app.on_message(filters.command("help"))
async def help(c, m):
    await m.reply_text("Simply use `/search Naruto` to find videos and download in various qualities.")

@app.on_message(filters.command("search"))
async def search(c, m):
    query = " ".join(m.command[1:])
    if not query: return await m.reply("Usage: `/search name`")
    
    msg = await m.reply("🔍 Searching...")
    try:
        data = {"search_text": query, "tags": [], "brands": [], "blacklist": [], "order_by": "created_at_unix", "ordering": "desc", "page": 0}
        r = requests.post("https://search.htv-services.com/", json=data)
        results = r.json().get("hits", [])
        if not results: return await msg.edit("❌ No results.")

        btn = []
        for h in results[:8]:
            res = eval(h) if isinstance(h, str) else h
            btn.append([InlineKeyboardButton(res['name'], callback_data=f"slug_{res['slug']}")])
        await msg.edit("✅ Select a result:", reply_markup=InlineKeyboardMarkup(btn))
    except Exception as e: await msg.edit(f"❌ Error: {e}")

@app.on_callback_query(filters.regex("^slug_"))
async def q_list(c, cb):
    slug = cb.data.split("_")[1]
    await cb.answer("Fetching Qualities...")
    hv_id = get_hv_id(slug)
    sig, t = generate_credentials()
    streams = get_streams(hv_id, sig, t)
    if not streams: return await cb.edit_message_text("❌ No streams found.")
    
    btn = [[InlineKeyboardButton(f"🎬 {s['res']}", callback_data=f"dl_{slug}_{s['height']}")] for s in streams]
    await cb.edit_message_text(f"📺 **Video:** `{slug}`\nSelect quality:", reply_markup=InlineKeyboardMarkup(btn))

@app.on_callback_query(filters.regex("^dl_"))
async def download(c, cb):
    _, slug, height = cb.data.split("_")
    await cb.edit_message_text("📥 Downloading... This may take a while.")
    
    hv_id = get_hv_id(slug)
    sig, t = generate_credentials()
    streams = get_streams(hv_id, sig, t)
    sel = next((s for s in streams if str(s['height']) == height), None)
    
    file_path = f"{slug}_{height}p.mp4"
    ydl_opts = {'outtmpl': file_path, 'http_headers': {'Referer': 'https://hanime.tv/', 'Origin': 'https://hanime.tv'}, 'format': 'best', 'quiet': True}

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([sel['url']])
        await cb.edit_message_text("📤 Uploading...")
        await c.send_video(cb.message.chat.id, video=file_path, caption=f"✅ {slug} ({height}p)")
        os.remove(file_path)
    except Exception as e: await cb.edit_message_text(f"❌ Error: {e}")

if __name__ == "__main__":
    # Start Health check in background
    threading.Thread(target=run_health_server, daemon=True).start()
    print("Bot is starting...")
    app.run()
