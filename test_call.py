import socket
import struct
import time
import uuid
import re

IP_BOUND_IF = 25
idx = socket.if_nametoindex('en0')

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, struct.pack('I', idx))
sock.bind(('192.168.1.17', 5060))
sock.settimeout(1.0)

ata_ip = '192.168.1.10'
ata_port = 5060
mac_ip = '192.168.1.17'

call_id = f"test-{uuid.uuid4().hex[:8]}@{mac_ip}"
from_tag = uuid.uuid4().hex[:8]

sdp = (
    "v=0\r\n"
    f"o=- {int(time.time())} 1 IN IP4 {mac_ip}\r\n"
    "s=-\r\n"
    f"c=IN IP4 {mac_ip}\r\n"
    "t=0 0\r\n"
    "m=audio 20000 RTP/AVP 0 101\r\n"
    "a=rtpmap:0 PCMU/8000\r\n"
    "a=rtpmap:101 telephone-event/8000\r\n"
    "a=fmtp:101 0-16\r\n"
    "a=sendrecv\r\n"
    "a=ptime:20\r\n"
)

invite = (
    f"INVITE sip:1001@{ata_ip}:{ata_port} SIP/2.0\r\n"
    f"Via: SIP/2.0/UDP {mac_ip}:5060;branch=z9hG4bK-{uuid.uuid4().hex[:6]};rport\r\n"
    f"From: <sip:1000@{mac_ip}>;tag={from_tag}\r\n"
    f"To: <sip:1001@{ata_ip}>\r\n"
    f"Call-ID: {call_id}\r\n"
    f"CSeq: 1 INVITE\r\n"
    f"Contact: <sip:1000@{mac_ip}:5060>\r\n"
    f"Max-Forwards: 70\r\n"
    f"Content-Type: application/sdp\r\n"
    f"Content-Length: {len(sdp)}\r\n\r\n"
    f"{sdp}"
)

print(f"--- SENDING INVITE to {ata_ip}:{ata_port} ---")
sock.sendto(invite.encode(), (ata_ip, ata_port))

start_time = time.time()
answered = False

while time.time() - start_time < 30:
    try:
        data, addr = sock.recvfrom(4096)
        raw = data.decode('utf-8', errors='ignore')
        print(f"\n<<< RECEIVED FROM {addr} ({time.time() - start_time:.2f}s) <<<\n{raw}")
        
        # Check if 200 OK
        if "200 OK" in raw and "CSeq: 1 INVITE" in raw:
            answered = True
            # Extract To header, Contact header
            to_match = re.search(r"To:\s*([^\r\n]+)", raw, re.IGNORECASE)
            to_hdr = to_match.group(1) if to_match else f"<sip:1001@{ata_ip}>"
            
            contact_match = re.search(r"Contact:\s*([^\r\n]+)", raw, re.IGNORECASE)
            contact_hdr = contact_match.group(1) if contact_match else f"<sip:1001@{ata_ip}:5060>"
            
            # Extract req-uri from Contact
            m_uri = re.search(r"<sip:([^>]+)>", contact_hdr)
            if m_uri:
                req_uri = f"sip:{m_uri.group(1)}"
            else:
                m_plain = re.search(r"(sip:[^\s;>]+)", contact_hdr)
                req_uri = m_plain.group(1) if m_plain else f"sip:1001@{ata_ip}:5060"
            
            # Send RFC-compliant ACK
            ack = (
                f"ACK {req_uri} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP {mac_ip}:5060;branch=z9hG4bK-{uuid.uuid4().hex[:6]}\r\n"
                f"From: <sip:1000@{mac_ip}>;tag={from_tag}\r\n"
                f"To: {to_hdr}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: 1 ACK\r\n"
                f"Max-Forwards: 70\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            print(f"\n>>> SENDING ACK >>>\n{ack}")
            sock.sendto(ack.encode(), addr)
            
        elif "BYE" in raw:
            print("\n!!! ATA SENT BYE !!!")
            # Reply 200 OK to BYE
            cseq_m = re.search(r"CSeq:\s*([^\r\n]+)", raw, re.IGNORECASE)
            cseq_str = cseq_m.group(1) if cseq_m else "1 BYE"
            to_m = re.search(r"To:\s*([^\r\n]+)", raw, re.IGNORECASE)
            to_str = to_m.group(1) if to_m else ""
            from_m = re.search(r"From:\s*([^\r\n]+)", raw, re.IGNORECASE)
            from_str = from_m.group(1) if from_m else ""
            via_m = re.search(r"Via:\s*([^\r\n]+)", raw, re.IGNORECASE)
            via_str = via_m.group(1) if via_m else ""
            
            bye_ok = (
                f"SIP/2.0 200 OK\r\n"
                f"Via: {via_str}\r\n"
                f"From: {from_str}\r\n"
                f"To: {to_str}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq_str}\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            sock.sendto(bye_ok.encode(), addr)
            break
            
    except socket.timeout:
        pass

sock.close()
