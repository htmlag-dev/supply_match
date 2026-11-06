"""Run against a disposable PostgreSQL database via scripts/test_postgres.py."""
import os
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

@unittest.skipUnless(os.environ.get('SUPPLY_MATCH_DATABASE_URL'),'PostgreSQL URL is not configured')
class PostgresTests(unittest.TestCase):
    def test_migration_dry_run_commit_and_duplicate_target(self):
        from app.database import Database
        from app.services.catalog_service import CatalogService
        from app.services.procurement_service import ProcurementService
        from app.postgres import connection
        from scripts.migrate_postgres import migrate_file
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'system.enc';key=secrets.token_bytes(32)
            with patch.dict(os.environ,{'SUPPLY_MATCH_DATABASE_URL':''}):
                db=Database(path,encryption_key=key);sid=db.add_supplier({'name':'Source seller'})
                CatalogService(db).save({'supplier_id':sid,'name':'Model','sku':'1'})
                ProcurementService(db).project({'name':'Source project'})
            original=path.read_bytes()
            preview=migrate_file(path,key);self.assertEqual(preview['suppliers'],1)
            result=migrate_file(path,key,commit=True);self.assertEqual(preview,result)
            db=Database(path,encryption_key=key);self.assertEqual(db.list_suppliers()[0]['name'],'Source seller')
            self.assertEqual(len(CatalogService(db).list()),1)
            self.assertEqual(db.add_supplier({'name':'Next seller'}),sid+1)
            self.assertEqual(path.read_bytes(),original)
            with self.assertRaises(ValueError):migrate_file(path,key,commit=True)
    def test_concurrent_usage_limit_and_workspace_isolation(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.database import Database
        with tempfile.TemporaryDirectory() as directory:
            db=Database(Path(directory)/(secrets.token_hex(16)+'.enc'))
            def reserve(_):
                try:db.reserve_usage('test',3);return True
                except ValueError:return False
            with ThreadPoolExecutor(max_workers=8) as executor:results=list(executor.map(reserve,range(12)))
            self.assertEqual(sum(results),3)
            db.add_supplier({'name':'Private'})
            other=Database(Path(directory)/(secrets.token_hex(16)+'.enc'));self.assertEqual(other.list_suppliers(),[])

    def test_pool_reuses_connection_and_resets_transaction_between_workspaces(self):
        from app.postgres import connection, close_pool
        close_pool()
        self.addCleanup(close_pool)
        first = Path(secrets.token_hex(16) + '.enc')
        second = Path(secrets.token_hex(16) + '.enc')
        with connection(first) as c:
            pid = c.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
            c.execute('CREATE TABLE sample(value TEXT)')
            c.execute('INSERT INTO sample(value) VALUES(?)', ('private',))
        with self.assertRaises(RuntimeError):
            with connection(first) as c:
                c.execute('INSERT INTO sample(value) VALUES(?)', ('rollback',))
                raise RuntimeError('Simulated failure')
        with connection(second) as c:
            self.assertEqual(c.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'], pid)
            c.execute('CREATE TABLE sample(value TEXT)')
            self.assertEqual(c.execute('SELECT * FROM sample').fetchall(), [])
        with connection(first) as c:
            self.assertEqual([r['value'] for r in c.execute('SELECT * FROM sample')], ['private'])
