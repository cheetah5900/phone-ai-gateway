import os
import socket
import shutil
from pathlib import Path

# ATA / Phone configuration
ATA_IP = os.getenv("ATA_IP", "192.168.1.10")
EXTENSION = os.getenv("EXTENSION", "1001")

def get_local_ip(target_ip: str = ATA_IP) -> str:
    """Auto-detect Mac local IP address on the interface reaching the ATA."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target_ip, 5060))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

# Network configuration
MAC_IP = os.getenv("MAC_IP", "192.168.1.17")
SIP_PORT = int(os.getenv("SIP_PORT", "5060"))
RTP_PORT = int(os.getenv("RTP_PORT", "20000"))

def get_interface_name(ip: str = MAC_IP) -> str:
    """Find the physical network interface name (e.g. en0) assigned to the given IP."""
    try:
        import subprocess
        out = subprocess.check_output(["ifconfig"], text=True)
        current_if = "en0"
        for line in out.splitlines():
            if line and not line.startswith(("\t", " ")) and ":" in line:
                current_if = line.split(":")[0]
            if f"inet {ip} " in line:
                return current_if
    except Exception:
        pass
    return "en0"

ETH_INTERFACE = os.getenv("ETH_INTERFACE", get_interface_name(MAC_IP))

def bind_socket_to_interface(sock: socket.socket, if_name: str = ETH_INTERFACE):
    """
    Bind a UDP/TCP socket directly to the physical interface (e.g. en0) on macOS Darwin.
    This guarantees packets go directly to the ATA over direct Ethernet cable
    even if Wi-Fi is active on the same subnet (192.168.1.x).
    """
    try:
        # IP_BOUND_IF = 25 on macOS Darwin BSD
        idx = socket.if_nametoindex(if_name)
        try:
            import struct
            sock.setsockopt(socket.IPPROTO_IP, 25, struct.pack('I', idx))
        except Exception:
            sock.setsockopt(socket.IPPROTO_IP, 25, idx)
    except Exception as e:
        pass

# Binaries discovery (works on both Apple Silicon and Intel Macs)
FFMPEG_BIN = os.getenv("FFMPEG_BIN", shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg")
WHISPER_BIN = os.getenv("WHISPER_BIN", shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli")

# Whisper Models
small_model = os.path.expanduser("~/.cache/whisper/ggml-small.bin")
base_model = os.path.expanduser("~/.cache/whisper/ggml-base.bin")
default_model = small_model if os.path.exists(small_model) else base_model
WHISPER_MODEL = os.getenv("WHISPER_MODEL", default_model)
WHISPER_LANG = os.getenv("WHISPER_LANG", "th")

# Operation Mode:
# - "audio_bridge": Direct Microphone & Headset (Zero STT, zero text typing, pure audio)
# - "ai_dictation": Whisper Speech-to-Text + AI Auto-Correction + Cursor Typing
OPERATION_MODE = os.getenv("OPERATION_MODE", "audio_bridge")
BRIDGE_OUTPUT_DEVICE = os.getenv("BRIDGE_OUTPUT_DEVICE", "teams") # "teams", "speakers", or device name
BRIDGE_MONITOR_SPEAKERS = os.getenv("BRIDGE_MONITOR_SPEAKERS", "false").lower() in ("true", "1", "yes")
EARPIECE_GAIN = float(os.getenv("EARPIECE_GAIN", "3.0")) # 3x digital volume boost to telephone earpiece

# Dictation & macOS integration
DICTATE_TO_MACOS = os.getenv("DICTATE_TO_MACOS", "true").lower() in ("true", "1", "yes")
NOTIFY_MACOS = os.getenv("NOTIFY_MACOS", "false").lower() in ("true", "1", "yes")

# Antigravity Brain for Text Refinement & Auto-Correction
USE_ANTIGRAVITY_BRAIN = os.getenv("USE_ANTIGRAVITY_BRAIN", "true").lower() in ("true", "1", "yes")
ANTIGRAVITY_BIN = os.getenv("ANTIGRAVITY_BIN", os.path.expanduser("~/.gemini/antigravity/bin/agentapi"))

# AI & Speech generation
SPEAK_RESPONSE = os.getenv("SPEAK_RESPONSE", "true").lower() in ("true", "1", "yes")
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge-tts")
TTS_VOICE_EDGE = os.getenv("TTS_VOICE_EDGE", "th-TH-PremwadeeNeural")
TTS_VOICE_MACOS = os.getenv("TTS_VOICE_MACOS", "Kanya")

# AI API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Sound Feedback Configuration
# Styles: "chime" (warm 2-tone marimba), "droplet" (soft water pop), "bell" (Rhodes electric bell)
READY_TONE_STYLE = os.getenv("READY_TONE_STYLE", "chime")
READY_TONE_VOLUME = int(os.getenv("READY_TONE_VOLUME", "4200"))

# Voice Activity Detection (VAD)
VAD_ENERGY_THRESHOLD = int(os.getenv("VAD_ENERGY_THRESHOLD", "400"))
VAD_SILENCE_TIMEOUT = float(os.getenv("VAD_SILENCE_TIMEOUT", "1.3"))
VAD_MIN_SPEECH_DURATION = float(os.getenv("VAD_MIN_SPEECH_DURATION", "0.4"))
