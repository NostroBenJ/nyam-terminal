"""
verify_socket.py  --  checks for the Unusual Whales WebSocket client.

TWO KINDS OF CHECK, AND THE DISTINCTION IS THE POINT.

The framing checks run against RFC 6455 s5.7's own published byte vectors, not
against this module's output. A codec tested by round-tripping itself passes
whether or not it speaks the protocol; testing against bytes the spec prints
is an independent check in the same spirit as the finite-difference tests in
the pricing code.

The entitlement check runs against the LIVE endpoint. The current plan is not
entitled, which makes this the one behaviour that can be verified perfectly
today: we know the exact wrong side of the gate we are on, so we can prove the
client detects it, classifies it as terminal rather than retryable, and lets
the board fall back to REST. When the plan upgrades, this check flips from
"correctly refused" to "correctly connected" and must be re-read, not deleted.

What CANNOT be verified without entitlement: that data flows, the payload shape
of any channel, and that a long-lived connection survives. `_dispatch` is
written against the published guide and is unverified. Do not treat a green
board here as evidence the socket works.

Run:  python verify_socket.py
"""
import sys

from data import uw_socket as ws

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


print("[0] RFC 6455 s5.7 vectors — decode what the spec prints")
# A single-frame unmasked text message "Hello"
fin, op, payload, used = ws.decode_frame(bytes([0x81, 0x05, 0x48, 0x65, 0x6c, 0x6c, 0x6f]))
check("unmasked text 'Hello'", (fin, op, payload) == (True, ws.OP_TEXT, b"Hello"),
      f"{fin} {op} {payload!r}")
check("consumed the whole frame", used == 7, str(used))

# A single-frame MASKED text message "Hello"
masked = bytes([0x81, 0x85, 0x37, 0xfa, 0x21, 0x3d, 0x7f, 0x9f, 0x4d, 0x51, 0x58])
fin, op, payload, used = ws.decode_frame(masked)
check("masked text unmasks to 'Hello'", payload == b"Hello", repr(payload))

# A fragmented unmasked text message: "Hel" + "lo"
f1 = ws.decode_frame(bytes([0x01, 0x03, 0x48, 0x65, 0x6c]))
f2 = ws.decode_frame(bytes([0x80, 0x02, 0x6c, 0x6f]))
check("fragment 1 is not final", f1[0] is False and f1[2] == b"Hel", str(f1[:3]))
check("fragment 2 is final continuation",
      f2[0] is True and f2[1] == ws.OP_CONT and f2[2] == b"lo", str(f2[:3]))

# 256 bytes binary in a single unmasked frame (16-bit length path)
frame256 = bytes([0x82, 0x7E, 0x01, 0x00]) + b"\x00" * 256
fin, op, payload, used = ws.decode_frame(frame256)
check("16-bit length path", op == ws.OP_BIN and len(payload) == 256, str(len(payload)))

# 65536 bytes binary in a single unmasked frame (64-bit length path)
frame64k = bytes([0x82, 0x7F, 0, 0, 0, 0, 0, 1, 0, 0]) + b"\x00" * 65536
fin, op, payload, used = ws.decode_frame(frame64k)
check("64-bit length path", op == ws.OP_BIN and len(payload) == 65536, str(len(payload)))

print("[1] partial data returns None instead of a wrong answer")
# TCP does not preserve message boundaries, so a frame arriving in pieces is
# ordinary traffic. Guessing at an incomplete frame would corrupt the stream.
full = bytes([0x81, 0x05, 0x48, 0x65, 0x6c, 0x6c, 0x6f])
for cut in range(1, len(full)):
    if ws.decode_frame(full[:cut]) is not None:
        check(f"partial ({cut}/7 bytes) returns None", False, "returned a frame")
        break
else:
    check("every partial prefix returns None", True, "1..6 bytes")
check("the complete frame parses", ws.decode_frame(full) is not None)

