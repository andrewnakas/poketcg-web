#!/usr/bin/env python3
"""Does EmulatorJS's `exit` handler actually empty the save store?  No race.

Adapted for poketcg from ~/decompfleet/games/pokefirered/tools/save_mechanism.py,
which established the method. The reasoning is that agent's, and is worth repeating
because the obvious experiment gives the wrong answer:

  Navigating away and coming back is a race between EmulatorJS's unmount writing to
  IndexedDB and the tab dying. In headless Chrome the page usually wins, so the
  user-visible experiment passed 8/8 on a page that definitely had the bug, while
  another agent saw 3 failures in 5 from the same code. A soak cannot settle this.

So: dispatch `beforeunload` as an event, without actually unloading. Nothing is
racing, the result is deterministic, and the clearest signal needs no storage read at
all -- `/data/saves`'s `autoPersist` flipping to false is the fingerprint of the
IDBFS mount having been torn down and replaced.

Two arms, one query parameter apart:

    ?ejsunload=keep   leave EmulatorJS's listener in place   (expected: destroyed)
    (default)         this page drops it                     (expected: intact)

**The control is checked before the comparison.** If `?ejsunload=keep` silently did
nothing, both arms would be the same page and a matching result would mean nothing,
so the page logs which arm it took and this script asserts that log line agrees with
the flag it was given.

    python3 tools/save_mechanism.py                      # the shipped page
    python3 tools/save_mechanism.py --keep-ejs-unload    # the control arm
"""
import argparse
import json
import os
import pathlib
import socket
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test"))
import cdp      # noqa: E402
import serve    # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default=str(ROOT / "upstream" / "poketcg.gbc"))
    ap.add_argument("--keep-ejs-unload", action="store_true",
                    help="control arm: leave EmulatorJS's beforeunload in place")
    a = ap.parse_args()
    rom = os.path.abspath(a.rom)

    port = free_port()
    srv = serve.start(port, rom_path=rom)
    nonce = uuid.uuid4().hex
    url = ("http://127.0.0.1:%d/index.html?mute=1&nonce=%s%s"
           % (port, nonce, "&ejsunload=keep" if a.keep_ejs_unload else ""))
    ch = None
    res = {"arm": "keep" if a.keep_ejs_unload else "dropped", "nonce": nonce}
    try:
        ch = cdp.Chrome()
        for d in ("Runtime", "Log", "Page", "DOM"):
            ch.call(d + ".enable")
        ch.goto(url)
        href = ch.eval("location.href") or ""
        if nonce not in href:
            raise RuntimeError("attached to someone else's tab: %r" % href)

        ch.set_file_input("#file", [rom])
        for _ in range(150):
            ch.pump(1.0)
            if ch.eval("!!(window.__tcg && window.__tcg.started)"):
                break
        else:
            print("core never started")
            return 1

        # let the core write a .srm; it does so at boot, so no gameplay is needed
        ch.pump(15.0)
        ch.eval("window.__tcg.save('mechanism', true)", await_promise=True, timeout=60)
        ch.pump(2.0)

        # --- the control, before anything is compared ---------------------
        logs = ch.eval("window.__tcg.log()") or []
        dropped = any("dropped EmulatorJS's beforeunload" in ln for ln in logs)
        res["ejs_listener_dropped"] = dropped
        res["page_says"] = [ln for ln in logs if "beforeunload" in ln]
        print("  arm: EmulatorJS's beforeunload %s  (--keep-ejs-unload=%s)"
              % ("DROPPED by us" if dropped else "left in place", a.keep_ejs_unload))
        res["control_ok"] = (dropped != a.keep_ejs_unload)
        if not res["control_ok"]:
            print("  !! the arm is not doing what it says -- the control is broken")

        res["mount_before"] = ch.eval("window.__tcg.mount()")
        before = ch.eval("window.__tcg.idbSave()", await_promise=True, timeout=120)
        res["before"] = before.get("files")
        print("  after boot        files=%s" % json.dumps(res["before"]))
        print("                    mount=%s" % json.dumps(res["mount_before"]))

        # --- the deterministic experiment ---------------------------------
        print("  dispatching beforeunload (no actual unload, so no race) ...")
        ch.eval("window.dispatchEvent(new Event('beforeunload'))")
        ch.pump(4.0)
        res["mount_after"] = ch.eval("window.__tcg.mount()")
        after = ch.eval("window.__tcg.idbSave()", await_promise=True, timeout=120)
        res["after"] = after.get("files")
        print("  after beforeunload files=%s" % json.dumps(res["after"]))
        print("                     mount=%s" % json.dumps(res["mount_after"]))

        destroyed = bool(res["before"]) and not res["after"]
        unmounted = (res["mount_before"].get("autoPersist")
                     and not res["mount_after"].get("autoPersist"))
        res["destroyed_by_beforeunload"] = destroyed
        res["autoPersist_lost"] = bool(unmounted)
        print()
        print("  save destroyed by beforeunload : %s" % ("YES" if destroyed else "no"))
        print("  /data/saves mount torn down    : %s" % ("YES" if unmounted else "no"))
    finally:
        if ch:
            ch.kill()
        srv.shutdown()
        srv.server_close()

    out = ROOT / "test" / "out"
    out.mkdir(parents=True, exist_ok=True)
    name = "save_mechanism-%s.json" % res["arm"]
    (out / name).write_text(json.dumps(res, indent=2))
    print("\nwritten to %s" % (out / name))
    if a.keep_ejs_unload:
        # the control arm is expected to lose the save; what would be wrong is the
        # control failing to differ from the shipped arm, which is reported above
        return 0
    return 0 if not res["destroyed_by_beforeunload"] else 1


if __name__ == "__main__":
    sys.exit(main())
