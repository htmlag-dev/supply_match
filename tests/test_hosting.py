import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.hosting import configure_render, bootstrap_admin, render_proxy
from app.services.auth_service import AuthService


class HostingTests(unittest.TestCase):
    def env(self, **overrides):
        return patch.dict(os.environ, {
            'RENDER': 'true', 'RENDER_EXTERNAL_URL': 'https://test.onrender.com',
            'SUPPLY_MATCH_DATABASE_URL': 'postgresql://test.invalid/db?sslmode=require',
            'SUPPLY_MATCH_ENCRYPTION_KEY': base64.urlsafe_b64encode(b'x' * 32).decode(),
            **overrides,
        }, clear=True)

    def test_render_configures_https_host_and_port(self):
        with self.env(PORT='12345'):
            self.assertEqual(configure_render(), 12345)
            self.assertEqual(os.environ['SUPPLY_MATCH_PUBLIC_HOST'], 'test.onrender.com')
            self.assertEqual(os.environ['SUPPLY_MATCH_ENV'], 'production')

    def test_custom_domain_keeps_render_domain(self):
        with self.env(SUPPLY_MATCH_PUBLIC_URL='https://demo.example.org'):
            configure_render()
            self.assertEqual(set(os.environ['SUPPLY_MATCH_PUBLIC_HOST'].split(',')),
                             {'demo.example.org', 'test.onrender.com'})

    def test_refuses_missing_storage_key_and_invalid_port(self):
        for config in [{'SUPPLY_MATCH_DATABASE_URL': ''}, {'SUPPLY_MATCH_ENCRYPTION_KEY': ''}, {'PORT': '0'}]:
            with self.subTest(config=config), self.env(**config):
                with self.assertRaises(RuntimeError):
                    configure_render()

    def test_only_render_https_proxy_allowed(self):
        for url in ['http://test.onrender.com', 'https://onrender.com.evil.example', 'https://user@test.onrender.com']:
            with self.env(RENDER_EXTERNAL_URL=url):
                self.assertFalse(render_proxy())
        with self.env(RENDER='false'):
            self.assertFalse(render_proxy())

    def test_bootstrap_creates_admin_once_without_resetting_password(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'SUPPLY_MATCH_BOOTSTRAP_EMAIL': 'owner@example.test',
            'SUPPLY_MATCH_BOOTSTRAP_PASSWORD': 'TestPassword12345!',
        }, clear=True):
            auth = AuthService(Path(folder) / 'accounts.enc', key=b'x' * 32)
            bootstrap_admin(auth)
            with auth.conn() as c:
                original = dict(c.execute('SELECT * FROM users').fetchone())
            os.environ['SUPPLY_MATCH_BOOTSTRAP_PASSWORD'] = 'ChangedPassword12345!'
            bootstrap_admin(auth)
            with auth.conn() as c:
                users = c.execute('SELECT * FROM users').fetchall()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0]['role'], 'admin')
            self.assertEqual(users[0]['email_verified'], 1)
            self.assertEqual(users[0]['password'], original['password'])