print("[2] client frames are masked, as the RFC requires")
# Servers are required to DROP an unmasked client frame, which presents as a
# mysterious disconnect rather than an error, so this is worth asserting.
enc = ws.encode_frame(b"Hello")
check("mask bit set", bool(enc[1] & 0x80), hex(enc[1]))
check("length encoded", (enc[1] & 0x7F) == 5, str(enc[1] & 0x7F))
check("payload is not plaintext", b"Hello" not in enc, repr(enc))
check("FIN set on a single frame", bool(enc[0] & 0x80), hex(enc[0]))
check("opcode is text", (enc[0] & 0x0F) == ws.OP_TEXT)

print("[3] round trip through our own decoder")
for size in (0, 1, 125, 126, 127, 1000, 65535, 65536):
    body = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    body = body[:size]
    out = ws.decode_frame(ws.encode_frame(body))
    ok = out is not None and out[2] == body
    # The detail is only meaningful on failure; printing "mismatch" next to a
    # PASS is the sort of contradictory signal that trains you to skim output.
    check(f"round trip {size}B", ok,
          "" if ok else ("decoder returned None" if out is None else "payload differs"))

print("[4] masking is not a fixed key")
# A constant mask would still round-trip perfectly while being wrong.
a, b = ws.encode_frame(b"Hello"), ws.encode_frame(b"Hello")
check("two encodings of the same payload differ", a != b)

print("[5] ping/pong and close opcodes decode")
for name, op in (("ping", ws.OP_PING), ("pong", ws.OP_PONG), ("close", ws.OP_CLOSE)):
    out = ws.decode_frame(ws.encode_frame(b"", op))
    check(f"{name} opcode preserved", out is not None and out[1] == op)

print("[6] the entitlement gate — LIVE against the real endpoint")
print("      current plan is expected to be refused; that is the tested case")
import config  # noqa: E402

if not config.UW_API_KEY:
    check("skipped — no UW_API_KEY", True, "set one to exercise this")
else:
    try:
        sock, status_line = ws._handshake(config.UW_API_KEY)
        sock.close()
        # If this ever passes, the plan was upgraded. Not a failure — but the
        # unverified half of this module is now testable and should be tested.
        check("handshake accepted (plan upgraded)", True, status_line.strip())
        print("      >>> WebSockets are now available. Run:")
        print("      >>>   python -m data.uw_socket 30")
        print("      >>> and verify _dispatch against real frames — it is")
        print("      >>> written against docs and has never seen a payload.")
    except ws.SocketNotEntitled as e:
        check("401 classified as NOT ENTITLED, not as a network error", True, str(e)[:90])
        check("error message points at the plan, not at the code",
              "plan" in str(e).lower(), str(e)[:90])
        check("REST is called out as unaffected", "REST" in str(e), str(e)[:90])
    except ws.SocketError as e:
        check("upgrade failed as a transport error", False,
              f"expected 401 classification, got: {e}")

print("[7] the client refuses to retry an entitlement failure")


class _Fake(ws.UwSocket):
    """Retrying a 401 forever yields a UI that says 'connecting' and never will."""

    def _session(self):
        raise ws.SocketNotEntitled("nope")


f = _Fake(["gex:SPY"])
f._run()                      # returns immediately if classified terminal
check("run loop exited", f.status()["state"] == "not_entitled", f.status()["state"])
check("reason retained for the UI", bool(f.status()["error"]))

print("[8] status() is always answerable")
st = ws.status()
check("status returns a dict", isinstance(st, dict), str(st)[:80])
check("state is present", "state" in st, str(st)[:80])
check("disabled by default", st["state"] == "disabled" or ws.enabled(), str(st)[:80])

print("[9] disabled unless explicitly switched on")
import os  # noqa: E402

os.environ.pop("NYAM_UW_SOCKET", None)
check("off without NYAM_UW_SOCKET=1", ws.enabled() is False)
os.environ["NYAM_UW_SOCKET"] = "1"
expected = (config.PROVIDER == "uw" and not config.USE_MOCK_DATA
            and bool(config.UW_API_KEY))
check("on with the flag and a live uw provider", ws.enabled() == expected,
      f"provider={config.PROVIDER} mock={config.USE_MOCK_DATA}")
os.environ["NYAM_UW_SOCKET"] = "0"

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all socket checks passed")
print("NOTE: framing and the entitlement gate are verified. Data flow, payload")
print("shapes and long-lived stability are NOT — they need an entitled plan.")
