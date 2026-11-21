"""
Diagnostic ACE image (tools/ace-fw/patch_diag.py): get_status.fan_speed
carries the last reset cause and the fault that preceded it. The driver logs
it once after every (re)connect; stock firmware (fan_speed 7000) stays silent.
"""

import unittest

try:
    from tests._fakes import make_manager, open_fake_port, status_result
except ImportError:  # run from inside tests/
    from _fakes import make_manager, open_fake_port, status_result

from ace.reporting import decode_reset_report


def diag_value(flags=0, fault=0, pc=0):
    return 1 << 23 | flags << 24 | fault << 20 | (pc & 0xFFFFF)


class DecodeTests(unittest.TestCase):
    def test_stock_value_is_not_a_report(self):
        self.assertIsNone(decode_reset_report(7000))
        self.assertIsNone(decode_reset_report(None))
        self.assertIsNone(decode_reset_report("x"))

    def test_watchdog_after_hardfault(self):
        reset, fault = decode_reset_report(diag_value(flags=0b001001, fault=1, pc=0x0800C1F4))
        self.assertEqual(reset, "pin, watchdog")
        self.assertEqual(fault, "HardFault at 0x0800c1f4")

    def test_power_on_without_fault(self):
        self.assertEqual(decode_reset_report(diag_value(flags=0b000010)),
                         ("power-on/low-voltage", None))

    def test_flags_cleared_by_bootloader(self):
        self.assertEqual(decode_reset_report(diag_value())[0],
                         "unknown (flags cleared before the app)")

    def test_assert(self):
        self.assertEqual(decode_reset_report(diag_value(flags=8, fault=5, pc=0x08017A10))[1],
                         "assert at 0x08017a10")

    def test_garbage_high_bits_rejected(self):
        self.assertIsNone(decode_reset_report(diag_value() | 1 << 30))


class ConnectLogTests(unittest.TestCase):
    def setUp(self):
        self.printer, self.mgr = make_manager()
        self.ace = self.mgr.instances[0]
        open_fake_port(self.ace.serial_mgr)

    def hb(self, fan_speed):
        result = status_result()
        result["fan_speed"] = fan_speed
        self.ace._on_heartbeat_response({"code": 0, "result": result})

    def reset_lines(self):
        return [m for m in self.printer.gcode.info if "last ACE reset" in m]

    def test_logged_once_per_connect(self):
        self.ace._on_ace_connect()
        self.hb(diag_value(flags=8, fault=3, pc=0x08012345))
        self.hb(diag_value(flags=8, fault=3, pc=0x08012345))
        self.assertEqual(self.reset_lines(),
                         ["ACE[0]: last ACE reset: watchdog; before it: BusFault at 0x08012345"])
        self.ace._on_ace_connect()
        self.hb(diag_value(flags=2))
        self.assertEqual(len(self.reset_lines()), 2)

    def test_stock_firmware_silent(self):
        self.ace._on_ace_connect()
        self.hb(7000)
        self.assertEqual(self.reset_lines(), [])


if __name__ == "__main__":
    unittest.main()
