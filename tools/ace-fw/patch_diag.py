#!/usr/bin/env python3
"""ACE Pro Gen1 V1.3.86K: reset-cause diagnostics (on top of patch_cooldown.py).

Why: during prints the ACE reboots (lights off, USB hub + MCU re-enumerate).
All fault handlers of the stock image spin in `b .` until the watchdog resets
the MCU, and nothing reads the reset-cause register, so the host cannot tell a
crash from a power dip. This patch makes the ACE report it in get_status.

`get_status.fan_speed` (a constant 7000 in the stock image) becomes:

    bit 23      1 = diagnostic image (7000 has it clear)
    bits 24-29  reset flags of the last boot: RCU_RSTSCK bits 26-31
                (26 pin, 27 power-on/low-voltage, 28 software,
                 29 free watchdog, 30 window watchdog, 31 low-power)
    bits 20-22  fault before that reset: 0 none, 1 HardFault, 2 MemManage,
                3 BusFault, 4 UsageFault, 5 C assert
    bits 0-19   low 20 bits of the faulting PC (assert: of its caller);
                the address is 0x08000000 | value

Changes (all new code is appended after the image, at CODE):
  - reset vector -> stub: read RCU_RSTSCK + the fault record left in RAM,
    build the report word, clear the reset flags, jump to the stock reset
  - HardFault/MemManage/BusFault/UsageFault vectors -> stubs that save the
    stacked PC/LR and fault type in RAM, then spin as before (watchdog reset)
  - __aeabi_assert (0x08015ae0): record caller and line, then continue as stock
  - get_status (0x08011902) `movw r3, #7000` -> `bl DIAG_GET`

The record lives at REC = 0x20009C80, just above everything the app uses (RW+ZI
end at the initial MSP 0x20009C68, scatter table at 0x08021AD4), so it
survives a watchdog reset. A magic word marks a valid fault record.
"""
import hashlib
import struct
import sys

BASE = 0x08008000
SRC_SIZE = 105652
CODE = 0x08021CC0
REC = 0x20009C80
MAGIC = 0xACED1F17
RCU_RSTSCK = 0x40021024
STOCK_RESET = 0x08008211
GET_STATUS_FAN = 0x08011902   # movw r3, #0x1b58
ASSERT_FN = 0x08015AE0        # push {r1,r2,r3,lr}; mov r7, r0
VER_ADDRS = (0x080106D8, 0x080110D8, 0x08011B34, 0x0801B100)


# --- minimal Thumb encoder --------------------------------------------------

def h(v):
    return struct.pack("<H", v)


