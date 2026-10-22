import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from app.database import Database
from app.services.access_service import AccessService
from app.services.ai_service import AIClient, AIError, validate_endpoint
from app.services.billing_service import BillingService
from app.services.cost_service import calculate_cost
from app.services.payment_service import YooKassaGateway, PaymentError
from app.services.purchase_service import PurchaseService
from app.services.supplier_service import SupplierService


class Features(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(os.path.join(self.tmp.name,'test.db'))

    def tearDown(self): self.tmp.cleanup()

    def test_migration_preserves_old_suppliers(self):
        filename=os.path.join(self.tmp.name,'old.db')
        connection=sqlite3.connect(filename)
        connection.execute("CREATE TABLE suppliers(id INTEGER PRIMARY KEY, name TEXT, type TEXT, offer TEXT, conditions TEXT, contact TEXT, site TEXT, why TEXT, city TEXT, status TEXT, note TEXT)")
        connection.execute("INSERT INTO suppliers(id,name,note) VALUES(1,'Existing','Keep me')")
        connection.commit(); connection.close()
        db=Database(filename)
        self.assertEqual(db.get_supplier(1)['note'],'Keep me')
        self.assertEqual(db.get_supplier(1)['trade_format'],'')
        db.update_supplier(1,{'country':'Китай','currency':'CNY','moq':'100 пар'})
        self.assertEqual(Database(filename).get_supplier(1)['moq'],'100 пар')

    def test_cost_import_and_resale(self):
        result=calculate_cost({'quantity':'100','unit_price':'50','currency':'CNY','base_currency':'RUB','rate':'13','shipping':'5000','taxes':'7000','other':'1000','resale_price':'1200'})
        self.assertEqual(result['goods'],'65000.00')
        self.assertEqual(result['total'],'78000.00')
        self.assertEqual(result['per_unit'],'780.00')
        self.assertEqual(result['profit'],'42000.00')
        self.assertEqual(result['margin'],'35.00')
        domestic=calculate_cost({'quantity':'3','unit_price':'0.10','currency':'RUB','base_currency':'RUB','rate':'999'})
        self.assertEqual(domestic['total'],'0.30')
        self.assertEqual(domestic['rate'],'1')
        for value in ('NaN','Infinity','-1','0','1e20'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                calculate_cost({'quantity':value,'unit_price':'10','currency':'RUB','base_currency':'RUB'})

    def test_trial_limits_and_failure_refund(self):
        access=AccessService(self.db)
        self.assertEqual(access.state()['plan'],'trial')
        with self.assertRaises(RuntimeError):
            with access.ai_request(): raise RuntimeError('provider failed')
        self.assertEqual(access.state()['ai_used'],0)
        self.db.set_setting('trial_started_at',(datetime.now(timezone.utc)-timedelta(days=15)).isoformat())
        self.assertEqual(access.state()['plan'],'start')
        for _ in range(5):
            with access.ai_request(): pass
        with self.assertRaises(ValueError):
            with access.ai_request(): pass
        self.assertEqual(access.state()['ai_used'],5)
        self.db.add_supplier({'name':'One'},limit=1)
        with self.assertRaises(ValueError): self.db.add_supplier({'name':'Two'},limit=1)
        self.assertIsNone(self.db.add_supplier({'name':'One'},limit=1))

    def test_live_search_passes_evidence_and_filters_invented_domains(self):
        class FakeAI:
            def generate(inner,prompt,**kwargs):
                inner.prompt=prompt; inner.kwargs=kwargs
                return '[{"name":"Seller","site":"https://invented.example","country":"Китай"}]'
        ai=FakeAI(); service=SupplierService(self.db,ai)
        service.web=unittest.mock.Mock(key='test')
        service.web.search.return_value=[{'url':'https://source.example/listing','title':'Shoes','content':'Real search snippet'}]
        result=service.search_with_sources(city='Москва',product='обувь',web=True,country='Китай',quantity='100 пар',trade_format='B2B')
        self.assertEqual(result['mode'],'tavily')
        self.assertEqual(result['suppliers'][0]['site'],'')
        self.assertIn('Real search snippet',ai.prompt)
        self.assertIn('100 пар',ai.prompt)
        self.assertFalse(ai.kwargs['web_search'])

    def test_auto_fallback_uses_only_free_router(self):
        ai=AIClient(self.db)
        with patch.dict(os.environ,{'GEMINI_API_KEY':'','SUPPLY_MATCH_OPENROUTER_KEY':'managed-secret'}),patch.object(AIClient,'local_ready',new_callable=unittest.mock.PropertyMock,return_value=True),patch.object(ai,'_generate',side_effect=[AIError('busy'),'result']) as generate:
            self.assertEqual(ai.generate('hello'),'result')
            self.assertEqual(generate.call_args_list[0].args[0],'local')
            self.assertEqual(generate.call_args_list[1].args[:3],('openrouter','managed-secret','openrouter/free'))

    def test_custom_endpoint_rejects_insecure_and_internal_addresses(self):
        for url in ('http://example.com/api','https://user:pass@example.com/api','https://127.0.0.1/api','https://[::1]/api'):
            with self.subTest(url=url),self.assertRaises(ValueError): validate_endpoint(url)

    def test_ungrounded_search_does_not_publish_invented_prices_or_contacts(self):
        fake=unittest.mock.Mock()
        fake.generate.return_value='[{"name":"Candidate","conditions":"Delivery guaranteed","contact":"fake@example.com","currency":"CNY","unit_price":"42","moq":"100","lead_time":"2 days","incoterms":"DDP"}]'
        result=SupplierService(self.db,fake).search('Москва','обувь',web=False)
        for field in ('conditions','contact','currency','unit_price','moq','lead_time','incoterms'):
            self.assertEqual(result[0][field],'')

    def test_purchase_recalculates_and_deduplicates(self):
        sid=self.db.add_supplier({'name':'Shoe manufacturer','country':'Китай'})
        data={'supplier_id':sid,'product':'Обувь','quantity':'100','unit_price':'50','shipping':'100','currency':'CNY','trade_format':'B2B','buyer_email':'buyer@example.com','address':'Москва','consent':True,'idempotency_key':'p'*24,'amount':'1'}
        result=PurchaseService(self.db).create(data)
        self.assertEqual(result['order']['amount'],'5100.00')
        self.assertEqual(result['order']['seller_name'],'Shoe manufacturer')
        self.assertEqual(PurchaseService(self.db).create(data)['order']['id'],result['order']['id'])
        self.assertEqual(len(self.db.list_purchases()),1)
        self.assertFalse(result['payment_enabled'])
        with self.assertRaises(ValueError): PurchaseService(self.db).create({**data,'unit_price':'51'})
        for changes in ({'quantity':'0'},{'unit_price':'-2'},{'buyer_email':'bad'},{'supplier_id':True},{'consent':False}):
            with self.subTest(changes=changes),self.assertRaises(ValueError): PurchaseService(self.db).create({**data,**changes})

    def test_payment_verified_by_provider_and_webhook_is_idempotent(self):
        billing=BillingService(self.db)
        order=billing.checkout({'plan':'pro','period':'monthly','email':'buyer@example.com','company':'','consent':True,'idempotency_key':'o'*24})['order']
        gateway=YooKassaGateway(self.db)
        payment_id='payment-test-1234567890'
        environment={'SUPPLY_MATCH_BILLING_SHOP_ID':'shop123','SUPPLY_MATCH_BILLING_SECRET':'secret','SUPPLY_MATCH_PAYMENT_RETURN_URL':'https://app.example.com/#pricing'}
        response={'id':payment_id,'confirmation':{'confirmation_url':'https://yoomoney.ru/checkout/test'}}
        with patch.dict(os.environ,environment),patch.object(gateway,'_request',return_value=response) as send:
            checkout=gateway.checkout('subscription',order,'billing')
            self.assertTrue(checkout['payment_enabled'])
            gateway.checkout('subscription',order,'billing')
            self.assertEqual(send.call_count,1)
            self.assertEqual(send.call_args.args[2]['amount']['value'],'990.00')
        remote={'id':payment_id,'recipient':{'account_id':'shop123'},'amount':{'value':'990.00','currency':'RUB'},'metadata':{'order_id':order['id'],'kind':'subscription'},'status':'succeeded','paid':True}
        # Incoming notification can claim anything. Only the provider's GET result is authoritative.
        with patch.dict(os.environ,environment),patch.object(gateway,'_request',return_value={**remote,'status':'pending','paid':False}):
            gateway.webhook({'object':{'id':payment_id,'status':'succeeded'}})
            self.assertNotEqual(billing.state()['current_plan'],'pro')
        with patch.dict(os.environ,environment),patch.object(gateway,'_request',return_value={**remote,'amount':{'value':'1.00','currency':'RUB'}}):
            with self.assertRaises(PaymentError): gateway.webhook({'object':{'id':payment_id}})
        with patch.dict(os.environ,environment),patch.object(gateway,'_request',return_value=remote):
            gateway.webhook({'object':{'id':payment_id}})
            until=self.db.get_setting('subscription_until')
            gateway.webhook({'object':{'id':payment_id}})
            self.assertEqual(until,self.db.get_setting('subscription_until'))
        self.assertEqual(billing.state()['current_plan'],'pro')
        self.assertEqual(self.db.list_orders()[0]['status'],'paid')


if __name__=='__main__': unittest.main()
