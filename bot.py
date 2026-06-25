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
    try:
        HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever()
    except Exception: pass

# --- HANIME LOGIC ---
BASE = "https://hanime.tv/api/v8"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"

def get_nav_headers():
    return {
        "User-Agent": UA,
        "Accept": "application/json",
        "Origin": "https://hanime.tv",
        "Referer": "https://hanime.tv/",
        "X-Signature-Version": "web2"
    }

def get_hv_id(slug: str):
    try:
        # We use the video endpoint to get the ID from the slug
        r = requests.get(f"{BASE}/video?id={slug}", headers=get_nav_headers(), timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        return str(data.get("hentai_video", {}).get("id"))
    except: return None

def generate_credentials():
    try:
        r = requests.get("https://hanime.tv/", headers={"User-Agent": UA}, timeout=10)
        match = re.search(r'src="(https://hanime-cdn\.com/js/vendor\.[^"]+)"', r.text)
        if not match: return None, None
        
        vendor_js = requests.get(match.group(1), headers={"User-Agent": UA}).text
        # Logic to extract ssignature and stime via Node
        js_preamble = "delete globalThis.process; var window = { top: { location: { origin: 'https://hanime.tv' } }, addEventListener: () => {} }; globalThis.window = window;"
        script = js_preamble + "\n" + vendor_js
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(script)
            tmp_path = f.name
        
        res = subprocess.run(["node", "-e", script + "; console.log(window.ssignature); console.log(window.stime);"], capture_output=True, text=True, timeout=10)
        output = res.stdout.strip().split("\n")
        os.unlink(tmp_path)
        if len(output) >= 2:
            return output[0].strip(), output[1].strip()
    except: pass
    return None, None

def get_streams(hv_id, sig, t):
    if not hv_id or not sig: return []
    url = f"{BASE}/guest/videos/{hv_id}/manifest"
    headers = get_nav_headers()
    headers.update({"X-Signature": sig, "X-Time": t})
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200: return []
        data = r.json()
        streams = []
        for server in data.get("videos_manifest", {}).get("servers", []):
            for s in server.get("streams", []):
                if s.get("url"):
                    streams.append({"url": s['url'], "res": f"{s['height']}p", "height": s['height']})
        return sorted(streams, key=lambda x: x["height"], reverse=True)
    except: return []

# --- BOT HANDLERS ---
app = Client("hanime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start(c, m):
    await m.reply_text("👋 **Hanime Downloader**\n\n- Send `/search <name>`\n- Or paste a Hanime link!")

@app.on_message(filters.command("search"))
async def search(c, m):
    query = " ".join(m.command[1:])
    if not query: return await m.reply("Usage: `/search Naruto`")
    
    msg = await m.reply("🔍 Searching...")
    try:
        # Hanime Search API
        search_url = "https://search.htv-services.com/"
        data = {"search_text": query, "tags": [], "brands": [], "blacklist": [], "order_by": "created_at_unix", "ordering": "desc", "page": 0}
        r = requests.post(search_url, json=data, headers={"User-Agent": UA}, timeout=10)
        
        if r.status_code != 200:
            return await msg.edit(f"❌ Search blocked (Code {r.status_code}). Try again later.")

        results = r.json().get("hits", [])
        if not results: return await msg.edit("❌ No results found.")

        btn = []
        # Result hits can be strings or dicts depending on the API state
        for h in results[:8]:
            item = json.loads(h) if isinstance(h, str) else h
            btn.append([InlineKeyboardButton(item['name'], callback_data=f"slug_{item['slug']}")])
        
        await msg.edit("✅ Search Results:", reply_markup=InlineKeyboardMarkup(btn))
    except Exception as e: 
        await msg.edit(f"❌ Search Error: `{str(e)[:50]}`")

@app.on_message(filters.regex(r"hanime\.tv/videos/hentai/(.+)"))
async def link_handler(c, m):
    slug = m.matches[0].group(1).split('?')[0].split('/')[0]
    await process_slug(c, m, slug)

async def process_slug(c, m, slug):
    wait = await m.reply("🔓 Cracking protection...")
    hv_id = get_hv_id(slug)
    if not hv_id:
        return await wait.edit("❌ Could not find Video ID.")
        
    sig, t = generate_credentials()
    if not sig:
        return await wait.edit("❌ Failed to generate security signature.")
        
    streams = get_streams(hv_id, sig, t)
    if not streams:
        return await wait.edit("❌ No streams found. Video might be premium or blocked.")
    
    # Filter unique resolutions
    seen = set()
    unique_streams = []
    for s in streams:
        if s['res'] not in seen:
            unique_streams.append(s)
            seen.add(s['res'])

    btn = [[InlineKeyboardButton(f"🎬 {s['res']}", callback_data=f"q_{slug}_{s['height']}")] for s in unique_streams]
    await wait.edit(f"📺 **Video:** `{slug}`\nSelect quality:", reply_markup=InlineKeyboardMarkup(btn))

@app.on_callback_query(filters.regex("^slug_"))
async def cb_slug(c, cb):
    slug = cb.data.split("_")[1]
    await process_slug(c, cb.message, slug)

@app.on_callback_query(filters.regex("^q_"))
async def cb_quality(c, cb):
    _, slug, height = cb.data.split("_")
    btn = [
        [InlineKeyboardButton("📥 Download Video", callback_data=f"action_dl_{slug}_{height}")],
        [InlineKeyboardButton("🔗 Get Direct Link", callback_data=f"action_link_{slug}_{height}")]
    ]
    await cb.edit_message_text(f"Quality: **{height}p**\nAction:", reply_markup=InlineKeyboardMarkup(btn))

@app.on_callback_query(filters.regex("^action_"))
async def cb_action(c, cb):
    _, action, slug, height = cb.data.split("_")
    await cb.answer("Processing...")
    
    hv_id = get_hv_id(slug)
    sig, t = generate_credentials()
    streams = get_streams(hv_id, sig, t)
    sel = next((s for s in streams if str(s['height']) == height), None)

    if not sel: return await cb.edit_message_text("❌ Link expired. Search again.")

    if action == "link":
        await cb.edit_message_text(f"✅ **Direct Link ({height}p):**\n\n`{sel['url']}`\n\n_Note: Links expire quickly._")
    
    elif action == "dl":
        msg = await cb.edit_message_text("📥 Downloading to server...")
        file_path = f"{slug}_{height}p.mp4"
        ydl_opts = {
            'outtmpl': file_path, 
            'format': 'best',
            'http_headers': {'Referer': 'https://hanime.tv/', 'User-Agent': UA},
            'quiet': True,
            'nocheckcertificate': True
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([sel['url']])
            await msg.edit("📤 Uploading to Telegram...")
            await c.send_video(cb.message.chat.id, video=file_path, caption=f"✅ {slug} ({height}p)")
            os.remove(file_path)
            await msg.delete()
        except Exception as e: 
            await msg.edit(f"❌ Error: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    app.run()
