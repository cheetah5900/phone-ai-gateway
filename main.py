#!/usr/bin/env python3
import os
import sys
import time
import signal
import argparse
from pathlib import Path

# Load local .env if present
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

from config import (
    MAC_IP, SIP_PORT, RTP_PORT, ATA_IP, DICTATE_TO_MACOS, SPEAK_RESPONSE,
    USE_ANTIGRAVITY_BRAIN, OPERATION_MODE, BRIDGE_OUTPUT_DEVICE,
    bind_socket_to_interface
)
from sip_server import SIPServer
import ai_handler

server: SIPServer = None

def handle_speech_utterance(raw_audio: bytes):
    """Callback when user finishes speaking a sentence/phrase into the phone."""
    print("[Pipeline] Transcribing audio with Whisper...")
    start_t = time.time()
    raw_text = ai_handler.transcribe(raw_audio)
    elapsed_stt = time.time() - start_t

    if not raw_text:
        print("[Pipeline] ⚠️ No speech recognized or too quiet.")
        return

    print(f"\n==========================================")
    print(f"🎙️  [WHISPER RAW] ({elapsed_stt:.2f}s): \"{raw_text}\"")

    # Fast in-memory phonetic & contextual speech refiner (no chat windows created)
    final_text = ai_handler.smart_refine_text(raw_text)
    if final_text != raw_text:
        print(f"✨ [SMART REFINED]: \"{final_text}\"")

    print(f"==========================================")

    # 1. Dictation into macOS active app
    if DICTATE_TO_MACOS:
        ai_handler.dictate_to_macos(final_text)

    # 2. AI Response and voice playback
    if SPEAK_RESPONSE:
        ans = ai_handler.ask_ai(final_text)
        if ans:
            print(f"\n🤖 [AI RESPONSE]:\n👉 \"{ans}\"")
            print("[Pipeline] Synthesizing speech...")
            audio = ai_handler.synthesize_speech(ans)
            if audio and server:
                print("[Pipeline] 🔊 Playing answer back to phone handset...")
                server.play_response_to_phone(audio)
        else:
            # Pleasant soft ready chime - musical & comfortable on the ears
            if server:
                server.play_ready_tone()
        print("[Pipeline] Ready for next sentence.\n")

def make_call_to_phone(server_instance: SIPServer):
    """Trigger the Mac to call the phone (makes phone ring)."""
    import socket
    sdp = (
        "v=0\r\n"
        f"o=MacSIP 1 1 IN IP4 {MAC_IP}\r\n"
        "s=PhoneAI\r\n"
        f"c=IN IP4 {MAC_IP}\r\n"
        "t=0 0\r\n"
        f"m=audio {RTP_PORT} RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=sendrecv\r\n"
    )
    req = (
        f"INVITE sip:{ATA_IP}:{SIP_PORT} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {MAC_IP}:{SIP_PORT};branch=z9hG4bK-callout;rport\r\n"
        f"From: <sip:ai@{MAC_IP}>;tag=callouttag\r\n"
        f"To: <sip:1001@{ATA_IP}>\r\n"
        f"Call-ID: outcall-1@{MAC_IP}\r\n"
        f"CSeq: 1 INVITE\r\n"
        f"Contact: <sip:ai@{MAC_IP}:{SIP_PORT}>\r\n"
        f"Max-Forwards: 70\r\n"
        f"Content-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp)}\r\n\r\n"
        f"{sdp}"
    )
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bind_socket_to_interface(s)
    s.sendto(req.encode(), (ATA_IP, SIP_PORT))
    s.close()
    print(f"[SIP] 🔔 Ringing phone at {ATA_IP}...")

def main():
    global server
    parser = argparse.ArgumentParser(description="Landline Phone AI & Dictation Gateway for macOS")
    parser.add_argument("--call", action="store_true", help="Ring the phone from Mac")
    args = parser.parse_args()

    print("=" * 60)
    print("📞  Mac Landline Phone Audio & AI Gateway")
    print("=" * 60)
    print(f"  • Mac IP:           {MAC_IP}:{SIP_PORT}")
    print(f"  • Planet ATA IP:    {ATA_IP}")
    if OPERATION_MODE == "audio_bridge":
        print("  • Mode:             FULL-DUPLEX HEADSET (Microphone + Earpiece)")
        print("  • Mac Virtual Mic:  Microsoft Teams Audio (Speak into phone)")
        print("  • Mac Virtual Spk:  BlackHole 2ch (Listen through phone earpiece)")
    else:
        print("  • Mode:             AI DICTATION (Whisper STT + Auto-Correction)")
        print(f"  • Dictation to Mac: {'ENABLED (Cmd+V into active app)' if DICTATE_TO_MACOS else 'DISABLED'}")
        print(f"  • AI Voice Reply:   {'ENABLED' if SPEAK_RESPONSE else 'DISABLED'}")
    print("=" * 60)

    server = SIPServer(on_speech_utterance=handle_speech_utterance)
    server.start()

    if args.call:
        time.sleep(1)
        make_call_to_phone(server)

    print("\n🟢 System is READY!")
    print("👉 How to use:")
    if OPERATION_MODE == "audio_bridge":
        print("   1. Pick up the telephone handset (off-hook).")
        print("   2. You will hear a pleasant connection chime in the earpiece.")
        print("   3. In macOS (Zoom, Meet, Discord, QuickTime, or Sound Settings):")
        print("      • Microphone (Input):  Select 'Microsoft Teams Audio'")
        print("      • Speaker (Output):     Select 'BlackHole 2ch'")
        print("   4. You can now talk into the handset mic AND hear Mac sound in the earpiece!")
    else:
        print("   1. Pick up the telephone handset (off-hook).")
        print("   2. You will hear a connection chime in the earpiece.")
        print("   3. Speak in Thai or English.")
        print("   4. The text will automatically be typed into macOS active window,")
        print("      and the AI will speak the answer back to your phone handset earpiece!")
    print("\nPress Ctrl+C to stop.\n")

    def signal_handler(sig, frame):
        print("\nShutting down server...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
