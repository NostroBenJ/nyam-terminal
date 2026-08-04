"""
uw_socket.py  --  Unusual Whales WebSocket client. Stdlib only.

WHY THIS EXISTS BEFORE THE ENTITLEMENT DOES
The current trial key is NOT entitled to WebSockets: `wss://api.unusualwhales.com/socket`
answers 401 Unauthorized ("Contact support@unusualwhales.com for more
information"). The plan is to upgrade, so this is built now and gated off,
rather than bolted on later against a live subscription with real money on the
screen.

WHAT IS AND IS NOT VERIFIED
Verified today: the frame codec against RFC 6455's own test vectors, the
handshake, and the entitlement gate itself — being on the wrong side of a 401
is the one thing we can test perfectly, and `verify_socket.py` asserts we
detect it, report it, and fall back rather than retrying into a wall.
NOT verified: that data flows, the payload shape of any channel, or that a
long-lived connection survives. Those need entitlement. Everything downstream
of `_dispatch` is written against the published channel docs and should be
treated as unverified until `probe_socket()` prints real frames.

A NOTE ON THE PATH. It is `/socket`, not `/api/socket`. `/api/socket` is the
REST endpoint that *documents* the channels; it accepts a WebSocket upgrade and
then never speaks the protocol, so it returns a completely convincing
101 Switching Protocols and no data. That false positive cost an hour and a
wrong answer to the user — hence the constant below and this paragraph.

NO THIRD-PARTY DEPENDENCY. RFC 6455 framing is about eighty lines and the
alternative is adding `websockets` to a PyInstaller bundle for the sake of
them. The pricing core stays importable with zero dependencies; this keeps the
data layer honest to the same standard.
"""
import base64
import json
import os
import socket
import ssl
import struct
import threading
import time

import config
from data import unusual_whales as uw

HOST = "api.unusualwhales.com"
# NOT "/api/socket". See the module docstring; this distinction is load-bearing.
PATH = "/socket"
PORT = 443

# Frame opcodes (RFC 6455 s5.2)
OP_CONT, OP_TEXT, OP_BIN = 0x0, 0x1, 0x2
OP_CLOSE, OP_PING, OP_PONG = 0x8, 0x9, 0xA

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 30           # server sends heartbeats; silence past this is dead
RECONNECT_BASE = 2.0
RECONNECT_MAX = 60.0


class SocketNotEntitled(RuntimeError):
    """
    The key is valid for REST but not for WebSockets (HTTP 401 on upgrade).

    Distinct from a network failure ON PURPOSE. A network error should be
    retried with backoff; an entitlement failure should stop immediately and
    say so, because retrying it forever produces a dashboard that looks like
    it is connecting and never will.
    """


class SocketError(RuntimeError):
    """Transport-level failure. Retryable."""


# ---------------------------------------------------------------------------
# RFC 6455 framing
# ---------------------------------------------------------------------------
def encode_frame(payload: bytes, opcode: int = OP_TEXT) -> bytes:
    """
    Build a masked client frame.

    Client-to-server frames MUST be masked (RFC 6455 s5.3) and servers are
    required to drop the connection if they are not — which presents as a
    mysterious immediate disconnect rather than an error message, so this is
    not a detail worth improvising.
    """
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    if n < 126:
        header = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
    elif n < 65536:
        header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
    return header + mask + masked


