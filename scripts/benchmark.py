"""Isolated synthetic HTTP benchmark. Never touches the application data."""
import http.client,json,secrets,statistics,tempfile,threading,time,sys
from pathlib import Path
from unittest.mock import patch,PropertyMock
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from http.server import ThreadingHTTPServer
from app.database import Database
from app.services.auth_service import AuthService
from app.services.ai_service import AIClient
from app.services.supplier_service import SupplierService
from app.services.catalog_service import CatalogService
from app.server import make_handler
with tempfile.TemporaryDirectory() as temp:
    auth=AuthService(Path(temp)/'accounts.enc',secrets.token_bytes(32));user=auth.register(dict(email='benchmark@example.com',name='Benchmark',password='benchmark temporary password'));token,_=auth.session(user);db=auth.workspace(user['id']);catalog=CatalogService(db)
    sellers=[db.add_supplier(dict(name='Synthetic seller '+str(i))) for i in range(20)]
    base=catalog.clean(dict(supplier_id=sellers[0],name='Synthetic item',currency='RUB',availability='available',unit_price='100'))
    with db._conn() as c:
        rows=[]
        for i in range(2000):
            item={**base,'supplier_id':sellers[i%20],'name':'Synthetic model '+str(i),'note':'x'*500};rows.append((f'{i:032x}',item['supplier_id'],json.dumps(item)))
        c.executemany('INSERT INTO catalog_items VALUES(?,?,?)',rows)
    ai=AIClient(db);server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(db,SupplierService(db,ai),ai,auth=auth));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    report={}
    try:
        with patch.object(AIClient,'local_ready',new_callable=PropertyMock,return_value=False):
            for path in ('/api/state','/api/catalog?supplier_id='+str(sellers[0]),'/api/cart'):
                times=[];size=0
                for _ in range(12):
                    connection=http.client.HTTPConnection('127.0.0.1',server.server_port);start=time.perf_counter();connection.request('GET',path,headers={'Cookie':'sm_session='+token});response=connection.getresponse();body=response.read();times.append((time.perf_counter()-start)*1000);connection.close();assert response.status==200,(path,response.status,body[:100]);size=len(body)
                report[path]={'median_ms':round(statistics.median(times),2),'max_ms':round(max(times),2),'response_bytes':size}
    finally:server.shutdown();server.server_close();thread.join()
    print(json.dumps(report,indent=2))
