#!/usr/bin/env python3
"""
Preview the pleasant ready sounds directly through Mac speakers.
Usage:
    python3 test_tones.py [chime|droplet|bell|welcome]
"""
import sys
import os
import wave
import struct
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_handler
import config

style = sys.argv[1] if len(sys.argv) > 1 else "all"

def play_mulaw(data: bytes, title: str):
    print(f"🔊 Playing '{title}' ({len(data)} bytes)...")
    # Decode mu-law to linear 16-bit PCM for afplay
    samples = []
    for b in data:
        u = ~b & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        if sign != 0:
            sample = -sample
        samples.append(sample)

    wav_path = "/tmp/preview_tone.wav"
    with wave.open(wav_path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(8000)
        f.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    subprocess.run(["afplay", wav_path])

if style == "all":
    print("========================================")
    print("🎶 Testing Sound Options for Telephone AI")
    print("========================================")
    play_mulaw(ai_handler.generate_ready_tone("chime"), "1. Chime (Marimba C5->E5, Default)")
    import time
    time.sleep(0.5)
    play_mulaw(ai_handler.generate_ready_tone("droplet"), "2. Droplet (Water Pop)")
    import time
    time.sleep(0.5)
    play_mulaw(ai_handler.generate_ready_tone("bell"), "3. Bell (Rhodes Bell)")
    import time
    time.sleep(0.5)
    play_mulaw(ai_handler.generate_welcome_chime(), "4. Welcome Chime (Off-hook)")
else:
    if style == "welcome":
        play_mulaw(ai_handler.generate_welcome_chime(), "Welcome Chime")
    else:
        play_mulaw(ai_handler.generate_ready_tone(style), f"Ready Tone: {style}")
