import os,tempfile,unittest
from unittest.mock import Mock,patch
from app.database import Database
from app.services.search_service import WebSearchClient
from app.services.supplier_service import SupplierService
class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(os.path.join(self.temp.name,'test.db'))
    def tearDown(self):self.temp.cleanup()
    def test_multiple_queries_deduplicate_but_preserve_listings(self):
        fake=Mock();fake.text.return_value=[{'title':'Seller','href':'https://seller.example/item?utm_source=test','body':'supplier snippet'},{'title':'Same','href':'https://seller.example/item','body':'duplicate'},{'title':'Other listing','href':'https://seller.example/other','body':'different'},{'title':'Bad','href':'javascript:alert(1)'}]
        with patch('ddgs.DDGS',return_value=fake):sources,failures=WebSearchClient(self.db).discover('notebooks',city='Moscow',page=2)
        self.assertEqual(failures,0);self.assertEqual(len(sources),2);self.assertEqual(fake.text.call_count,4)
        self.assertEqual(len(set(c.args[0] for c in fake.text.call_args_list)),4)
        self.assertTrue(all(c.kwargs['page']==2 for c in fake.text.call_args_list))
    def test_live_pages_are_not_replaced_by_famous_model_companies(self):
        ai=Mock();service=SupplierService(self.db,ai);service.web=Mock(key='')
        service.web.discover.return_value=([{'url':'https://small.example/catalog','title':'Small local supplier','content':'public search snippet'}],0)
        result=service.search_with_sources(city='Moscow',product='notebooks',trade_format='B2B',web=True,page=2)
        self.assertEqual(result['mode'],'discovery');self.assertEqual(result['page'],2)
        self.assertEqual(result['suppliers'][0]['site'],'https://small.example/catalog')
        for k in ('contact','unit_price','country','moq'):self.assertEqual(result['suppliers'][0][k],'')
        ai.generate.assert_not_called()
