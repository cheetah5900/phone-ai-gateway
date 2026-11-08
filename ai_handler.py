import os
import sys
import time
import subprocess
import asyncio
import json
import re
import urllib.request
import urllib.parse
from config import (
    FFMPEG_BIN, WHISPER_BIN, WHISPER_MODEL, WHISPER_LANG,
    DICTATE_TO_MACOS, NOTIFY_MACOS, SPEAK_RESPONSE,
    TTS_ENGINE, TTS_VOICE_EDGE, TTS_VOICE_MACOS,
    GEMINI_API_KEY, OPENAI_API_KEY,
    READY_TONE_STYLE, READY_TONE_VOLUME
)

def smart_refine_text(text: str) -> str:
    """Fast, local in-memory phonetic & contextual speech refiner (0ms, no API, no chat windows)."""
    if not text:
        return ""

    replacements = [
        (r'\bกดสอบ\b', 'ทดสอบ'),
        (r'\bกบสอบ\b', 'ทดสอบ'),
        (r'^กดสอบ', 'ทดสอบ'),
        (r'พิม(?=[ก-ฮ\s]|$)', 'พิมพ์'),
        (r'พีม(?=[ก-ฮ\s]|$)', 'พิมพ์'),
        (r'โทรเศษ', 'โทรศัพท์'),
        (r'โทรสับ', 'โทรศัพท์'),
        (r'เค้าความ', 'ข้อความ'),
        (r'เค้ารับ', 'ข้อความ'),
        (r'เคอเซ่อ', 'เคอร์เซอร์'),
        (r'เคอร์เซ่อ', 'เคอร์เซอร์'),
        (r'มักมัก', 'มากๆ'),
        (r'เส็ด', 'เสร็จ'),
        (r'เทียง', 'เที่ยง'),
        (r'เรย', 'เลย'),
        (r'\bคับ\b', 'ครับ'),
        (r'\bจิง\b', 'จริง'),
        (r'\bก้อ\b', 'ก็'),
        (r'ป่าว', 'เปล่า'),
        (r'อยุ่', 'อยู่'),
        (r'มั้ย', 'ไหม'),
        (r'ไม๊', 'ไหม'),
    ]

    out = text
    for pattern, rep in replacements:
        out = re.sub(pattern, rep, out)
    return out

