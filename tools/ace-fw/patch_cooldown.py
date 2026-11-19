#!/usr/bin/env python3
"""ACE Pro Gen1 firmware V1.3.863: post-drying fan cooldown 180 s -> 60 s.

FUN_0800ca5c, state 7 (heat dissipation): counter at +0x76 is compared with
`cmp.w r0, #0x708` and incremented every 100 ms until it exceeds the limit.
We change the immediate to 0x258 (600 -> ~60 s) and mark the version string
as V1.3.86K so the printer side can tell the patched image apart.
"""
import hashlib
import sys

BASE = 0x08008000
CMP_ADDR = 0x0800CAA8          # b0 f5 e1 6f  ->  cmp.w r0, #0x708
CMP_OLD = bytes.fromhex("b0f5e16f")
CMP_NEW = bytes.fromhex("b0f5167f")  # cmp.w r0, #0x258
VER_OLD = b"V1.3.863"
VER_NEW = b"V1.3.86K"
VER_ADDRS = (0x080106D8, 0x080110D8, 0x08011B34, 0x0801B100)


def crc16_mcrf4xx(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def patch(src):
    img = bytearray(src)
    if len(img) != 105652:
        raise SystemExit("unexpected size %d (want 105652, V1.3.863)" % len(img))
    off = CMP_ADDR - BASE
    if img[off:off + 4] != CMP_OLD:
        raise SystemExit("cmp instruction not found at 0x%08x" % CMP_ADDR)
    img[off:off + 4] = CMP_NEW
    for a in VER_ADDRS:
        o = a - BASE
        if img[o:o + 8] != VER_OLD:
            raise SystemExit("version string not found at 0x%08x" % a)
        img[o:o + 8] = VER_NEW
    return bytes(img)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: patch_cooldown.py ACE_V1.3.863.bin OUT.bin")
    src = open(sys.argv[1], "rb").read()
    out = patch(src)
    diff = [i for i in range(len(src)) if src[i] != out[i]]
    open(sys.argv[2], "wb").write(out)
    print("in  sha256 %s crc16 0x%04x" % (hashlib.sha256(src).hexdigest(), crc16_mcrf4xx(src)))
    print("out sha256 %s crc16 0x%04x" % (hashlib.sha256(out).hexdigest(), crc16_mcrf4xx(out)))
    print("changed bytes: %d at %s" % (len(diff), ", ".join("0x%08x" % (BASE + i) for i in diff)))


if __name__ == "__main__":
    main()