def decode_frame(data: bytes):
    """
    Parse one server frame from `data`.

    Returns (fin, opcode, payload, bytes_consumed), or None when `data` does
    not yet hold a complete frame. Returning None rather than raising is what
    lets the reader accumulate across recv() boundaries: TCP does not preserve
    message boundaries, so a frame arriving in three pieces is normal traffic
    and not an error.
    """
    if len(data) < 2:
        return None
    b0, b1 = data[0], data[1]
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    ln = b1 & 0x7F
    off = 2
    if ln == 126:
        if len(data) < off + 2:
            return None
        ln = struct.unpack("!H", data[off:off + 2])[0]
        off += 2
    elif ln == 127:
        if len(data) < off + 8:
            return None
        ln = struct.unpack("!Q", data[off:off + 8])[0]
        off += 8
    mask = b""
    if masked:
        # Servers must not mask. Handled anyway rather than trusted.
        if len(data) < off + 4:
            return None
        mask = data[off:off + 4]
        off += 4
    if len(data) < off + ln:
        return None
    payload = data[off:off + ln]
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload, off + ln


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------
def _handshake(token: str):
    """
    Open the TLS socket and perform the upgrade.

    Raises SocketNotEntitled on 401/403 so the caller can stop rather than
    retry, and SocketError on anything else.
    """
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {PATH}?token={token} HTTP/1.1\r\n"
        f"Host: {HOST}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        # Same Cloudflare edge as the REST API, same User-Agent requirement:
        # urllib's default is blocked with error 1010 before reaching the app.
        f"User-Agent: {uw.USER_AGENT}\r\n"
        "\r\n"
    )
    raw = socket.create_connection((HOST, PORT), timeout=CONNECT_TIMEOUT)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)
    sock.sendall(req.encode())

    header = b""
    sock.settimeout(CONNECT_TIMEOUT)
    while b"\r\n\r\n" not in header:
        chunk = sock.recv(1)
        if not chunk:
            break
        header += chunk
        if len(header) > 16384:
            break
    text = header.decode("utf-8", "replace")
    status_line = text.split("\r\n")[0] if text else ""

    if " 101 " in status_line:
        return sock, status_line
    sock.close()
    if " 401 " in status_line or " 403 " in status_line:
        raise SocketNotEntitled(
            f"WebSocket not available on this plan ({status_line.strip()}). "
            "REST endpoints are unaffected."
        )
    raise SocketError(f"upgrade refused: {status_line.strip() or 'no response'}")


class UwSocket:
    """
    A background WebSocket consumer.

    Owns a thread, a connection, and the newest payload seen per channel. The
    REST path stays authoritative for anything this has not received; a socket
    that has been up for four seconds knows almost nothing, and treating its
    empty cache as "no data" would blank a board that REST could have filled.
    """

    def __init__(self, channels, on_message=None):
        self.channels = list(channels)
        self.on_message = on_message
        self.latest = {}
        self.state = "idle"          # idle|connecting|open|closed|not_entitled|error
        self.error = None
        self.connected_at = None
        self.frames = 0
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="uw-socket")
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
        if self._thread:
            self._thread.join(timeout=5)

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self.state,
                "error": self.error,
                "channels": list(self.channels),
                "frames": self.frames,
                "uptime_s": (int(time.time() - self.connected_at)
                             if self.connected_at else None),
                "channels_seen": sorted(self.latest.keys()),
            }

    def get(self, channel: str):
        with self._lock:
            return self.latest.get(channel)

    # -- worker -------------------------------------------------------------
    def _run(self):
        delay = RECONNECT_BASE
        while not self._stop.is_set():
            try:
                self._session()
                delay = RECONNECT_BASE          # a clean session resets backoff
            except SocketNotEntitled as e:
                # TERMINAL. Retrying an entitlement failure produces a UI that
                # says "connecting" forever; the board must fall back to REST
                # and say why, once.
                with self._lock:
                    self.state, self.error = "not_entitled", str(e)
                return
            except (SocketError, OSError, ssl.SSLError) as e:
                with self._lock:
                    self.state, self.error = "error", f"{type(e).__name__}: {e}"
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, RECONNECT_MAX)

    def _session(self):
        with self._lock:
            self.state = "connecting"
        sock, _ = _handshake(config.UW_API_KEY)
        with self._lock:
            self._sock = sock
            self.state = "open"
            self.connected_at = time.time()
            self.error = None

        for ch in self.channels:
            sock.sendall(encode_frame(
                json.dumps({"channel": ch, "msg_type": "join"}).encode()))

        sock.settimeout(READ_TIMEOUT)
        buf = b""
        pending = b""            # reassembly across fragmented frames
        try:
            while not self._stop.is_set():
                chunk = sock.recv(65536)
                if not chunk:
                    raise SocketError("server closed the connection")
                buf += chunk
                while True:
                    parsed = decode_frame(buf)
                    if parsed is None:
                        break
                    fin, opcode, payload, used = parsed
                    buf = buf[used:]
                    if opcode == OP_CLOSE:
                        raise SocketError("server sent close")
                    if opcode == OP_PING:
                        sock.sendall(encode_frame(payload, OP_PONG))
                        continue
                    if opcode == OP_PONG:
                        continue
                    pending += payload
                    if fin:
                        self._dispatch(pending)
                        pending = b""
        finally:
            with self._lock:
                self._sock = None
                if self.state == "open":
                    self.state = "closed"
            try:
                sock.close()
            except OSError:
                pass

    def _dispatch(self, payload: bytes):
        """
        UW sends [<CHANNEL_NAME>, <PAYLOAD>] per the published guide.

        UNVERIFIED against live data — no entitlement to observe one. A frame
        that does not match is kept under "_raw" rather than dropped, so the
        first real session shows the true shape instead of silently discarding
        everything.
        """
        try:
            msg = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        with self._lock:
            self.frames += 1
            if isinstance(msg, list) and len(msg) == 2 and isinstance(msg[0], str):
                self.latest[msg[0]] = msg[1]
            else:
                self.latest["_raw"] = msg
        if self.on_message:
            try:
                self.on_message(msg)
            except Exception:      # a bad callback must not kill the reader
                pass


