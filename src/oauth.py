import json
import os
import configparser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from gi.repository import GLib


class OAuthCallbackServer:
    def __init__(
        self, on_token, redirect_uri, state, host="127.0.0.1", port=8765
    ):
        self.on_token = on_token
        self.redirect_uri = redirect_uri
        self.state = state
        callback = self

        class Handler(BaseHTTPRequestHandler):
            def send_text(self, status, body, content_type="text/plain; charset=utf-8"):
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                if self.path.split("?", 1)[0] != "/callback":
                    return self.send_text(404, "Not found")
                page = """<!doctype html><html><head><meta charset="utf-8">
<title>AniList sign-in</title></head><body>
<p id="status">Completing AniList sign-in...</p>
<script>
const expectedState = __EXPECTED_STATE__;
const token = new URLSearchParams(location.hash.slice(1)).get('access_token');
const state = new URLSearchParams(location.hash.slice(1)).get('state');
history.replaceState(null, '', location.pathname);
if (!token || state !== expectedState) {
  document.getElementById('status').textContent =
        'AniList authorization could not be verified. Please retry.';
} else {
  fetch('/token', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({access_token: token, state: state})})
    .then(response => {
      document.getElementById('status').textContent = response.ok
        ? 'AniList connected. You can close this tab.'
        : 'Could not pass the token to the app. Please retry.';
    })
    .catch(() => document.getElementById('status').textContent =
      'Could not reach the app. Please retry.');
}
</script></body></html>"""
                page = page.replace("__EXPECTED_STATE__", json.dumps(callback.state))
                self.send_text(200, page, "text/html; charset=utf-8")

            def do_POST(self):
                if self.path != "/token":
                    return self.send_text(404, "Not found")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 8192:
                        raise ValueError("Invalid request size")
                    payload = json.loads(self.rfile.read(length))
                    token = payload.get("access_token", "")
                    state = payload.get("state", "")
                    if (
                        not isinstance(token, str)
                        or not token
                        or state != callback.state
                    ):
                        raise ValueError("Missing access token")
                except (ValueError, json.JSONDecodeError):
                    return self.send_text(400, "Invalid token payload")
                self.send_text(200, "Token received")
                GLib.idle_add(callback.on_token, token)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def read_client_id():
    if client_id := os.environ.get("ANILIST_CLIENT_ID", "").strip():
        return client_id
    config_paths = (
        Path("/etc/ani-gtk/config.ini"),
        Path(__file__).resolve().parent.parent / "config.ini",
    )
    for config_path in config_paths:
        config = configparser.ConfigParser()
        try:
            config.read(config_path)
            client_id = config.get("ANILIST", "client_id", fallback="").strip()
            if client_id:
                return client_id
        except (OSError, configparser.Error):
            continue
    return ""