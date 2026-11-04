"""HTTP integration: authentication, CSRF and workspace isolation for added routes."""
import json
import secrets
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from app.database import Database
from app.server import make_handler
from app.services.ai_service import AIClient
from app.services.auth_service import AuthService
from app.services.supplier_service import SupplierService

class ProcurementHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);key=secrets.token_bytes(32)
        self.auth=AuthService(root/'accounts.enc',key);self.tokens=[]
        for index in range(2):
            user=self.auth.register({'email':f'user{index}@example.test','name':'User','password':'TestPassword12345'})
            self.tokens.append(self.auth.session(user))
        db=Database(root/'system.enc',encryption_key=key);ai=AIClient(db)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(db,SupplierService(db,ai),ai,auth=self.auth))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def request(self,path,method='GET',body=None,user=0,csrf=True):
        headers={'Content-Type':'application/json'}
        if user is not None:
            token,secret=self.tokens[user];headers['Authorization']='Session '+token
            if csrf:headers['X-CSRF-Token']=secret
        req=urllib.request.Request('http://127.0.0.1:'+str(self.server.server_port)+path,data=json.dumps(body or {}).encode() if method!='GET' else None,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as error:
            with error:return error.code,json.load(error)
    def test_new_routes_are_private_and_isolated(self):
        self.assertEqual(self.request('/api/procurement',user=None)[0],401)
        self.assertEqual(self.request('/api/procurement/projects','POST',{'name':'Secret'},csrf=False)[0],403)
        code,p=self.request('/api/procurement/projects','POST',{'name':'Secret'});self.assertEqual(code,200)
        self.assertEqual(len(self.request('/api/procurement')[1]['projects']),1)
        self.assertEqual(self.request('/api/procurement',user=1)[1]['projects'],[])
        self.assertEqual(self.request('/api/procurement/projects/'+p['id'],'PATCH',{'name':'Intrusion'},user=1)[0],400)
        self.assertEqual(self.request('/api/procurement/compare','POST',{'comparison_key':'test','quantity':'1'})[0],200)
    def test_public_password_recovery_validation(self):
        self.assertEqual(self.request('/api/auth/reset-password','POST',{'challenge_id':'x','code':'123456','password':'NewPassword1234'},user=None)[0],400)

    def test_registration_without_mail_creates_immediate_session(self):
        from unittest.mock import patch, Mock
        self.auth.mailer = Mock()
        self.auth.mailer.configured.return_value = False
        data = {'email': 'new@example.test', 'name': 'New', 'password': 'StrongPassword12345!'}
        with patch.dict('os.environ', {'SUPPLY_MATCH_REQUIRE_EMAIL_VERIFICATION': 'false'}):
            code, result = self.request('/api/auth/register', 'POST', data, user=None)
            self.assertEqual(code, 200)
            self.assertIn('csrf', result)
            self.assertEqual(result['user']['email_verified'], 0)
            self.assertNotIn('verification_required', result)
            self.auth.mailer.send_code.assert_not_called()
            user = self.auth.login(data)
            token, _ = self.auth.session(user)
            self.assertEqual(self.auth.authenticate(token)[0]['id'], user['id'])
            code, result = self.request('/api/auth/login', 'POST', data, user=None)
            self.assertEqual(code, 200)
            self.assertIn('csrf', result)
            with self.auth.conn() as c:
                c.execute('UPDATE users SET disabled=1 WHERE id=?', (user['id'],))
            from app.services.auth_service import Unauthorized
            with self.assertRaises(Unauthorized):
                self.auth.authenticate(token)
