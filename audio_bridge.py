import array
import time
import threading
from typing import Optional, Callable
import sounddevice as sd

# Precompute 8-bit mu-law to 16-bit linear PCM lookup table
MULAW_TO_PCM = array.array("h", [0] * 256)
for b in range(256):
    u = ~b & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    if sign != 0:
        sample = -sample
    MULAW_TO_PCM[b] = sample

# Precompute 16-bit linear PCM to 8-bit mu-law for reverse direction
def _pcm_to_mulaw(sample: int) -> int:
    if sample > 32767: sample = 32767
    elif sample < -32768: sample = -32768
    sign = 0x80 if sample < 0 else 0
    if sample < 0: sample = -sample
    sample += 0x84
    if sample > 32767: sample = 32767
    exponent = 7
    for exp in range(7, -1, -1):
        if sample & (1 << (exp + 7)):
            exponent = exp
            break
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF

class AudioBridge:
    """
    Real-time, zero-latency audio bridge connecting the VoIP Landline telephone 
    directly with macOS CoreAudio as a virtual microphone and headphone.
    """
    def __init__(self, target_device_hint: str = "teams", monitor_speakers: bool = False):
        self.target_hint = target_device_hint.lower()
        self.monitor_speakers = monitor_speakers
        
        self.output_device_idx: Optional[int] = None
        self.output_device_name: str = "Unknown"
        self.speaker_device_idx: Optional[int] = None
        
        self.out_stream: Optional[sd.RawOutputStream] = None
        self.speaker_stream: Optional[sd.RawOutputStream] = None
        self.earpiece_in_stream: Optional[sd.RawInputStream] = None
        self.send_rtp_callback: Optional[Callable[[bytes], None]] = None
        self.earpiece_thread: Optional[threading.Thread] = None
        self.running = False
        self._find_devices()

    def _find_devices(self):
        devices = sd.query_devices()
        
        # 1. Look for virtual sound card to capture Mac sound to phone earpiece (BlackHole)
        self.earpiece_device_idx = None
        for idx, d in enumerate(devices):
            name = d["name"].lower()
            if "blackhole" in name and d["max_input_channels"] > 0:
                self.earpiece_device_idx = idx
                print(f"[AudioBridge] 🎧 Found BlackHole for Mac-to-Phone audio: Device #{idx} ({d['name']})")
                break

        # 2. Look for virtual mic sink (Microsoft Teams Audio - speak into phone handset)
        self.output_device_idx = None
        # First priority: find Teams virtual audio device
        for idx, d in enumerate(devices):
            name = d["name"].lower()
            if d["max_output_channels"] > 0:
                if self.target_hint in name or ("teams" in name and self.target_hint == "teams"):
                    self.output_device_idx = idx
                    self.output_device_name = d["name"]
                    break

        # If Teams not found and target is not physical speakers, look for another non-BlackHole virtual output
        if self.output_device_idx is None and self.target_hint != "speakers":
            for idx, d in enumerate(devices):
                name = d["name"].lower()
                if d["max_output_channels"] > 0 and idx != self.earpiece_device_idx:
                    if "virtual" in name or "sink" in name or "mic" in name:
                        self.output_device_idx = idx
                        self.output_device_name = d["name"]
                        break

        # Fallback to default output if still not found
        if self.output_device_idx is None:
            default_out = sd.default.device[1]
            if default_out is not None and default_out >= 0:
                self.output_device_idx = default_out
                self.output_device_name = devices[default_out]["name"]

        # 3. Look for physical speakers if monitor is enabled
        if self.monitor_speakers:
            for idx, d in enumerate(devices):
                if "speaker" in d["name"].lower() and d["max_output_channels"] > 0:
                    self.speaker_device_idx = idx
                    break

        print(f"[AudioBridge] 🎙️ Virtual Mic Target: Device #{self.output_device_idx} ({self.output_device_name})")

    def start(self):
        if self.running:
            return
        self.running = True
        try:
            if self.output_device_idx is not None:
                self.out_stream = sd.RawOutputStream(
                    device=self.output_device_idx,
                    samplerate=48000,
                    channels=1,
                    dtype="int16",
                    blocksize=960,
                    latency="low"
                )
                self.out_stream.start()

            if self.monitor_speakers and self.speaker_device_idx is not None:
                self.speaker_stream = sd.RawOutputStream(
                    device=self.speaker_device_idx,
                    samplerate=48000,
                    channels=1,
                    dtype="int16",
                    blocksize=960,
                    latency="low"
                )
                self.speaker_stream.start()

            # Start earpiece reverse stream if BlackHole is present and callback is registered
            if self.earpiece_device_idx is not None and self.send_rtp_callback:
                self.earpiece_in_stream = sd.RawInputStream(
                    device=self.earpiece_device_idx,
                    samplerate=48000,
                    channels=2,
                    dtype="int16",
                    blocksize=960,
                    latency="low"
                )
                self.earpiece_in_stream.start()
                self.earpiece_thread = threading.Thread(target=self._earpiece_loop, daemon=True)
                self.earpiece_thread.start()
                print("[AudioBridge] 🔊 Full-duplex: Mac audio streaming into phone earpiece enabled!")

            print("[AudioBridge] 🟢 Real-time audio stream connected to Mac microphone!")
        except Exception as e:
            print(f"[AudioBridge] Error starting stream: {e}")

    def register_earpiece_callback(self, callback: Callable[[bytes], None]):
        self.send_rtp_callback = callback

    def _earpiece_loop(self):
        """Read 48kHz stereo audio from BlackHole, downsample to 8kHz mono mu-law, and stream to phone earpiece."""
        while self.running and self.earpiece_in_stream:
            try:
                data, overflow = self.earpiece_in_stream.read(960)
                if not data or not self.send_rtp_callback:
                    continue
                samples = array.array("h")
                samples.frombytes(data)
                # 960 stereo frames -> 160 8kHz mono mu-law bytes
                out = bytearray()
                for frame_idx in range(0, 960, 6):
                    total = 0
                    for sub in range(6):
                        idx = (frame_idx + sub) * 2
                        total += (samples[idx] + samples[idx + 1]) // 2
                    mono_avg = total // 6
                    out.append(_pcm_to_mulaw(mono_avg))
                if out:
                    self.send_rtp_callback(bytes(out))
            except Exception:
                break

    def stop(self):
        self.running = False
        if self.out_stream:
            try:
                self.out_stream.stop()
                self.out_stream.close()
            except Exception:
                pass
            self.out_stream = None

        if self.speaker_stream:
            try:
                self.speaker_stream.stop()
                self.speaker_stream.close()
            except Exception:
                pass
            self.speaker_stream = None

        if self.earpiece_in_stream:
            try:
                self.earpiece_in_stream.stop()
                self.earpiece_in_stream.close()
            except Exception:
                pass
            self.earpiece_in_stream = None
        print("[AudioBridge] 🔴 Real-time audio stream stopped.")

    def write_telephone_audio(self, mulaw_bytes: bytes):
        """
        Receive 8kHz mu-law audio chunk from phone handset mic,
        upsample to 48kHz linear PCM, and write directly into Mac CoreAudio.
        """
        if not self.running or not self.out_stream:
            return

        n = len(mulaw_bytes)
        if n == 0:
            return

        # 8kHz -> 48kHz linear interpolation (6x)
        out = array.array("h")
        table = MULAW_TO_PCM
        for i in range(n - 1):
            s1 = table[mulaw_bytes[i]]
            s2 = table[mulaw_bytes[i+1]]
            diff = s2 - s1
            out.append(s1)
            out.append(s1 + diff // 6)
            out.append(s1 + (diff * 2) // 6)
            out.append(s1 + (diff * 3) // 6)
            out.append(s1 + (diff * 4) // 6)
            out.append(s1 + (diff * 5) // 6)
            
        last = table[mulaw_bytes[-1]]
        for _ in range(6):
            out.append(last)

        pcm_bytes = out.tobytes()
        try:
            self.out_stream.write(pcm_bytes)
            if self.speaker_stream:
                self.speaker_stream.write(pcm_bytes)
        except Exception:
            pass

# Global singleton
_bridge_instance: Optional[AudioBridge] = None

def get_audio_bridge(target_hint: str = "teams", monitor_speakers: bool = False) -> AudioBridge:
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = AudioBridge(target_hint, monitor_speakers)
    return _bridge_instance
