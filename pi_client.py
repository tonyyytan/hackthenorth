"""Drop-in transport from the Raspberry Pi's mic capture to server.py's /utterance endpoint.

Your Pi already captures audio and (presumably) has its own code deciding when an
utterance is "done" (silence detection, VAD, whatever). This module is just the last
step: get that utterance to the machine running server.py over the LAN.

Auto-discovers the server the same way ServerDiscovery.cs does on the Quest --
listens for server.py's UDP broadcast (see server.py's _broadcast_presence) instead
of needing a hardcoded IP, so it survives the venue Wi-Fi handing out a different
address than expected.

Usage from your existing Pi code:

    import pi_client

    # once, at startup -- blocks until it hears the broadcast
    server = pi_client.discover(timeout=15)

    # each time your VAD/silence-detection decides an utterance is complete:
    pi_client.send_text(server, text="so what have you been working on")

    # or, if you'd rather let server.py transcribe (OpenAI-compatible STT,
    # needs STT_API_KEY or OPENAI_API_KEY set in server/.env):
    pi_client.send_audio(server, wav_bytes)

person_id is optional in both -- server.py defaults it to FOCUS (whoever the Quest's
own face detection currently sees as the biggest/closest face), which is usually
exactly right: the Pi doesn't need to know who it's listening to, since the Quest's
camera already knows who the wearer is looking at.

Standalone self-test (no mic needed):
    python pi_client.py --test-text "hello, testing the pipeline"
"""
import argparse
import base64
import json
import os
import socket
import urllib.request

DISCOVERY_PORT = 41234
DISCOVERY_MAGIC = b"HACKTHENORTH_ID_SERVER:"


def discover(timeout=15.0):
    """Blocks until server.py's presence broadcast is heard, returns 'http://ip:port'.
    Raises TimeoutError if nothing is heard -- check both devices are on the same
    network and that server.py is actually running.

    If the SERVER_URL env var is set, returns it immediately with no network wait --
    some networks (e.g. phone personal hotspots) restrict broadcast/multicast between
    connected clients even though normal HTTP between them works fine. Set this as a
    manual fallback: SERVER_URL=http://<lan-ip>:8000"""
    override = os.environ.get("SERVER_URL")
    if override:
        print(f"pi_client: using SERVER_URL override: {override}")
        return override

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(timeout)
    try:
        while True:
            data, (ip, _) = sock.recvfrom(1024)
            if data.startswith(DISCOVERY_MAGIC):
                port = data[len(DISCOVERY_MAGIC):].decode()
                url = f"http://{ip}:{port}"
                print(f"pi_client: found server at {url}")
                return url
    except socket.timeout:
        raise TimeoutError(
            f"pi_client: no server broadcast heard in {timeout}s -- "
            "is server.py running, and is this Pi on the same network as it?")
    finally:
        sock.close()


def _post(server_url, payload, timeout=15.0):
    req = urllib.request.Request(
        f"{server_url}/utterance", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def send_text(server_url, text, person_id=None):
    """person_id=None lets server.py default to FOCUS (the Quest's currently-seen face)."""
    payload = {"text": text}
    if person_id:
        payload["person_id"] = person_id
    return _post(server_url, payload)


def send_audio(server_url, wav_bytes, person_id=None):
    """wav_bytes: raw WAV file bytes. Transcribed server-side (needs STT_API_KEY/
    OPENAI_API_KEY set in server/.env) before being buffered as an utterance."""
    payload = {"audio_b64": base64.b64encode(wav_bytes).decode()}
    if person_id:
        payload["person_id"] = person_id
    return _post(server_url, payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-text", help="Send this text as a one-shot self-test.")
    parser.add_argument("--server", help="Skip discovery, use this URL directly (http://ip:8000).")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    url = args.server or discover(args.timeout)
    if args.test_text:
        print(send_text(url, args.test_text))
    else:
        print(f"pi_client: server is {url}. Pass --test-text to send a real test utterance.")
