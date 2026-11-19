"""Test: Updates page - ace-driver version displayed."""

import os
import tempfile
import unittest

from fake_moonraker import FakePrinter, FakeWifiManager, serve
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker


class TestUpdatesAceVersion(unittest.TestCase):
    def test_get_ace_driver_version_with_file(self):
        """get_ace_driver_version should return file content when file exists."""
        tmp = tempfile.mkdtemp()
        version_path = os.path.join(tmp, "ace-driver.version")
        ace_version = "1.3.863"
        with open(version_path, "w") as f:
            f.write(ace_version)

        version = App.get_ace_driver_version(rinkhals_home=tmp)
        self.assertEqual(version, ace_version)

    def test_get_ace_driver_version_without_file(self):
        """get_ace_driver_version should return '-' when file doesn't exist."""
        tmp = tempfile.mkdtemp()
        version = App.get_ace_driver_version(rinkhals_home=tmp)
        self.assertEqual(version, "-")

    def test_updates_page_shows_ace_driver_row(self):
        tmp = tempfile.mkdtemp()
        version_path = os.path.join(tmp, "open-kobra", "ace-driver.version")
        os.makedirs(os.path.dirname(version_path), exist_ok=True)
        with open(version_path, "w") as f:
            f.write("0.2.2")

        printer = FakePrinter()
        httpd = serve(printer, simulate=False)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        app = App(
            Moonraker("127.0.0.1", httpd.server_address[1]),
            wifi=FakeWifiManager(),
            rinkhals_home=tmp,
        )
        app.refresh()
        app.open("updates")
        txts = [item.widget.text for item in app.layout() if hasattr(item.widget, "text")]
        self.assertIn("ace-driver", txts)
        self.assertIn("0.2.2", txts)

    def test_updates_page_shows_ace_driver_dash_when_no_file(self):
        tmp = tempfile.mkdtemp()
        printer = FakePrinter()
        httpd = serve(printer, simulate=False)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        app = App(
            Moonraker("127.0.0.1", httpd.server_address[1]),
            wifi=FakeWifiManager(),
            rinkhals_home=tmp,
        )
        app.refresh()
        app.open("updates")
        txts = [item.widget.text for item in app.layout() if hasattr(item.widget, "text")]
        self.assertIn("ace-driver", txts)
        self.assertIn("-", txts)

    def test_updates_page_shows_ace_driver_row_format(self):
        """Verify ace-driver version row existence and format in updates page."""
        from kobra_screen.widgets import Label
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(tmp, ignore_errors=True))
        version_path = os.path.join(tmp, "open-kobra", "ace-driver.version")
        os.makedirs(os.path.dirname(version_path), exist_ok=True)
        with open(version_path, "w") as f:
            f.write("1.3.863\n")

        printer = FakePrinter()
        httpd = serve(printer, simulate=False)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        app = App(
            Moonraker("127.0.0.1", httpd.server_address[1]),
            wifi=FakeWifiManager(),
            rinkhals_home=tmp,
        )
        app.refresh()
        app.open("updates")
        labels = [item.widget for item in app.layout() if isinstance(item.widget, Label)]
        ace_label = next((l for l in labels if l.text == "ace-driver"), None)
        self.assertIsNotNone(ace_label, "ace-driver label not found on updates page")
        self.assertEqual(ace_label.size, "small")
        self.assertEqual(ace_label.color, "muted")

        ver_label = next((l for l in labels if l.text == "1.3.863"), None)
        self.assertIsNotNone(ver_label, "ace-driver version value label not found")
        self.assertEqual(ver_label.size, "small")
        self.assertEqual(ver_label.align, "right")


if __name__ == "__main__":
    unittest.main()
