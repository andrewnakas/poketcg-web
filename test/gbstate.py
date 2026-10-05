#!/usr/bin/env python3
"""Pull work RAM out of a libretro save state from the gambatte core.

RetroArch wraps the core's own serialisation in a RASTATE container:

    "RASTATE" <version:1>  then  <4-char block id> <size:u32le> <payload>

The "MEM " block's payload is gambatte's own state, which is a flat run of

    <label> NUL <size:u24be> <size bytes of data>

after a short fixed preamble. Labels include `wram`, `sram`, `vram` and `hram`,
so the arrays are *found*, never assumed to be at a fixed offset -- block sizes
ahead of them vary with the cartridge.

The parse is self-checking in two ways:

  * the record list must consume the MEM block exactly, with no slop; and
  * `verify_against_sram()` compares the state's `sram` record with the save file
    the core wrote independently. They are produced by different code paths, so
    an agreement across 32 KiB is strong evidence the container is being read
    correctly.

WRAM addressing: $C000-$CFFF is always bank 0 on a Game Boy, and gambatte stores
the banks contiguously from bank 0, so a $Cxxx address is simply `addr - 0xC000`
into the array. Every symbol this project reads lives in that range; `read()`
refuses anything else rather than silently returning the wrong bank.
"""
import pathlib
import struct
import sys

WRAM_BASE = 0xC000
WRAM_BANK0_END = 0xD000
WRAM_TOP = 0xE000


class StateParseError(Exception):
    pass


def rastate_blocks(buf: bytes):
    if buf[:7] != b"RASTATE":
        raise StateParseError("not a RASTATE container: %r" % buf[:8])
    off, out = 8, {}
    while off + 8 <= len(buf):
        name = buf[off:off + 4]
        (size,) = struct.unpack_from("<I", buf, off + 4)
        payload = buf[off + 8:off + 8 + size]
        out.setdefault(name.decode("latin1"), payload)
        if size == 0 or off + 8 + size > len(buf):
            break
        off += 8 + size
    return out


def gambatte_records(mem: bytes):
    """[(label, data)] for the whole MEM payload, or raise."""
    # A short preamble precedes the first record; find it by trying each start
    # and keeping the one whose record list consumes the block exactly. This is
    # cheap and removes the one hardcoded number that would otherwise be here.
    for base in range(0, 16):
        recs, o, ok = [], base, True
        while o < len(mem):
            n = mem.find(b"\x00", o)
            if n < 0 or not (0 < n - o <= 12):
                ok = False
                break
            label = mem[o:n]
            if not label.isascii() or not all(32 <= c < 127 for c in label):
                ok = False
                break
            size = int.from_bytes(mem[n + 1:n + 4], "big")
            data_off = n + 4
            if data_off + size > len(mem):
                ok = False
                break
            recs.append((label.decode("ascii"), mem[data_off:data_off + size]))
            o = data_off + size
        if ok and o == len(mem) and recs:
            labels = [r[0] for r in recs]
            if len(labels) != len(set(labels)):
                continue
            if "wram" in labels and "sram" in labels:
                return recs
    raise StateParseError("could not parse the gambatte MEM block")


class GbState:
    def __init__(self, raw: bytes):
        self.raw = raw
        blocks = rastate_blocks(raw)
        if "MEM " not in blocks:
            raise StateParseError("no MEM block; blocks=%r" % list(blocks))
        self.records = dict(gambatte_records(blocks["MEM "]))
        self.wram = self.records["wram"]
        self.sram = self.records["sram"]
        if len(self.wram) < 0x1000:
            raise StateParseError("wram record is only %d bytes" % len(self.wram))

    # --- access ----------------------------------------------------------
    def read(self, addr: int, n: int = 1) -> bytes:
        """Read `n` bytes of WRAM at a Game Boy address.

        $C000-$CFFF is bank 0 on every Game Boy. $D000-$DFFF is banked on CGB,
        but poketcg never writes rSVBK (grep the tree: the symbol appears only in
        hardware.inc), so bank 1 is mapped there for the whole run. Anything
        outside WRAM is refused rather than silently mapped to the wrong place.
        """
        if WRAM_BASE <= addr < WRAM_BANK0_END:
            o = addr - WRAM_BASE
        elif WRAM_BANK0_END <= addr < WRAM_TOP:
            o = 0x1000 + (addr - WRAM_BANK0_END)      # bank 1
        else:
            raise ValueError("$%04x is not in WRAM ($C000-$DFFF)" % addr)
        return self.wram[o:o + n]

    def byte(self, addr: int) -> int:
        return self.read(addr, 1)[0]

    def sym(self, symbols, name, n=1):
        """Read a symbol by name, from the .sym of the build under test."""
        if name not in symbols:
            raise KeyError(name)
        return self.read(symbols[name][1], n)

    def symbyte(self, symbols, name) -> int:
        return self.sym(symbols, name, 1)[0]

    def verify_against_sram(self, srm: bytes) -> bool:
        """The core's own .srm must equal the state's `sram` record."""
        return bool(srm) and self.sram[:len(srm)] == srm


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    st = GbState(pathlib.Path(argv[1]).read_bytes())
    print("records: %d" % len(st.records))
    for k in ("wram", "sram", "vram", "hram", "rombank", "rambank"):
        if k in st.records:
            print("  %-8s %6d bytes" % (k, len(st.records[k])))
    if len(argv) > 2:
        srm = pathlib.Path(argv[2]).read_bytes()
        print("state sram == %s : %s" % (argv[2], st.verify_against_sram(srm)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
