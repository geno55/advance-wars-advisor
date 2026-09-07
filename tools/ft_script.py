"""Read a mission's tutorial script -- the Field Training lessons (DERIVATION 62).

A mission record (0x08287478 + 60 * map, tools/extract_ai.py) carries two
script pointers at +8 (the lesson) and +0xC (the ending texts). A script
is 16-byte records [op, ptr, a, b] run by the interpreter at 0x0801844C
(threads at 0x03001D50, one opcode handler per entry of 0x08281BA0). The
ops that matter for driving a lesson:

    0x19 ptr        show a text (pages split by 0x0F, lines by 0x0D)
    0x27 table      register event handlers: pairs (event byte, handler
                    script) ending in 0xFF, written to 0x03004280
    0x28 0 yx k     put the cursor on tile (x = lo16 / 16, y = hi16 / 16);
                    k = 7 on the map, 8/9/0xB with a unit selected
    0x1E to 0 fn    branch to `to` when the native predicate holds
    0x0D to 0 n     branch on flag n
    0x05            stop (the thread waits for an event)
    0x04            end

The event codes are what the player does: 0x2C a unit selected, 0x19 Fire,
0x21 Wait, 0x18 Capt, 0x1F Join, 0x0F an attack resolved, 0x11 the map menu,
0x15 Options, 0x29 End (measured with harness hooks on 0x08018BA8).

    python tools/ft_script.py 117            # map 117 = Field Training 2
    python tools/ft_script.py 117 --all      # every record, not just texts and tables
"""
import argparse
import pathlib
import struct

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROM = ROOT.parent / "Advance Wars (USA) (Rev 1).gba"
BASE = 0x08000000
RECORDS = 0x08287478
RECORD_SIZE = 60
FT_TABLE = 0x08287210            # the fourteen Field Training scripts, in order

EVENT_NAMES = {0x0F: "attack", 0x11: "map-menu", 0x15: "Options", 0x18: "Capt", 0x19: "Fire",
               0x1C: "Load", 0x1D: "Drop", 0x1F: "Join", 0x20: "Supply", 0x21: "Wait", 0x22: "map-item", 0x25: "map-item", 0x27: "map-item",
               0x29: "End", 0x2A: "map-item", 0x2C: "select"}


class Script:
    def __init__(self, rom: bytes):
        self.rom = rom

    def u32(self, a):
        return struct.unpack_from("<I", self.rom, a - BASE)[0]

    def text(self, a, limit=None):
        out = []
        i = a - BASE
        while self.rom[i] != 0:
            b = self.rom[i]
            if 0x20 <= b < 0x7F:
                out.append(chr(b))
            elif b == 0x0D:
                out.append(" / ")
            elif b == 0x0F:
                out.append(" || ")
            elif b in (0x82, 0x83):
                out.append("[")
            elif b == 0x80:
                out.append("]")
            else:
                out.append("<%02x>" % b)
            i += 1
        s = "".join(out)
        return s if limit is None or len(s) <= limit else s[:limit] + "..."

    def table(self, t):
        pairs = []
        while True:
            ev = self.rom[t - BASE]
            if ev == 0xFF:
                break
            pairs.append((ev, self.u32(t + 4)))
            t += 8
        return pairs

    def blocks(self, entries):
        """Walk from the entries and every handler / branch target found;
        yields (start, [(addr, op, ptr, a, b), ...]) per block."""
        seen = set()
        queue = list(entries)
        while queue:
            start = queue.pop(0)
            if start in seen or not BASE <= start < BASE + len(self.rom):
                continue
            recs = []
            a = start
            for _ in range(512):
                if a in seen:
                    break
                seen.add(a)
                op, p, x, y = (self.u32(a), self.u32(a + 4), self.u32(a + 8), self.u32(a + 12))
                recs.append((a, op, p, x, y))
                if op == 0x27:
                    queue.extend(h for _, h in self.table(p))
                elif op in (0x0C, 0x0D, 0x0E, 0x1E) and BASE <= p < BASE + len(self.rom):
                    queue.append(p)
                a += 16
                if op in (4, 5) or op > 0x3F:
                    break
            yield start, recs


def ev_name(ev):
    return f"{ev:02X}" + (f"={EVENT_NAMES[ev]}" if ev in EVENT_NAMES else "")


def describe(sc: Script, rec, full: bool):
    a, op, p, x, y = rec
    if op == 0x19:
        return f"{a:08X}  text     {sc.text(p, 160)}"
    if op == 0x27:
        pairs = ", ".join(f"{ev_name(ev)} -> {h:08X}" for ev, h in sc.table(p))
        return f"{a:08X}  events   {pairs}"
    if op == 0x28:
        return f"{a:08X}  cursor   ({(x & 0xFFFF) // 16},{(x >> 16) // 16}) mode {y}"
    if op == 0x1E:
        return f"{a:08X}  branch   -> {p:08X} when {y:08X}() holds"
    if op == 0x0D:
        return f"{a:08X}  branch   -> {p:08X} on flag {y}"
    if not full:
        return None
    return f"{a:08X}  op {op:02X}    {p:08X} {x:08X} {y:08X}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("map", type=int, help="map id (settings +2): 116..129 are Field Training 1..14")
    ap.add_argument("--all", action="store_true", help="print every record")
    ap.add_argument("--rom", default=str(ROM))
    a = ap.parse_args()
    sc = Script(pathlib.Path(a.rom).read_bytes())
    rec = RECORDS + RECORD_SIZE * a.map
    lesson, ending = sc.u32(rec + 8), sc.u32(rec + 0xC)
    print(f"map {a.map}: lesson script {lesson:08X}, ending script {ending:08X}, "
          f"par {struct.unpack_from('<H', sc.rom, rec - BASE + 0x20)[0]}")
    for start, recs in sc.blocks([lesson, ending]):
        lines = [d for d in (describe(sc, r, a.all) for r in recs) if d]
        if lines:
            print(f"-- block {start:08X}")
            print("\n".join(lines))


if __name__ == "__main__":
    main()
