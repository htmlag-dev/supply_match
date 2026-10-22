"""Точка запуска: python main.py   (для телефона в той же Wi-Fi сети: python main.py --lan)"""
import argparse
import sys

from app.server import run

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Гастер · Supply Match")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--lan", action="store_true", help="открыть доступ в вашей Wi-Fi сети (нужны аккаунт и SUPPLY_MATCH_PUBLIC_HOST)")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-ai-download", action="store_true", help="не загружать локальную модель автоматически")
    a = p.parse_args()
    run("0.0.0.0" if a.lan else "127.0.0.1", a.port, not a.no_browser, auto_setup=not a.no_ai_download)
