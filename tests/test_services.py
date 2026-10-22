import os, tempfile, unittest
from app.database import Database
from app.services.ai_service import parse_json, AIError
from app.services.supplier_service import SupplierService


class FakeAI:
    def __init__(self, reply): self.reply = reply
    def generate(self, *a, **k): return self.reply


class Tests(unittest.TestCase):
    def setUp(self):
        self.db = Database(os.path.join(tempfile.mkdtemp(), "t.db"))

    def test_parse_json(self):
        self.assertEqual(parse_json('текст ```json\n[{"a":1}]\n``` ещё'), [{"a": 1}])
        with self.assertRaises(AIError): parse_json("нет json")

    def test_crud(self):
        sid = self.db.add_supplier({"name": "Тест", "city": "Москва"})
        self.assertIsNone(self.db.add_supplier({"name": "Тест"}))
        self.db.update_supplier(sid, {"status": "приглашён"})
        self.assertEqual(self.db.get_supplier(sid)["status"], "приглашён")
        self.db.delete_supplier(sid)
        self.assertEqual(self.db.list_suppliers(), [])

    def test_search_and_rank(self):
        svc = SupplierService(self.db, FakeAI('[{"name":"A","type":"обжарщик"}]'))
        self.assertEqual(svc.search("Москва", "кофе")[0]["city"], "Москва")
        self.db.add_supplier({"name": "A"})
        svc.ai = FakeAI('{"ranking":[{"name":"A","score":80,"reason":"ок"}],"advice":""}')
        self.assertEqual(svc.rank("дёшево")["ranking"][0]["score"], 80)

    def test_settings(self):
        self.db.set_setting("model", "x"); self.assertEqual(self.db.get_setting("model"), "x")


if __name__ == "__main__":
    unittest.main()
