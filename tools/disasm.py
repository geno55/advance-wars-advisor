"""THUMB disassembler with literal-pool annotation, for reading AW1's game code.

Annotating `ldr rN, [pc, #imm]` with the resolved constant is the whole game --
without it the listing is unreadable, with it the table lookups and magic
numbers jump straight out.

    python disasm.py rom.gba 0x060A80 0x060E00
"""
import pathlib
import struct
import sys

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

ROM_BASE = 0x08000000

# Anything we have already identified gets named in the listing.
KNOWN = {
    0x08283B48: "DAMAGE_PRIMARY",
    0x08283D88: "DAMAGE_SECONDARY",
    0x08282CC8: "UNIT_NAMES",
}


def annotate(rom, insn):
    """If this is a PC-relative load, resolve what it actually loads."""
    if not insn.mnemonic.startswith("ldr"):
        return ""
    if "[pc," not in insn.op_str.replace(" ", ""):
        return ""
    try:
        imm = int(insn.op_str.split("#")[-1].rstrip("]"), 0)
    except ValueError:
        return ""
    target = ((insn.address + 4) & ~3) + imm
    off = target - ROM_BASE
    if not (0 <= off < len(rom) - 4):
        return ""
    val = struct.unpack("<I", rom[off:off + 4])[0]
    name = KNOWN.get(val)
    tag = f" = 0x{val:08X}"
    if name:
        tag += f"  <{name}>"
    elif 0x08000000 <= val < 0x08400000:
        tag += "  (rom ptr)"
    elif 0x02000000 <= val < 0x02040000:
        tag += "  (ewram)"
    elif 0x03000000 <= val < 0x03008000:
        tag += "  (iwram)"
    elif val < 0x10000:
        tag += f"  ({val} dec)"
    return tag


def main(path, lo, hi):
    rom = pathlib.Path(path).read_bytes()
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    md.detail = False
    code = rom[lo:hi]
    for insn in md.disasm(code, ROM_BASE + lo):
        raw = " ".join(f"{b:02x}" for b in insn.bytes)
        print(f"{insn.address:08X}  {raw:<12s} {insn.mnemonic:<8s} {insn.op_str}"
              f"{annotate(rom, insn)}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2], 0), int(sys.argv[3], 0))
