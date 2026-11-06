"""Render configuration; no credentials are written to disk or logs."""
import os
from urllib.parse import urlsplit


def render_proxy():
    url = urlsplit(os.environ.get('RENDER_EXTERNAL_URL', ''))
    return (os.environ.get('RENDER') == 'true' and url.scheme == 'https'
            and bool(url.hostname) and url.hostname.endswith('.onrender.com')
            and not url.username and not url.password and url.port in (None, 443))


def configure_render():
    if not render_proxy():
        raise RuntimeError('Этот запуск предназначен для HTTPS-сервиса Render.')
    if not os.environ.get('SUPPLY_MATCH_DATABASE_URL'):
        raise RuntimeError('Задайте SUPPLY_MATCH_DATABASE_URL: локальные файлы Render не сохраняются.')
    if not os.environ.get('SUPPLY_MATCH_ENCRYPTION_KEY'):
        raise RuntimeError('Задайте постоянный SUPPLY_MATCH_ENCRYPTION_KEY.')
    port = int(os.environ.get('PORT', '10000'))
    if not 1 <= port <= 65535:
        raise RuntimeError('Некорректный PORT.')
    os.environ['SUPPLY_MATCH_ENV'] = 'production'
    os.environ.setdefault('SUPPLY_MATCH_NO_AI', '1')
    public = os.environ.get('SUPPLY_MATCH_PUBLIC_URL') or os.environ['RENDER_EXTERNAL_URL']
    parsed = urlsplit(public)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError('Публичный адрес должен использовать HTTPS.')
    os.environ['SUPPLY_MATCH_PUBLIC_URL'] = public.rstrip('/')
    hosts = {parsed.hostname, urlsplit(os.environ['RENDER_EXTERNAL_URL']).hostname}
    os.environ['SUPPLY_MATCH_PUBLIC_HOST'] = ','.join(sorted(hosts))
    return port


def bootstrap_admin(auth):
    email = os.environ.get('SUPPLY_MATCH_BOOTSTRAP_EMAIL', '').strip().lower()
    password = os.environ.get('SUPPLY_MATCH_BOOTSTRAP_PASSWORD', '')
    if not email or not password:
        return
    with auth.conn() as connection:
        if connection.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            return
        if connection.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
            raise RuntimeError('Bootstrap email уже занят. Используйте управление аккаунтами.')
    auth.register({'email': email, 'password': password, 'name': 'Владелец'}, role='admin')
