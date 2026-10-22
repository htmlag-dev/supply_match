"""One serial worker; only opted-in daily searches, in-app notifications."""
import logging
import os
import threading
import time
from app.services.procurement_service import ProcurementService

class SearchMonitor:
    def __init__(self, auth):
        self.auth=auth;self.stopped=threading.Event();self.thread=None
    def tick(self):
        with self.auth.conn() as c:
            users=[r['id'] for r in c.execute('SELECT id FROM users WHERE disabled=0 AND email_verified=1')]
        for uid in users:
            if self.stopped.is_set():return
            # Do not create workspaces for accounts that never used the application.
            if not self.auth.workspace_exists(uid):continue
            service=ProcurementService(self.auth.workspace(uid))
            with service.db._conn() as c:
                due=[r['id'] for r in c.execute('SELECT id FROM saved_searches WHERE enabled=1 AND next_run<=?',(int(time.time()),))]
            for sid in due:
                if self.stopped.is_set():return
                try:service.run_search(sid)
                except ValueError:logging.warning('Saved search failed; details available in account monitoring.')
    def start(self):
        def run():
            while not self.stopped.wait(60):
                try:self.tick()
                except Exception:logging.exception('Search monitor iteration failed')
        self.thread=threading.Thread(target=run,name='saved-search-monitor',daemon=True);self.thread.start()
    def stop(self):self.stopped.set()
