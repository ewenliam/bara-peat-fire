"""One command for the live WhatsApp demo: Bara server, Cloudflare quick tunnel, Meta webhook URL.

A quick tunnel gets a new address on every start, so this script reads that address from
cloudflared and points the Meta app's WhatsApp webhook at it (POST /{app-id}/subscriptions with
an app access token; Meta checks the URL with a GET before it answers). Ctrl+C stops the server
and the tunnel; the webhook keeps the old address until the next run.

Needs in .env: WA_ACCESS_TOKEN, WA_APP_SECRET, WA_VERIFY_TOKEN. Nothing secret is printed.

Usage:
    .venv/Scripts/python src/run_demo.py            # server + tunnel + webhook update
    .venv/Scripts/python src/run_demo.py --no-meta  # server + tunnel only
"""
import argparse
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PORT = 8600
GRAPH = "https://graph.facebook.com"
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED = [r"C:\Program Files (x86)\cloudflared\cloudflared.exe", r"C:\Program Files\cloudflared\cloudflared.exe"]


def cloudflared() -> str:
    found = shutil.which("cloudflared") or next((p for p in CLOUDFLARED if Path(p).exists()), None)
    if not found:
        raise SystemExit("cloudflared not found; install it with: winget install --id Cloudflare.cloudflared")
    return found


def wait_for(url: str, ok: set[int], seconds: float, what: str) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if requests.get(url, timeout=5).status_code in ok:
                return
        except requests.RequestException:
            pass  # not up yet (server starting, or the new tunnel name not resolvable yet)
        time.sleep(2)
    raise SystemExit(f"{what} did not come up within {seconds:.0f} s")


def start_tunnel() -> tuple[subprocess.Popen, str]:
    proc = subprocess.Popen([cloudflared(), "tunnel", "--url", f"http://localhost:{PORT}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    found: list[str] = []

    def drain() -> None:  # keep reading so cloudflared never blocks on a full pipe
        for line in proc.stdout:
            m = TUNNEL_URL.search(line)
            if m and not found:
                found.append(m.group(0))

    threading.Thread(target=drain, daemon=True).start()
    end = time.time() + 60
    while not found and time.time() < end and proc.poll() is None:
        time.sleep(0.5)
    if not found:
        proc.terminate()
        raise SystemExit("cloudflared did not print a tunnel address within 60 s")
    return proc, found[0]


def point_meta(public_url: str) -> str:
    env = {k: (v or "").strip() for k, v in dotenv_values(ROOT / ".env").items()}
    user_token, secret = env["WA_ACCESS_TOKEN"], env["WA_APP_SECRET"]
    debug = requests.get(f"{GRAPH}/debug_token", params={"input_token": user_token},
                         headers={"Authorization": f"Bearer {user_token}"}, timeout=20).json()
    if "data" not in debug or not debug["data"].get("is_valid"):
        raise SystemExit("WA_ACCESS_TOKEN is not valid; generate a new one and run again")
    app_id = debug["data"]["app_id"]
    app_token = f"{app_id}|{secret}"
    r = requests.post(f"{GRAPH}/{app_id}/subscriptions", timeout=60, data={
        "object": "whatsapp_business_account", "callback_url": f"{public_url}/webhook",
        "verify_token": env["WA_VERIFY_TOKEN"], "fields": "messages", "access_token": app_token})
    body = r.json()
    if not body.get("success"):
        raise SystemExit(f"Meta refused the webhook update: {body.get('error', {}).get('message', body)}")
    subs = requests.get(f"{GRAPH}/{app_id}/subscriptions", params={"access_token": app_token}, timeout=20).json()
    wa = [s for s in subs.get("data", []) if s.get("object") == "whatsapp_business_account"]
    return f"{wa[0]['callback_url']} (fields: {', '.join(f['name'] for f in wa[0]['fields'])})" if wa else "not listed"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-meta", action="store_true", help="do not update the Meta webhook URL")
    args = ap.parse_args()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--app-dir", str(ROOT / "src"),
                               "--port", str(PORT)], cwd=ROOT)
    tunnel = None
    try:
        wait_for(f"http://localhost:{PORT}/api/meta", {200}, 180, "Bara server")
        print(f"Bara is up: http://localhost:{PORT}", flush=True)
        tunnel, url = start_tunnel()
        wait_for(f"{url}/webhook?hub.mode=subscribe&hub.verify_token=x&hub.challenge=1", {403}, 120, "the tunnel")
        print(f"Tunnel is up: {url}", flush=True)
        if not args.no_meta:
            print(f"Meta webhook now points at {point_meta(url)}", flush=True)
        print("Ready: send a voice note to the Bara test number. Ctrl+C stops everything.", flush=True)
        while server.poll() is None and tunnel.poll() is None:
            time.sleep(1)
        print("The server or the tunnel stopped; shutting down.", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (tunnel, server):
            if p and p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()
