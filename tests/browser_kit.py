"""Browser tests: the real dashboard in headless Chromium, against the real app replaying a
made-up day (scenario_kit.py). Chromium is driven over its DevTools protocol with the
`websockets` package the app already uses; no other test dependency.

Isolation and privacy:
- The app runs as `python -m app.replay play` with a scrubbed environment: none of your
  variables (SEC_USER_AGENT, tokens…) reach it. It reads a throwaway config, never your
  config.toml, keeps its data in a temporary folder, and a replay never uses the network.
- Chromium gets a fresh throwaway profile, never yours. Its background traffic (updates, sync,
  telemetry, translation, safe-browsing lookups) is switched off, and every request that isn't
  for this machine goes to a dead proxy, so the page can only talk to the local test server.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
CHROMIUM = next((p for name in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome")
                 if (p := shutil.which(name))), None)
QUIET_CHROMIUM = [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-default-apps",
    "--disable-extensions", "--disable-domain-reliability", "--disable-breakpad", "--no-pings",
    "--disable-client-side-phishing-detection", "--metrics-recording-only",
    "--disable-features=Translate,OptimizationHints,MediaRouter,AutofillServerCommunication",
    "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=127.0.0.1;localhost",  # nothing else is reachable
    "--window-size=1400,1000",
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(check, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while True:
        with suppress(Exception):  # not up yet: keep polling
            if result := check():
                return result
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {what}")
        time.sleep(0.1)


class ReplayServer:
    """The app replaying `recording` from `start` (HH:MM ET), on a free local port."""

    def __init__(self, recording: Path, folder: Path, start: str, config: str = ""):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "config.toml").write_text(config)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(folder), "LANG": "C.UTF-8",
               "MARKET_PULSE_CONFIG": str(folder / "config.toml"), "MARKET_PULSE_DATA": str(folder / "data")}
        self._log = open(folder / "server.log", "w")  # noqa: SIM115 - the server's output for its lifetime; closed in stop()
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "app.replay", "play", str(recording), "--from", start, "--port", str(self.port)],
            cwd=ROOT, env=env, stdout=self._log, stderr=subprocess.STDOUT)
        wait_for(lambda: httpx.get(f"{self.url}/", timeout=1).status_code in (200, 401), 30, "the replay server")

    def get(self, path: str, **kw) -> httpx.Response:
        return httpx.get(self.url + path, timeout=5, **kw)

    def stop(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._log.close()


class Browser:
    def __init__(self, folder: Path):
        self.port = free_port()
        self.profile = folder / "chromium-profile"
        self._proc = subprocess.Popen(
            [CHROMIUM, *QUIET_CHROMIUM, f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_for(lambda: httpx.get(f"http://127.0.0.1:{self.port}/json/version", timeout=1).status_code == 200,
                 30, "Chromium")

    def page(self) -> Page:
        """A new tab, in front (background tabs get their timers throttled)."""
        base = f"http://127.0.0.1:{self.port}/json"
        target = httpx.put(f"{base}/new?about:blank", timeout=5).json()
        httpx.get(f"{base}/activate/{target['id']}", timeout=5)
        return Page(target["webSocketDebuggerUrl"], lambda: httpx.get(f"{base}/close/{target['id']}", timeout=5))

    def stop(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        # Chromium's helper processes can write for a moment after it exits: retry briefly.
        for _ in range(20):
            shutil.rmtree(self.profile, ignore_errors=True)
            if not self.profile.exists():
                break
            time.sleep(0.25)


class Page:
    """One tab. eval() runs JavaScript; every exception or console error the page logs is
    kept in .errors, and tests fail on any."""

    def __init__(self, ws_url: str, close_tab=None):
        self._connection = connect(ws_url, max_size=None, open_timeout=10)
        self._ws = self._connection.__enter__()
        self._close_tab = close_tab
        self._next = 0
        self.events: list[dict] = []
        for method in ("Page.enable", "Runtime.enable", "Log.enable"):
            self.send(method)

    def send(self, method: str, params: dict | None = None, timeout: float = 15) -> dict:
        self._next += 1
        self._ws.send(json.dumps({"id": self._next, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            message = json.loads(self._ws.recv(timeout=max(0.1, deadline - time.monotonic())))
            if message.get("id") == self._next:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message["result"]
            self.events.append(message)

    def goto(self, url: str) -> None:
        self.events.clear()
        self.send("Page.navigate", {"url": url})
        wait_for(lambda: self.eval("document.readyState") == "complete", 15, f"{url} to load")

    def eval(self, expression: str):
        result = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("exception", {}).get("description", "error"))
        return result["result"].get("value")

    def wait(self, expression: str, timeout: float = 10):
        """Until the JavaScript expression is truthy; returns its value. Ask for a plain value:
        an element comes back as {}, which Python reads as false (use !!element)."""
        return wait_for(lambda: self.eval(expression), timeout, expression)

    def click(self, selector: str) -> None:
        self.eval(f"document.querySelector({json.dumps(selector)}).click()")

    def text(self, selector: str = "body") -> str:
        return self.eval(f"document.querySelector({json.dumps(selector)})?.innerText ?? ''")

    @property
    def errors(self) -> list[str]:
        self.send("Runtime.evaluate", {"expression": "0"})  # collect what's arrived
        out = []
        for e in self.events:
            p = e.get("params", {})
            if e.get("method") == "Runtime.exceptionThrown":
                d = p["exceptionDetails"]
                out.append(d.get("exception", {}).get("description") or d.get("text", "exception"))
            elif e.get("method") == "Runtime.consoleAPICalled" and p.get("type") == "error":
                out.append(" ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))
            elif e.get("method") == "Log.entryAdded" and p["entry"]["level"] == "error":
                if not p["entry"].get("url", "").endswith("/favicon.ico"):
                    out.append(p["entry"]["text"])
        return out

    def close(self) -> None:
        self._connection.__exit__(None, None, None)
        if self._close_tab:
            self._close_tab()