# ---------------------------------------------------------------------------
# module-level singleton, off unless explicitly enabled
# ---------------------------------------------------------------------------
_client = None


def enabled() -> bool:
    """
    WebSockets are opt-in via NYAM_UW_SOCKET=1.

    Off by default because the current plan 401s, and a feature that cannot
    work should not be attempting to work on every engine start. Flip it the
    day the plan upgrades, then run `python -m data.uw_socket probe`.
    """
    return (config.PROVIDER == "uw"
            and not config.USE_MOCK_DATA
            and bool(config.UW_API_KEY)
            and os.getenv("NYAM_UW_SOCKET", "0") == "1")


DEFAULT_CHANNELS = ["gex:SPY", "price:SPY", "flow-alerts", "off_lit_trades"]


def client():
    global _client
    if _client is None:
        _client = UwSocket(DEFAULT_CHANNELS)
    return _client


def status() -> dict:
    """For /api/status. Always answers; never raises."""
    if not enabled():
        return {"state": "disabled",
                "reason": "NYAM_UW_SOCKET is not 1, or provider is not live uw"}
    try:
        return client().status()
    except Exception as e:  # noqa: BLE001
        return {"state": "error", "error": f"{type(e).__name__}: {e}"}


def probe(seconds: int = 20):
    """Connect, subscribe, and print whatever arrives. The day-one command."""
    print(f"connecting wss://{HOST}{PATH}")
    print(f"channels: {', '.join(DEFAULT_CHANNELS)}\n")
    c = UwSocket(DEFAULT_CHANNELS, on_message=lambda m: print(f"  {str(m)[:200]}"))
    c.start()
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(1)
        st = c.status()
        if st["state"] == "not_entitled":
            print(f"\nNOT ENTITLED: {st['error']}")
            print("REST is unaffected — the board keeps working on polling.")
            c.stop()
            return st
    st = c.status()
    print(f"\nstate={st['state']} frames={st['frames']} "
          f"channels_seen={st['channels_seen']}")
    c.stop()
    return st


if __name__ == "__main__":
    import sys
    probe(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
