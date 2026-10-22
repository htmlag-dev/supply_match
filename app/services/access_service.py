"""Локальные тарифы, пробный период и учёт AI-запросов."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

LIMITS = {"start": {"ai": 5, "suppliers": 20, "history": False},
          "trial": {"ai": 50, "suppliers": 100, "history": True},
          "pro": {"ai": 300, "suppliers": 1000, "history": True}}


class AccessService:
    def __init__(self, db):
        self.db = db
        with db._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('trial_started_at',?)", (datetime.now(timezone.utc).isoformat(),))

    def state(self):
        now = datetime.now(timezone.utc)
        started = datetime.fromisoformat(self.db.get_setting("trial_started_at"))
        trial_end = started + timedelta(days=14)
        plan = "trial" if now < trial_end else "start"
        # Только серверное подтверждение платежа должно записывать срок подписки.
        until = self.db.get_setting("subscription_until")
        if self.db.get_setting("subscription_plan") == "pro" and until and datetime.fromisoformat(until) > now:
            plan = "pro"
        bucket = "trial" if plan == "trial" else plan + ":" + now.strftime("%Y-%m")
        return {"plan": plan, "limits": LIMITS[plan], "ai_used": self.db.usage_count(bucket),
                "bucket": bucket, "trial_ends_at": trial_end.isoformat(),
                "trial_days_left": max(0, (trial_end - now).days + (1 if trial_end > now else 0)),
                "subscription_until": until or None}

    @contextmanager
    def ai_request(self):
        access = self.state()
        self.db.reserve_usage(access["bucket"], access["limits"]["ai"])
        try:
            yield
        except Exception:
            self.db.release_usage(access["bucket"])
            raise
