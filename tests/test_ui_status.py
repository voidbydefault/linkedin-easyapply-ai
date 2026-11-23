import os
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import config_ui


class TestUIStatus(unittest.TestCase):
    def setUp(self):
        self.client = config_ui.app.test_client()

    def test_bot_status_reports_stopped_and_modes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            status_path = os.path.join(tmp_dir, ".bot_active")
            with patch.object(config_ui, "BOT_STATUS_FILE", status_path):
                res_stopped = self.client.get("/bot_status")
                self.assertEqual(res_stopped.status_code, 200)
                self.assertEqual(res_stopped.get_json(), {"running": False, "mode": None})

                with open(status_path, "w", encoding="utf-8") as f:
                    f.write("scout")
                res_scout = self.client.get("/bot_status")
                self.assertEqual(res_scout.status_code, 200)
                self.assertEqual(res_scout.get_json(), {"running": True, "mode": "scout"})

                with open(status_path, "w", encoding="utf-8") as f:
                    f.write("apply")
                res_apply = self.client.get("/bot_status")
                self.assertEqual(res_apply.status_code, 200)
                self.assertEqual(res_apply.get_json(), {"running": True, "mode": "apply"})

    def test_index_renders_single_stop_button_with_mode_labels(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertEqual(html.count('id="btnStop"'), 1)
        self.assertIn("🛑 Stop Bot Execution", html)
        self.assertIn("'Scout'", html)
        self.assertIn("'Applying'", html)


if __name__ == "__main__":
    unittest.main()
