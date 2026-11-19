"""Test: History screen - source='ace' labeled as 'ACE'."""

import unittest
from fake_moonraker import FakePrinter, serve
from kobra_screen.app import App
from kobra_screen.moonraker import Moonraker
from kobra_screen.screens import _event_source_label


class TestHistoryAceLabel(unittest.TestCase):
    def setUp(self):
        self.printer = FakePrinter()
        self.httpd = serve(self.printer, simulate=False)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.tmp = __import__('tempfile').TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Moonraker("127.0.0.1", self.httpd.server_address[1]), cache_dir=self.tmp.name)
        self.app.refresh()

    def test_ace_source_labeled_as_ACE(self):
        """source='ace' should be labeled as 'ACE' in the events list in TR and EN."""
        self.assertEqual(_event_source_label("ace", "tr"), "ACE")
        self.assertEqual(_event_source_label("ace", "en"), "ACE")

    def test_ace_source_label_default_lang_ERR_15(self):
        """_event_source_label('ace') == 'ACE' (ERR-15)."""
        self.assertEqual(_event_source_label("ace"), "ACE")


if __name__ == "__main__":
    unittest.main()
