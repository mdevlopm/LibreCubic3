"""get_info after an ACE IAP update: binary boot version record (raw control chars)."""

import struct
import unittest

import _fakes  # noqa: F401  (sets up sys.path for the ace package)
from ace.protocol_ace1 import AceJsonProtocolAdapter
from ace.serial_manager import AceSerialManager, _printable_version

# Raw get_info payload read from a real ACE (firmware V1.3.86K) after an IAP update.
RAW = (b'{"id":7,"code":0,"result":{"id":0,"slots":4,"model":"Anycubic Color Engine Pro",'
       b'"firmware":"V1.3.86K","boot_firmware":"\x07\x10\x08\x04\x03\t\x04\x12\x03A\x12N\x12Y\x12CV1.3.86K",'
       b'"structure_version":"0"},"msg":"success"}')


def _crc(payload):
    return AceSerialManager._calc_crc(None, payload)


def _frame(payload):
    crc = _crc(payload)
    return b"\xff\xaa" + struct.pack("<H", len(payload)) + payload + struct.pack("<H", crc) + b"\xfe"


class IapBootRecordTests(unittest.TestCase):
    def test_control_characters_in_get_info_are_accepted(self):
        proto = AceJsonProtocolAdapter()
        responses, _, notices = proto.extract_responses(bytearray(_frame(RAW)), _crc)
        self.assertEqual(notices, [])
        self.assertEqual(responses[0]["result"]["firmware"], "V1.3.86K")

    def test_binary_boot_version_is_reported_as_unknown(self):
        self.assertEqual(_printable_version("V1.0.1"), "V1.0.1")
        self.assertEqual(_printable_version("\x07\x10\x08\x04CV1.3.86K"), "?")
        self.assertEqual(_printable_version(None), "")


if __name__ == "__main__":
    unittest.main()
