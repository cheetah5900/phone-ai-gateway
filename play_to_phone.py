#!/usr/bin/env python3
"""
Play any audio file (MP3, WAV, M4A, etc.) or TTS voice directly into the telephone handset earpiece.
Usage:
    python3 play_to_phone.py <audio_file>
    python3 play_to_phone.py --text "สวัสดีครับ นี่คือเสียงจากคอมพิวเตอร์ Mac"
"""
import sys
import os
import subprocess
import argparse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import FFMPEG_BIN, TTS_VOICE_MACOS

def convert_to_mulaw(input_file: str) -> bytes:
    raw_path = "/tmp/play_phone_temp.raw"
    subprocess.run([
        FFMPEG_BIN, "-y",
        "-i", input_file,
        "-filter:a", "volume=3.0",
        "-ar", "8000", "-ac", "1",
        "-f", "mulaw",
        raw_path
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    with open(raw_path, "rb") as f:
        return f.read()

def play_via_gateway(raw_mulaw: bytes):
    url = "http://127.0.0.1:5065/play"
    req = urllib.request.Request(url, data=raw_mulaw, headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"🔊 Playing audio to telephone handset... (HTTP {resp.status})")
    except Exception as e:
        print(f"❌ Error sending audio to gateway: {e}")
        print("💡 Make sure you have picked up the phone handset (off-hook) before playing!")

def main():
    parser = argparse.ArgumentParser(description="Play audio to telephone handset")
    parser.add_argument("file", nargs="?", help="Audio file path (MP3, WAV, etc.)")
    parser.add_argument("--text", help="Speak Thai or English text into phone earpiece")
    args = parser.parse_args()

    if args.text:
        aiff_path = "/tmp/phone_say.aiff"
        subprocess.run(["say", "-v", TTS_VOICE_MACOS, "-o", aiff_path, args.text], check=True)
        raw = convert_to_mulaw(aiff_path)
        play_via_gateway(raw)
    elif args.file:
        if not os.path.exists(args.file):
            print(f"File not found: {args.file}")
            sys.exit(1)
        raw = convert_to_mulaw(args.file)
        play_via_gateway(raw)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
