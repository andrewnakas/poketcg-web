#!/usr/bin/env python3
"""Decode Pokemon TCG's battery save, using the symbol table and constants of the
build under test.

Nothing in here is transcribed from a wiki or a memory map found online. Every
address, size, bank and magic number is read at run time out of:

    upstream/poketcg.sym                     every address and its SRAM bank
    upstream/src/constants/duel_constants.asm SAVE_DUEL_* layout, the checksum
                                              seed, DUELTYPE_*
    upstream/src/constants/misc_constants.asm RNGVARS_SIZE
    upstream/src/engine/duel/core.asm         the DuelDataToSave table, i.e. which
                                              WRAM ranges the game copies and in
                                              what order
    upstream/src/constants/charmaps.asm       the text encoding

Why this is the gate for "played a real duel":

`PrintDuelMenuAndHandleInput` (engine/duel/core.asm) calls `SaveDuelData` every
time it draws the player's duel menu. `SaveDuelData` copies the live duel state
out of WRAM into `sCurrentDuel` in SRAM and writes a two-byte checksum over it
that the game itself verifies on load (`ValidateSavedDuelDataFromHL`). So a
battery save containing a sCurrentDuel record whose checksum recomputes is proof
that the duel engine ran, that it reached the point where the player is being
asked to choose a move, and that the cartridge's own validator would accept it.

A title-screen attract sequence cannot produce one: there is no duel menu, so
SaveDuelData is never called, and the record stays invalid.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UP = ROOT / "upstream"

SRAM_BASE = 0xA000
SRAM_BANK_SIZE = 0x2000


# --------------------------------------------------------------------- parsing

def load_symbols(sym_path=None):
    """{name: (bank, addr)} from the .sym rgblink just emitted."""
    sym_path = pathlib.Path(sym_path or (UP / "poketcg.sym"))
    out = {}
    pat = re.compile(r"^([0-9A-Fa-f]{2,}):([0-9A-Fa-f]{4})\s+(\S+)\s*$")
    for line in sym_path.read_text(errors="replace").splitlines():
        m = pat.match(line)
        if m:
            # several labels can share an address; keep them all
            out[m.group(3)] = (int(m.group(1), 16), int(m.group(2), 16))
    if not out:
        raise RuntimeError("no symbols in %s" % sym_path)
    return out


def load_constants(*rel_paths):
    """`DEF NAME EQU value` plus the `RSRESET` / `RB` / `RW` runs.

    rgbasm's RS counter is just a running offset, so following it literally
    reproduces the numbers the assembler itself produced -- which is how
    SAVE_DUEL_DATA_SIZE and RNGVARS_SIZE are defined.
    """
    out = {}
    pat = re.compile(r"^\s*DEF\s+(\w+)\s+EQU\s+(.+?)\s*(?:;.*)?$")
    rs_pat = re.compile(r"^\s*DEF\s+(\w+)\s+(RB|RW|RL)\s*(\S*)\s*(?:;.*)?$")
    for rel in rel_paths:
        rs = 0
        for raw in (UP / rel).read_text(errors="replace").splitlines():
            line = raw.split(";")[0].rstrip()
            if line.strip().upper() == "RSRESET":
                rs = 0
                continue
            m = rs_pat.match(line)
            if m:
                unit = {"RB": 1, "RW": 2, "RL": 4}[m.group(2)]
                n = _eval(m.group(3), out) if m.group(3) else 1
                out[m.group(1)] = rs
                rs += unit * (n if n is not None else 1)
                continue
            m = pat.match(line)
            if not m:
                continue
            name, expr = m.group(1), m.group(2).strip()
            if expr == "_RS":
                out[name] = rs
                continue
            m2 = re.fullmatch(r"_RS\s*-\s*(\w+)", expr)
            if m2 and m2.group(1) in out:
                out[name] = rs - out[m2.group(1)]
                continue
            v = _eval(expr, out)
            if v is not None:
                out[name] = v
    return out


def _eval(expr, env):
    expr = expr.strip()
    m = re.fullmatch(r"\$([0-9A-Fa-f]+)", expr)
    if m:
        return int(m.group(1), 16)
    if re.fullmatch(r"\d+", expr):
        return int(expr)
    if expr in env:
        return env[expr]
    m = re.fullmatch(r"(\w+)\s*/\s*(\d+)", expr)
    if m and m.group(1) in env:
        return env[m.group(1)] // int(m.group(2))
    return None


def load_charmap():
    """The game's own text encoding, from its charmap directives.

    Two maps come back. The half-width font is one byte per character
    (`charmap "A", $41`). Names are stored in the full-width font, which is two:
    `fwcharmap TX_FULLWIDTH3, "A", $30` expands, via the macro at the top of
    charmaps.asm, to `charmap "A", $03, $30`. Decoding a name with the one-byte map
    alone produces mojibake like '\\x030\\x030' for "AA", which is how this was
    noticed.
    """
    one, two = {}, {}
    txt = (UP / "src/constants/charmaps.asm").read_text(
        encoding="utf-8", errors="replace")
    fw = {}
    for line in txt.splitlines():
        m = re.match(r"^\s*DEF\s+(TX_FULLWIDTH\d)\s+EQU\s+\$([0-9A-Fa-f]+)", line)
        if m:
            fw[m.group(1)] = int(m.group(2), 16)
    # the constants live in text_constants.asm, not charmaps.asm
    for line in (UP / "src/constants/text_constants.asm").read_text(
            errors="replace").splitlines():
        m = re.match(r"^\s*DEF\s+(TX_FULLWIDTH\d)\s+EQU\s+\$([0-9A-Fa-f]+)", line)
        if m:
            fw[m.group(1)] = int(m.group(2), 16)

    c1 = re.compile(r'^\s*charmap\s+"(.*)",\s*\$?([0-9A-Fa-f]+)\s*(?:;.*)?$')
    cfw = re.compile(r'^\s*fwcharmap\s+(\w+),\s*"(.*)",\s*\$?([0-9A-Fa-f]+)\s*(?:;.*)?$')
    for line in txt.splitlines():
        m = c1.match(line)
        if m:
            one.setdefault(int(m.group(2), 16), m.group(1))
            continue
        m = cfw.match(line)
        if m:
            lead, ch, lo = m.group(1), m.group(2), int(m.group(3), 16)
            if lead in fw and fw[lead] != 0:
                two.setdefault((fw[lead], lo), ch)
            elif lead in fw:
                one.setdefault(lo, ch)
    return one, two


def load_duel_data_table(syms, consts):
    """The DuelDataToSave table, read out of engine/duel/core.asm.

    Each row is `dw <start symbol>, <end symbol> - <start symbol>` (or an
    explicit size), terminated by `dw NULL`. This is the authority on what the
    saved 826 bytes actually contain and in what order, so it is parsed rather
    than assumed.
    """
    txt = (UP / "src/engine/duel/core.asm").read_text(errors="replace")
    lines = txt.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith("DuelDataToSave:"))
    segs = []
    for line in lines[i + 1:]:
        line = line.split(";")[0].strip()
        if not line:
            continue
        if not line.startswith("dw "):
            break
        body = line[3:].strip()
        if body == "NULL":
            break
        start_s, size_s = [p.strip() for p in body.split(",", 1)]
        if start_s not in syms:
            raise RuntimeError("DuelDataToSave names an unknown symbol: %r" % start_s)
        start = syms[start_s][1]
        m = re.fullmatch(r"(\w+)\s*-\s*(\w+)", size_s)
        if m:
            size = syms[m.group(1)][1] - syms[m.group(2)][1]
        elif size_s in consts:
            size = consts[size_s]
        elif re.fullmatch(r"\$?[0-9A-Fa-f]+", size_s):
            size = int(size_s.lstrip("$"), 16 if size_s.startswith("$") else 10)
        else:
            raise RuntimeError("cannot resolve DuelDataToSave size %r" % size_s)
        segs.append((start_s, start, size))
    if not segs:
        raise RuntimeError("DuelDataToSave parsed empty")
    return segs


# ----------------------------------------------------------------- the decoder

class TcgSave:
    def __init__(self, srm: bytes, sym_path=None):
        self.srm = srm
        self.sym = load_symbols(sym_path)
        self.consts = load_constants("src/constants/duel_constants.asm",
                                     "src/constants/misc_constants.asm",
                                     "src/constants/name_constants.asm")
        self.charmap, self.charmap_fw = load_charmap()
        self.segs = load_duel_data_table(self.sym, self.consts)
        self.seed = self.consts["SAVE_DUEL_CHECKSUM_SEED"]
        self.hdr_size = self.consts["SAVE_DUEL_HEADER_SIZE"]
        self.data_size = self.consts["SAVE_DUEL_DATA_SIZE"]
        total = sum(s for _, _, s in self.segs)
        if total != self.data_size:
            raise RuntimeError(
                "DuelDataToSave sums to %d but SAVE_DUEL_DATA_SIZE is %d"
                % (total, self.data_size))

    # --- addressing -------------------------------------------------------
    def off(self, name):
        """File offset of a symbol, from its bank and address in the .sym."""
        bank, addr = self.sym[name]
        if not (SRAM_BASE <= addr < SRAM_BASE + SRAM_BANK_SIZE):
            raise RuntimeError("%s at %02x:%04x is not in SRAM" % (name, bank, addr))
        return bank * SRAM_BANK_SIZE + (addr - SRAM_BASE)

    def read(self, name, n):
        o = self.off(name)
        return self.srm[o:o + n]

    # --- text -------------------------------------------------------------
    def text(self, raw):
        """Decode a text buffer, TX_END ($00) terminated.

        $01-$04 are the TX_FULLWIDTH1-4 lead bytes, so they are taken together
        with the byte that follows; everything else is the one-byte half-width
        font.
        """
        out, i = [], 0
        while i < len(raw):
            b = raw[i]
            if b == 0:                      # TX_END
                break
            if 1 <= b <= 4 and i + 1 < len(raw) and (b, raw[i + 1]) in self.charmap_fw:
                out.append(self.charmap_fw[(b, raw[i + 1])])
                i += 2
                continue
            out.append(self.charmap.get(b, "\\x%02x" % b))
            i += 1
        return "".join(out)

    def player_name(self):
        n = self.consts.get("NAME_BUFFER_LENGTH", 16)
        return self.text(self.read("sPlayerName", n))

    # --- the duel record --------------------------------------------------
    def duel(self, which="sCurrentDuel"):
        """Decode and validate the record at a named SRAM symbol."""
        base = self.off(which)
        d = self._decode(self.srm[base:base + self.hdr_size + self.data_size])
        d["slot"] = which
        d["offset"] = base
        return d

    def _decode(self, rec):
        if len(rec) < self.hdr_size + self.data_size:
            return {"present": False, "valid_flag": 0, "checksum_ok": False,
                    "duel_type": 0, "duel_type_name": "n/a",
                    "checksum_stored": None, "checksum_recomputed": None,
                    "bytes_checksummed": 0, "state": {},
                    "reason": "save is shorter than one duel record"}
        valid = rec[self.consts["SAVE_DUEL_HEADER_VALID_FLAG"]]
        chk_lo, chk_hi = (rec[self.consts["SAVE_DUEL_HEADER_CHECKSUM"]],
                          rec[self.consts["SAVE_DUEL_HEADER_CHECKSUM"] + 1])
        duel_type = rec[self.consts["SAVE_DUEL_HEADER_DUEL_TYPE"]]
        data = rec[self.hdr_size:]

        # SaveDuelDataToDE: de = SAVE_DUEL_CHECKSUM_SEED, bc = SIZE - 6
        #   e -= [hl] ; d ^= [hl]
        # then stores e as the low byte and d as the high byte.
        n = self.data_size - 6
        e, d = self.seed & 0xFF, (self.seed >> 8) & 0xFF
        for b in data[:n]:
            e = (e - b) & 0xFF
            d = d ^ b

        out = {
            "present": valid != 0,
            "valid_flag": valid,
            "checksum_stored": [chk_lo, chk_hi],
            "checksum_recomputed": [e, d],
            "checksum_ok": (e == chk_lo and d == chk_hi),
            "duel_type": duel_type,
            "duel_type_name": self.duel_type_name(duel_type),
            "bytes_checksummed": n,
        }
        out.update(self.decode_segments(data))
        return out

    def scan(self):
        """Every offset in the save that holds a checksum-valid duel record.

        `SaveDuelData` does not switch SRAM banks -- it writes to the address of
        `sCurrentDuel` in whatever bank happens to be selected -- so which slot a
        record lands in depends on the caller. Rather than assume, scan: a record
        is only reported when the cartridge's own checksum over its 820 data
        bytes reproduces the two bytes stored in its header, which is a 1-in-65536
        coincidence per offset even before the valid flag is required.
        """
        hits = []
        n = self.data_size - 6
        need = self.hdr_size + self.data_size
        seed_e, seed_d = self.seed & 0xFF, (self.seed >> 8) & 0xFF
        for base in range(0, len(self.srm) - need + 1):
            if self.srm[base] != 1:                       # valid flag first: cheap
                continue
            data = self.srm[base + self.hdr_size:base + self.hdr_size + n]
            e, d = seed_e, seed_d
            for b in data:
                e = (e - b) & 0xFF
                d ^= b
            if e == self.srm[base + 1] and d == self.srm[base + 2]:
                hits.append(base)
        return hits

    def where(self, offset):
        """(bank, address, nearest symbol) for a save-file offset."""
        bank, addr = divmod(offset, SRAM_BANK_SIZE)
        addr += SRAM_BASE
        name = None
        for sym, (b, a) in self.sym.items():
            if b == bank and a == addr and sym.startswith("s"):
                name = sym
                break
        return bank, addr, name

    def duel_at(self, offset):
        """Decode a record at a raw file offset (see scan())."""
        bank, addr, name = self.where(offset)
        d = self._decode(self.srm[offset:offset + self.hdr_size + self.data_size])
        d["offset"] = offset
        d["bank"] = bank
        d["address"] = "$%04x" % addr
        d["slot"] = name or "offset 0x%04x (bank %d, $%04x)" % (offset, bank, addr)
        return d

    def duel_type_name(self, v):
        names = {self.consts[k]: k for k in self.consts
                 if k.startswith("DUELTYPE_")}
        if v in names:
            return names[v]
        practice = self.consts.get("DUELTYPE_PRACTICE")
        # "for normal duels (vs AI), wDuelType is $80 + [wOpponentDeckID]"
        if practice is not None and v > practice:
            return "AI duel (DUELTYPE_PRACTICE + deck id %d)" % (v - practice)
        return "unknown ($%02x)" % v

    def decode_segments(self, data):
        """Map the saved blob back onto the WRAM symbols it came from."""
        pos, where = 0, {}
        for name, start, size in self.segs:
            where[name] = (pos, start, size)
            pos += size

        def at(sym):
            """Byte of WRAM symbol `sym` inside the saved blob, or None if that
            address is not one of the ranges DuelDataToSave copies."""
            if sym not in self.sym:
                return None
            addr = self.sym[sym][1]
            for _, (p, start, size) in where.items():
                if start <= addr < start + size:
                    return data[p + (addr - start)]
            return None

        fields = ["wWhoseTurn", "wDuelTurns", "wDuelFinished", "wDuelInitialPrizes",
                  "wDuelType", "wDuelistType", "wOpponentDeckID", "wIsPracticeDuel",
                  "wDuelTheme", "wPlayerArenaCard", "wPlayerArenaCardHP",
                  "wPlayerPrizes", "wPlayerNumberOfCardsNotInDeck",
                  "wOpponentArenaCard", "wOpponentArenaCardHP", "wOpponentPrizes"]
        st = {f: at(f) for f in fields}
        st = {k: v for k, v in st.items() if v is not None}

        # The hand's length is a counter the game keeps, not a terminator:
        # DUELVARS_NUMBER_OF_CARDS_IN_HAND. Walking wPlayerHand for an $ff
        # sentinel reports a constant 60 and means nothing.
        st["player_hand_size"] = at("wPlayerNumberOfCardsInHand")
        st["segments"] = {k: {"offset": v[0], "wram": "$%04x" % v[1], "size": v[2]}
                          for k, v in where.items()}
        return {"state": st}


# ------------------------------------------------------------------------ main

def main(argv):
    if len(argv) < 2:
        print(__doc__)
        print("usage: duelsave.py <save.srm> [sym]")
        return 2
    srm = pathlib.Path(argv[1]).read_bytes()
    s = TcgSave(srm, argv[2] if len(argv) > 2 else None)
    print("save            : %d bytes" % len(srm))
    print("player name     : %r" % s.player_name())
    print("DuelDataToSave  : %s" % ", ".join(
        "%s+%d" % (n, sz) for n, _, sz in s.segs))

    hits = s.scan()
    print("\nchecksum-valid duel records anywhere in the save: %s"
          % ([hex(h) for h in hits] or "none"))
    for h in hits:
        d = s.duel_at(h)
        print("  0x%04x  bank %d %s  %s" % (h, d["bank"], d["address"], d["slot"]))
        for k, v in sorted(d["state"].items()):
            if k != "segments":
                print("      %-32s %s" % (k, v))

    for which in ("sCurrentDuel", "sBackupCurrentDuel"):
        if which not in s.sym:
            continue
        d = s.duel(which)
        print("\n%s @ 0x%04x" % (which, s.off(which)))
        print("  valid flag        : %s" % d.get("valid_flag"))
        print("  checksum stored   : %s" % d.get("checksum_stored"))
        print("  checksum recomputed: %s   -> %s"
              % (d.get("checksum_recomputed"),
                 "MATCH" if d.get("checksum_ok") else "mismatch"))
        print("  duel type         : $%02x  %s"
              % (d.get("duel_type", 0), d.get("duel_type_name")))
        for k, v in sorted(d.get("state", {}).items()):
            if k != "segments":
                print("    %-32s %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
