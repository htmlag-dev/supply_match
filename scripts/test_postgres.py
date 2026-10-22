"""Run service tests in isolated schemas in a disposable PostgreSQL database.
Only randomly named test schemas created by this run are removed.
"""
import os
import secrets
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

if __name__=='__main__':
    if not os.environ.get('SUPPLY_MATCH_DATABASE_URL'):sys.exit('Set SUPPLY_MATCH_DATABASE_URL to a disposable test database.')
    import psycopg
    from psycopg import sql
    class Result(unittest.TextTestResult):
        def startTest(self,test):
            self.namespace='test_'+secrets.token_hex(4)
            os.environ['SUPPLY_MATCH_DATABASE_NAMESPACE']=self.namespace
            super().startTest(test)
        def stopTest(self,test):
            with psycopg.connect(os.environ['SUPPLY_MATCH_DATABASE_URL']) as c:
                names=c.execute('SELECT schema_name FROM information_schema.schemata WHERE left(schema_name,%s)=%s',(len(self.namespace)+1,self.namespace+'_')).fetchall()
                for row in names:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(row[0])))
            super().stopTest(test)
    names=['tests.test_cart','tests.test_procurement.ProcurementTests.test_projects_links_budget_and_isolation','tests.test_procurement.ProcurementTests.test_exact_comparison_costs_currency_and_moq','tests.test_procurement.ProcurementTests.test_order_transition_history_payment_truth_and_revision','tests.test_procurement.ProcurementTests.test_saved_search_dedupe_failure_no_false_alerts_and_cooldown','tests.test_procurement.ProcurementTests.test_import_preview_upsert_atomicity_and_stale_token','tests.test_procurement.RecoveryTests.test_attempt_limit_and_unknown_account_response','tests.test_procurement.RecoveryTests.test_reset_is_single_use_and_revokes_all_sessions','tests.test_procurement_http','tests.test_postgres']
    result=unittest.TextTestRunner(resultclass=Result,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    sys.exit(not result.wasSuccessful())
