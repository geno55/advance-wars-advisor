"""Dump the GBA cartridge header so we can pin exactly which build we target."""
import sys, hashlib, pathlib

rom = pathlib.Path(sys.argv[1]).read_bytes()

print(f"size      : {len(rom)} bytes ({len(rom)/1024/1024:.2f} MiB)")
print(f"sha1      : {hashlib.sha1(rom).hexdigest()}")
print(f"md5       : {hashlib.md5(rom).hexdigest()}")
print(f"title     : {rom[0xA0:0xAC].decode('ascii', 'replace').rstrip(chr(0))!r}")
print(f"game code : {rom[0xAC:0xB0].decode('ascii', 'replace')!r}")
print(f"maker     : {rom[0xB0:0xB2].decode('ascii', 'replace')!r}")
print(f"unit code : 0x{rom[0xB3]:02X}")
print(f"version   : {rom[0xBC]}")

# Header checksum: sum of 0xA0..0xBC, per GBA spec.
chk = (-(0x19 + sum(rom[0xA0:0xBD])) ) & 0xFF
print(f"hdr cksum : stored=0x{rom[0xBD]:02X} computed=0x{chk:02X} "
      f"{'OK' if chk == rom[0xBD] else 'MISMATCH'}")
