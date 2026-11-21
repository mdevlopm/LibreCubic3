#!/usr/bin/env python3
"""
Tests for patch_diag.py. The added code is run in a tiny emulator of exactly
the Thumb instructions it uses, so the encodings are checked by behaviour,
not only by disassembly.
"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import patch_diag as pd

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SRC = os.environ.get("ACE_86K_IMAGE") or os.path.join(ROOT, "refs", "ace-fw", "ACE_V1.3.86K_cooldown60.bin")


class Thumb:
    """Executes the subset of Thumb used by patch_diag (raises on anything else)."""

    def __init__(self, img, mem):
        self.img, self.mem = img, mem
        self.r = [0] * 16
        self.n = self.z = False
        self.msp = self.psp = 0

    def half(self, a):
        return struct.unpack_from("<H", self.img, a - pd.BASE)[0]

    def word(self, a):
        if pd.BASE <= a < pd.BASE + len(self.img):
            return struct.unpack_from("<I", self.img, a - pd.BASE)[0]
        return self.mem.get(a, 0)

    def flags(self, v):
        self.n, self.z = bool(v & 0x80000000), v == 0

    def run(self, pc, stop, limit=200):
        for _ in range(limit):
            if pc in stop:
                return pc
            i = self.half(pc)
            nxt = pc + 2
            if i & 0xF800 == 0x4800:                                   # ldr lit
                self.r[(i >> 8) & 7] = self.word(((pc + 4) & ~3) + (i & 0xFF) * 4)
            elif i & 0xF800 == 0x6800:                                 # ldr imm
                self.r[i & 7] = self.word(self.r[(i >> 3) & 7] + ((i >> 6) & 31) * 4)
            elif i & 0xF800 == 0x6000:                                 # str imm
                self.mem[self.r[(i >> 3) & 7] + ((i >> 6) & 31) * 4] = self.r[i & 7]
            elif i & 0xF800 == 0x0000 and i != 0:                      # lsls
                v = (self.r[(i >> 3) & 7] << ((i >> 6) & 31)) & 0xFFFFFFFF
                self.r[i & 7] = v
                self.flags(v)
            elif i & 0xF800 == 0x0800:                                 # lsrs
                v = self.r[(i >> 3) & 7] >> ((i >> 6) & 31)
                self.r[i & 7] = v
                self.flags(v)
            elif i & 0xFFC0 == 0x4300:                                 # orrs
                v = self.r[i & 7] | self.r[(i >> 3) & 7]
                self.r[i & 7] = v
                self.flags(v)
            elif i & 0xFFC0 == 0x4280:                                 # cmp
                v = (self.r[i & 7] - self.r[(i >> 3) & 7]) & 0xFFFFFFFF
                self.flags(v)
            elif i & 0xF800 == 0x2000:                                 # movs
                self.r[(i >> 8) & 7] = i & 0xFF
                self.flags(i & 0xFF)
            elif i & 0xFF00 in (0xD100, 0xD400):                       # bne / bmi
                taken = (not self.z) if i & 0xFF00 == 0xD100 else self.n
                if taken:
                    off = i & 0xFF
                    nxt = pc + 4 + ((off - 256) if off & 0x80 else off) * 2
            elif i == 0xE7FE:
                return pc                                              # b .
            elif i & 0xF800 == 0xE000:                                 # b
                off = i & 0x7FF
                nxt = pc + 4 + ((off - 2048) if off & 0x400 else off) * 2
            elif i & 0xFF87 == 0x4700:                                 # bx
                nxt = self.r[(i >> 3) & 15] & ~1
            elif i == 0x4671:
                self.r[1] = self.r[14]
            elif i == 0x4607:
                self.r[7] = self.r[0]
            elif i & 0xFE00 in (0xB400, 0xBC00):                       # push / pop
                regs = [n for n in range(8) if i & (1 << n)]
                if i & 0x100:
                    regs.append(14 if i & 0x800 == 0 else 15)
                if i & 0x800 == 0:
                    self.r[13] -= 4 * len(regs)
                    for k, n in enumerate(regs):
                        self.mem[self.r[13] + 4 * k] = self.r[n]
                else:
                    for k, n in enumerate(regs):
                        self.r[n] = self.mem[self.r[13] + 4 * k]
                    self.r[13] += 4 * len(regs)
            elif i == 0xF3EF:                                          # mrs
                i2 = self.half(pc + 2)
                self.r[(i2 >> 8) & 15] = self.msp if i2 & 0xFF == 8 else self.psp
                nxt = pc + 4
            elif i & 0xF800 == 0xF000:                                 # b.w / bl
                i2 = self.half(pc + 2)
                s = (i >> 10) & 1
                j1, j2 = (i2 >> 13) & 1, (i2 >> 11) & 1
                i1, i2b = 1 - (j1 ^ s), 1 - (j2 ^ s)
                off = s << 24 | i1 << 23 | i2b << 22 | (i & 0x3FF) << 12 | (i2 & 0x7FF) << 1
                if s:
                    off -= 1 << 25
                if i2 & 0x4000:
                    self.r[14] = (pc + 4) | 1
                nxt = pc + 4 + off
            else:
                raise AssertionError("unexpected instruction %04x at %08x" % (i, pc))
            pc = nxt
        raise AssertionError("did not stop")


@unittest.skipUnless(os.path.exists(SRC), "V1.3.86K image not in refs/")
class PatchDiagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = open(SRC, "rb").read()
        cls.src = src
        cls.img, cls.lb = pd.patch(src)

    def emu(self, mem=None):
        return Thumb(self.img, dict(mem or {}))

    def boot(self, csr, rec=None):
        mem = {pd.RCU_RSTSCK: csr}
        mem.update(rec or {})
        cpu = self.emu(mem)
        stop = cpu.run(struct.unpack_from("<I", self.img, 4)[0] & ~1, {pd.STOCK_RESET & ~1})
        self.assertEqual(stop, pd.STOCK_RESET & ~1)
        return cpu

    def test_only_intended_bytes_change_in_the_old_image(self):
        changed = {i & ~3 for i in range(len(self.src)) if self.src[i] != self.img[i]}
        want = {4, 12, 16, 20, 24, pd.GET_STATUS_FAN - pd.BASE - 2, pd.GET_STATUS_FAN - pd.BASE + 2,
                pd.ASSERT_FN - pd.BASE}
        self.assertEqual(changed, want)
        self.assertLessEqual(len(self.img), 0x1C000)

    def test_boot_after_watchdog_with_hardfault_record(self):
        rec = {pd.REC: pd.MAGIC, pd.REC + 4: 0x0800C1F5, pd.REC + 8: 1}
        cpu = self.boot(csr=(1 << 29) | (1 << 26) | 1, rec=rec)
        report = cpu.mem[pd.REC + 12]
        self.assertEqual(report, 1 << 23 | 0b001001 << 24 | 1 << 20 | 0x0C1F5)
        self.assertEqual(cpu.mem[pd.REC], 0)                  # record consumed
        self.assertEqual(cpu.mem[pd.RCU_RSTSCK], (1 << 29) | (1 << 26) | 1 | (1 << 24))

    def test_boot_power_on_without_record(self):
        cpu = self.boot(csr=1 << 27, rec={pd.REC: 0x12345678, pd.REC + 4: 0x08001234})
        self.assertEqual(cpu.mem[pd.REC + 12], 1 << 23 | 0b10 << 24)

    def test_get_status_reads_report(self):
        cpu = self.emu({pd.REC + 12: 0x09812345})
        cpu.r[14] = 0x08011907
        cpu.run(pd.GET_STATUS_FAN, {0x08011906})
        self.assertEqual(cpu.r[3], 0x09812345)

    def test_fault_handler_records_stacked_pc(self):
        for vec, kind in ((12, 1), (16, 2), (20, 3), (24, 4)):
            for exc_return, use_psp in ((0xFFFFFFF9, False), (0xFFFFFFFD, True)):
                cpu = self.emu({0x20001018: 0x0800ABCD, 0x20001014: 0x08001111,
                                0x20002018: 0x0801BEEF, 0x20002014: 0x08002222})
                cpu.msp, cpu.psp = 0x20001000, 0x20002000
                cpu.r[14] = exc_return
                cpu.run(struct.unpack_from("<I", self.img, vec)[0] & ~1, set())
                self.assertEqual(cpu.mem[pd.REC], pd.MAGIC)
                self.assertEqual(cpu.mem[pd.REC + 8], kind)
                self.assertEqual(cpu.mem[pd.REC + 4], 0x0801BEEF if use_psp else 0x0800ABCD)

    def test_assert_records_caller_and_continues_stock_code(self):
        cpu = self.emu()
        cpu.r[:4] = [0x11, 0x22, 0x58E, 0x33]
        cpu.r[13] = 0x20003000
        cpu.r[14] = 0x08017001
        cpu.run(pd.ASSERT_FN, {pd.ASSERT_FN + 4})
        # stack now holds the stock prologue's push {r1, r2, r3, lr}
        self.assertEqual(cpu.r[13], 0x20003000 - 16)
        self.assertEqual([cpu.mem[0x20003000 - 16 + 4 * k] for k in range(4)],
                         [0x22, 0x58E, 0x33, 0x08017001])
        self.assertEqual(cpu.mem[pd.REC], pd.MAGIC)
        self.assertEqual(cpu.mem[pd.REC + 4], 0x08017001)
        self.assertEqual(cpu.mem[pd.REC + 8], 5)
        self.assertEqual(cpu.mem[pd.REC + 16], 0x58E)
        self.assertEqual(cpu.r[:4], [0x11, 0x22, 0x58E, 0x33])
        self.assertEqual(cpu.r[7], 0x11)

    def test_record_is_outside_app_ram(self):
        self.assertGreaterEqual(pd.REC, 0x20009C68)              # end of RW+ZI = initial MSP
        self.assertLess(pd.REC + 20, 0x2000A000)                 # inside 40 KB


if __name__ == "__main__":
    unittest.main()
