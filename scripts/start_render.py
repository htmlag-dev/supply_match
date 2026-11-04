"""Launch behind Render's managed HTTPS proxy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from app.hosting import configure_render, bootstrap_admin
    port = configure_render()
    from app.services.auth_service import AuthService
    from app.server import run
    bootstrap_admin(AuthService())
    run('0.0.0.0', port, open_browser=False, auto_setup=False)


if __name__ == '__main__':
    main()
