import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from conexgram import service


class ServiceTests(unittest.TestCase):
    def test_install_service_uses_current_python_not_runtime_command(self):
        with patch.object(service.platform, "system", return_value="Darwin"), patch.object(
            service.sys, "executable", "/private/venv/bin/python"
        ), patch.object(service, "_install_macos", return_value="installed") as install_macos:
            result = service.install_service(
                Path("~/.conexgram/config.json"),
                start=False,
                runtime_binary="/opt/conexgram/bin/conexgram",
            )

        self.assertEqual(result, "installed")
        install_macos.assert_called_once_with(
            "/private/venv/bin/python",
            str(Path("~/.conexgram/config.json").expanduser()),
            "/opt/conexgram/bin/conexgram",
            False,
        )

    def test_macos_service_runs_gateway_as_python_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("conexgram.service.Path.home", return_value=home):
                service._install_macos(
                    "/private/Conexgram & Gateway/bin/python",
                    str(home / "config & prod.json"),
                    "/opt/Conexgram & Agent/bin/conexgram",
                    start=False,
                )

            plist = home / "Library" / "LaunchAgents" / "com.conexgram.agent.plist"
            content = plist.read_text(encoding="utf-8")
            self.assertIn("<string>/private/Conexgram &amp; Gateway/bin/python</string>", content)
            self.assertIn("<string>-m</string>\n    <string>conexgram</string>", content)
            self.assertIn("<string>--runtime-bin</string>", content)
            self.assertIn("/opt/Conexgram &amp; Agent/bin/conexgram", content)
            self.assertIn("config &amp; prod.json", content)
            self.assertNotIn("<string>conexgram-gateway</string>", content)


if __name__ == "__main__":
    unittest.main()
