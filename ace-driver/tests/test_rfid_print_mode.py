"""
Stock gklib turns the ACE's RFID recognition off for the whole print
(start_print / resume) and back on at pause, cancel and finish. The driver
does the same from the 2 s health tick, once per change.
"""

import unittest

try:
    from tests._fakes import make_manager, open_fake_port
except ImportError:  # run from inside tests/
    from _fakes import make_manager, open_fake_port


class RfidPrintModeTests(unittest.TestCase):
    def setUp(self):
        self.printer, self.mgr = make_manager(print_state="standby")
        self.ace = self.mgr.instances[0]
        open_fake_port(self.ace.serial_mgr)
        self.queue = self.ace.serial_mgr._queue

    def methods(self):
        return [r[0].get("method") for r in list(self.queue.queue)]

    def reply(self, code=0):
        request, callback = list(self.queue.queue)[-1][:2]
        self.queue.queue.clear()
        callback({"code": code, "msg": "success" if code == 0 else "err"})
        return request["method"]

    def tick(self, state):
        self.printer.print_stats.state = state
        self.mgr.update_rfid_print_mode()

    def test_disabled_while_printing_enabled_after(self):
        self.tick("standby")
        self.assertEqual(self.reply(), "enable_rfid")
        self.tick("printing")
        self.assertEqual(self.reply(), "disable_rfid")
        self.tick("printing")
        self.assertEqual(self.methods(), [])  # once per change
        self.tick("paused")
        self.assertEqual(self.reply(), "enable_rfid")
        self.tick("printing")
        self.assertEqual(self.reply(), "disable_rfid")
        self.tick("complete")
        self.assertEqual(self.reply(), "enable_rfid")

    def test_no_second_request_while_one_is_pending(self):
        self.tick("printing")
        self.tick("printing")
        self.assertEqual(self.methods(), ["disable_rfid"])

    def test_failed_request_is_retried(self):
        self.tick("printing")
        self.reply(code=1)
        self.tick("printing")
        self.assertEqual(self.methods(), ["disable_rfid"])

    def test_reconnect_sends_state_again(self):
        self.tick("printing")
        self.reply()
        self.ace._on_ace_connect()  # ACE rebooted: RFID back on
        self.tick("printing")
        self.assertEqual(self.methods(), ["disable_rfid"])

    def test_not_sent_while_disconnected(self):
        self.ace.serial_mgr._connected = False
        self.tick("printing")
        self.assertEqual(self.methods(), [])

    def test_protocol_without_rfid_switch_is_skipped(self):
        def unsupported():
            raise NotImplementedError()
        self.ace.protocol.build_disable_rfid_request = unsupported
        self.tick("printing")
        self.assertEqual(self.methods(), [])
        self.tick("printing")
        self.assertEqual(self.methods(), [])


if __name__ == "__main__":
    unittest.main()
