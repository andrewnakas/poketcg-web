#!/usr/bin/env python3
"""Headless check of the Pokemon TCG browser target.

Two stages, in one tab, in order:

  stage 1  pick the ROM through the page's own <input type=file>, play the
           opening to a real duel, and prove it is a duel using the game's own
           saved duel record -- decoded with the symbol table of the build under
           test, and with its checksum recomputed by the cartridge's algorithm.
  stage 2  navigate away and back, and read the battery save out of IndexedDB
           *before any ROM is picked*, so nothing is running that could rewrite
           what is being measured.

Everything is muted twice over: Chrome runs --mute-audio and the page is loaded
with ?mute=1, which sets EJS_volume = 0.

    python3 test/browsercheck.py

Writes screenshots and test/out/result.json. Exit status 0 only if every check
passed.
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import random
import socket
import sys
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
import cdp        # noqa: E402
import serve      # noqa: E402
import duelsave   # noqa: E402
import gbstate    # noqa: E402
import pngstat    # noqa: E402

OUT = HERE / "out"

# libretro joypad ids
B, SELECT, START, UP, DOWN, LEFT, RIGHT, A = 0, 2, 3, 4, 5, 6, 7, 8

# EmulatorJS 4.2.3 pings its CDN only when the host is localhost
# (src/emulator.js:207), which is the worst way round: noisy in the test, silent
# in production. The page's CSP refuses it. Expect exactly this one refusal.
ALLOWED_BLOCKED = "cdn.emulatorjs.org"
# The CDN does not publish a build report for this core at this version. Harmless.
ALLOWED_404 = "cores/reports/gambatte.json"


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append({"check": name, "ok": bool(ok), "detail": str(detail)})
        print("  [%s] %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
        return bool(ok)

    @property
    def failed(self):
        return [r for r in self.rows if not r["ok"]]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def banner(msg):
    print("\n== %s %s" % (msg, "=" * max(0, 68 - len(msg))))


# ------------------------------------------------------------------- page bits

def wait_started(ch, limit=120):
    end = time.time() + limit
    while time.time() < end:
        ch.pump(1.0)
        if ch.eval("!!(window.__tcg && window.__tcg.started)"):
            return True
    return False


def grab_state(ch):
    """A libretro save state, parsed. This is the only way to see live work RAM:
    the core exposes no memory-peek API, so the state is the memory."""
    s = ch.eval("window.__tcg.state()", timeout=180)
    if not isinstance(s, str):
        raise RuntimeError("getState failed: %r" % s)
    return gbstate.GbState(base64.b64decode(s))


def grab_sram(ch, live=True):
    """The SRAM image.

    live=True pushes the core's current SRAM into the FS copy first. Without
    that, getSaveFile(false) returns whatever was last flushed -- which during
    play is minutes stale, and made an early run of this test miss a duel record
    the game had already written.

    live=False is the read-only form, for after a reload: the writing form would
    put the core's not-yet-restored SRAM over a good save and then truthfully
    report blanks.
    """
    s = ch.eval("window.__tcg.%s()" % ("sramNow" if live else "sram"), timeout=180)
    return base64.b64decode(s) if s else None


def best_duel(srm, sym=None):
    """Find the duel record the game wrote, wherever it put it.

    `SaveDuelData` writes to the *address* of `sCurrentDuel` in whatever SRAM bank
    happens to be selected, so which slot a record lands in depends on the caller:
    `PrintDuelMenuAndHandleInput` calls it on bank 0 (-> `sCurrentDuel`), while
    `SaveDuelStateToSRAM` selects bank 2 first (-> `sBackupCurrentDuel`) and then
    writes a third copy into one of four rotating `sDuelBuffer`s on bank 3. An
    early version of this test only looked at `sCurrentDuel`, found nothing, and
    reported no duel while the game was plainly in one.

    So: scan the whole save, and only accept an offset whose 820 data bytes
    reproduce the two checksum bytes stored in its header, using the cartridge's
    own algorithm and seed. Return the record at the lowest offset, and name the
    slot from the symbol table.
    """
    s = duelsave.TcgSave(srm, sym)
    hits = s.scan()
    if hits:
        d = s.duel_at(hits[0])
        d["all_slots"] = [s.duel_at(h)["slot"] for h in hits]
        return d
    d = s.duel("sCurrentDuel")       # nothing valid: report the canonical slot
    d["all_slots"] = []
    return d


def grab_snapshot(ch):
    """State and SRAM with no frame boundary between them -- see __tcg.snapshot."""
    r = ch.eval("window.__tcg.snapshot()", timeout=180)
    if not isinstance(r, dict) or r.get("error"):
        raise RuntimeError("snapshot failed: %r" % r)
    srm = base64.b64decode(r["sram"]) if r.get("sram") else None
    return gbstate.GbState(base64.b64decode(r["state"])), srm


def shot(ch, name):
    p = OUT / ("%s.png" % name)
    ch.screenshot(p)
    return p


def canvas_box(ch):
    s = ch.eval("window.__tcg.screen()") or {}
    if not s.get("ok"):
        return None
    return (s["x"], s["y"], s["x"] + s["fw"], s["y"] + s["fh"])


# ------------------------------------------------------------------ the playing

class Masher:
    """Button input for the two halves of the opening.

    Neither half can be cleared with A alone, and for different reasons.

    **Dialogue (profile "intro"): A, with a B every sixth press.** On the first
    visit to Dr. Mason's lab, Sam opens the rules multichoice
    (scripts/mason_laboratory.asm -> show_sam_rules_multichoice); its first entry is
    "Energy", so A selects it, the script explains energy and jumps straight back to
    the same menu. Mashing A loops there forever -- it did, for 383 s, in the run
    that found this. The way out is the cancel path, and B is safe everywhere here:

      * text boxes advance on A *or* B (home/menus.asm WaitForWideTextBoxInput
        waits on PAD_A | PAD_B);
      * Yes/No menus ignore B entirely (HandleYesOrNoMenu reads only A and
        left/right), so B cannot answer "no" to anything;
      * a multichoice returns its configured cancel value -- for Sam's rules menu
        that is SAM_MENU_NOTHING_TO_ASK, which is exactly the exit, and for the
        starter-deck menu it is $00, i.e. the first deck.

    **The duel (profile "duel"): A with the D-pad mixed in.** The duel opens by
    asking the player to put a basic Pokemon in the arena, from a hand list whose
    first entry is a Trainer card. A on that does nothing at all, forever, with
    wPlayerArenaCard stuck at $ff -- which is exactly what two earlier runs of this
    test did for 300 s while looking like they were in a duel. The cursor has to
    move. A seeded weighted choice walks the list, and the practice duel re-prompts
    when the wrong card is picked, so a wrong guess costs nothing.

    The seed is fixed so a run is reproducible, but nothing downstream depends on
    the exact sequence: the pass/fail gate is the game's own state, not the input.
    """

    PROFILES = {
        "intro": None,                                   # the counter handles it
        "duel": [A] * 11 + [DOWN] * 5 + [UP] * 2 + [B] * 2,
    }

    def __init__(self, ch, seed=7):
        self.ch, self.n = ch, 0
        self.rng = random.Random(seed)

    def go(self, presses, profile="intro"):
        bag = self.PROFILES[profile]
        for _ in range(presses):
            self.n += 1
            btn = self.rng.choice(bag) if bag else (B if self.n % 6 == 0 else A)
            self.ch.hold(btn, 0.1)
            self.ch.pump(0.35)


def play_to_duel(ch, syms, log, budget=420.0):
    """Drive the opening with real button edges until the game saves a duel.

    The phases are gated on the game's own work RAM, read out of a save state and
    addressed with the build's symbol table -- not on a timer and not on what a
    screenshot looks like. Pokemon TCG's title screen runs an attract loop of card
    artwork that is perfectly photogenic and is not a duel.
    """
    t0 = time.time()
    phase = "intro"
    named = False
    shots = []
    mash = Masher(ch)

    def elapsed():
        return time.time() - t0

    while elapsed() < budget:
        s = grab_state(ch)
        name_len = s.symbyte(syms, "wNamingScreenBufferLength")
        duel_type = s.symbyte(syms, "wDuelType")
        arena = s.symbyte(syms, "wPlayerArenaCard")

        if duel_type != 0 or arena not in (0, 0xFF):
            phase = "duel"
            log.append("t=%.0fs duel engine live: wDuelType=$%02x wPlayerArenaCard=$%02x"
                       % (elapsed(), duel_type, arena))
            break

        if not named and name_len > 0:
            # On the naming screen. START parks the cursor on "End"
            # (engine/input_name.asm: on PAD_START it sets cursor 6,5 and loops),
            # then A selects it and FinalizeInputName runs.
            phase = "naming"
            log.append("t=%.0fs naming screen, %d bytes typed -> START, A"
                       % (elapsed(), name_len))
            shots.append(shot(ch, "play-2-naming"))
            ch.hold(START, 0.25)
            ch.pump(0.8)
            ch.hold(A, 0.25)
            ch.pump(1.5)
            named = True
            # wNamingScreenBufferLength is not cleared afterwards, so it cannot
            # be used to tell whether this worked; the screenshot after this and
            # the fact that the lab scripts run are what show it did.
            continue

        mash.go(10)

    else:
        shots.append(shot(ch, "play-timeout"))
        return {"reached": phase, "named": named, "seconds": elapsed(),
                "shots": [str(p) for p in shots]}

    # In the duel now. Keep going until the game reaches the player's duel menu,
    # which is where PrintDuelMenuAndHandleInput calls SaveDuelData and writes the
    # checksummed record this test gates on.
    shots.append(shot(ch, "play-3-duel-start"))
    deadline = time.time() + 420
    saved = None
    while time.time() < deadline:
        mash.go(12, profile="duel")
        srm = grab_sram(ch)
        if srm:
            d = best_duel(srm)
            if d and d["valid_flag"] == 1 and d["checksum_ok"]:
                saved = d
                log.append("t=%.0fs the game wrote a valid duel record in %s "
                           "(type $%02x %s, arena $%02x hp %d)"
                           % (elapsed(), ", ".join(d["all_slots"]), d["duel_type"],
                              d["duel_type_name"], d["state"]["wPlayerArenaCard"],
                              d["state"]["wPlayerArenaCardHP"]))
                break
    shots.append(shot(ch, "play-4-duel-menu"))
    return {"reached": "duel-saved" if saved else "duel", "named": named,
            "seconds": elapsed(), "duel": saved, "shots": [str(p) for p in shots]}


# ------------------------------------------------------------------- assertions

def network_hosts(ch):
    hosts = set()
    bodies = []
    for e in ch.events:
        if e.get("method") == "Network.requestWillBeSent":
            url = e["params"]["request"]["url"]
            if url.startswith(("data:", "blob:")):
                continue
            try:
                from urllib.parse import urlparse
                hosts.add(urlparse(url).netloc)
            except Exception:
                hosts.add(url[:40])
            if e["params"]["request"].get("postData"):
                bodies.append(url)
    return sorted(hosts), bodies


def http_failures(ch):
    """Every non-2xx/3xx response the browser actually received, by URL.

    Chrome's console line for a failed subresource is just "Failed to load
    resource: the server responded with a status of 404" with no URL, so the
    console alone cannot tell an expected 404 from an unexpected one. The network
    log can.
    """
    out = []
    for e in ch.events:
        if e.get("method") == "Network.responseReceived":
            r = e["params"]["response"]
            if r.get("status", 200) >= 400:
                out.append((r["status"], r["url"]))
        elif e.get("method") == "Network.loadingFailed":
            out.append(("failed", e["params"].get("errorText", "")))
    return out


def console_problems(ch, http_fails):
    """Console errors, minus the ones the network log already accounts for."""
    all_404s_known = all(ALLOWED_404 in str(u) or ALLOWED_BLOCKED in str(u)
                         for _, u in http_fails)
    bad = []
    for lvl, txt in ch.console():
        if lvl not in ("error", "exception"):
            continue
        if ALLOWED_BLOCKED in txt or ALLOWED_404 in txt:
            continue
        # the anonymous subresource-failure line, explained by the network log
        if all_404s_known and "Failed to load resource" in txt:
            continue
        bad.append("%s: %s" % (lvl, txt[:200]))
    return bad


# ------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default=str(ROOT / "upstream" / "poketcg.gbc"))
    ap.add_argument("--budget", type=float, default=420.0)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    rom = os.path.abspath(a.rom)
    rom_bytes = pathlib.Path(rom).read_bytes()
    rom_sha1 = hashlib.sha1(rom_bytes).hexdigest()
    sym = ROOT / "upstream" / "poketcg.sym"
    if not sym.exists():
        print("no %s -- run ./build.sh first (make DEBUG=1 emits the .sym)" % sym)
        return 2
    syms = duelsave.load_symbols(sym)

    C = Checks()
    R = {"rom": os.path.basename(rom), "rom_sha1_on_disk": rom_sha1,
         "started": time.strftime("%Y-%m-%d %H:%M:%S"), "log": []}
    log = R["log"]

    port = free_port()
    srv = serve.start(port, rom_path=rom)
    base = "http://127.0.0.1:%d/index.html" % port
    nonce1 = uuid.uuid4().hex
    ch = None
    try:
        ch = cdp.Chrome()
        for d in ("Runtime", "Log", "Page", "DOM", "Network"):
            ch.call(d + ".enable")

        # ---------------------------------------------------------- stage 1
        banner("stage 1: load the ROM and play")
        R["stage"] = 1
        ch.goto("%s?mute=1&hb&stage=1&nonce=%s" % (base, nonce1))

        href = ch.eval("location.href") or ""
        title = ch.eval("document.title")
        C.add("tab identity: our nonce is echoed back", nonce1 in href,
              "title=%r" % title)
        # ten agents share this box; a tab that cannot prove it is ours is not
        # evidence about our page

        C.add("ROM picker is present before anything is loaded",
              ch.eval("!!document.getElementById('file')"))

        ch.set_file_input("#file", [rom])
        ok = wait_started(ch, 150)
        C.add("core started after the page's own file-input handler ran", ok)
        if not ok:
            raise RuntimeError("core never started")

        core = ch.eval("window.__tcg.core()")
        C.add("core is gambatte", core == "gambatte", repr(core))

        page_sha1 = ch.eval("window.__tcg.sha1")
        C.add("sha1 the page computed over the bytes == sha1 on disk",
              page_sha1 == rom_sha1, page_sha1)
        C.add("page saw the full 1 MiB", ch.eval("window.__tcg.size") == len(rom_bytes),
              ch.eval("window.__tcg.size"))

        scr = ch.eval("window.__tcg.screen()")
        C.add("canvas has non-zero size",
              bool(scr and scr.get("ok") and scr["w"] > 0 and scr["h"] > 0),
              "%sx%s backing, %sx%s css" % (scr.get("w"), scr.get("h"),
                                            round(scr.get("cssW", 0)),
                                            round(scr.get("cssH", 0))) if scr else "none")
        R["screen"] = scr

        C.add("audio is muted in the page", ch.eval("window.EJS_volume") == 0,
              "EJS_volume=%s" % ch.eval("window.EJS_volume"))
        C.add("EmulatorJS's beforeunload listener was dropped",
              ch.eval("window.__tcg.ejsUnloadDropped") is True)

        f1 = ch.eval("window.__tcg.frame()")
        ch.pump(3.0)
        f2 = ch.eval("window.__tcg.frame()")
        C.add("frame counter is climbing", (f2 or 0) > (f1 or 0), "%s -> %s" % (f1, f2))

        p = shot(ch, "play-1-boot")
        box = canvas_box(ch)
        lum = pngstat.luma(p, box)
        C.add("the emulator drew a picture (screenshot, cropped to the canvas)",
              lum["mean"] > 8 and lum["lit"] > 0.2, str(lum))
        R["boot_luma"] = lum

        # --- the state parser must agree with the core's own save file -------
        # Taken in one synchronous call so the core cannot run between the two.
        s0, srm0 = grab_snapshot(ch)
        C.add("save-state parser agrees with the core's own .srm, 32 KiB",
              bool(srm0) and s0.verify_against_sram(srm0),
              "wram=%d sram=%d" % (len(s0.wram), len(s0.sram)))

        # --- play ------------------------------------------------------------
        banner("stage 1: playing to a duel (muted, real button edges)")
        res = play_to_duel(ch, syms, log, budget=a.budget)
        R["play"] = {k: v for k, v in res.items() if k != "duel"}
        for line in log:
            print("   ", line)

        C.add("reached the name-entry screen and confirmed a name", res["named"])

        duel = res.get("duel")
        C.add("the game wrote a duel record to SRAM", bool(duel))
        if duel:
            R["duel_in_play"] = duel
            st = duel["state"]
            C.add("the duel record's valid flag is TRUE", duel["valid_flag"] == 1,
                  "%s, flag=%s" % (", ".join(duel["all_slots"]), duel["valid_flag"]))
            C.add("the cartridge's own checksum over the duel data recomputes",
                  duel["checksum_ok"],
                  "stored %s == recomputed %s over %d bytes"
                  % (duel["checksum_stored"], duel["checksum_recomputed"],
                     duel["bytes_checksummed"]))
            C.add("duel type is a real duel, not an unset field",
                  duel["duel_type"] != 0, "$%02x %s" % (duel["duel_type"],
                                                        duel["duel_type_name"]))
            C.add("a Pokemon is in the player's arena",
                  st["wPlayerArenaCard"] not in (0, 0xFF),
                  "wPlayerArenaCard=$%02x hp=%d"
                  % (st["wPlayerArenaCard"], st["wPlayerArenaCardHP"]))
            C.add("arena HP is a legal value (multiple of 10, <= MAX_HP)",
                  st["wPlayerArenaCardHP"] % 10 == 0
                  and 0 < st["wPlayerArenaCardHP"] <= 120,
                  "%d" % st["wPlayerArenaCardHP"])
            C.add("the opponent has an arena Pokemon too",
                  st["wOpponentArenaCard"] not in (0, 0xFF),
                  "wOpponentArenaCard=$%02x hp=%d"
                  % (st["wOpponentArenaCard"], st["wOpponentArenaCardHP"]))
            C.add("prize cards were set for this duel",
                  0 < st["wDuelInitialPrizes"] <= 6, "%d" % st["wDuelInitialPrizes"])
            C.add("the player holds a hand of cards",
                  0 < (st.get("player_hand_size") or 0) <= 60,
                  "wPlayerNumberOfCardsInHand=%s" % st.get("player_hand_size"))
            # PLAYER_TURN is defined as HIGH(wPlayerDuelVariables); resolve it from
            # the symbol table rather than writing $c2 down here.
            player_turn = syms["wPlayerDuelVariables"][1] >> 8
            C.add("it is the player's turn, by the game's own PLAYER_TURN value",
                  st["wWhoseTurn"] == player_turn,
                  "wWhoseTurn=$%02x, HIGH(wPlayerDuelVariables)=$%02x"
                  % (st["wWhoseTurn"], player_turn))

            # --- the two independent views of the duel must agree -------------
            # The .srm copy is written by the game's own SaveDuelData routine;
            # the work-RAM copy comes out of gambatte's save state. Different
            # code paths entirely, so agreement is real evidence the addressing
            # is right rather than a self-consistent misreading.
            s1, _ = grab_snapshot(ch)
            live = {k: s1.symbyte(syms, k) for k in
                    ("wDuelType", "wDuelInitialPrizes", "wPlayerArenaCard",
                     "wOpponentArenaCard", "wIsPracticeDuel")}
            same = all(live[k] == st[k] for k in live)
            C.add("live work RAM and the saved duel record agree",
                  same, json.dumps(live))
            R["live_wram"] = live

            pshot = OUT / "play-4-duel-menu.png"
            if pshot.exists():
                lum2 = pngstat.luma(pshot, box)
                C.add("the duel screen is drawing", lum2["mean"] > 8, str(lum2))

        # --- egress ----------------------------------------------------------
        banner("stage 1: egress")
        hosts, bodies = network_hosts(ch)
        offsite = [h for h in hosts if not h.startswith("127.0.0.1")]
        C.add("the browser contacted only the local test server",
              not offsite, "hosts=%s" % hosts)
        C.add("no request carried a body", not bodies, str(bodies))
        eg = ch.eval("window.__egress ? "
                     "{blocked: window.__egress.blocked, bodies: window.__egress.bodies} : null")
        blocked = [b["url"] for b in (eg or {}).get("blocked", [])]
        unexpected = [u for u in blocked if ALLOWED_BLOCKED not in u]
        C.add("the runtime guard refused nothing unexpected",
              not unexpected, "refused=%s" % blocked)
        R["egress"] = {"hosts": hosts, "bodies": bodies, "blocked": blocked}

        fails = http_failures(ch)
        unknown = [(st, u) for st, u in fails
                   if ALLOWED_404 not in str(u) and ALLOWED_BLOCKED not in str(u)]
        C.add("no unexpected HTTP failures", not unknown, str(fails))
        R["http_failures"] = [list(map(str, f)) for f in fails]
        bad = console_problems(ch, fails)
        C.add("no unexpected console errors", not bad, "; ".join(bad)[:300])

        # --- flush and look at IndexedDB while still on the page --------------
        ch.eval("window.__tcg.save('test', true)", await_promise=True, timeout=60)
        ch.pump(2.0)
        idb1 = ch.eval("window.__tcg.idbSave()", await_promise=True, timeout=120)
        R["idb_stage1"] = {"db": idb1.get("db"), "files": idb1.get("files")}
        C.add("the battery save is in IndexedDB before leaving",
              bool(idb1.get("files")), json.dumps(idb1.get("files")))
        mount = ch.eval("window.__tcg.mount()")
        C.add("/data/saves is still an IDBFS mount with autoPersist",
              mount.get("mounted") and mount.get("autoPersist"), json.dumps(mount))

        srm_before = None
        for k, v in (idb1.get("blobs") or {}).items():
            if k.endswith(".srm"):
                srm_before = base64.b64decode(v["b64"])
                R["srm_key"] = k
                R["srm_sha1_before"] = v["sha1"]
        C.add("a .srm is present in the store", srm_before is not None,
              R.get("srm_key", ""))

        # The record that matters for persistence is the one that actually went
        # into storage, not the one read mid-play: the core keeps running between
        # the two, and the AI's turn can legitimately rewrite the slot. Decode
        # the stored copy and make that the "before" side of the comparison.
        duel_before = best_duel(srm_before, sym) if srm_before else None
        R["duel_stored_before_reload"] = duel_before
        if duel_before:
            C.add("the stored save holds a valid duel record before the reload",
                  duel_before["valid_flag"] == 1 and duel_before["checksum_ok"],
                  "%s: type $%02x %s, turn %d, arena $%02x hp %d"
                  % (duel_before["slot"], duel_before["duel_type"],
                     duel_before["duel_type_name"],
                     duel_before["state"]["wDuelTurns"],
                     duel_before["state"]["wPlayerArenaCard"],
                     duel_before["state"]["wPlayerArenaCardHP"]))

        # ---------------------------------------------------------- stage 2
        banner("stage 2: leave the page, come back, and look at the save")
        R["stage"] = 2
        nonce2 = uuid.uuid4().hex
        # Navigate away IN THE SAME TAB. Closing the tab over CDP can skip
        # beforeunload entirely, which is precisely the path that destroys the
        # save in stock EmulatorJS -- a test that closes the tab never sees it.
        ch.goto("about:blank")
        ch.pump(2.0)
        ch.goto("%s?mute=1&stage=2&nonce=%s" % (base, nonce2))
        href2 = ch.eval("location.href") or ""
        C.add("stage 2 is a genuinely new document with its own nonce",
              nonce2 in href2 and nonce1 not in href2, href2.split("?")[-1][:90])
        C.add("stage 2 has no ROM loaded, so nothing can rewrite the save",
              ch.eval("!(window.__tcg && window.__tcg.started)"))

        idb2 = ch.eval("window.__tcg.idbSave()", await_promise=True, timeout=120)
        R["idb_stage2"] = {"db": idb2.get("db"), "files": idb2.get("files")}
        C.add("the save store is not empty after the reload",
              bool(idb2.get("files")), json.dumps(idb2.get("files")))

        srm_after = None
        for k, v in (idb2.get("blobs") or {}).items():
            if k.endswith(".srm"):
                srm_after = base64.b64decode(v["b64"])
                R["srm_sha1_after"] = v["sha1"]
        C.add("the .srm survived byte for byte",
              srm_after is not None and srm_after == srm_before,
              "sha1 %s -> %s" % (R.get("srm_sha1_before"), R.get("srm_sha1_after")))

        if srm_after:
            d2 = best_duel(srm_after, sym)
            R["duel_after_reload"] = d2
            C.add("the restored save still holds a valid duel record",
                  d2["valid_flag"] == 1 and d2["checksum_ok"],
                  "%s: type $%02x %s, turn %d, arena $%02x hp %d"
                  % (d2["slot"], d2["duel_type"], d2["duel_type_name"],
                     d2["state"]["wDuelTurns"], d2["state"]["wPlayerArenaCard"],
                     d2["state"]["wPlayerArenaCardHP"]))
            if duel_before:
                C.add("the duel decodes identically before and after the reload",
                      d2["slot"] == duel_before["slot"]
                      and d2["state"] == duel_before["state"]
                      and d2["checksum_stored"] == duel_before["checksum_stored"])

        # --- and it still runs: load the ROM again and resume -----------------
        banner("stage 2: resume from the restored save")
        ch.set_file_input("#file", [rom])
        ok2 = wait_started(ch, 150)
        C.add("the ROM loads again on the restored page", ok2)
        if ok2:
            for _ in range(14):
                ch.hold(A, 0.1)
                ch.pump(0.4)
            p2 = shot(ch, "resume-1")
            box2 = canvas_box(ch)
            lum3 = pngstat.luma(p2, box2)
            C.add("the resumed page is drawing", lum3["mean"] > 8, str(lum3))
            srm3 = grab_sram(ch)
            if srm3:
                d3 = best_duel(srm3, sym)
                C.add("the duel record read back by the running core is still valid",
                      d3["valid_flag"] == 1 and d3["checksum_ok"],
                      "type $%02x turn %d" % (d3["duel_type"],
                                              d3["state"]["wDuelTurns"]))

        fails2 = http_failures(ch)
        unknown2 = [(st, u) for st, u in fails2
                    if ALLOWED_404 not in str(u) and ALLOWED_BLOCKED not in str(u)]
        C.add("no unexpected HTTP failures in stage 2", not unknown2, str(unknown2))
        bad2 = console_problems(ch, fails2)
        C.add("no unexpected console errors in stage 2", not bad2, "; ".join(bad2)[:300])

    finally:
        if ch:
            try:
                ch.kill()
            except Exception:
                pass
        srv.shutdown()
        srv.server_close()

    R["checks"] = C.rows
    R["passed"] = len(C.rows) - len(C.failed)
    R["total"] = len(C.rows)
    (OUT / "result.json").write_text(json.dumps(R, indent=2, default=str))
    banner("result")
    print("  %d/%d checks passed" % (R["passed"], R["total"]))
    for f in C.failed:
        print("  FAILED: %s  %s" % (f["check"], f["detail"]))
    print("  details: %s" % (OUT / "result.json"))
    return 0 if not C.failed else 1


if __name__ == "__main__":
    sys.exit(main())