def ldr_lit(rt, lit_addr, pc):
    off = lit_addr - ((pc + 4) & ~3)
    assert 0 <= off < 1024 and off % 4 == 0, (hex(pc), off)
    return h(0x4800 | rt << 8 | off // 4)


def ldr_imm(rt, rn, imm):
    assert imm % 4 == 0 and imm < 128
    return h(0x6800 | (imm // 4) << 6 | rn << 3 | rt)


def str_imm(rt, rn, imm):
    assert imm % 4 == 0 and imm < 128
    return h(0x6000 | (imm // 4) << 6 | rn << 3 | rt)


def lsls(rd, rm, sh):
    return h(sh << 6 | rm << 3 | rd)


def lsrs(rd, rm, sh):
    return h(0x0800 | sh << 6 | rm << 3 | rd)


def orrs(rdn, rm):
    return h(0x4300 | rm << 3 | rdn)


def cmp_r(rn, rm):
    return h(0x4280 | rm << 3 | rn)


def movs(rd, imm):
    return h(0x2000 | rd << 8 | imm)


def bx(rm):
    return h(0x4700 | rm << 3)


def b_cond(cond, target, pc):
    off = (target - (pc + 4)) // 2
    assert -128 <= off < 128
    return h(0xD000 | cond << 8 | (off & 0xFF))


def b_short(target, pc):
    off = (target - (pc + 4)) // 2
    assert -1024 <= off < 1024
    return h(0xE000 | (off & 0x7FF))


def b32(target, pc, link):
    off = target - (pc + 4)
    assert -(1 << 24) <= off < (1 << 24) and off % 2 == 0
    s = (off >> 24) & 1
    i1 = (off >> 23) & 1
    i2 = (off >> 22) & 1
    imm10 = (off >> 12) & 0x3FF
    imm11 = (off >> 1) & 0x7FF
    j1 = (1 - i1) ^ s
    j2 = (1 - i2) ^ s
    hw1 = 0xF000 | s << 10 | imm10
    hw2 = (0xD000 if link else 0x9000) | j1 << 13 | j2 << 11 | imm11
    return h(hw1) + h(hw2)


def mrs(rd, sysm):
    return h(0xF3EF) + h(0x8000 | rd << 8 | sysm)


MOV_R1_LR = h(0x4671)
PUSH_R0_R3 = h(0xB40F)
POP_R0_R3 = h(0xBC0F)
PUSH_R1_R3_LR = h(0xB50E)   # displaced from ASSERT_FN
MOV_R7_R0 = h(0x4607)       # displaced from ASSERT_FN
HANG = h(0xE7FE)            # b .
NE, MI = 1, 4


def assemble(prog, start):
    """prog: list of (label|None, fn(pc, labels)->bytes, size). Two passes."""
    labels, pc = {}, start
    for label, _, size in prog:
        if label:
            labels[label] = pc
        pc += size
    out, pc = b"", start
    for _, fn, size in prog:
        code = fn(pc, labels)
        assert len(code) == size, (hex(pc), len(code), size)
        out += code
        pc += size
    return out, labels


def program():
    P = []

    def op(code_fn, size, label=None):
        P.append((label, code_fn, size))

    # reset stub
    op(lambda pc, lb: ldr_lit(0, lb["lit_rcu"], pc), 2, "reset")
    op(lambda pc, lb: ldr_imm(1, 0, 0), 2)
    op(lambda pc, lb: lsrs(1, 1, 26), 2)
    op(lambda pc, lb: lsls(1, 1, 24), 2)
    op(lambda pc, lb: ldr_lit(2, lb["lit_rec"], pc), 2)
    op(lambda pc, lb: ldr_imm(3, 2, 0), 2)
    op(lambda pc, lb: ldr_lit(0, lb["lit_magic"], pc), 2)
    op(lambda pc, lb: cmp_r(3, 0), 2)
    op(lambda pc, lb: b_cond(NE, lb["nofault"], pc), 2)
    op(lambda pc, lb: ldr_imm(3, 2, 4), 2)
    op(lambda pc, lb: lsls(3, 3, 12), 2)
    op(lambda pc, lb: lsrs(3, 3, 12), 2)
    op(lambda pc, lb: orrs(1, 3), 2)
    op(lambda pc, lb: ldr_imm(3, 2, 8), 2)
    op(lambda pc, lb: lsls(3, 3, 29), 2)
    op(lambda pc, lb: lsrs(3, 3, 9), 2)
    op(lambda pc, lb: orrs(1, 3), 2)
    op(lambda pc, lb: movs(3, 0), 2)
    op(lambda pc, lb: str_imm(3, 2, 0), 2)
    op(lambda pc, lb: movs(3, 1), 2, "nofault")
    op(lambda pc, lb: lsls(3, 3, 23), 2)
    op(lambda pc, lb: orrs(1, 3), 2)
    op(lambda pc, lb: str_imm(1, 2, 12), 2)
    op(lambda pc, lb: ldr_lit(0, lb["lit_rcu"], pc), 2)
    op(lambda pc, lb: ldr_imm(3, 0, 0), 2)
    op(lambda pc, lb: movs(1, 1), 2)
    op(lambda pc, lb: lsls(1, 1, 24), 2)
    op(lambda pc, lb: orrs(3, 1), 2)
    op(lambda pc, lb: str_imm(3, 0, 0), 2)
    op(lambda pc, lb: ldr_lit(0, lb["lit_stock_reset"], pc), 2)
    op(lambda pc, lb: bx(0), 2)

    # get_status fan_speed value (r3); r0-r2 are reloaded by the caller
    op(lambda pc, lb: ldr_lit(3, lb["lit_rec"], pc), 2, "diag_get")
    op(lambda pc, lb: ldr_imm(3, 3, 12), 2)
    op(lambda pc, lb: bx(14), 2)

    # fault vectors: r2 = type
    for name, kind in (("hard", 1), ("mem", 2), ("bus", 3), ("usage", 4)):
        op(lambda pc, lb, k=kind: movs(2, k), 2, name)
        op(lambda pc, lb: b_short(lb["fault"], pc), 2)
    op(lambda pc, lb: MOV_R1_LR, 2, "fault")
    op(lambda pc, lb: lsls(1, 1, 29), 2)          # EXC_RETURN bit 2 -> N
    op(lambda pc, lb: b_cond(MI, lb["use_psp"], pc), 2)
    op(lambda pc, lb: mrs(0, 8), 4)                # MSP
    op(lambda pc, lb: b_short(lb["got_sp"], pc), 2)
    op(lambda pc, lb: mrs(0, 9), 4, "use_psp")    # PSP
    op(lambda pc, lb: ldr_imm(1, 0, 24), 2, "got_sp")  # stacked PC
    op(lambda pc, lb: ldr_lit(3, lb["lit_rec"], pc), 2)
    op(lambda pc, lb: str_imm(1, 3, 4), 2)
    op(lambda pc, lb: str_imm(2, 3, 8), 2)
    op(lambda pc, lb: ldr_imm(1, 0, 20), 2)        # stacked LR
    op(lambda pc, lb: str_imm(1, 3, 16), 2)
    op(lambda pc, lb: ldr_lit(1, lb["lit_magic"], pc), 2)
    op(lambda pc, lb: str_imm(1, 3, 0), 2)
    op(lambda pc, lb: HANG, 2)                      # watchdog resets, as stock

    # __aeabi_assert hook: record caller (lr) + line (r2), then run the stock code
    op(lambda pc, lb: PUSH_R0_R3, 2, "assert")
    op(lambda pc, lb: ldr_lit(0, lb["lit_rec"], pc), 2)
    op(lambda pc, lb: MOV_R1_LR, 2)
    op(lambda pc, lb: str_imm(1, 0, 4), 2)
    op(lambda pc, lb: movs(1, 5), 2)
    op(lambda pc, lb: str_imm(1, 0, 8), 2)
    op(lambda pc, lb: str_imm(2, 0, 16), 2)
    op(lambda pc, lb: ldr_lit(1, lb["lit_magic"], pc), 2)
    op(lambda pc, lb: str_imm(1, 0, 0), 2)
    op(lambda pc, lb: POP_R0_R3, 2)
    op(lambda pc, lb: PUSH_R1_R3_LR, 2)
    op(lambda pc, lb: MOV_R7_R0, 2)
    op(lambda pc, lb: b32(ASSERT_FN + 4, pc, link=False), 4)
    return P


def build_program():
    P = program()
    # literal pool, word aligned (nop padding)
    pad = (CODE + sum(s for _, _, s in P)) % 4
    P.append(("align", lambda pc, lb: h(0xBF00) * (pad // 2), pad))
    for name, value in (("lit_rcu", RCU_RSTSCK), ("lit_rec", REC), ("lit_magic", MAGIC),
                        ("lit_stock_reset", STOCK_RESET)):
        P.append((name, lambda pc, lb, v=value: struct.pack("<I", v), 4))
    return assemble(P, CODE)


def patch(src):
    img = bytearray(src)
    if len(img) != SRC_SIZE:
        raise SystemExit("unexpected size %d (want %d, V1.3.86K)" % (len(img), SRC_SIZE))
    for a in VER_ADDRS:
        if img[a - BASE:a - BASE + 8] != b"V1.3.86K":
            raise SystemExit("not a V1.3.86K image (version string at 0x%08x)" % a)
    vec = struct.unpack_from("<7I", img, 0)
    expect = (0x20009C68, 0x08008211, 0x0800A631, 0x08009E0B, 0x0800A62D, 0x08009C65, 0x0800BF3D)
    if vec != expect:
        raise SystemExit("vector table differs from V1.3.86K")
    if img[GET_STATUS_FAN - BASE:GET_STATUS_FAN - BASE + 4] != bytes.fromhex("41f65833"):
        raise SystemExit("movw r3, #7000 not found at 0x%08x" % GET_STATUS_FAN)
    if img[ASSERT_FN - BASE:ASSERT_FN - BASE + 4] != bytes.fromhex("0eb50746"):
        raise SystemExit("assert prologue not found at 0x%08x" % ASSERT_FN)

    code, lb = build_program()
    img += b"\x00" * (CODE - BASE - len(img))
    img += code
    struct.pack_into("<I", img, 4, lb["reset"] | 1)
    struct.pack_into("<I", img, 12, lb["hard"] | 1)
    struct.pack_into("<I", img, 16, lb["mem"] | 1)
    struct.pack_into("<I", img, 20, lb["bus"] | 1)
    struct.pack_into("<I", img, 24, lb["usage"] | 1)
    img[GET_STATUS_FAN - BASE:GET_STATUS_FAN - BASE + 4] = b32(lb["diag_get"], GET_STATUS_FAN, link=True)
    img[ASSERT_FN - BASE:ASSERT_FN - BASE + 4] = b32(lb["assert"], ASSERT_FN, link=False)
    if len(img) > 0x1C000:
        raise SystemExit("image too large for IAP")
    return bytes(img), lb



def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: patch_diag.py ACE_V1.3.86K_cooldown60.bin OUT.bin")
    src = open(sys.argv[1], "rb").read()
    out, lb = patch(src)
    open(sys.argv[2], "wb").write(out)
    print("in  sha256 %s size %d" % (hashlib.sha256(src).hexdigest(), len(src)))
    print("out sha256 %s size %d" % (hashlib.sha256(out).hexdigest(), len(out)))
    for k in ("reset", "diag_get", "hard", "mem", "bus", "usage", "fault", "assert"):
        print("%-9s 0x%08x" % (k, lb[k]))


if __name__ == "__main__":
    main()
