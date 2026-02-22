"""Test suite for Lieutenant — OpenClaw, Agent Gateway, Voice Daemon."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8800"))
DAEMON_PORT = int(os.getenv("VOICE_DAEMON_PORT", "8765"))
OPENCLAW_WS_URL = os.getenv("OPENCLAW_WS_URL", "ws://127.0.0.1:18789/ws")
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN", "ea2638236c394cc7c6f0e030aba10a5e0cc0378baffef2a9")

GATEWAY_BASE = f"http://127.0.0.1:{GATEWAY_PORT}"
DAEMON_BASE = f"http://127.0.0.1:{DAEMON_PORT}"

_passed = 0
_failed = 0
_errors: list[str] = []


def _report(name: str, ok: bool, detail: str = ""):
    global _passed, _failed
    icon = "✅" if ok else "❌"
    print(f"  {icon} {name}" + (f"  — {detail}" if detail else ""))
    if ok:
        _passed += 1
    else:
        _failed += 1
        _errors.append(f"{name}: {detail}")


# ═══════════════════════════════════════════════════════════════════════
#  1. OpenClaw WebSocket connectivity
# ═══════════════════════════════════════════════════════════════════════
async def test_openclaw_ws():
    """Test OpenClaw WS connection, authentication, and agent request."""
    print("\n🔌 OpenClaw WebSocket Tests")
    print("─" * 40)

    try:
        import websockets
    except ImportError:
        _report("import websockets", False, "websockets package not installed")
        return

    # Test 1: Connect and authenticate
    try:
        async with websockets.connect(OPENCLAW_WS_URL, close_timeout=5) as ws:
            # Expect challenge
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            challenge = json.loads(raw)
            ok = challenge.get("type") == "event" and challenge.get("event") == "connect.challenge"
            _report("WS connect + challenge", ok, f"got event: {challenge.get('event', 'none')}")

            if not ok:
                return

            # Send connect
            connect_req = {
                "type": "req",
                "id": uuid.uuid4().hex,
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 3,
                    "client": {"id": "test", "displayName": "Test", "version": "0.1.0", "platform": "test", "mode": "test"},
                    "caps": [],
                    "auth": {"token": OPENCLAW_TOKEN},
                    "role": "operator",
                    "scopes": ["operator.admin"],
                },
            }
            await ws.send(json.dumps(connect_req))
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            hello = json.loads(raw)
            ok = hello.get("ok") is True
            _report("WS authenticate", ok, hello.get("error", {}).get("message", "OK") if not ok else "authenticated")

            if not ok:
                return

            # Test 2: Send agent request
            agent_id = uuid.uuid4().hex
            agent_req = {
                "type": "req",
                "id": agent_id,
                "method": "agent",
                "params": {
                    "message": "Say hello in two words",
                    "agentId": "main",
                    "idempotencyKey": uuid.uuid4().hex,
                },
            }
            await ws.send(json.dumps(agent_req))

            got_stream = False
            got_final = False
            response_text = ""
            t0 = time.time()

            while time.time() - t0 < 30:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                msg = json.loads(raw)

                if msg.get("type") == "event" and msg.get("event") == "agent":
                    payload = msg.get("payload", {})
                    if payload.get("stream") == "assistant":
                        delta = payload.get("data", {}).get("delta", "")
                        response_text += delta
                        got_stream = True

                # "accepted" res — just acknowledgement, keep waiting
                if msg.get("type") == "res" and msg.get("id") == agent_id:
                    payload = msg.get("payload", {})
                    if payload.get("status") == "accepted":
                        continue

                # Final chat event
                if msg.get("type") == "event" and msg.get("event") == "chat":
                    if msg.get("payload", {}).get("state") == "final":
                        got_final = True
                        # Extract text from final event if no streaming
                        if not response_text:
                            payloads = msg.get("payload", {}).get("result", {}).get("payloads", [])
                            if payloads:
                                response_text = payloads[0].get("text", "")
                        break

            _report("Agent streaming", got_stream, f"received streaming deltas")
            _report("Agent final response", got_final and bool(response_text), f"\"{response_text[:80]}\"")
            elapsed = time.time() - t0
            _report("Agent response time", elapsed < 15, f"{elapsed:.1f}s")

    except Exception as e:
        _report("WS connection", False, str(e))


# ═══════════════════════════════════════════════════════════════════════
#  2. Agent Gateway HTTP API
# ═══════════════════════════════════════════════════════════════════════
async def test_agent_gateway():
    """Test Agent Gateway HTTP endpoints."""
    print("\n🤖 Agent Gateway Tests")
    print("─" * 40)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Test: Models endpoint
        try:
            r = await client.get(f"{GATEWAY_BASE}/v1/models")
            ok = r.status_code == 200 and "data" in r.json()
            _report("GET /v1/models", ok, f"status={r.status_code}")
        except Exception as e:
            _report("GET /v1/models", False, str(e))

        # Test: Language GET
        try:
            r = await client.get(f"{GATEWAY_BASE}/v1/language")
            ok = r.status_code == 200 and "language" in r.json()
            lang = r.json().get("language", "?")
            _report("GET /v1/language", ok, f"language={lang}")
        except Exception as e:
            _report("GET /v1/language", False, str(e))

        # Test: Language SET
        try:
            r = await client.post(f"{GATEWAY_BASE}/v1/language", json={"language": "en"})
            ok = r.status_code == 200 and r.json().get("language") == "en"
            _report("POST /v1/language (en)", ok)

            # Reset to Greek
            await client.post(f"{GATEWAY_BASE}/v1/language", json={"language": "el"})
        except Exception as e:
            _report("POST /v1/language", False, str(e))

        # Test: Chat completions (non-streaming)
        try:
            r = await client.post(
                f"{GATEWAY_BASE}/v1/chat/completions",
                json={
                    "model": "local-agent",
                    "messages": [{"role": "user", "content": "Πες γεια σε 2 λέξεις."}],
                    "stream": False,
                },
            )
            data = r.json()
            ok = r.status_code == 200 and len(data.get("choices", [{}])[0].get("message", {}).get("content", "")) > 0
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")[:60]
            _report("POST /v1/chat/completions (non-stream)", ok, f"\"{content}\"")
        except Exception as e:
            _report("POST /v1/chat/completions (non-stream)", False, str(e))

        # Test: Chat completions (streaming)
        try:
            tokens = []
            async with client.stream(
                "POST",
                f"{GATEWAY_BASE}/v1/chat/completions",
                json={
                    "model": "local-agent",
                    "messages": [{"role": "user", "content": "Say hi."}],
                    "stream": True,
                },
                headers={"Accept": "text/event-stream"},
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta:
                                tokens.append(delta)
                        except json.JSONDecodeError:
                            pass

            full = "".join(tokens)
            ok = len(tokens) > 0
            _report("POST /v1/chat/completions (stream)", ok, f"{len(tokens)} tokens, \"{full[:60]}\"")
        except Exception as e:
            _report("POST /v1/chat/completions (stream)", False, str(e))


# ═══════════════════════════════════════════════════════════════════════
#  3. Voice Daemon HTTP API
# ═══════════════════════════════════════════════════════════════════════
async def test_voice_daemon():
    """Test Voice Daemon HTTP endpoints."""
    print("\n🎙️ Voice Daemon Tests")
    print("─" * 40)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Test: Status
        try:
            r = await client.get(f"{DAEMON_BASE}/status")
            data = r.json()
            ok = r.status_code == 200 and "state" in data
            state = data.get("state", "?")
            mic_ok = data.get("mic", {}).get("healthy", False)
            _report("GET /status", ok, f"state={state}")
            _report("Microphone healthy", mic_ok, f"device={data.get('mic', {}).get('device', '?')}")
        except Exception as e:
            _report("GET /status", False, str(e))

        # Test: Language GET
        try:
            r = await client.get(f"{DAEMON_BASE}/control/language")
            ok = r.status_code == 200 and "language" in r.json()
            _report("GET /control/language", ok, f"lang={r.json().get('language', '?')}")
        except Exception as e:
            _report("GET /control/language", False, str(e))

        # Test: Language SET
        try:
            r = await client.post(f"{DAEMON_BASE}/control/language", json={"language": "en"})
            ok = r.status_code == 200 and r.json().get("language") == "en"
            _report("POST /control/language (en)", ok)

            # Reset to Greek
            await client.post(f"{DAEMON_BASE}/control/language", json={"language": "el"})
            _report("POST /control/language reset (el)", True)
        except Exception as e:
            _report("POST /control/language", False, str(e))


# ═══════════════════════════════════════════════════════════════════════
#  4. Voice Daemon WebSocket
# ═══════════════════════════════════════════════════════════════════════
async def test_daemon_ws():
    """Test Voice Daemon WebSocket connection."""
    print("\n📡 Voice Daemon WebSocket Tests")
    print("─" * 40)

    try:
        import websockets
    except ImportError:
        _report("import websockets", False, "websockets not installed")
        return

    try:
        async with websockets.connect(f"ws://127.0.0.1:{DAEMON_PORT}/ws", close_timeout=3) as ws:
            # Should receive state message
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            ok = msg.get("type") == "state"
            _report("WS connect + state msg", ok, f"state={msg.get('value', '?')}")

            # Wait for a mic level message (drain log history first)
            got_mic = False
            for _ in range(300):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                if msg.get("type") == "mic.level":
                    got_mic = True
                    break
            _report("Receiving mic levels", got_mic)

    except Exception as e:
        _report("WS connection", False, str(e))


# ═══════════════════════════════════════════════════════════════════════
#  5. End-to-end: Wake → STT → Agent → TTS
# ═══════════════════════════════════════════════════════════════════════
async def test_e2e_wake():
    """Test wake trigger produces state transitions."""
    print("\n🔄 End-to-End Wake Test")
    print("─" * 40)

    try:
        import websockets
    except ImportError:
        _report("websockets import", False)
        return

    async with httpx.AsyncClient(timeout=10.0) as http:
        try:
            # Ensure IDLE first
            status_r = await http.get(f"{DAEMON_BASE}/status")
            current = status_r.json().get("state", "?")
            if current != "IDLE":
                await http.post(f"{DAEMON_BASE}/control/stop")
                await asyncio.sleep(0.5)

            async with websockets.connect(f"ws://127.0.0.1:{DAEMON_PORT}/ws", close_timeout=5) as ws:
                # Drain initial messages
                for _ in range(10):
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=0.3)
                    except asyncio.TimeoutError:
                        break

                # Trigger wake
                r = await http.post(f"{DAEMON_BASE}/control/wake")
                ok = r.status_code == 200
                _report("POST /control/wake", ok)

                # Watch for state transitions
                states_seen = set()
                t0 = time.time()
                while time.time() - t0 < 5:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1)
                        msg = json.loads(raw)
                        if msg.get("type") == "state":
                            states_seen.add(msg["value"])
                    except asyncio.TimeoutError:
                        continue

                _report("State → SPEAKING (ack)", "SPEAKING" in states_seen, f"states: {states_seen}")
                _report("State → LISTENING", "LISTENING" in states_seen, f"states: {states_seen}")

                # Kill to return to IDLE
                await http.post(f"{DAEMON_BASE}/control/stop")

        except Exception as e:
            _report("E2E wake", False, str(e))


# ═══════════════════════════════════════════════════════════════════════
#  Run all
# ═══════════════════════════════════════════════════════════════════════
async def main():
    print("=" * 50)
    print("  LIEUTENANT TEST SUITE")
    print("=" * 50)
    print(f"  Gateway: {GATEWAY_BASE}")
    print(f"  Daemon:  {DAEMON_BASE}")
    print(f"  OpenClaw: {OPENCLAW_WS_URL}")

    await test_openclaw_ws()
    await test_agent_gateway()
    await test_voice_daemon()
    await test_daemon_ws()
    await test_e2e_wake()

    print("\n" + "=" * 50)
    print(f"  RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 50)

    if _errors:
        print("\n  Failures:")
        for e in _errors:
            print(f"    ❌ {e}")

    return _failed == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
