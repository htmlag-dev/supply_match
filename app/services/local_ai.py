"""Запускает установленную Qwen3 через llama.cpp, без ключей и системной установки."""
import json
import os
import subprocess
import secrets
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from app.config import DATA_DIR

LOCAL_URL = "http://127.0.0.1:8766"
LOCAL_KEY = os.environ.get("SUPPLY_MATCH_LOCAL_AI_KEY") or secrets.token_urlsafe(32)


def available():
    try:
        request = urllib.request.Request(LOCAL_URL + "/v1/models", headers={"Authorization": "Bearer " + LOCAL_KEY})
        with urllib.request.urlopen(request, timeout=0.8) as response:
            return any(m.get("id") == "supplymatch-qwen3" for m in json.load(response).get("data", []))
    except urllib.error.HTTPError as error:
        error.close()
        return False
    except Exception:
        return False


class LocalAIProcess:
    def __init__(self):
        self.process, self.log = None, None
        self.phase = "not_installed"
        self.stopping = False

    def start(self, auto_setup=True):
        # The bundled runtime and installer currently support Windows only.
        if os.name != "nt":
            return
        root = DATA_DIR / "local-ai"
        model = root / "Qwen3-4B-Q4_K_M.gguf"
        runtime = root / "runtime" / "llama-server.exe"
        if not model.is_file() or not runtime.is_file():
            if auto_setup and os.name == "nt":
                self.phase = "downloading"
                threading.Thread(target=self._install, daemon=True).start()
            return
        if available():
            self.phase = "ready"
            return
        self.phase = "loading"
        self.log = (root / "server.log").open("a", encoding="utf-8")
        child_env = {**os.environ, "LLAMA_API_KEY": LOCAL_KEY}
        try:
            self.process = subprocess.Popen([str(runtime), "--model", str(model), "--alias", "supplymatch-qwen3",
                "--host", "127.0.0.1", "--port", "8766", "--ctx-size", "8192", "--parallel", "1",
                "--threads", str(min(8, max(2, (os.cpu_count() or 4) // 2))), "--jinja",
                "--chat-template-kwargs", '{"enable_thinking":false}'],
                stdout=self.log, stderr=self.log, env=child_env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            self.phase = "error"
            self.log.close()
            self.log = None
            print("Не удалось запустить локальную Qwen3. Приложение продолжает работу без неё.", flush=True)
            return
        print("Локальная Qwen3 загружается. Первый ответ может потребовать больше времени.", flush=True)

    def _install(self):
        try:
            from scripts.setup_local_ai import setup
            setup()
            if not self.stopping:
                self.start(auto_setup=False)
        except Exception:
            self.phase = "error"
            print("Автоустановка Qwen3 не завершена. Повторите: python scripts/setup_local_ai.py", flush=True)

    def status(self):
        if self.process and self.process.poll() is not None:
            self.phase = "error"
        part = DATA_DIR / "local-ai" / "Qwen3-4B-Q4_K_M.gguf.part"
        progress = min(99, int(part.stat().st_size / 2491323904 * 100)) if self.phase == "downloading" and part.exists() else 0
        phase = "ready" if available() else self.phase
        return {"phase": phase, "progress": 100 if phase == "ready" else progress}

    def stop(self):
        self.stopping = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()
