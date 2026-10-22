import base64
import io
import json
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app.database import Database
from app.services.catalog_service import CatalogService
from app.services.cart_service import CartService
from app.services.procurement_service import ProcurementService
from app.services.auth_service import AuthService, AuthError, Unauthorized
from app.backup import create_backup, verify_backup, restore_backup

class ProcurementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.key=secrets.token_bytes(32)
        self.db=Database(self.root/'data'/'system.enc',encryption_key=self.key)
        self.service=ProcurementService(self.db);self.catalog=CatalogService(self.db)
        self.sid=self.db.add_supplier({'name':'Seller'})
    def tearDown(self):self.tmp.cleanup()
    def item(self,**extra):return self.catalog.save({'supplier_id':self.sid,'name':'Exact model','sku':'SKU1','comparison_key':'exact','unit_price':'10','currency':'RUB','availability':'available','stock_qty':'100','moq':'2',**extra})
    def purchase(self):
        order={'id':'a'*32,'supplier_id':self.sid,'seller_name':'Seller','product':'Model','quantity':'1','unit_price':'10.00','shipping':'0.00','amount':'10.00','currency':'RUB','buyer_email':'a@b.test','address':'Pickup','trade_format':'B2B','status':'awaiting_integration','created_at':'2026-01-01','idempotency_key':'b'*32}
        return self.db.create_purchase(order)
    def test_projects_links_budget_and_isolation(self):
        p=self.service.project({'name':'Office','budget':'100','currency':'RUB'})['id'];o=self.purchase()
        self.service.link(p,{'kind':'order','entity_id':o['id']});self.service.link(p,{'kind':'supplier','entity_id':self.sid})
        state=self.service.state()['projects'][0];self.assertEqual(state['committed'],'10.00');self.assertEqual(state['remaining'],'90.00')
        with self.assertRaises(ValueError):self.service.link(p,{'kind':'order','entity_id':'not-my-order'})
        self.service.delete_project(p);self.assertEqual(self.service.state()['projects'],[]);self.assertEqual(len(self.db.list_purchases()),1)
    def test_order_transition_history_payment_truth_and_revision(self):
        order=self.purchase();start=self.service.order(order['id'])
        with self.assertRaises(ValueError):self.service.order(order['id'],{**start,'stage':'paid'})
        shipped=self.service.order(order['id'],{**start,'stage':'shipped','tracking':'TRACK','carrier':'Courier','note':'Seller dispatched'})
        self.assertEqual(shipped['revision'],1);self.assertEqual(len(shipped['events']),1)
        with self.assertRaises(ValueError):self.service.order(order['id'],{**start,'stage':'shipped'})
        received=self.service.order(order['id'],{**shipped,'stage':'received'})
        done=self.service.order(order['id'],{**received,'stage':'completed'})
        with self.assertRaises(ValueError):self.service.order(order['id'],{**done,'stage':'agreement'})
        self.assertEqual(self.db.list_purchases()[0]['status'],'awaiting_integration')
    def test_exact_comparison_costs_currency_and_moq(self):
        self.item();self.item(name='Different',sku='OTHER',comparison_key='different',unit_price='1')
        cart=CartService(self.db);cart.save({**cart.state(),'fees':{str(self.sid)+':RUB':dict(shipping='5',taxes='0',other='0')}})
        result=self.service.compare({'comparison_key':'exact','quantity':'3'})['offers'];self.assertEqual(len(result),1);self.assertEqual(result[0]['total'],'35.00');self.assertTrue(result[0]['eligible'])
        result=self.service.compare({'comparison_key':'exact','quantity':'1'})['offers'];self.assertFalse(result[0]['eligible'])
        self.item(sku='USD',currency='USD');offers=self.service.compare({'comparison_key':'exact','quantity':'3'})['offers'];self.assertIsNone(offers[-1]['total'])
    def test_verification_not_inferred_from_edit_and_price_alert(self):
        item=self.item();self.assertTrue(self.catalog.get(item['id'])['stale'])
        from datetime import datetime,timezone,timedelta
        item=self.catalog.save({**item,'checked_at':datetime.now(timezone.utc).isoformat()},item['id']);self.assertFalse(self.catalog.get(item['id'])['stale'])
        self.catalog.save({**item,'unit_price':'11'},item['id']);alerts=self.service.state()['notifications'];self.assertEqual(len(alerts),1)
        self.service.read_alert(alerts[0]['id']);self.assertTrue(self.service.state()['notifications'][0]['is_read'])
        with self.assertRaises(ValueError):self.item(checked_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
    def test_import_preview_upsert_atomicity_and_stale_token(self):
        self.item();data={'supplier_id':self.sid,'csv':'name;sku;unit_price;currency\nNew name;SKU1;12;RUB\nAnother;SKU2;8;RUB'}
        preview=self.catalog.import_price_list(data);self.assertEqual(preview['updated'],1);self.assertEqual(preview['added'],1);self.assertEqual(len(self.catalog.list()),1)
        self.catalog.import_price_list({**data,'preview_token':preview['preview_token']},commit=True);self.assertEqual(len(self.catalog.list()),2)
        with self.assertRaises(ValueError):self.catalog.import_price_list({**data,'preview_token':preview['preview_token']},commit=True)
        before=self.catalog.list()
        with self.assertRaises(ValueError):self.catalog.import_price_list({'supplier_id':self.sid,'csv':'name;unit_price\nValid;1\nInvalid;-2'},True)
        self.assertEqual(before,self.catalog.list())
        with self.assertRaises(ValueError):self.catalog.import_price_list({'supplier_id':self.sid,'csv':'name;sku\nA;same\nB;same'},True)
    def test_changed_terms_invalidate_verification_and_import_notifies(self):
        from datetime import datetime,timezone
        item=self.item(checked_at=datetime.now(timezone.utc).isoformat())
        edited={k:v for k,v in item.items() if k!='checked_at'}
        self.catalog.save({**edited,'unit_price':'15'},item['id'])
        self.assertEqual(self.catalog.get(item['id'])['checked_at'],'')
        self.catalog.import_csv(self.sid,'name;sku;unit_price;currency\nExact model;SKU1;20;RUB')
        self.assertEqual(len(self.service.state()['notifications']),2)
        self.assertTrue(self.catalog.get(item['id'])['stale'])

    def test_xlsx_first_sheet_and_formulas_rejected(self):
        # Minimal fixture via OOXML, no authoring dependency or cached formula values.
        import zipfile
        def fixture(formula=False):
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w') as z:
                z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
                z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Price" sheetId="1" r:id="rId1"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
                value='<f>1+1</f><v>2</v>' if formula else '<is><t>Model</t></is>'
                z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>name</t></is></c></row><row r="2"><c r="A2"'+('' if formula else ' t="inlineStr"')+'>'+value+'</c></row></sheetData></worksheet>')
            return base64.b64encode(output.getvalue()).decode()
        preview=self.catalog.import_price_list({'supplier_id':self.sid,'format':'xlsx','file':fixture()});self.assertEqual(preview['added'],1)
        with self.assertRaises(ValueError):self.catalog.import_price_list({'supplier_id':self.sid,'format':'xlsx','file':fixture(True)})
    def test_saved_search_dedupe_failure_no_false_alerts_and_cooldown(self):
        sid=self.service.save_search({'name':'Search','enabled':True,'params':{'product':'PC','city':'Moscow'}})['id']
        sources=[{'url':'https://example.test/model','title':'Model'}]
        with patch('app.services.search_service.WebSearchClient.discover',return_value=(sources,0)):
            self.assertEqual(self.service.run_search(sid)['new'],1)
            with self.assertRaises(ValueError):self.service.run_search(sid)
            with self.db._conn() as c:c.execute('UPDATE saved_searches SET last_run=0 WHERE id=?',(sid,))
            self.assertEqual(self.service.run_search(sid)['new'],0)
        self.assertEqual(len(self.service.state()['notifications']),1)
        with self.db._conn() as c:c.execute('UPDATE saved_searches SET last_run=0 WHERE id=?',(sid,))
        with patch('app.services.search_service.WebSearchClient.discover',return_value=(sources,1)):
            with self.assertRaises(ValueError):self.service.run_search(sid)
        self.assertEqual(len(self.service.state()['notifications']),1)
        self.assertTrue(self.service.state()['searches'][0]['error'])
    def test_backup_restore_integrity_and_wrong_key(self):
        self.item();archive=self.root/'backup.smb';self.assertEqual(create_backup(self.root/'data',archive,self.key),1)
        self.assertEqual(len(verify_backup(archive,self.key)),1)
        with self.assertRaises(Exception):verify_backup(archive,secrets.token_bytes(32))
        restored=self.root/'restored';restore_backup(archive,restored,self.key)
        db=Database(restored/'system.enc',encryption_key=self.key);self.assertEqual(len(CatalogService(db).list()),1)
        with self.assertRaises(ValueError):restore_backup(archive,restored,self.key)
        raw=archive.read_bytes();archive.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        with self.assertRaises(Exception):verify_backup(archive,self.key)

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.auth=AuthService(Path(self.tmp.name)/'accounts.enc',secrets.token_bytes(32))
        self.user=self.auth.register({'email':'person@example.test','name':'Person','password':'OriginalPassword123'})
        self.token,_=self.auth.session(self.user)
    def tearDown(self):self.tmp.cleanup()
    def request_code(self):
        with patch.object(self.auth.mailer,'configured',return_value=True),patch.object(self.auth.mailer,'send_code') as send:
            result=self.auth.request_password_reset({'email':self.user['email']});code=send.call_args.args[1]
        return result['challenge_id'],code
    def test_reset_is_single_use_and_revokes_all_sessions(self):
        challenge,code=self.request_code();data={'challenge_id':challenge,'code':code,'password':'ReplacementPassword123'}
        self.auth.reset_password(data)
        with self.assertRaises(Unauthorized):self.auth.authenticate(self.token)
        with self.assertRaises(AuthError):self.auth.reset_password(data)
        self.assertEqual(self.auth.login({'email':self.user['email'],'password':data['password']})['id'],self.user['id'])
    def test_attempt_limit_and_unknown_account_response(self):
        challenge,code=self.request_code();wrong='000000' if code!='000000' else '111111'
        for _ in range(5):
            with self.assertRaises(AuthError):self.auth.reset_password({'challenge_id':challenge,'code':wrong,'password':'Replacement123'})
        with self.assertRaises(AuthError):self.auth.reset_password({'challenge_id':challenge,'code':code,'password':'Replacement123'})
        with patch.object(self.auth.mailer,'configured',return_value=True),patch.object(self.auth.mailer,'send_code') as send:
            result=self.auth.request_password_reset({'email':'unknown@example.test'});send.assert_not_called();self.assertTrue(result['challenge_id'])

    def test_restored_backup_revokes_existing_session(self):
        root=Path(self.tmp.name);archive=root/'account-backup.smb'
        create_backup(root,archive,self.auth.key)
        restored=root/'restored';restore_backup(archive,restored,self.auth.key)
        recovered=AuthService(restored/'accounts.enc',self.auth.key)
        with self.assertRaises(Unauthorized):recovered.authenticate(self.token)
        self.assertEqual(recovered.login({'email':self.user['email'],'password':'OriginalPassword123'})['id'],self.user['id'])

class MonitorTests(unittest.TestCase):
    def test_only_enabled_due_searches_of_existing_workspaces_run(self):
        from app.services.monitor_service import SearchMonitor
        with tempfile.TemporaryDirectory() as directory:
            auth=AuthService(Path(directory)/'accounts.enc',secrets.token_bytes(32))
            user=auth.register({'email':'watch@example.test','name':'Watch','password':'WatchPassword123'})
            db=auth.workspace(user['id']);svc=ProcurementService(db)
            enabled=svc.save_search({'name':'On','enabled':True,'params':{'product':'PC','city':'Moscow'}})['id']
            svc.save_search({'name':'Off','enabled':False,'params':{'product':'PC','city':'Moscow'}})
            with db._conn() as c:c.execute('UPDATE saved_searches SET next_run=0')
            with patch.object(ProcurementService,'run_search') as run:
                SearchMonitor(auth).tick();self.assertEqual(run.call_args.args[0],enabled);self.assertEqual(run.call_count,1)
