import http.client
import json
import os
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from app.database import Database
from app.server import make_handler
from app.services.auth_service import AuthService
from app.services.ai_service import AIClient
from app.services.supplier_service import SupplierService
from app.services.access_service import AccessService
from scripts.build_hixo import ASSETS, build


class HixoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'SUPPLY_MATCH_NO_AI': '1',
            'SUPPLY_MATCH_ALLOWED_ORIGINS': 'https://our-site.hixo.dev'}, clear=True)
        self.env.start()
        self.db = Database(self.root / 'test.db')

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_search_never_calls_model_even_with_old_keys_and_web_false(self):
        self.db.set_setting('tavily_key', 'unused-test-value')
        ai = Mock()
        service = SupplierService(self.db, ai)
        with patch.object(service.web, 'discover', return_value=([
            {'url': 'https://seller.example/item', 'title': 'Actual page', 'content': 'Snippet'}], 0)):
            result = service.search_with_sources(city='Москва', product='стул', web=False)
        ai.generate.assert_not_called()
        self.assertEqual(result['mode'], 'discovery')
        self.assertEqual(result['suppliers'][0]['unit_price'], '')
        self.assertEqual(result['sources'][0]['url'], 'https://seller.example/item')

    def test_template_and_completeness_use_no_model_or_ai_quota(self):
        ai = Mock()
        sid = self.db.add_supplier({'name': 'Продавец', 'city': 'Москва', 'unit_price': '20', 'currency': 'RUB'})
        service = SupplierService(self.db, ai)
        self.assertIn('Москва', service.invite_text(sid))
        result = service.rank('самый надёжный')
        self.assertEqual(result['mode'], 'completeness')
        self.assertEqual(result['ranking'][0]['score'], 33)
        ai.generate.assert_not_called()
        access = AccessService(self.db)
        with access.search_request(): pass
        self.assertEqual(access.state()['ai_used'], 0)

    def test_static_session_requires_exact_origin_csrf_and_revokes(self):
        auth = AuthService(self.root / 'accounts.enc', b'x' * 32)
        user = auth.register({'email': 'fixture@example.test', 'name': 'Fixture', 'password': 'fixture only password 2026'})
        ai = AIClient(self.db)
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.db, SupplierService(self.db, ai), ai, auth=auth))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def request(path, method='GET', data=None, origin='https://our-site.hixo.dev', token='', csrf=''):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
            headers = {'Origin': origin, 'Content-Type': 'application/json', 'X-SupplyMatch-Web': '1'}
            if token: headers['Authorization'] = 'Session ' + token
            if csrf: headers['X-CSRF-Token'] = csrf
            conn.request(method, path, json.dumps(data) if data is not None else None, headers)
            response = conn.getresponse(); body = json.loads(response.read()); code = response.status; heads = dict(response.getheaders());conn.close()
            return code, body, heads
        try:
            code, _, heads = request('/api/auth/login', 'OPTIONS')
            self.assertEqual(code, 200)
            self.assertEqual(heads['Access-Control-Allow-Origin'], 'https://our-site.hixo.dev')
            self.assertIn('X-SupplyMatch-Web', heads['Access-Control-Allow-Headers'])
            self.assertEqual(request('/api/auth/login', 'POST', {}, origin='https://evil.hixo.dev')[0], 403)
            code, login, heads = request('/api/auth/login', 'POST', {'email': user['email'], 'password': 'fixture only password 2026'})
            self.assertEqual(code, 200)
            self.assertNotIn('Set-Cookie', heads)
            token, csrf = login['session_token'], login['csrf']
            self.assertEqual(request('/api/auth/me', token=token)[1]['user']['id'], user['id'])
            self.assertEqual(request('/api/suppliers', 'POST', {'name': 'test'}, token=token)[0], 403)
            self.assertEqual(request('/api/suppliers', 'POST', {'name': 'test'}, token=token, csrf=csrf)[0], 200)
            self.assertEqual(request('/api/auth/logout', 'POST', {}, token=token, csrf=csrf)[0], 200)
            self.assertEqual(request('/api/state', token=token)[0], 401)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_zip_has_only_public_assets_and_disables_hixo_forms(self):
        output = build('https://supply-match.onrender.com', self.root / 'hixo.zip')
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(archive.namelist()), set(ASSETS))
            html = archive.read('index.html').decode()
            self.assertIn('name="static-api" content="https://supply-match.onrender.com"', html)
            self.assertNotIn('src="/', html)
            self.assertEqual(html.count('<form '), html.count('<form data-hixo="off" '))
            self.assertIn('<form data-hixo="off" data-project-link', archive.read('procurement.js').decode())
        for invalid in ('http://localhost', 'https://user:secret@server.example', 'https://server.example/path', 'https://server.example?key=secret'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):build(invalid, self.root/'bad.zip')
