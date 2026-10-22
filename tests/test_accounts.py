import http.client
import json
import secrets
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from app.database import Database
from app.services.auth_service import AuthService,Forbidden,Unauthorized
from app.services.ai_service import AIClient
from app.services.supplier_service import SupplierService
from app.server import make_handler
from cryptography.exceptions import InvalidTag

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.key=secrets.token_bytes(32)
        self.auth=AuthService(self.path/'accounts.enc',self.key)
        self.a=self.auth.register(dict(email='a@example.com',name='Alice',password='a secure password 2026'))
        self.b=self.auth.register(dict(email='b@example.com',name='Bob',password='another password 2026'))
    def tearDown(self):self.temp.cleanup()
    def test_encryption_password_and_tamper(self):
        raw=(self.path/'accounts.enc').read_bytes()
        self.assertNotIn(b'a@example.com',raw);self.assertNotIn(b'a secure password',raw)
        with self.auth.conn() as c:self.assertNotIn('a secure password',c.execute('SELECT password FROM users LIMIT 1').fetchone()[0])
        damaged=raw[:-1]+bytes([raw[-1]^1]);(self.path/'accounts.enc').write_bytes(damaged)
        with self.assertRaises(InvalidTag):self.auth.login(dict(email='a@example.com',password='a secure password 2026'))
    def test_session_revoke_and_role_guard(self):
        token,csrf=self.auth.session(self.a);self.assertEqual(self.auth.authenticate(token)[0]['id'],self.a['id']);self.assertTrue(csrf)
        self.auth.logout(token)
        with self.assertRaises(Unauthorized):self.auth.authenticate(token)
        with self.assertRaises(Forbidden):self.auth.update_user(self.a,self.a['id'],dict(role='admin',disabled=False))
    def test_user_isolation(self):
        a=self.auth.workspace(self.a['id']);b=self.auth.workspace(self.b['id']);a.add_supplier({'name':'Private Alice supplier'})
        self.assertEqual(b.list_suppliers(),[]);self.assertIsNone(b.get_supplier(1))
        self.assertNotIn(b'Private Alice',Path(a.path).read_bytes())
        self.assertEqual(self.auth.workspace(self.a['id']).get_supplier(1)['name'],'Private Alice supplier')
    def test_http_access_csrf_and_admin(self):
        db=Database(self.path/'unused.db');ai=AIClient(db)
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(db,SupplierService(db,ai),ai,auth=self.auth));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,method='GET',data=None,token='',csrf=''):
            con=http.client.HTTPConnection('127.0.0.1',server.server_port);headers={'Content-Type':'application/json'}
            if token:headers['Cookie']='sm_session='+token
            if csrf:headers['X-CSRF-Token']=csrf
            con.request(method,path,json.dumps(data) if data is not None else None,headers);response=con.getresponse();status=response.status;body=json.loads(response.read());con.close();return status,body
        try:
            self.assertEqual(request('/api/state')[0],401)
            token,csrf=self.auth.session(self.a)
            self.assertEqual(request('/api/admin/state',token=token)[0],403)
            self.assertEqual(request('/api/suppliers','POST',{'name':'secret'},token)[0],403)
            self.assertEqual(request('/api/suppliers','POST',{'name':'secret'},token,csrf)[0],200)
            other,other_csrf=self.auth.session(self.b)
            self.assertEqual(request('/api/suppliers/1','DELETE',{},other,csrf)[0],403)
            self.assertEqual(request('/api/suppliers/1','DELETE',{},other,other_csrf)[0],404)
            with self.auth.conn() as c:c.execute("UPDATE users SET role='admin' WHERE id=?",(self.a['id'],))
            self.assertEqual(request('/api/admin/state',token=token)[0],200)
            self.assertEqual(request('/api/auth/logout','POST',{},token,csrf)[0],200)
            self.assertEqual(request('/api/state',token=token)[0],401)
        finally:server.shutdown();server.server_close();thread.join()
    def test_month_session_and_hashed_device(self):
        from unittest.mock import patch
        with patch('app.services.auth_service.time.time',return_value=1800000000):
            token,_=self.auth.session(self.a,ip='203.0.113.42',device='Test browser')
        with self.auth.conn() as c:
            row=c.execute('SELECT * FROM sessions').fetchone()
            self.assertEqual(row['expires']-1800000000,30*86400)
            self.assertNotEqual(row['ip_hash'],'203.0.113.42');self.assertNotEqual(row['device_hash'],'Test browser')
            self.assertNotEqual(row['token'],token)
        with patch('app.services.auth_service.time.time',return_value=1800000000+29*86400):self.assertEqual(self.auth.authenticate(token)[0]['id'],self.a['id'])
        with patch('app.services.auth_service.time.time',return_value=1800000000+31*86400):
            with self.assertRaises(Unauthorized):self.auth.authenticate(token)
    def test_username_and_no_role_injection(self):
        user=self.auth.register(dict(username='new_user',email='new@example.com',name='New',password='user long password 2026',role='admin'))
        self.assertEqual(user['role'],'user')
        self.assertEqual(self.auth.login(dict(email='NEW_USER',password='user long password 2026'))['id'],user['id'])
    def test_oauth_unconfigured_and_state_replay(self):
        from unittest.mock import patch
        with patch.dict('os.environ',{},clear=True):self.assertEqual(self.auth.providers(),{'google':False,'apple':False})
        with patch.dict('os.environ',{'SUPPLY_MATCH_GOOGLE_CLIENT_ID':'test','SUPPLY_MATCH_GOOGLE_CLIENT_SECRET':'secret','SUPPLY_MATCH_PUBLIC_URL':'https://example.com'}):
            url,browser=self.auth.oauth_start('google');self.assertIn('code_challenge=',url)
            from urllib.parse import parse_qs,urlsplit
            state=parse_qs(urlsplit(url).query)['state'][0]
            with self.assertRaises(Unauthorized):self.auth.oauth_finish('google',{'state':state,'code':'bad'},'different-browser')
            with self.assertRaises(Unauthorized):self.auth.oauth_finish('google',{'state':'forged','code':'bad'},browser)

if __name__=='__main__':unittest.main()
