import socket
import re
import uuid
import time
import threading
import http.server
from typing import Optional, Callable
from config import (
    MAC_IP, SIP_PORT, RTP_PORT, ATA_IP, EXTENSION, READY_TONE_STYLE,
    bind_socket_to_interface
)
import config
from rtp_media import RTPMediaSession
import ai_handler

class SIPServer:
    def __init__(self, on_speech_utterance: Callable[[bytes], None]):
        self.mac_ip = MAC_IP
        self.sip_port = SIP_PORT
        self.rtp_port = RTP_PORT
        self.ata_ip = ATA_IP
        self.on_speech_utterance = on_speech_utterance

        self.sock: Optional[socket.socket] = None
        self.running = False

        # Active Call Dialog state
        self.media_session: Optional[RTPMediaSession] = None
        self.active_call_id: Optional[str] = None
        self.active_from: Optional[str] = None
        self.active_to: Optional[str] = None
        self.active_cseq: Optional[str] = None
        self.remote_contact: Optional[str] = None
        self.pending_rtp_ip: Optional[str] = None
        self.pending_rtp_port: int = 20000
        self.remote_sip_addr: Optional[tuple] = None
        self.is_in_call = False
        self.to_tag: str = uuid.uuid4().hex[:8]
        self.from_tag: str = uuid.uuid4().hex[:8]

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_socket_to_interface(self.sock)
        try:
            self.sock.bind((self.mac_ip, self.sip_port))
        except Exception:
            self.sock.bind(("0.0.0.0", self.sip_port))
        self.running = True

        print(f"[SIP] Server listening on UDP {self.mac_ip}:{self.sip_port}")
        threading.Thread(target=self._listen_loop, daemon=True).start()
        threading.Thread(target=self._control_http_server, daemon=True).start()

    def stop(self):
        self.running = False
        if self.media_session:
            self.media_session.stop()
            self.media_session = None
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        print("[SIP] Server stopped.")

    def _listen_loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except Exception:
                break

            try:
                msg = data.decode("utf-8", errors="ignore")
                msg_clean = msg.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
                lines = msg_clean.split("\n")
                if not lines or not lines[0]:
                    continue

                first_line = lines[0].strip()
                parts = first_line.split()
                if not parts:
                    continue

                if not first_line.startswith("REGISTER"):
                    print(f"[SIP IN] {first_line} from {addr}")

                headers = {}
                vias = []
                body = ""
                is_body = False
                for line in lines[1:]:
                    if is_body:
                        body += line + "\r\n"
                    elif line.strip() == "":
                        is_body = True
                    else:
                        if ":" in line:
                            k, v = line.split(":", 1)
                            k_lower = k.strip().lower()
                            v_clean = v.strip()
                            if k_lower == "via":
                                vias.append(v_clean)
                            if k_lower not in headers:
                                headers[k_lower] = v_clean

                # Store primary Via
                headers["via_all"] = vias
                if vias and "via" not in headers:
                    headers["via"] = vias[0]

                if parts[0].startswith("SIP/"):
                    status_code = parts[1] if len(parts) > 1 else ""
                    self._handle_sip_response(status_code, headers, body, addr)
                else:
                    method = parts[0]
                    self._handle_sip_request(method, headers, body, addr, lines)
            except Exception as e:
                import traceback
                print(f"[SIP] ⚠️ Error parsing packet from {addr}: {e}")
                traceback.print_exc()

    def _handle_sip_request(self, method: str, headers: dict, body: str, addr: tuple, raw_lines: list):
        via = headers.get("via", "")
        from_hdr = headers.get("from", "")
        to_hdr = headers.get("to", "")
        call_id = headers.get("call-id", "")
        cseq = headers.get("cseq", "")

        # Format Via header lines for response
        vias_lines = ""
        for v in headers.get("via_all", [via]):
            vias_lines += f"Via: {v}\r\n"
        if not vias_lines:
            vias_lines = f"Via: {via}\r\n"

        if method == "OPTIONS":
            resp = (
                f"SIP/2.0 200 OK\r\n"
                f"{vias_lines}"
                f"From: {from_hdr}\r\n"
                f"To: {to_hdr};tag={self.to_tag}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq}\r\n"
                f"Allow: INVITE,ACK,CANCEL,OPTIONS,BYE\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            self.sock.sendto(resp.encode(), addr)

        elif method == "REGISTER":
            contact = headers.get("contact", f"<sip:{EXTENSION}@{addr[0]}:{addr[1]}>")
            resp = (
                f"SIP/2.0 200 OK\r\n"
                f"{vias_lines}"
                f"From: {from_hdr}\r\n"
                f"To: {to_hdr};tag={self.to_tag}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq}\r\n"
                f"Contact: {contact};expires=3600\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            self.sock.sendto(resp.encode(), addr)
            print(f"[SIP] ✅ REGISTER received from {addr[0]}. Answered 200 OK.")

        elif method == "INVITE":
            print(f"\n[SIP] 📞 Incoming call from {from_hdr} (Handset lifted / dialed)")
            self.active_call_id = call_id
            self.active_from = from_hdr
            self.active_to = to_hdr
            self.active_cseq = cseq
            self.remote_contact = headers.get("contact", f"<sip:{EXTENSION}@{addr[0]}:5060>")
            self.remote_sip_addr = addr
            self.to_tag = uuid.uuid4().hex[:8]

            # Parse SDP to find Phone's RTP destination
            remote_rtp_ip = addr[0]
            remote_rtp_port = 20000

            m_ip = re.search(r"c=IN IP4 ([\d\.]+)", body)
            if m_ip:
                remote_rtp_ip = m_ip.group(1)

            m_port = re.search(r"m=audio (\d+)", body)
            if m_port:
                remote_rtp_port = int(m_port.group(1))

            self.pending_rtp_ip = remote_rtp_ip
            self.pending_rtp_port = remote_rtp_port
            print(f"[SIP] Remote RTP audio stream expected at {remote_rtp_ip}:{remote_rtp_port}")

            # Send 100 Trying
            resp_trying = (
                f"SIP/2.0 100 Trying\r\n"
                f"{vias_lines}"
                f"From: {from_hdr}\r\n"
                f"To: {to_hdr}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq}\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            self.sock.sendto(resp_trying.encode(), addr)

            # Ensure To header has tag for 200 OK
            to_resp = to_hdr
            if ";tag=" not in to_resp.lower():
                to_resp = f"{to_hdr};tag={self.to_tag}"

            # Prepare SDP for 200 OK (Step 255 verified baseline)
            sdp = (
                "v=0\r\n"
                f"o=MacSIP 1 1 IN IP4 {self.mac_ip}\r\n"
                "s=PhoneAI\r\n"
                f"c=IN IP4 {self.mac_ip}\r\n"
                "t=0 0\r\n"
                f"m=audio {self.rtp_port} RTP/AVP 0\r\n"
                "a=rtpmap:0 PCMU/8000\r\n"
                "a=sendrecv\r\n"
            )

            # Send 200 OK
            resp_ok = (
                f"SIP/2.0 200 OK\r\n"
                f"{vias_lines}"
                f"From: {from_hdr}\r\n"
                f"To: {to_resp}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq}\r\n"
                f"Contact: <sip:{EXTENSION}@{self.mac_ip}:{self.sip_port}>\r\n"
                f"Content-Type: application/sdp\r\n"
                f"Content-Length: {len(sdp)}\r\n\r\n"
                f"{sdp}"
            )
            self.sock.sendto(resp_ok.encode(), addr)
            print("[SIP] Sent 200 OK (Call Accepted). Starting media session immediately...")

            # Start media immediately (Step 255 verified baseline)
            self._start_media(remote_rtp_ip, remote_rtp_port)

        elif method == "ACK":
            print("[SIP] 🔗 ACK received! Call Established & Audio Channel Connected!")
            self.is_in_call = True

        elif method == "BYE":
            reason = headers.get("reason", "")
            print(f"\n[SIP] 📴 Call Ended (Handset on hook). Reason: {reason or 'Normal on-hook'}")
            to_resp = to_hdr
            if ";tag=" not in to_resp.lower():
                to_resp = f"{to_hdr};tag={self.to_tag}"
            resp = (
                f"SIP/2.0 200 OK\r\n"
                f"{vias_lines}"
                f"From: {from_hdr}\r\n"
                f"To: {to_resp}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq}\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            self.sock.sendto(resp.encode(), addr)
            if self.media_session:
                self.media_session.stop()
                self.media_session = None
            self.active_call_id = None
            self.is_in_call = False

        elif method == "CANCEL":
            print("\n[SIP] 🛑 Call Cancelled.")
            to_resp = to_hdr
            if ";tag=" not in to_resp.lower():
                to_resp = f"{to_hdr};tag={self.to_tag}"
            resp = (
                f"SIP/2.0 200 OK\r\n"
                f"{vias_lines}"
                f"From: {from_hdr}\r\n"
                f"To: {to_resp}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq}\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            self.sock.sendto(resp.encode(), addr)
            if self.media_session:
                self.media_session.stop()
                self.media_session = None
            self.is_in_call = False

    def _start_media(self, remote_rtp_ip: str, remote_rtp_port: int):
        """Start RTP audio session and play welcome chime."""
        if self.media_session:
            self.media_session.stop()
        self.media_session = RTPMediaSession(self.mac_ip, self.rtp_port, self.on_speech_utterance)
        self.media_session.start(remote_rtp_ip, remote_rtp_port)
        self.is_in_call = True

        def play_greeting():
            time.sleep(0.3)
            tone1 = ai_handler.generate_tone(600, 0.15, volume=10000)
            tone2 = ai_handler.generate_tone(900, 0.25, volume=10000)
            silence = b'\xFF' * 800
            if self.media_session and self.media_session.running:
                self.media_session.send_audio(tone1 + silence + tone2)

        threading.Thread(target=play_greeting, daemon=True).start()

    def _handle_sip_response(self, code: str, headers: dict, body: str, addr: tuple):
        cseq = headers.get("cseq", "")
        if code == "180":
            print("[SIP] 🔔 Phone is ringing (180 Ringing)...")
        elif code == "200":
            if "INVITE" in cseq:
                print(f"[SIP] 200 OK received for INVITE! SDP:\n{body.strip()}")
                to_hdr = headers.get("to", "")
                from_hdr = headers.get("from", "")
                call_id = headers.get("call-id", "")
                contact = headers.get("contact", f"<sip:{EXTENSION}@{addr[0]}:5060>")

                # Extract remote RTP
                remote_rtp_ip = addr[0]
                remote_rtp_port = 20000
                m_ip = re.search(r"c=IN IP4 ([\d\.]+)", body)
                if m_ip:
                    remote_rtp_ip = m_ip.group(1)
                m_port = re.search(r"m=audio (\d+)", body)
                if m_port:
                    remote_rtp_port = int(m_port.group(1))

                # Send ACK
                m_uri = re.search(r"<sips?:([^>]+)>", contact)
                if m_uri:
                    req_uri = m_uri.group(1)
                else:
                    m_plain = re.search(r"(?:sips?:)?([^\s;>]+)", contact)
                    req_uri = m_plain.group(1) if m_plain else f"{EXTENSION}@{addr[0]}:5060"
                req_uri = re.sub(r"^sips?:", "", req_uri)

                ack = (
                    f"ACK sip:{req_uri} SIP/2.0\r\n"
                    f"Via: SIP/2.0/UDP {self.mac_ip}:{self.sip_port};branch=z9hG4bK-ack-{uuid.uuid4().hex[:6]}\r\n"
                    f"From: {from_hdr}\r\n"
                    f"To: {to_hdr}\r\n"
                    f"Call-ID: {call_id}\r\n"
                    f"CSeq: 1 ACK\r\n"
                    f"Max-Forwards: 70\r\n"
                    f"Content-Length: 0\r\n\r\n"
                )
                self.sock.sendto(ack.encode(), addr)
                print(f"[SIP] 🔗 Audio stream started with {remote_rtp_ip}:{remote_rtp_port}")
                self._start_media(remote_rtp_ip, remote_rtp_port)
        else:
            print(f"[SIP] Response received: {code} for CSeq {cseq}")
            if "INVITE" in cseq and int(code) >= 300:
                # RFC 3261: Non-2xx final responses to INVITE MUST be ACKed
                to_hdr = headers.get("to", "")
                from_hdr = headers.get("from", "")
                call_id = headers.get("call-id", "")
                v_branch = headers.get("via", "").split("branch=")[-1].split(";")[0] if "branch=" in headers.get("via", "") else f"z9hG4bK-{uuid.uuid4().hex[:6]}"
                ack = (
                    f"ACK sip:{self.ata_ip}:{self.sip_port} SIP/2.0\r\n"
                    f"Via: SIP/2.0/UDP {self.mac_ip}:{self.sip_port};branch={v_branch}\r\n"
                    f"From: {from_hdr}\r\n"
                    f"To: {to_hdr}\r\n"
                    f"Call-ID: {call_id}\r\n"
                    f"CSeq: 1 ACK\r\n"
                    f"Max-Forwards: 70\r\n"
                    f"Content-Length: 0\r\n\r\n"
                )
                self.sock.sendto(ack.encode(), addr)
            if int(code) >= 400 and self.is_in_call:
                self.is_in_call = False
                if self.media_session:
                    self.media_session.stop()
                    self.media_session = None

    def call_phone(self):
        """Initiate call from Mac to ring the telephone."""
        sdp = (
            "v=0\r\n"
            f"o=MacSIP 1 1 IN IP4 {self.mac_ip}\r\n"
            "s=PhoneAI\r\n"
            f"c=IN IP4 {self.mac_ip}\r\n"
            "t=0 0\r\n"
            f"m=audio {self.rtp_port} RTP/AVP 0\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            "a=sendrecv\r\n"
        )
        call_id = f"out-{uuid.uuid4().hex[:12]}@{self.mac_ip}"
        self.active_call_id = call_id
        self.from_tag = uuid.uuid4().hex[:8]
        req = (
            f"INVITE sip:{self.ata_ip}:{self.sip_port} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.mac_ip}:{self.sip_port};branch=z9hG4bK-inv-{uuid.uuid4().hex[:6]};rport\r\n"
            f"From: <sip:ai@{self.mac_ip}>;tag={self.from_tag}\r\n"
            f"To: <sip:{EXTENSION}@{self.ata_ip}>\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: 1 INVITE\r\n"
            f"Contact: <sip:{EXTENSION}@{self.mac_ip}:{self.sip_port}>\r\n"
            f"Max-Forwards: 70\r\n"
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n\r\n"
            f"{sdp}"
        )
        self.sock.sendto(req.encode(), (self.ata_ip, self.sip_port))
        print(f"[SIP] 🔔 Ringing telephone at {self.ata_ip} (Call-ID: {call_id})")

    def hangup(self):
        """End any active call or cancel ringing."""
        if self.active_call_id:
            if not self.is_in_call:
                # Cancel pending outbound call
                cancel = (
                    f"CANCEL sip:{self.ata_ip}:{self.sip_port} SIP/2.0\r\n"
                    f"Via: SIP/2.0/UDP {self.mac_ip}:{self.sip_port};branch=z9hG4bK-cancel-{uuid.uuid4().hex[:6]}\r\n"
                    f"From: <sip:ai@{self.mac_ip}>;tag={self.from_tag}\r\n"
                    f"To: <sip:{EXTENSION}@{self.ata_ip}>\r\n"
                    f"Call-ID: {self.active_call_id}\r\n"
                    f"CSeq: 1 CANCEL\r\n"
                    f"Max-Forwards: 70\r\n"
                    f"Content-Length: 0\r\n\r\n"
                )
                self.sock.sendto(cancel.encode(), (self.ata_ip, self.sip_port))
                print("[SIP] 🛑 CANCEL sent from Mac (Stopped ringing).")
            elif self.remote_sip_addr:
                bye = (
                    f"BYE sip:{EXTENSION}@{self.remote_sip_addr[0]}:{self.remote_sip_addr[1]} SIP/2.0\r\n"
                    f"Via: SIP/2.0/UDP {self.mac_ip}:{self.sip_port};branch=z9hG4bK-bye-{uuid.uuid4().hex[:6]}\r\n"
                    f"From: {self.active_to}\r\n"
                    f"To: {self.active_from}\r\n"
                    f"Call-ID: {self.active_call_id}\r\n"
                    f"CSeq: 2 BYE\r\n"
                    f"Max-Forwards: 70\r\n"
                    f"Content-Length: 0\r\n\r\n"
                )
                self.sock.sendto(bye.encode(), self.remote_sip_addr)
                print("[SIP] 📴 BYE sent from Mac.")
        if self.media_session:
            self.media_session.stop()
            self.media_session = None
        self.is_in_call = False
        self.active_call_id = None

    def play_ready_tone(self, style: str = None):
        """Play a very pleasant, warm chime."""
        if self.media_session and self.media_session.running:
            tone = ai_handler.generate_ready_tone(style=style) if style else ai_handler.generate_ready_tone()
            self.media_session.send_audio(tone)

    def play_response_to_phone(self, raw_mulaw_audio: bytes):
        """Play voice response back into telephone handset."""
        if self.media_session and self.media_session.running:
            self.media_session.send_audio(raw_mulaw_audio)
            ready_tone = ai_handler.generate_ready_tone()
            self.media_session.send_audio(ready_tone)

    def _control_http_server(self):
        """Lightweight HTTP server on localhost:5065 for easy control & testing."""
        server_ref = self

        class ControlHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/call":
                    server_ref.call_phone()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ringing", "message": "Calling telephone..."}')
                elif self.path == "/hangup":
                    server_ref.hangup()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok", "message": "Call terminated"}')
                elif self.path.startswith("/test-tone"):
                    import urllib.parse
                    parsed = urllib.parse.urlparse(self.path)
                    params = urllib.parse.parse_qs(parsed.query)
                    style = params.get("style", [None])[0]
                    server_ref.play_ready_tone(style=style)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(f'{{"status": "ok", "style": "{style or config.READY_TONE_STYLE}"}}'.encode())
                elif self.path == "/status":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    in_call = "true" if server_ref.is_in_call else "false"
                    self.wfile.write(f'{{"in_call": {in_call}, "mac_ip": "{server_ref.mac_ip}"}}'.encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path == "/play":
                    length = int(self.headers.get("Content-Length", 0))
                    raw_audio = self.rfile.read(length) if length > 0 else b""
                    if server_ref.media_session and server_ref.media_session.running:
                        server_ref.media_session.send_audio(raw_audio)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"status": "ok", "message": "Audio played"}')
                    else:
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"status": "error", "message": "Phone not in call"}')
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass  # quiet HTTP logging

        try:
            httpd = http.server.HTTPServer(("127.0.0.1", 5065), ControlHandler)
            httpd.serve_forever()
        except Exception:
            pass
