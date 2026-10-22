import json,secrets,tempfile,unittest
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
from app.database import Database
from app.services.catalog_service import CatalogService
from app.services.cart_service import CartService

class CartTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Database(Path(self.tmp.name)/'cart.enc',encryption_key=secrets.token_bytes(32));self.a=self.db.add_supplier({'name':'Seller A','trade_format':'B2B'});self.b=self.db.add_supplier({'name':'Seller B','trade_format':'B2B'});self.catalog=CatalogService(self.db);self.cart=CartService(self.db)
    def tearDown(self):self.tmp.cleanup()
    def item(self,seller,name,price,key,**kwargs):return self.catalog.save({'supplier_id':seller,'name':name,'category':'Computers' if 'PC' in name else 'Irons','unit_price':price,'comparison_key':key,'availability':'available','currency':'RUB','lead_min':2,'lead_max':5,**kwargs})
    def save(self,lines,fees=None,**kwargs):
        state=self.cart.state();return self.cart.save({**state,'lines':[dict(item_id=i['id'],quantity=q) for i,q in lines],'fees':fees or {str(s)+':RUB':dict(shipping='0',taxes='0',other='0') for s in (self.a,self.b)},**kwargs})
    def test_mixed_models_categories_and_delivery_dates(self):
        pc=self.item(self.a,'PC Model A','50000','pc-a');iron=self.item(self.a,'Iron B','3000','iron-b');other=self.item(self.b,'PC Model C','40000','pc-c');placed=date.today()+timedelta(days=3)
        self.save([(pc,'2'),(iron,'1'),(other,'1')],order_date=placed.isoformat());quote=self.cart.quote()
        self.assertEqual(len(quote['lines']),3);self.assertEqual(len(quote['groups']),2);self.assertEqual(quote['total'],'143000.00');self.assertTrue(quote['ready']);self.assertEqual(quote['lines'][0]['arrival']['to'],(placed+timedelta(days=5)).isoformat())
        again=self.cart.save(self.cart.state());self.assertEqual(again['revision'],1)
    def test_unknown_values_never_become_zero_and_prevent_checkout(self):
        item=self.catalog.save({'supplier_id':self.a,'name':'Unknown model'});self.save([(item,'1')],fees={})
        quote=self.cart.quote();self.assertIsNone(quote['total']);self.assertIsNone(quote['lines'][0]['unit_price']);self.assertIsNone(quote['lines'][0]['arrival']);self.assertFalse(quote['ready'])
        with self.assertRaises(ValueError):self.cart.checkout({'quote_id':quote['quote_id']})
    def test_optimizer_counts_shared_shipping_once_and_exact_models_only(self):
        pc_a=self.item(self.a,'PC A','100','pc-a');iron_a=self.item(self.a,'Iron B','100','iron-b');pc_b=self.item(self.b,'PC A same','90','pc-a');self.item(self.b,'Iron B same','90','iron-b');self.item(self.b,'Different PC','1','different-pc')
        fees={str(self.a)+':RUB':dict(shipping='0',taxes='0',other='0'),str(self.b)+':RUB':dict(shipping='15',taxes='0',other='0')}
        self.save([(pc_a,'1'),(iron_a,'1')],fees);result=self.cart.optimize();self.assertEqual(result['quote']['total'],'195.00');self.assertEqual(result['saving'],'5.00');self.assertTrue(all(x['supplier_id']==self.b for x in result['quote']['lines']))
    def test_stock_deadline_and_missing_exchange_rate(self):
        item=self.item(self.a,'PC','10','pc',stock_qty='1',currency='CNY');self.save([(item,'2')],deadline=(date.today()+timedelta(days=1)).isoformat());q=self.cart.quote();self.assertIsNone(q['total']);self.assertFalse(q['ready']);self.assertTrue(any('остаток' in s for s in q['errors']));self.assertTrue(any('срок' in s for s in q['errors']))
    def test_grouped_checkout_is_idempotent_and_snapshots_models(self):
        pc=self.item(self.a,'PC A','100','pc-a');iron=self.item(self.a,'Iron B','20','iron-b');other=self.item(self.b,'PC C','70','pc-c');self.save([(pc,'2'),(iron,'1'),(other,'1')]);q=self.cart.quote();data=dict(quote_id=q['quote_id'],buyer_email='buyer@example.com',address='Self pickup',consent=True,idempotency_key='a'*48)
        result=self.cart.checkout(data);self.assertEqual(len(result['orders']),2);self.assertEqual(len(self.db.list_purchases()),2)
        repeat=self.cart.checkout(data);self.assertEqual([x['order']['id'] for x in repeat['orders']],[x['order']['id'] for x in result['orders']]);self.assertEqual(len(json.loads(result['orders'][0]['order']['items'])),2)
        self.assertTrue(all(not x['payment_enabled'] for x in result['orders']))
    def test_catalog_import_validates_all_rows_and_preserves_unknowns(self):
        with self.assertRaises(ValueError):self.catalog.import_csv(self.a,'name;unit_price;currency\nGood;100;RUB\nBad;-1;RUB')
        self.assertEqual(self.catalog.list(),[])
        result=self.catalog.import_csv(self.a,'name;category;currency\nPC;Computers;RUB\nIron;Irons;RUB');self.assertEqual(result['added'],2);self.assertIsNone(self.catalog.list()[0]['unit_price'])
    def test_revision_and_quote_tampering_rejected(self):
        item=self.item(self.a,'PC','100','pc');stale=self.cart.state();self.save([(item,'1')])
        with self.assertRaises(ValueError):self.cart.save(stale)
        with self.assertRaises(ValueError):self.cart.checkout(dict(quote_id='fake',consent=True))
