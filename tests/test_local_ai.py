import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.local_ai import LocalAIProcess


class LocalAIStartupTests(unittest.TestCase):
    def test_windows_files_are_not_started_on_mac(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "local-ai"
            (root / "runtime").mkdir(parents=True)
            (root / "runtime" / "llama-server.exe").touch()
            (root / "Qwen3-4B-Q4_K_M.gguf").touch()
            with patch("app.services.local_ai.DATA_DIR", Path(directory)), patch(
                "app.services.local_ai.os", SimpleNamespace(name="posix")
            ), patch("app.services.local_ai.subprocess.Popen") as spawn:
                process = LocalAIProcess()
                process.start(auto_setup=False)
                spawn.assert_not_called()
                self.assertIsNone(process.log)
                self.assertEqual(process.phase, "not_installed")

    def test_runtime_failure_does_not_crash_application(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "local-ai"
            (root / "runtime").mkdir(parents=True)
            (root / "runtime" / "llama-server.exe").touch()
            (root / "Qwen3-4B-Q4_K_M.gguf").touch()
            with patch("app.services.local_ai.DATA_DIR", Path(directory)), patch(
                "app.services.local_ai.os", SimpleNamespace(name="nt", environ={}, cpu_count=lambda: 4)
            ), patch("app.services.local_ai.available", return_value=False), patch(
                "app.services.local_ai.subprocess.Popen", side_effect=OSError("Invalid runtime")
            ):
                process = LocalAIProcess()
                process.start(auto_setup=False)
                self.assertEqual(process.phase, "error")
                self.assertIsNone(process.log)
                process.stop()
