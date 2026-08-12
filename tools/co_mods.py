"""Enumerate COs and their per-unit attack/defence modifiers, from the ROM.

Address arithmetic taken off the disassembly at 0x08060BD4:

    r4   = co * 128 + unit_type * 4         (unit_type is 1-BASED here)
    addr = 0x08284A0C + 0x40 + 292 + r4  =  0x08284B70 + co*128 + type*4
    ptr  = [addr]                            (a pointer, not the value)
    atk  = ptr[5],  def = ptr[6]

so each CO record is 128 bytes, and the per-unit modifier slots live inside it.
The record also carries a name pointer, which is what turns an index into
something you can actually pick on the CO select screen.

Why this matters: calibration assumes the CO applies no modifier. "Neutral CO"
is not a thing you can select -- you pick a name. This tells you which name.
"""
import pathlib
import struct
import sys

ROM_BASE = 0x08000000
REC0 = 0x284B60          # first CO record
STRIDE = 0x80
MOD_BASE = 0x284B70      # REC0 + 0x10, indexed by 1-based type * 4
NAME_OFF = 0x74

UNIT_IDS = {
    0: "Infantry", 1: "Mech", 2: "MdTank", 4: "Tank", 5: "Recon", 6: "APC",
    9: "Artillery", 10: "Rockets", 13: "AntiAir", 14: "Missiles",
    15: "Fighter", 16: "Bomber", 18: "BCopter", 19: "TCopter",
    20: "Battleship", 21: "Cruiser", 22: "Lander", 23: "Sub",
}


def cstr(rom, off, limit=20):
    out = bytearray()
    for i in range(off, min(off + limit, len(rom))):
        if rom[i] == 0:
            break
        if not (0x20 <= rom[i] < 0x7F):
            return None
        out.append(rom[i])
    return out.decode("ascii") if len(out) >= 2 else None


def word(rom, off):
    return struct.unpack_from("<I", rom, off)[0] if off + 4 <= len(rom) else 0


def co_name(rom, co):
    v = word(rom, REC0 + co * STRIDE + NAME_OFF)
    off = v - ROM_BASE
    return cstr(rom, off) if 0 <= off < len(rom) else None


def modifiers(rom, co):
    """{unit: (attack, defence)} or None if the slots are not valid pointers."""
    out = {}
    for uid, name in UNIT_IDS.items():
        # +1 because the game indexes with the 1-based in-RAM type id
        ptr = word(rom, MOD_BASE + co * STRIDE + (uid + 1) * 4)
        off = ptr - ROM_BASE
        if not (0 <= off + 6 < len(rom)):
            return None
        out[name] = (rom[off + 5], rom[off + 6])
    return out


def main(path, ncos=16):
    rom = pathlib.Path(path).read_bytes()
    print(f"{'idx':>3s} {'name':10s} {'verdict'}")
    neutral = []
    for co in range(ncos):
        name = co_name(rom, co)
        mods = modifiers(rom, co)
        if mods is None:
            print(f"{co:>3d} {str(name or '?'):10s} (slots not valid pointers - "
                  "past the end of the table)")
            continue
        vals = set(mods.values())
        if vals == {(100, 100)}:
            verdict = "NEUTRAL - all 18 units at 100/100. Safe for calibration."
            neutral.append((co, name))
        else:
            odd = {u: v for u, v in mods.items() if v != (100, 100)}
            shown = ", ".join(f"{u} {a}/{d}" for u, (a, d) in list(odd.items())[:4])
            verdict = f"biased on {len(odd)} unit(s): {shown}"
        print(f"{co:>3d} {str(name or '(unnamed)'):10s} {verdict}")

    # Sanity-check the model before anyone acts on the output. AW1 has 12 COs;
    # if most slots do not resolve, the stride/base guess is wrong and every
    # verdict above is noise.
    resolved = sum(1 for co in range(ncos) if modifiers(rom, co) is not None)
    named = sum(1 for co in range(ncos) if co_name(rom, co))
    print()
    print(f"resolved {resolved}/{ncos} records, {named} with readable names")
    if resolved < 10 or named < 8:
        print()
        print("*** DO NOT TRUST THE VERDICTS ABOVE. ***")
        print("The CO table is not a uniform 128-byte ROM array the way this")
        print("script assumes: most slots do not resolve, and the ones that do")
        print("disagree with known behaviour (it calls Max neutral, and Max is")
        print("the direct-combat CO). Either the per-CO modifiers are patched")
        print("into RAM at battle start, or the record layout differs from the")
        print("co*128 arithmetic the damage path uses.")
        print()
        print("Until this is resolved, determine CO neutrality EMPIRICALLY:")
        print("  Tank vs Infantry, both full HP, defender on a road.")
        print("  Base damage is 75 (ROM-verified), so with no CO modifier and")
        print("  0-star terrain the defender should land on 16-25 internal HP.")
        print("  Outside that band => a CO modifier, terrain stars, or both.")
        sys.exit(2)
    if neutral:
        names = [n for _, n in neutral if n]
        print("Candidate neutral CO(s):", ", ".join(names) or "(unnamed)")
        print("Verify empirically before trusting: see the Tank/Infantry check above.")
    else:
        print("No fully neutral CO found -- calibration must model the modifier.")


if __name__ == "__main__":
    main(sys.argv[1])
