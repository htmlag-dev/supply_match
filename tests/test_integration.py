import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from app.database import Database
from app.server import make_handler
from app.services.ai_service import AIClient, AIError
from app.services.billing_service import BillingService
from app.services.supplier_service import SupplierService
from tests.test_services import FakeAI


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, 'app.db'))
        self.env = patch.dict(os.environ, {'GEMINI_API_KEY':'', 'OPENROUTER_API_KEY':'', 'GROQ_API_KEY':''})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_provider_keys_and_legacy_migration(self):
        ai = AIClient(self.db)
        self.assertEqual(ai.provider, 'auto')
        self.db.set_setting('api_key', 'old-gemini-key')
        self.db.set_setting('model', 'old-model')
        self.assertEqual((ai.provider, ai.key, ai.model), ('gemini', 'old-gemini-key', 'old-model'))
        self.db.set_setting('provider', 'groq')
        self.db.set_setting('key_groq', 'groq-secret')
        self.assertEqual(ai.key, 'groq-secret')
        self.assertNotEqual(ai.model, 'old-model')
        with self.assertRaises(AIError):
            ai.generate('search', web_search=True)

    def test_openrouter_request_and_response(self):
        self.db.set_setting('provider', 'openrouter')
        self.db.set_setting('key_openrouter', 'secret')
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"answer"}}]}'
        with patch('urllib.request.urlopen', return_value=response) as send:
            self.assertEqual(AIClient(self.db).generate('test'), 'answer')
            request = send.call_args.args[0]
            self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/chat/completions')
            self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
            self.assertEqual(json.loads(request.data)['model'], 'openrouter/free')
        with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('url',429,'limit',{},None)):
            with self.assertRaisesRegex(AIError,'Лимит'):
                AIClient(self.db).generate('test')

    def test_ai_output_validation(self):
        svc = SupplierService(self.db, FakeAI('[3]'))
        with self.assertRaises(AIError): svc.search('Москва','кофе')
        with self.assertRaises(ValueError): svc.search('','кофе')
        self.db.add_supplier({'name':'Known'})
        svc.ai = FakeAI('{"ranking":[{"name":"Unknown","score":100},{"name":"Known","score":400},{"name":"Known","score":20}]}')
        self.assertEqual(svc.rank('test')['ranking'], [{'name':'Known','score':100,'reason':''}])

    def test_billing_server_prices_and_idempotency(self):
        billing = BillingService(self.db)
        data = {'plan':'pro','period':'yearly','email':'test@example.com','company':'Coffee','consent':True,'idempotency_key':'a'*24,'amount':1,'status':'paid'}
        first = billing.checkout(data)
        second = billing.checkout(data)
        self.assertEqual(first['order']['id'],second['order']['id'])
        self.assertEqual(first['order']['amount'],990000)
        self.assertEqual(first['order']['status'],'awaiting_integration')
        self.assertIsNone(first['checkout_url'])
        self.assertEqual(billing.state()['current_plan'],'trial')
        self.assertEqual(len(self.db.list_orders()),1)
        with self.assertRaises(ValueError): billing.checkout({**data,'plan':'team'})
        for change in ({'email':'invalid'}, {'period':'invalid'}, {'consent':False}, {'plan':'start'}, {'idempotency_key':''}):
            with self.subTest(change=change), self.assertRaises(ValueError): billing.checkout({**data,**change})

    def test_http_crud_validation_checkout_and_export(self):
        ai = AIClient(self.db)
        server = ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.db,SupplierService(self.db,ai),ai))
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = 'http://127.0.0.1:'+str(server.server_port)
        def request(path, method='GET', data=None, extra=None):
            headers={'Content-Type':'application/json',**(extra or {})}
            req=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),headers=headers,method=method)
            try:
                with urllib.request.urlopen(req) as response:
                    return response.status,response.read(),response.headers
            except urllib.error.HTTPError as response:
                with response:
                    return response.code,response.read(),response.headers
        try:
            for path in ('/','/pricing','/checkout','/settings','/app.js','/styles.css','/manifest.webmanifest','/sw.js','/icon-192.png','/icon-512.png'):
                code,body,headers=request(path)
                self.assertEqual(code,200,path)
                self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
            code,body,_=request('/api/suppliers','POST',{'name':'=formula','city':'Москва','site':'https://example.com'})
            sid=json.loads(body)['id']
            self.assertEqual(code,200)
            self.assertEqual(request('/api/suppliers/'+str(sid),'PATCH',{'offer':'Зерно','status':'ответил'})[0],200)
            self.assertEqual(self.db.get_supplier(sid)['offer'],'Зерно')
            self.assertEqual(request('/api/suppliers','POST',{'name':'Bad','site':'javascript:alert(1)'})[0],400)
            self.assertEqual(request('/api/suppliers/'+str(sid),'PATCH',{'status':'invalid'})[0],400)
            self.assertEqual(request('/api/settings','POST',{'provider':'bad'})[0],400)
            self.assertEqual(request('/api/settings','POST',{'provider':'groq','key':'secret'})[0],200)
            self.assertNotIn(b'secret',request('/api/state')[1])
            self.assertEqual(request('/api/settings','POST',{}, {'Origin':'https://evil.example'})[0],403)
            self.assertEqual(request('/api/state',extra={'Host':'evil.example'})[0],403)
            with patch.dict(os.environ, {'SUPPLY_MATCH_TOKEN':'workspace-secret', 'SUPPLY_MATCH_ALLOWED_ORIGINS':'capacitor://localhost'}):
                self.assertEqual(request('/api/state')[0],401)
                self.assertEqual(request('/api/state',extra={'Authorization':'Bearer workspace-secret'})[0],200)
                code,body,headers=request('/api/state',extra={'Authorization':'Bearer workspace-secret','Origin':'capacitor://localhost'})
                self.assertEqual(code,200)
                self.assertEqual(headers['Access-Control-Allow-Origin'],'capacitor://localhost')
                self.assertEqual(request('/api/state','OPTIONS',extra={'Origin':'capacitor://localhost'})[0],200)
            self.assertEqual(request('/api/suppliers','POST',[])[0],400)
            self.assertIn("'=formula",request('/api/export')[1].decode('utf-8-sig'))
            checkout={'plan':'team','period':'monthly','email':'test@example.com','company':'','consent':True,'idempotency_key':'b'*24}
            code,body,_=request('/api/billing/checkout','POST',checkout)
            self.assertEqual(code,200)
            self.assertEqual(json.loads(body)['order']['amount'],249000)
            self.assertEqual(request('/api/suppliers/'+str(sid),'DELETE')[0],200)
            self.assertEqual(request('/api/suppliers/'+str(sid),'PATCH',{'note':'x'})[0],404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__': unittest.main()
