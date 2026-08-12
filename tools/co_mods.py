"""Read per-CO, per-unit attack modifiers out of the ROM.

Address arithmetic taken straight off the disassembly at 0x08060BD4:

    r4   = co * 128 + unit_type * 4
    r1   = r4 + 292                      (0x92 << 1, the no-power-active path)
    addr = 0x08284A0C + 0x40 + r1
    ptr  = [addr]                        (a pointer, not the value)
    mod  = ptr[5]                        (the modifier byte)

so  entry = 0x08284B70 + co*128 + type*4.

The point of this: the calibration protocol assumes a neutral CO. If that CO's
modifiers are not all 100, every observation is silently scaled and the whole
calibration is garbage. So verify rather than assume.
"""
import pathlib
import struct
import sys

ROM_BASE = 0x08000000
TABLE = 0x284B70

UNIT_IDS = {
    0: "Infantry", 1: "Mech", 2: "MdTank", 4: "Tank", 5: "Recon", 6: "APC",
    9: "Artillery", 10: "Rockets", 13: "AntiAir", 14: "Missiles",
    15: "Fighter", 16: "Bomber", 18: "BCopter", 19: "TCopter",
    20: "Battleship", 21: "Cruiser", 22: "Lander", 23: "Sub",
}


def mod_for(rom, co, unit_type):
    addr = TABLE + co * 128 + unit_type * 4
    if addr + 4 > len(rom):
        return None
    ptr = struct.unpack_from("<I", rom, addr)[0]
    off = ptr - ROM_BASE
    if not (0 <= off + 5 < len(rom)):
        return None
    return rom[off + 5]


def main(path, ncos=12):
    rom = pathlib.Path(path).read_bytes()
    order = sorted(UNIT_IDS)
    print(f"{'co':>3s} " + "".join(f"{UNIT_IDS[t][:4]:>5s}" for t in order) + "   verdict")
    for co in range(ncos):
        mods = [mod_for(rom, co, t) for t in order]
        if any(m is None for m in mods):
            print(f"{co:>3d}  (out of range)")
            continue
        if all(m == 100 for m in mods):
            verdict = "NEUTRAL - safe for calibration"
        elif all(m == 0 for m in mods) or all(m > 200 for m in mods):
            verdict = "(not a CO slot?)"
        else:
            hi = [UNIT_IDS[t] for t, m in zip(order, mods) if m > 100]
            lo = [UNIT_IDS[t] for t, m in zip(order, mods) if m < 100]
            verdict = "biased"
            if hi:
                verdict += f" up:{','.join(hi[:4])}"
            if lo:
                verdict += f" down:{','.join(lo[:4])}"
        print(f"{co:>3d} " + "".join(f"{m:>5d}" for m in mods) + f"   {verdict}")


if __name__ == "__main__":
    main(sys.argv[1])
