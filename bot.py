import os
import time
import hashlib
import re
import subprocess
import tempfile
import requests
import yt_dlp
from pyrogram import Client, filters, types
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "12345"))
API_HASH = os.environ.get("API_HASH", "your_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_token")

app = Client("hanime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- YOUR PROVIDED CODE (UNCHANGED LOGIC) ---
BASE = "https://hanime.tv/api/v8"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
BASE_HEADERS = {
    "User-Agent": UA, "Referer": "https://hanime.tv/", "Origin": "https://hanime.tv",
    "Accept": "application/json", "Content-Type": "application/json", "X-Signature-Version": "web2",
}
JS_PREAMBLE = """
delete globalThis.process;
var window = new Proxy({ top: { location: { origin: "https://hanime.tv" } }, addEventListener: (e, cb) => {} }, {
    set(o, k, v) { if (k == "ssignature" || k == "stime") console.log(k, v); o[k] = v; return true; }
});
globalThis.window = window;
"""
_vendor_script_cache = None

def make_headers(path: str) -> dict:
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
        f.write(script)
        tmp_path = f.name
    try:
        result = subprocess.run(["node", tmp_path], capture_output=True, text=True, timeout=15)
        creds = {l.split(" ")[0]: l.split(" ")[1].strip() for l in result.stdout.strip().split("\n") if " " in l}
        return creds.get("ssignature"), creds.get("stime")
    finally:
        if os.path.exists(tmp_path): os.unlink(tmp_path)

def get_streams(hv_id, sig, t):
    r = requests.get(f"https://hanime.tv/api/v8/guest/videos/{hv_id}/manifest", 
                     headers={**BASE_HEADERS, "X-Signature": sig, "X-Time": t})
    if r.status_code != 200: return []
    streams = []
    for server in r.json().get("videos_manifest", {}).get("servers", []):
        for s in server.get("streams", []):
            streams.append({"url": s['url'], "res": f"{s['height']}p", "height": s['height']})
    return sorted(streams, key=lambda x: x["height"], reverse=True)

# --- BOT HANDLERS ---

@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text(f"👋 Hello {message.from_user.first_name}!\n\nUse /search <name> to find videos.")

@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply_text("🔍 **Commands:**\n/search <query> - Search Hanime\n/start - Restart Bot")

@app.on_message(filters.command("search"))
async def search_cmd(client, message):
    query = " ".join(message.command[1:])
    if not query: return await message.reply("❌ Please provide a name. Example: `/search Naruto`")
    
    msg = await message.reply("🔍 Searching...")
    search_url = "https://search.htv-services.com/"
    data = {"search_text": query, "tags": [], "brands": [], "blacklist": [], "order_by": "created_at_unix", "ordering": "desc", "page": 0}
    try:
        r = requests.post(search_url, json=data)
        results = r.json().get("hits", [])
        if not results: return await msg.edit("❌ No results found.")

        buttons = []
        for hit in results[:10]: # Top 10 results
            h = eval(hit) if isinstance(hit, str) else hit
            buttons.append([InlineKeyboardButton(h['name'], callback_data=f"slug_{h['slug']}")])
        
        await msg.edit("✅ Select a result:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        await msg.edit(f"❌ Search Error: {e}")

@app.on_callback_query(filters.regex("^slug_"))
async def select_video(client, callback_query):
    slug = callback_query.data.split("_")[1]
    await callback_query.edit_message_text("🔓 Bypassing security & fetching qualities...")
    
    hv_id = get_hv_id(slug)
    sig, t = generate_credentials()
    streams = get_streams(hv_id, sig, t)
    
    if not streams: return await callback_query.edit_message_text("❌ No streams found.")

    buttons = []
    # We store the slug and height in callback to identify the link later
    for s in streams:
        buttons.append([InlineKeyboardButton(f"🎬 {s['res']}", callback_data=f"dl_{slug}_{s['height']}")])
    
    await callback_query.edit_message_text(f"📺 **Video:** {slug}\nChoose Quality:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex("^dl_"))
async def download_handler(client, callback_query):
    _, slug, height = callback_query.data.split("_")
    await callback_query.edit_message_text("📥 Processing download... please wait.")
    
    hv_id = get_hv_id(slug)
    sig, t = generate_credentials()
    streams = get_streams(hv_id, sig, t)
    selected = next((s for s in streams if str(s['height']) == height), None)
    
    if not selected: return await callback_query.edit_message_text("❌ Link expired.")

    file_path = f"{slug}_{height}p.mp4"
    ydl_opts = {
        'outtmpl': file_path,
        'http_headers': {'Referer': 'https://hanime.tv/', 'Origin': 'https://hanime.tv'},
        'format': 'best', 'quiet': True, 'no_warnings': True
    }

    try:
        await callback_query.edit_message_text("⏳ Downloading m3u8 via yt-dlp...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([selected['url']])
        
        await callback_query.edit_message_text("📤 Uploading to Telegram...")
        await client.send_video(
            callback_query.message.chat.id, 
            video=file_path, 
            caption=f"✅ **{slug}**\nQuality: {height}p"
        )
        os.remove(file_path) # Clean up
    except Exception as e:
        await callback_query.edit_message_text(f"❌ Error: {str(e)}")

print("Bot Started!")
app.run()
