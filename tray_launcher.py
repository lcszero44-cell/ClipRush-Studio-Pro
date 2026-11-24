"""ClipRush Studio Pro - Silent Launcher & System Tray Controller.
1. Frees port 8765 if occupied
2. Detects .venv, FFmpeg, and checks/starts Ollama (qwen3.5:9b)
3. Starts FastAPI server cleanly in background (no console window)
4. Opens http://127.0.0.1:8765 automatically once /api/health/live returns 200 OK
5. Hosts System Tray icon: 'Abrir ClipRush Studio Pro', 'Abrir Pasta Saida', 'Encerrar Servidor'
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

BASE_DIR = Path(r"D:\ClipRush_Pro_V2")
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# pythonw.exe on Windows sets sys.stdout / sys.stderr to None, which breaks uvicorn logging isatty()
_LOG_DIR = BASE_DIR / "data"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
if sys.stdout is None or sys.stderr is None:
    _log_fh = open(_LOG_DIR / "launcher.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_fh
    if sys.stderr is None:
        sys.stderr = _log_fh

import httpx
import pystray
import uvicorn
from PIL import Image

from backend.config import ASSETS_DIR, PREFERRED_FFMPEG_BIN, get_output_dir

HOST = "127.0.0.1"  # P0 Security: strict loopback-only bind (inaccessible from LAN/Wi-Fi)
PORT = 8765
assert HOST == "127.0.0.1", "ClipRush Studio Pro must bind exclusively to 127.0.0.1"
APP_URL = f"http://{HOST}:{PORT}"
HEALTH_URL = f"{APP_URL}/api/health/live"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _is_port_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def free_port_8765() -> None:
    """Terminate any existing process listening on TCP port 8765 and wait until port is free."""
    try:
        res = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            encoding="latin-1",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        my_pid = os.getpid()
        pids_to_kill: set[int] = set()
        for line in (res.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{PORT}"):
                state = parts[3].upper()
                if state in ("LISTENING", "ESCUTANDO", "LISTEN"):
                    try:
                        pid = int(parts[4])
                        if pid != my_pid and pid > 4:
                            pids_to_kill.add(pid)
                    except ValueError:
                        pass
        for pid in pids_to_kill:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        # Wait up to 6 seconds until TCP port 8765 is actually released by Windows
        deadline = time.time() + 6.0
        while time.time() < deadline:
            if _is_port_free(HOST, PORT):
                return
            time.sleep(0.25)
    except Exception:
        pass


def ensure_ffmpeg_in_path() -> None:
    if PREFERRED_FFMPEG_BIN.exists():
        current_path = os.environ.get("PATH", "")
        if str(PREFERRED_FFMPEG_BIN) not in current_path:
            os.environ["PATH"] = f"{PREFERRED_FFMPEG_BIN};{current_path}"


def ensure_ollama_running() -> None:
    """Check http://localhost:11434 and start Ollama silently if offline."""
    try:
        with httpx.Client(timeout=1.2) as client:
            r = client.get("http://localhost:11434/api/tags")
            if r.status_code == 200:
                return
    except Exception:
        pass

    ollama_exe = shutil.which("ollama") or str(Path.home() / "AppData/Local/Programs/Ollama/ollama.exe")
    if Path(ollama_exe).exists():
        try:
            subprocess.Popen([ollama_exe, "serve"], creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass


def wait_and_open_browser(open_browser: bool = True) -> None:
    if not open_browser:
        return
    deadline = time.time() + 25.0
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                r = client.get(HEALTH_URL)
                if r.status_code == 200:
                    webbrowser.open(APP_URL)
                    return
        except Exception:
            pass
        time.sleep(0.35)


def load_tray_image() -> Image.Image:
    png_path = ASSETS_DIR / "cliprush.png"
    if png_path.exists():
        return Image.open(png_path).resize((64, 64))
    return Image.new("RGBA", (64, 64), (99, 102, 241, 255))


def main() -> None:
    no_browser = "--no-browser" in sys.argv
    no_tray = "--no-tray" in sys.argv

    free_port_8765()
    ensure_ffmpeg_in_path()
    threading.Thread(target=ensure_ollama_running, daemon=True).start()

    from backend.app import app

    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)

    if no_tray:
        threading.Thread(target=wait_and_open_browser, args=(not no_browser,), daemon=True).start()
        server.run()
        return

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    threading.Thread(target=wait_and_open_browser, args=(not no_browser,), daemon=True).start()

    def on_open_app(icon, item) -> None:
        webbrowser.open(APP_URL)

    def on_open_output(icon, item) -> None:
        out = get_output_dir()
        out.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(out))  # type: ignore[attr-defined]

    def on_exit(icon, item) -> None:
        server.should_exit = True
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Abrir ClipRush Studio Pro", on_open_app, default=True),
        pystray.MenuItem("Abrir Pasta Saida", on_open_output),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Encerrar Servidor", on_exit),
    )
    tray_icon = pystray.Icon("ClipRushStudioPro", load_tray_image(), "ClipRush Studio Pro", menu)
    tray_icon.run()


if __name__ == "__main__":
    main()
