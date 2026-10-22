import json,secrets,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from app.database import Database
from app.services.auth_service import AuthService
from app.services.catalog_service import CatalogService
from app.services.cart_service import CartService

class OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.key=secrets.token_bytes(32);self.db=Database(Path(self.tmp.name)/'workspace.enc',encryption_key=self.key)
    def tearDown(self):self.tmp.cleanup()
    def test_settings_read_once_per_scope_and_invalidate_after_any_write(self):
        self.db.set_setting('test','one')
        with patch.object(self.db,'_conn',wraps=self.db._conn) as connection:
            with self.db.settings_snapshot():
                for _ in range(12):self.assertEqual(self.db.get_setting('test'),'one')
                self.assertEqual(connection.call_count,1)
                self.db.set_setting('test','two');self.assertEqual(self.db.get_setting('test'),'two');self.assertEqual(connection.call_count,3)
                with self.db._conn() as c:c.execute("UPDATE settings SET v='three' WHERE k='test'")
                self.assertEqual(self.db.get_setting('test'),'three')
        self.db.set_setting('test','four')
        with self.db.settings_snapshot():self.assertEqual(self.db.get_setting('test'),'four')
    def test_settings_scopes_are_isolated_across_threads_and_nested_writes(self):
        self.db.set_setting('test','one');loaded=threading.Event();updated=threading.Event();seen=[]
        def first():
            with self.db.settings_snapshot():
                seen.append(self.db.get_setting('test'));loaded.set();updated.wait(3);seen.append(self.db.get_setting('test'))
        def second():
            loaded.wait(3);self.db.set_setting('test','two')
            with self.db.settings_snapshot():seen.append(self.db.get_setting('test'))
            updated.set()
        a=threading.Thread(target=first);b=threading.Thread(target=second);a.start();b.start();a.join(4);b.join(4)
        self.assertEqual(seen,['one','two','one'])
        with self.db.settings_snapshot():
            self.assertEqual(self.db.get_setting('test'),'two')
            with self.db.settings_snapshot():self.db.set_setting('test','three')
            self.assertEqual(self.db.get_setting('test'),'three')
    def test_session_activity_does_not_rewrite_each_request_or_extend_expiry(self):
        auth=AuthService(Path(self.tmp.name)/'accounts.enc',self.key);user=auth.register(dict(email='test@example.com',name='Test',password='safe temporary test password'))
        with patch('app.services.auth_service.time.time',return_value=1800000000):token,_=auth.session(user)
        with patch('app.services.auth_service.time.time',return_value=1800000060):auth.authenticate(token)
        with auth.conn() as c:self.assertEqual(c.execute('SELECT last_seen FROM sessions').fetchone()[0],1800000000)
        with patch('app.services.auth_service.time.time',return_value=1800000300):auth.authenticate(token)
        with auth.conn() as c:
            row=c.execute('SELECT last_seen,expires FROM sessions').fetchone();self.assertEqual(row['last_seen'],1800000300);self.assertEqual(row['expires'],1800000000+30*86400)
        self.assertIs(auth.workspace(user['id']),auth.workspace(user['id']))
    def test_catalog_scope_and_cart_response_do_not_include_unselected_models(self):
        a=self.db.add_supplier(dict(name='One'));b=self.db.add_supplier(dict(name='Two'));catalog=CatalogService(self.db)
        one=catalog.save(dict(supplier_id=a,name='Selected model'));two=catalog.save(dict(supplier_id=b,name='Unselected private model'))
        self.assertEqual([x['id'] for x in catalog.list(a)],[one['id']]);self.assertEqual(catalog.get(two['id'])['supplier_id'],b)
        cart=CartService(self.db);cart.save({**cart.state(),'lines':[dict(item_id=one['id'],quantity='1')]});view=cart.view()
        self.assertNotIn('Unselected private model',json.dumps(view));self.assertEqual(len(view['catalog_groups']),2)
        catalog.delete(one['id']);self.assertTrue(cart.quote()['errors']);cart.save({**cart.state(),'lines':[]});self.assertEqual(cart.state()['lines'],[])
    def test_malformed_catalog_ids_are_rejected_cleanly(self):
        cart=CartService(self.db)
        for value in ({'bad':1},['bad'],None,123):
            with self.subTest(value=value),self.assertRaises(ValueError):cart.save({**cart.state(),'lines':[dict(item_id=value,quantity='1')]})