def transcribe(raw_mulaw_bytes: bytes) -> str:
    """Convert raw 8kHz mu-law audio to 16kHz WAV and run whisper-cli."""
    if not raw_mulaw_bytes or len(raw_mulaw_bytes) < 8000 * 0.3:
        return ""

    raw_path = "/tmp/phone_input.raw"
    wav_path = "/tmp/phone_input.wav"

    with open(raw_path, "wb") as f:
        f.write(raw_mulaw_bytes)

    # Convert to 16kHz WAV
    try:
        subprocess.run([
            FFMPEG_BIN, "-y",
            "-f", "mulaw", "-ar", "8000", "-ac", "1",
            "-i", raw_path,
            "-ar", "16000",
            wav_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception as e:
        print(f"[AI] Error converting audio with ffmpeg: {e}")
        return ""

    model_to_use = WHISPER_MODEL
    small_model = os.path.expanduser("~/.cache/whisper/ggml-small.bin")
    if os.path.exists(small_model):
        model_to_use = small_model

    try:
        cmd = [
            WHISPER_BIN,
            "-m", model_to_use,
            "-l", WHISPER_LANG,
            "-nt",
            "-f", wav_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        text = res.stdout.strip()
        text = re.sub(r"\[.*?\]|\(.*?\)", "", text).strip()
        return text
    except Exception as e:
        print(f"[AI] Error running whisper-cli: {e}")
        return ""

def dictate_to_macos(text: str):
    """Pastes text EXACTLY ONCE at the active cursor in the focused macOS app."""
    if not text:
        return
    print(f"[Dictation] ✍️ Pasting to active cursor: '{text}'")

    # 1. Put text on macOS system clipboard natively via pbcopy
    paste_content = text + " "
    try:
        subprocess.run(["pbcopy"], input=paste_content.encode("utf-8"), check=True)
    except Exception as e:
        print(f"[Dictation] pbcopy error: {e}")
        return

    # Brief delay for clipboard buffer synchronization
    time.sleep(0.04)

    # 2. Simulate Cmd+V using key code 9 (physical 'V' key, language-independent)
    # Target the frontmost application process specifically - ONCE only!
    script = '''
    tell application "System Events"
        set frontApp to first application process whose frontmost is true
        set frontmost of frontApp to true
        key code 9 using command down
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", script], check=True)
    except Exception as e:
        print(f"[Dictation] osascript paste error: {e}")

    # Optional notification only if explicitly enabled
    if NOTIFY_MACOS:
        safe_text = text.replace('\\', '\\\\').replace('"', '\\"')
        notif_script = f'''display notification "{safe_text}" with title "📞 Phone Dictation"'''
        try:
            subprocess.run(["osascript", "-e", notif_script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except:
            pass

def ask_ai(prompt: str) -> str:
    """Generate a response only if there is an AI query or question. Never say 'พิมพ์ข้อความเรียบร้อยแล้วครับ'."""
    if not prompt:
        return ""

    p_lower = prompt.lower().strip()
    is_question = any(q in p_lower for q in ["อะไร", "ทำไม", "ยังไง", "อย่างไร", "ไหม", "หรือเปล่า", "กี่โมง", "เท่าไหร่", "ช่วย", "อธิบาย"])

    if not is_question:
        return ""

    # Check for Gemini API Key if user provided one in .env (pure background HTTPS call, no chat tabs!)
    gemini_key = GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
            payload = {
                "contents": [{
                    "parts": [{
                        "text": f"คุณคือผู้ช่วยเสียงตอบรับทางโทรศัพท์ จงตอบคำถามต่อไปนี้เป็นภาษาไทยให้สั้นกระชับที่สุด ไม่เกิน 1-2 ประโยค เพื่ออ่านออกเสียงผ่านหูโทรศัพท์:\n\n{prompt}"
                    }]
                }]
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                ans = data['candidates'][0]['content']['parts'][0]['text'].strip()
                return ans
        except Exception as e:
            print(f"[AI] Gemini API error: {e}")

    # Check for OpenAI API Key
    openai_key = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "คุณคือผู้ช่วย AI ผ่านทางโทรศัพท์ จงตอบเป็นภาษาไทยให้สั้นกระชับที่สุด ไม่เกิน 1-2 ประโยค เพื่ออ่านออกเสียงผ่านหูโทรศัพท์"},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 100
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode('utf-8'),
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {openai_key}'
                },
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data['choices'][0]['message']['content'].strip()
        except Exception as e:
            print(f"[AI] OpenAI API error: {e}")

    # Built-in quick answers for basic questions
    now = time.localtime()
    if any(w in p_lower for w in ["กี่โมง", "เวลา"]):
        return f"ขณะนี้เวลา {now.tm_hour} นาฬิกา {now.tm_min} นาทีครับ"
    elif any(w in p_lower for w in ["วันที่", "วันอะไร"]):
        months = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
        return f"วันนี้วันที่ {now.tm_mday} เดือน {months[now.tm_mon-1]} ครับ"

    return ""

async def _synthesize_edge_tts(text: str, mp3_path: str):
    import edge_tts
    comm = edge_tts.Communicate(text, TTS_VOICE_EDGE)
    await comm.save(mp3_path)

def synthesize_speech(text: str) -> bytes:
    """Convert text to 8kHz mu-law audio bytes for telephone playback."""
    if not text:
        return b""

    mp3_path = "/tmp/phone_tts.mp3"
    raw_path = "/tmp/phone_tts.raw"

    success = False
    if TTS_ENGINE == "edge-tts":
        try:
            asyncio.run(_synthesize_edge_tts(text, mp3_path))
            success = os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0
        except Exception as e:
            print(f"[TTS] Edge-TTS error, falling back to macOS say: {e}")
            success = False

    if not success:
        aiff_path = "/tmp/phone_tts.aiff"
        try:
            subprocess.run(["say", "-v", TTS_VOICE_MACOS, "-o", aiff_path, text], check=True)
            mp3_path = aiff_path
            success = True
        except Exception as e:
            print(f"[TTS] macOS say error: {e}")
            return b""

    try:
        subprocess.run([
            FFMPEG_BIN, "-y",
            "-i", mp3_path,
            "-ar", "8000", "-ac", "1",
            "-f", "mulaw",
            raw_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        with open(raw_path, "rb") as f:
            return f.read()
    except Exception as e:
        print(f"[TTS] FFmpeg mu-law conversion error: {e}")
        return b""

def linear16_to_mulaw(sample: int) -> int:
    """Encode 16-bit linear PCM sample (-32768 to 32767) to 8-bit G.711 mu-law."""
    if sample > 32767:
        sample = 32767
    elif sample < -32768:
        sample = -32768
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample += 0x84
    if sample > 32767:
        sample = 32767
    exponent = 7
    for exp in range(7, -1, -1):
        if sample & (1 << (exp + 7)):
            exponent = exp
            break
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF

def generate_tone(freq=600, duration_sec=0.1, volume=4000, attack_ms=8) -> bytes:
    """Generate a smooth telephone chime/beep in 8kHz mu-law with smooth envelope and warm harmonics."""
    import math
    raw = bytearray()
    num_samples = int(8000 * duration_sec)
    att_sec = attack_ms / 1000.0
    for i in range(num_samples):
        t = i / 8000.0
        # Raised-cosine attack (eliminates sudden click/pop transient)
        att = min(1.0, (1.0 - math.cos(math.pi * min(t, att_sec) / att_sec)) / 2.0) if att_sec > 0 else 1.0
        # Natural exponential decay
        decay = math.exp(-t / (duration_sec * 0.45))
        sample = int(volume * att * decay * (
            0.85 * math.sin(2 * math.pi * freq * t) +
            0.15 * math.sin(4 * math.pi * freq * t)
        ))
        raw.append(linear16_to_mulaw(sample))
    return bytes(raw)

def generate_ready_tone(style: str = READY_TONE_STYLE, volume: int = READY_TONE_VOLUME) -> bytes:
    """
    Generate a warm, pleasant musical ready chime.
    Completely eliminates the harsh 900Hz square-edge click.
    
    Styles:
    - 'chime': Warm ascending two-tone marimba (C5 523Hz -> E5 659Hz, ~0.14s)
    - 'droplet': Gentle water drop / soft bubble pop (pitch curves 450Hz -> 680Hz, ~0.09s)
    - 'bell': Soft Fender Rhodes chime (D5 587Hz with warm harmonics, ~0.10s)
    """
    import math
    sr = 8000
    raw = bytearray()
    
    if style == "droplet":
        dur = 0.09
        n = int(sr * dur)
        for i in range(n):
            t = i / sr
            att = min(1.0, (1.0 - math.cos(math.pi * min(t, 0.008) / 0.008)) / 2.0)
            dec = math.exp(-t / 0.032)
            freq = 460 + 220 * (t / dur)
            sample = int(volume * att * dec * math.sin(2 * math.pi * freq * t))
            raw.append(linear16_to_mulaw(sample))
            
    elif style == "bell":
        dur = 0.10
        freq = 587.33  # D5
        n = int(sr * dur)
        for i in range(n):
            t = i / sr
            att = min(1.0, (1.0 - math.cos(math.pi * min(t, 0.006) / 0.006)) / 2.0)
            dec = math.exp(-t / 0.038)
            sample = int(volume * att * dec * (
                0.80 * math.sin(2 * math.pi * freq * t) +
                0.16 * math.sin(4 * math.pi * freq * t) +
                0.04 * math.sin(6 * math.pi * freq * t)
            ))
            raw.append(linear16_to_mulaw(sample))
            
    else:  # default 'chime' (warm 2-tone marimba: C5 -> E5)
        notes = [
            (523.25, 0.055, volume),             # C5 (55ms)
            (659.25, 0.085, int(volume * 0.92))  # E5 (85ms)
        ]
        for freq, dur, vol in notes:
            n = int(sr * dur)
            for i in range(n):
                t = i / sr
                att = min(1.0, (1.0 - math.cos(math.pi * min(t, 0.006) / 0.006)) / 2.0)
                dec = math.exp(-t / (dur * 0.42))
                sample = int(vol * att * dec * (
                    0.82 * math.sin(2 * math.pi * freq * t) +
                    0.14 * math.sin(4 * math.pi * freq * t) +
                    0.04 * math.sin(6 * math.pi * freq * t)
                ))
                raw.append(linear16_to_mulaw(sample))
                
    return bytes(raw)

def generate_welcome_chime(volume: int = READY_TONE_VOLUME) -> bytes:
    """Warm, friendly 4-note ascending chord when taking phone off hook (C5 -> E5 -> G5 -> C6)."""
    import math
    sr = 8000
    raw = bytearray()
    notes = [
        (523.25, 0.15, volume),
        (659.25, 0.15, int(volume * 0.95)),
        (783.99, 0.18, int(volume * 0.90)),
        (1046.50, 0.35, int(volume * 0.85)),
    ]
    for freq, dur, vol in notes:
        n = int(sr * dur)
        for i in range(n):
            t = i / sr
            att = min(1.0, (1.0 - math.cos(math.pi * min(t, 0.01) / 0.01)) / 2.0)
            dec = math.exp(-t / (dur * 0.55))
            sample = int(vol * att * dec * (
                0.78 * math.sin(2 * math.pi * freq * t) +
                0.18 * math.sin(4 * math.pi * freq * t) +
                0.04 * math.sin(6 * math.pi * freq * t)
            ))
            raw.append(linear16_to_mulaw(sample))
    return bytes(raw)
