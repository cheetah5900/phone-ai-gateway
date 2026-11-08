import socket
import struct
import time
import math
import threading
from typing import Callable, Optional
from config import (
    VAD_ENERGY_THRESHOLD, VAD_SILENCE_TIMEOUT, VAD_MIN_SPEECH_DURATION,
    OPERATION_MODE, BRIDGE_OUTPUT_DEVICE, BRIDGE_MONITOR_SPEAKERS,
    bind_socket_to_interface
)
from audio_bridge import get_audio_bridge

# Precompute standard G.711 mu-law to 16-bit linear PCM lookup table
def _build_mulaw_lut():
    lut = []
    for b in range(256):
        u = ~b & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        if sign == 0:
            sample = -sample
        lut.append(sample)
    return lut

MULAW_LUT = _build_mulaw_lut()

class RTPMediaSession:
    def __init__(self, local_ip: str, local_port: int, on_speech_utterance: Callable[[bytes], None]):
        self.local_ip = local_ip
        self.local_port = local_port
        self.on_speech_utterance = on_speech_utterance

        self.remote_ip: Optional[str] = None
        self.remote_port: Optional[int] = None
        self.bridge = get_audio_bridge(BRIDGE_OUTPUT_DEVICE, BRIDGE_MONITOR_SPEAKERS)

        self.sock: Optional[socket.socket] = None
        self.running = False
        self.recv_thread: Optional[threading.Thread] = None

        # Outgoing RTP state
        self.seq = 1000
        self.timestamp = 160000
        self.ssrc = 0x12345678

        # Playback lock so we don't interleave playback
        self.send_lock = threading.Lock()
        self.is_playing = False

    def start(self, remote_ip: str, remote_port: int):
        self.remote_ip = remote_ip
        self.remote_port = remote_port
        self.running = True

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_socket_to_interface(self.sock)
        try:
            self.sock.bind((self.local_ip, self.local_port))
        except Exception:
            self.sock.bind(("0.0.0.0", self.local_port))
        self.sock.settimeout(0.5)

        if OPERATION_MODE == "audio_bridge":
            self.bridge.register_earpiece_callback(self.send_rtp_frame)
            self.bridge.start()

        print(f"[RTP] Media session started: {self.local_ip}:{self.local_port} <-> {self.remote_ip}:{self.remote_port}")
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.recv_thread.start()

    def stop(self):
        self.running = False
        if OPERATION_MODE == "audio_bridge":
            self.bridge.stop()
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
        print("[RTP] Media session closed.")

    def _recv_loop(self):
        speech_buffer = bytearray()
        is_speaking = False
        silence_start_time = None

        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                # Check silence timeout if waiting
                if is_speaking and silence_start_time:
                    if time.time() - silence_start_time >= VAD_SILENCE_TIMEOUT:
                        is_speaking = False
                        utterance = bytes(speech_buffer)
                        speech_buffer.clear()
                        silence_start_time = None
                        if len(utterance) >= VAD_MIN_SPEECH_DURATION * 8000:
                            threading.Thread(target=self.on_speech_utterance, args=(utterance,), daemon=True).start()
                continue
            except Exception:
                break

            if len(data) < 12:
                continue

            # Parse RTP Header (RFC 3550)
            v_p_x_cc = data[0]
            cc = v_p_x_cc & 0x0F
            header_len = 12 + (cc * 4)
            if len(data) <= header_len:
                continue

            payload = data[header_len:]
            if not payload:
                continue

            # Real-time Full Duplex Audio Bridge (Zero STT, stream mic live to Mac)
            if OPERATION_MODE == "audio_bridge":
                self.bridge.write_telephone_audio(payload)
                continue

            # If currently playing AI response to handset in dictation mode, ignore incoming mic to prevent echo
            if self.is_playing:
                continue

            # Calculate RMS energy of mu-law samples
            samples = [MULAW_LUT[b] for b in payload]
            rms = math.sqrt(sum(s * s for s in samples) / len(samples))

            if rms >= VAD_ENERGY_THRESHOLD:
                if not is_speaking:
                    is_speaking = True
                    print(f"\n[Voice] 🎙️ Speech detected (RMS: {rms:.0f})...")
                speech_buffer.extend(payload)
                silence_start_time = None
            else:
                if is_speaking:
                    speech_buffer.extend(payload)
                    if silence_start_time is None:
                        silence_start_time = time.time()
                    elif time.time() - silence_start_time >= VAD_SILENCE_TIMEOUT:
                        is_speaking = False
                        utterance = bytes(speech_buffer)
                        speech_buffer.clear()
                        silence_start_time = None
                        print(f"[Voice] ⏹️ Speech ended. Collected {len(utterance)} bytes ({len(utterance)/8000:.2f}s). Processing...")
                        if len(utterance) >= VAD_MIN_SPEECH_DURATION * 8000:
                            threading.Thread(target=self.on_speech_utterance, args=(utterance,), daemon=True).start()

    def send_rtp_frame(self, chunk_160: bytes):
        """Send a single real-time 20ms mu-law frame immediately over RTP (for full-duplex bridge)."""
        if not self.running or not self.sock or not self.remote_ip or not self.remote_port:
            return

        # If welcome chime or response audio is currently playing, yield to it
        if self.is_playing:
            return

        if len(chunk_160) < 160:
            chunk_160 = chunk_160 + (b'\xFF' * (160 - len(chunk_160)))

        with self.send_lock:
            header = struct.pack('!BBHII', 0x80, 0x00, self.seq, self.timestamp, self.ssrc)
            packet = header + chunk_160[:160]

            try:
                self.sock.sendto(packet, (self.remote_ip, self.remote_port))
            except Exception:
                pass

            self.seq = (self.seq + 1) & 0xFFFF
            self.timestamp = (self.timestamp + 160) & 0xFFFFFFFF

    def send_audio(self, raw_mulaw_bytes: bytes):
        """Stream 8kHz mu-law audio to remote RTP endpoint in 20ms frames."""
        if not self.running or not self.sock or not self.remote_ip or not self.remote_port:
            print(f"[RTP OUT] ⚠️ Cannot send audio: running={self.running}, sock={self.sock}, remote={self.remote_ip}:{self.remote_port}")
            return

        print(f"[RTP OUT] 🔊 Streaming {len(raw_mulaw_bytes)} bytes ({len(raw_mulaw_bytes)/8000:.2f}s) to {self.remote_ip}:{self.remote_port}")
        self.is_playing = True
        try:
            frame_size = 160  # 160 bytes = 20ms at 8000Hz 8-bit
            total_len = len(raw_mulaw_bytes)
            next_send_time = time.monotonic()

            for offset in range(0, total_len, frame_size):
                if not self.running:
                    break

                chunk = raw_mulaw_bytes[offset:offset + frame_size]
                if len(chunk) < frame_size:
                    chunk = chunk + (b'\xFF' * (frame_size - len(chunk)))

                # 12-byte RTP header:
                # Version=2, PayloadType=0 (PCMU)
                header = struct.pack('!BBHII', 0x80, 0x00, self.seq, self.timestamp, self.ssrc)
                packet = header + chunk

                with self.send_lock:
                    try:
                        self.sock.sendto(packet, (self.remote_ip, self.remote_port))
                    except Exception as e:
                        print(f"[RTP] Send error: {e}")
                        break

                    self.seq = (self.seq + 1) & 0xFFFF
                    self.timestamp = (self.timestamp + 160) & 0xFFFFFFFF

                next_send_time += 0.020  # 20ms
                sleep_time = next_send_time - time.monotonic()
                if sleep_time > 0:
                    time.sleep(sleep_time)
        finally:
            self.is_playing = False
