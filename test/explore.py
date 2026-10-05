#!/usr/bin/env python3
"""Scratch probe: drive the game by hand and watch its own state.

Plays to the duel once, saves a libretro state, and can then be re-run with
--from-state to iterate on the in-duel input without replaying the ten-minute
intro each time.
"""
import argparse
import base64
import os
import pathlib
import random
import socket
import sys
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cdp, serve, duelsave, gbstate  # noqa: E402

OUT = HERE / "out"
B, SELECT, START, UP, DOWN, LEFT, RIGHT, A = 0, 2, 3, 4, 5, 6, 7, 8
STATE_FILE = OUT / "at-duel.state"


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-state", action="store_true")
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    rom = os.path.abspath(str(HERE.parent / "upstream" / "poketcg.gbc"))
    syms = duelsave.load_symbols()
    rng = random.Random(a.seed)
    port = free_port()
    srv = serve.start(port, rom_path=rom)
    nonce = uuid.uuid4().hex
    ch = None
    try:
        ch = cdp.Chrome()
        for d in ("Runtime", "Log", "Page", "DOM"):
            ch.call(d + ".enable")
        ch.goto("http://127.0.0.1:%d/index.html?mute=1&nonce=%s" % (port, nonce))
        ch.set_file_input("#file", [rom])
        for _ in range(90):
            ch.pump(1.0)
            if ch.eval("!!(window.__tcg && window.__tcg.started)"):
                break

        def snap():
            r = ch.eval("window.__tcg.snapshot()", timeout=180)
            return (gbstate.GbState(base64.b64decode(r["state"])),
                    base64.b64decode(r["sram"]) if r.get("sram") else None,
                    r["state"])

        if a.from_state and STATE_FILE.exists():
            b64s = base64.b64encode(STATE_FILE.read_bytes()).decode()
            ch.eval("(function(s){var b=atob(s);var u=new Uint8Array(b.length);"
                    "for(var i=0;i<b.length;i++)u[i]=b.charCodeAt(i);"
                    "window.EJS_emulator.gameManager.loadState(u);})('%s')" % b64s,
                    timeout=180)
            ch.pump(4.0)
            print("loaded state from", STATE_FILE)
        else:
            n = 0
            named = False
            t0 = time.time()
            while time.time() - t0 < 900:
                st, srm, raw = snap()
                nl = st.symbyte(syms, "wNamingScreenBufferLength")
                dt = st.symbyte(syms, "wDuelType")
                if dt:
                    STATE_FILE.write_bytes(base64.b64decode(raw))
                    print("at duel after %.0fs, state saved" % (time.time() - t0))
                    break
                if not named and nl > 0:
                    ch.hold(START, 0.25); ch.pump(0.8)
                    ch.hold(A, 0.25); ch.pump(1.5)
                    named = True
                    continue
                for _ in range(10):
                    n += 1
                    ch.hold(B if n % 6 == 0 else A, 0.1); ch.pump(0.35)
            else:
                print("never reached a duel"); return

        # --- in the duel: weighted random input until a record appears -------
        t1 = time.time()
        buttons = [A] * 11 + [DOWN] * 5 + [UP] * 2 + [B] * 2
        while time.time() - t1 < a.seconds:
            for _ in range(12):
                ch.hold(rng.choice(buttons), 0.1)
                ch.pump(0.3)
            st, srm, raw = snap()
            s = duelsave.TcgSave(srm)
            hits = s.scan()
            print("t=%4.0f arena=$%02x oppArena=$%02x turns=%d prizes=%d finished=%d hits=%s"
                  % (time.time() - t1,
                     st.symbyte(syms, "wPlayerArenaCard"),
                     st.symbyte(syms, "wOpponentArenaCard"),
                     st.symbyte(syms, "wDuelTurns"),
                     st.symbyte(syms, "wDuelInitialPrizes"),
                     st.symbyte(syms, "wDuelFinished"),
                     hits), flush=True)
            if hits:
                for h in hits:
                    d = s.duel_at(h)
                    print("  -> %s type $%02x %s turn %s arena $%02x hp %s" % (
                        d["slot"], d["duel_type"], d["duel_type_name"],
                        d["state"].get("wDuelTurns"),
                        d["state"].get("wPlayerArenaCard"),
                        d["state"].get("wPlayerArenaCardHP")), flush=True)
                (OUT / "duel.srm").write_bytes(srm)
                ch.screenshot(OUT / "e4-duel.png")
                break
            ch.screenshot(OUT / "e4-progress.png")
    finally:
        if ch:
            ch.kill()
        srv.shutdown(); srv.server_close()


if __name__ == "__main__":
    main()
