# poketcg — Pokémon Trading Card Game (GBC)

**Status: byte-matching build, and a bring-your-own-ROM browser target driven from
the file picker into a real duel, with the battery save proven to survive a page
reload.** Everything below was run on this box on 2026-10-05.

---

## 0. The ROM hash question, answered first

The brief said `~/decompfleet/roms/poketcg.gbc` had sha1
`0f8670a583255cff3e5b7ca71b5d7454d928fc48`, and flagged that the same sha1 had been
recorded for Pokémon Yellow, which cannot be true of two different games.

**Recomputed here. The files are not duplicates, and the poketcg record is the
correct one.**

| file | size | sha1 | md5 | cartridge title |
|---|---|---|---|---|
| `roms/poketcg.gbc` | 1,048,576 | `0f8670a583255cff3e5b7ca71b5d7454d928fc48` | `219b2cc64e5a052003015d4bd4c622cd` | `POKECARD` / `AXQE` |
| `roms/pokeyellow.gb` | 1,048,576 | `cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1` | `d9290db87b1f0a23b89f99ee4469e34b` | `POKEMON YELLOW` |

So the staging record that is wrong is the **Pokémon Yellow** one — it was given
poketcg's hash. Nothing about this project was built against the wrong file: the
header at `$134` reads `POKECARD`, the game code is `AXQE`, and the hash is the one
pret documents.

**Verified against upstream before building anything.** `pret/poketcg`'s `README.md`
and its `rom.sha1` both state
`0f8670a583255cff3e5b7ca71b5d7454d928fc48  poketcg.gbc`, which is the staged file
exactly. `make compare` checks against that same `rom.sha1`.

---

## 1. Upstream revision (pinned)

| | |
|---|---|
| Repo | https://github.com/pret/poketcg |
| Revision | `4afd40f47153d37f0ba88bcc3bb2ce41034c6c92` |
| Date / subject | 2026-09-22 — *Update RGBDS to 1.0.4 (although 1.0.0 still works)* |
| Branch | `master`; this commit was the branch tip when pinned |
| Tags | the repo has none — the commit hash is the only pin available |

Pinned in `build.sh` as `PRET_REV`, which checks that exact commit out, so a re-run
cannot silently drift onto a newer master.

## 2. Toolchain

| Tool | Version | Where it came from |
|---|---|---|
| rgbds | **1.0.4** (rgbasm / rgblink / rgbfix / rgbgfx) | prebuilt static Linux x86_64 release tarball, shared at `~/decompfleet/shared/rgbds-1.0.4/bin` |
| GNU make | 4.4.1 | system |
| gcc | 15.2.0 | system — builds `tools/` (scan_includes, compressor, bgmap, gfx, make_patch) only |
| OS | Ubuntu 26.04, x86_64, 12 cores | |

rgbds was already installed for this fleet by the `pokegold` agent;
`~/decompfleet/shared/rgbds-notes.md` has the recipe. The tarball's sha256 is
`3e19e50ea602e316be709160acaf0a708b1fa6d32d219ec541f244442895dbb7`, and `build.sh`
re-checks it if it ever has to fetch it again. The binaries are fully static.

Building rgbds from source — what the gbdev instructions say to do — is not possible
on this machine: `bison` is not installed and `sudo` needs interactive auth. The
distro package is 1.0.1, which is not the pinned version.

**Which rgbds does this repo want?** Three places say, and here they all agree,
which is worth stating because they do not always:

- `src/rgbdscheck.asm` asserts only a **minimum**: "poketcg requires rgbds v1.0.0 or
  newer". Passing that does not imply a byte match — rgbgfx output and the LZ
  settings move between minor versions.
- `.github/workflows/main.yml` checks out `gbdev/rgbds` at **`ref: v1.0.4`** and
  builds it. That is what pret's CI compares against.
- `.rgbds-version` (a file `pokegold` did not have, and which the fleet notes warn
  to look for) contains **`1.0.4`**.

## 3. Hashes — both targets match

`make DEBUG=1 compare` builds the ROM and the Virtual Console patch and checks them
against upstream's `rom.sha1`. Both pass, from a `git clean -xfd` tree, in 0.6 s on
12 cores:

| Target | sha1 | upstream `rom.sha1` |
|---|---|---|
| `poketcg.gbc` | `0f8670a583255cff3e5b7ca71b5d7454d928fc48` | match |
| `poketcg.patch` (VC) | `3efeae633fde122e2651502aecbb2614131d1327` | match |

### Against the staged dump — byte for byte, not just same-hash

| File | sha1 | md5 | size |
|---|---|---|---|
| `~/decompfleet/roms/poketcg.gbc` (staged) | `0f8670a583255cff3e5b7ca71b5d7454d928fc48` | `219b2cc64e5a052003015d4bd4c622cd` | 1048576 |
| `upstream/poketcg.gbc` (built) | `0f8670a583255cff3e5b7ca71b5d7454d928fc48` | `219b2cc64e5a052003015d4bd4c622cd` | 1048576 |

`cmp` reports them identical.

### DEBUG=1 and plain `make` produce the same ROM

pret's CI compares with `DEBUG=1`, which only adds `-E` to rgbasm (export every
symbol). Both paths were built from clean here and `cmp` says the two `poketcg.gbc`
files are byte-identical, so the symbol-rich build the browser test decodes state
with is the same ROM that matches the cartridge. `build.sh` uses `DEBUG=1` for that
reason. `git diff-index HEAD` is clean after a build, which is the other thing CI
asserts.

## 4. Browser target

`web/` is a static site. Serve it from anywhere; there is no server side.

- **Core:** libretro **gambatte**, via **EmulatorJS 4.2.3**, both vendored under
  `web/data/` (licences and sources in `web/THIRD_PARTY.md`). No emscripten build was
  needed — the core is a GPL-2.0 wasm build published by the EmulatorJS project,
  which is this fleet's established pattern and what `pokegold` and `pokecrystal`
  shipped for the same console.
- **The user supplies the ROM.** File picker or drag-and-drop → `file.arrayBuffer()`
  → SHA-1 via WebCrypto → handed to the core as an in-memory `File`. The one ROM
  pret builds is recognised by hash and named in the status bar; any other 1 MiB GBC
  ROM still loads, labelled as unrecognised. The size check rejects anything that is
  not exactly 1,048,576 bytes with an explanation rather than a silent failure.
- **It is never uploaded, and the CSP is the boundary.** The page carries
  `connect-src 'self' blob: data:` and `form-action 'none'` in a
  `<meta http-equiv="Content-Security-Policy">`. That is enforced by the browser, not
  by this project's JavaScript: no script on the page can reach a remote origin even
  if it tries, there is no race with EmulatorJS's own scripts to win, and a reviewer
  can check one line instead of auditing a monkey-patch. `web/no-phone-home.js` is a
  second, *subordinate* layer — it wraps `fetch`, `XMLHttpRequest`, `sendBeacon`,
  `WebSocket` and `RTCPeerConnection` so that blocked attempts can be *logged* for
  the test output. The assertion that matters is made at the network layer, from
  CDP's own `Network.requestWillBeSent` log, not from anything the page says about
  itself.
- **Saves persist.** `EJS_gameName = "poketcg"` is set explicitly, so the core always
  writes `/data/saves/Gambatte/poketcg.srm` on emscripten's IDBFS mount, which is
  backed by IndexedDB. Without that, `getBaseFileName()` falls through to the ROM's
  filename, and an in-memory `File` would otherwise give a different name each load.
  The page also flushes every 10 s and on `visibilitychange`, because IDBFS's
  `autoPersist` debounces.
- `EJS_disableDatabases = true` and `EJS_CacheLimit = 0` keep the ROM itself out of
  IndexedDB. It exists only in page memory, for the life of the tab. Neither flag
  affects the `.srm`, which lives on the IDBFS mount rather than in EmulatorJS's own
  stores.

### Stock EmulatorJS 4.2.3 destroys the battery save, and this page patches around it

`emulator.js:1092` registers a `beforeunload` listener that fires EmulatorJS's `exit`
event; the handler at `GameManager.js:44` ends with `FS.unmount("/data/saves")`, and
the IndexedDB store behind that mount comes back **empty**. `index.html` therefore
wraps `window.addEventListener` *before* injecting `data/loader.js`, drops that one
listener, and registers its own, which writes and syncs and never unmounts. Every
flush is additionally guarded by a check that `/data/saves` is still an IDBFS mount
with `autoPersist`, because syncing a path that is no longer a mount makes IDBFS's
reconcile delete the remote entries.

**The obvious test for this is a weak one.** Navigating away and coming back is a
race between the teardown's IndexedDB writes and the tab dying, and the page usually
wins it in headless Chrome — the `pokefirered` agent got 8/8 "kept" on a page that
had the bug. So `tools/save_mechanism.py` (copied from that agent) tests it
deterministically instead: it dispatches `beforeunload` as an event without actually
unloading, so nothing is racing, and then reads the mount state and the store. It
runs both arms — `?ejsunload=keep` leaves EmulatorJS's listener in place — and
asserts that the two arms really are different pages before comparing them, because
two arms one query parameter apart are worthless if the parameter silently does
nothing.

## 5. Layout

```
build.sh              fetch/verify rgbds 1.0.4, clone+pin pret/poketcg, build, verify hashes
upstream/             the disassembly (cloned by build.sh, gitignored, never vendored)
web/                  the browser target — static, deployable as-is
  index.html          ROM picker, emulator host, save flushing, read-only test hooks
  no-phone-home.js    runtime egress logger (loaded first; the CSP is the real boundary)
  data/               EmulatorJS 4.2.3 + gambatte core (vendored, GPL — see THIRD_PARTY.md)
test/
  browsercheck.py     the two-stage headless check
  cdp.py              minimal CDP client (hand-rolled websocket; no pip on this box)
  serve.py            threaded static server + one test-only route for the ROM
  duelsave.py         decodes the battery save with the build's own .sym and constants
  gbstate.py          pulls work RAM out of a gambatte libretro save state
  explore.py          scratch probe used to map the opening (not part of the suite)
  out/                screenshots and result JSON (gitignored)
tools/
  save_mechanism.py   the deterministic beforeunload test (method from pokefirered)
  pngstat.py          stdlib PNG brightness reader (from pokefirered)
```

No ROM, and nothing extracted from one, is in this directory or in `web/`.
`.gitignore` excludes every ROM extension (including `*.md` for Genesis, with
`STATUS.md` and `THIRD_PARTY.md` negated), `*.srm`, `*.sav`, `*.state`, `*.patch`,
`upstream/` and `test/out/`.

## 6. What was actually tested

`test/browsercheck.py` — headless Chrome 151 over a hand-rolled CDP client (no pip on
this box, so no playwright or selenium). **Audio off twice: Chrome runs
`--mute-audio` and the page runs `?mute=1`, which sets `EJS_volume = 0`.** Run it
with `python3 test/browsercheck.py`. Screenshots land in `test/out/` (gitignored); I
looked at every one of them.

### Proving the tab is mine

Ten agents share this machine, and a fixed DevTools port lets Chrome attach to
somebody else's browser — `/json/list` then hands you their page and you grade their
game as yours. So:

- `--remote-debugging-port=0`, with the real port read back from `DevToolsActivePort`
  inside this harness's own private `--user-data-dir`. `cdp.py` has no port argument,
  on purpose.
- a fresh nonce in the URL, per stage, which the attached target must echo back from
  `location.href` — and stage 2 additionally asserts that stage 1's nonce is *gone*,
  so a stale document cannot be read as the new one;
- `EJS_emulator.getCore()` must be `gambatte`;
- the SHA-1 **the page computed over the ROM bytes** must equal the SHA-1 of the file
  on disk.

The static server also binds an ephemeral port.

### The run — 45/45 checks

Last full run 2026-10-05, from `git clean` sources and a fresh Chrome profile.
`test/out/result.json` has that run's actual numbers.

| | |
|---|---|
| result | **PASS, 45/45** |
| ROM accepted through the page's own `<input type=file>` (`DOM.setFileInputFiles`) | yes |
| page-computed sha1 == on-disk sha1 | `0f8670a583255cff3e5b7ca71b5d7454d928fc48` |
| core identity | `gambatte` |
| canvas | 960×686 backing and on screen, non-zero |
| frame counter | climbing (21 → 201 over 3 s) |
| muted | `EJS_volume = 0` and `--mute-audio` |
| hosts contacted, per CDP's network log | `127.0.0.1:<test port>` only |
| outbound request bodies | none |
| console errors | none unaccounted for |

### "Played a duel", not "watched a card spin"

Pokémon TCG's title screen runs an attract loop of card artwork, and the opening is
a long scripted sequence in Dr. Mason's laboratory. Screenshots of any of it look
like a game being played. So the gate is the game's own data, decoded with the symbol
table of **the build under test** — `test/duelsave.py` parses `upstream/poketcg.sym`,
`src/constants/duel_constants.asm`, `src/constants/misc_constants.asm`,
`src/constants/charmaps.asm` and the `DuelDataToSave` table in
`src/engine/duel/core.asm` at run time. Nothing is hardcoded from a wiki.

The duel engine writes a snapshot of itself to the battery save: `SaveDuelStateToSRAM`
is called on the player's turn and `PrintDuelMenuAndHandleInput` calls `SaveDuelData`
when it draws the duel menu. Each writes a 4-byte header (valid flag, two checksum
bytes, duel type) and 826 bytes of duel state, and the cartridge verifies that
checksum itself on load (`ValidateSavedDuelDataFromHL`). The test **recomputes it**
with the game's own algorithm and seed (`SAVE_DUEL_CHECKSUM_SEED = $2345`,
`e -= byte; d ^= byte` over `SAVE_DUEL_DATA_SIZE - 6` bytes) and only accepts a record
that reproduces the stored pair.

What the saved record decoded to in the last run, and why each line rules out a demo:

```
slot                 sBackupCurrentDuel (bank 2, $bc00) and a rotating sDuelBuffer
                     at bank 3 $a010 -- both written by SaveDuelStateToSRAM
valid flag           1
checksum stored      [90, 200]  == recomputed [90, 200]   over 820 bytes
wDuelType            $80   = DUELTYPE_PRACTICE
wIsPracticeDuel      1
wDuelInitialPrizes   2     = PRIZES_2, which is what the lab script passes to start_duel
wPlayerArenaCard     $06   hp 40      (a Pokemon is in play, HP a legal multiple of 10)
wOpponentArenaCard   $04   hp 50
wPlayerPrizes        3     = %11, i.e. both prize cards still face down
wPlayerNumberOfCardsInHand  6
wWhoseTurn           $c2   = HIGH(wPlayerDuelVariables), i.e. PLAYER_TURN
```

`wWhoseTurn` is checked against `HIGH(wPlayerDuelVariables)` resolved from the symbol
table rather than against a literal `$c2`, which is how the game defines `PLAYER_TURN`.

**Two independent views of the duel agree.** The record above is written by the
game's own `SaveDuelData` into SRAM. The same fields were also read out of live work
RAM, by parsing gambatte's libretro save state (`test/gbstate.py`) and addressing it
with the same symbols. Those are entirely different code paths — the cartridge's
save routine versus the emulator's serialiser — and they match, which is what makes
the addressing trustworthy rather than merely self-consistent. The state parser is
separately cross-checked against the core's own `.srm` over all 32 KiB, taken in one
synchronous call so the core cannot run in between.

### Saves survive, and the proof is not the reload-soak

Two independent demonstrations:

**1. The deterministic mechanism test** (`tools/save_mechanism.py`), both arms, on
this page:

| | shipped page | `?ejsunload=keep` (control) |
|---|---|---|
| page logged which arm it took | "dropped EmulatorJS's beforeunload" | "kept EmulatorJS's beforeunload" |
| IndexedDB after boot | `{poketcg.srm: 32768}` | `{poketcg.srm: 32768}` |
| after `dispatchEvent(new Event("beforeunload"))` | `{poketcg.srm: 32768}` | **`{}`** |
| `/data/saves` mount afterwards | `mounted, autoPersist: true` | `mounted, autoPersist: **false**` |

The control arm reproduces the bug on demand, the shipped arm does not, and the arms
were confirmed to differ before the comparison was believed.

**2. A real reload, in the browsercheck.** Stage 1 plays into the duel and flushes;
the harness then navigates away to `about:blank` **in the same tab** (closing the tab
over CDP can skip `beforeunload` altogether, which is the one path that matters) and
back with a fresh nonce. Stage 2 reads the IDBFS database **before any ROM is
picked**, so no core exists that could rewrite what is being measured, and the
database name is enumerated with `indexedDB.databases()` rather than guessed —
`indexedDB.open()` creates a database that does not exist, so a wrong guess invents
an empty one and then reports "the save was lost".

```
/data/saves/Gambatte/poketcg.srm   32768 bytes
sha1 07de1d7e2631b50f43b6f7e26f58bdf3877a522c  ->  07de1d7e2631b50f43b6f7e26f58bdf3877a522c
duel record decodes identically before and after, slot and checksum included
```

The page then loads the ROM again and the running core reads the same valid duel
record back out of the restored SRAM, so the bytes are not merely sitting in storage
— the core has them mapped as the cartridge's RAM.

**What this does *not* show is the game's own CONTINUE screen.** On that second load
the game starts the opening again. That is correct behaviour for how far the test
plays: Pokémon TCG writes its *general* save data (name, collection, decks) at points
later than the first turn of the tutorial duel, and `LoadAndValidateDuelSaveData`
also wants a valid `sCurrentDuel` in bank 0, which only
`PrintDuelMenuAndHandleInput` writes. The duel record this test gates on comes from
`SaveDuelStateToSRAM`, in bank 2 and bank 3. So: the battery save demonstrably
survives the reload byte for byte, and the game demonstrably does not yet have enough
saved to offer a resume. Both statements are true and neither should be rounded into
the other.

### Three things that produced confidently wrong answers first

Recorded because each one looked like a real failure and was not:

1. **"The game never wrote a duel record."** It had. `SaveDuelData` writes to the
   *address* of `sCurrentDuel` in whatever SRAM bank is currently selected, so
   `SaveDuelStateToSRAM` — which selects bank 2 first — lands on
   `sBackupCurrentDuel`, and a third copy goes into one of four rotating
   `sDuelBuffer`s on bank 3. Looking only at `sCurrentDuel` found nothing while the
   game was plainly in a duel. The test now scans the whole 32 KiB and accepts only
   offsets whose checksum recomputes, then names the slot from the symbol table.
2. **A stale SRAM read.** `getSaveFile(false)` returns the FS copy, which is only
   refreshed by `saveSaveFiles()`. Polling it during play returns whatever was last
   flushed — minutes old — so the duel record was missed for a second reason at the
   same time. The live read now pushes the core's SRAM into the FS copy first. The
   *read-only* form is still the one used after a reload, because the writing form
   would put the core's not-yet-restored SRAM over a good save and then truthfully
   report blanks.
3. **"It is in a duel" was not enough.** Two runs sat in the duel for 300 s with
   `wPlayerArenaCard` stuck at `$ff` — no Pokémon in the arena at all. The duel opens
   by asking the player to choose a basic Pokémon from a hand whose first entry is a
   Trainer card, and A on that does nothing forever. Mashing A alone also cannot clear
   the opening: Sam's rules multichoice re-opens itself after every answer, so A loops
   there indefinitely (383 s, in the run that found it). The harness mixes B into the
   dialogue — safe, because text boxes accept A *or* B, Yes/No menus ignore B entirely,
   and a multichoice's cancel value is the exit — and the D-pad into the duel.

## 7. What was NOT tested — read this before trusting anything above

- **Only the opening was played, and only up to the first turn of the tutorial
  duel.** The duel reached is the forced practice duel against Sam in Dr. Mason's
  laboratory (`wDuelType = DUELTYPE_PRACTICE`, `wIsPracticeDuel = 1`). It is a real
  duel in the real duel engine — arena, bench, hand, prizes, HP, turn ownership — but
  it is the scripted tutorial, and **no duel was won, and no AI duel was ever
  started.** `wDuelType` for a normal opponent is `$80 + deck id`; nothing here ever
  produced one. The clubs, the badges, booster packs, deck building, the Card Pop!
  and link features, the Grand Masters and the ending are all completely unexercised.
- **No audio was ever enabled**, so audio output is entirely unverified. The fleet
  rules require muting; that means "sound works" is not a claim I can make.
- **Only headless Chrome 151 on Linux.** No Firefox, no Safari, no iOS, no Android.
  EmulatorJS's on-screen touch gamepad is configured but was never pressed, and no
  physical gamepad was tested.
- **The OS file dialog was not driven.** The test hands real on-disk files to the real
  `<input type=file>` with `DOM.setFileInputFiles`, so the page's own change handler
  runs end to end, but the native picker UI itself is untested. Drag-and-drop is wired
  to the same entry point and was likewise never driven.
- **GitHub Pages deployment is untested.** The site was only ever served from
  `http://127.0.0.1` by `test/serve.py`. One behavioural difference to expect:
  EmulatorJS 4.2.3 only pings `cdn.emulatorjs.org` when the host is localhost
  (`src/emulator.js:207`), so the test logs one refused request that would not happen
  in production.
- **The Virtual Console patch builds and matches its documented sha1, but was never
  applied or run.** Only `poketcg.gbc` was booted.
- **EmulatorJS save *states* were not tested.** The deliverable here is the battery
  save (SRAM on the IDBFS mount), and `EJS_disableDatabases = true` switches off the
  IndexedDB stores EmulatorJS keeps its own save states in. Quick-save/load-state from
  its menu is therefore expected not to persist, and that was not exercised either way.
- **Per-core save directory:** the save lands in `/data/saves/Gambatte/`, a
  subdirectory EmulatorJS's `mountFileSystems()` does not create. It was created
  successfully by the core in every run here; the page also pre-creates it from
  `getSaveFilePath()` as insurance, but the failure mode that insurance guards against
  was never observed, so it is untested.
- **One known-harmless 404 remains:** `data/cores/reports/gambatte.json`. EmulatorJS
  looks for an optional per-core build report that the CDN does not publish for this
  core at this version; the `-legacy` (WebGL1) core variant is what loads as a result.
  It is whitelisted in the test by URL from the network log, not fixed, because
  inventing that file could change which core variant loads.
- **The game's own CONTINUE path was never reached.** After the reload the ROM starts
  the opening again, because the test stops at the first turn of the tutorial duel and
  the game has not yet written its general save data (name, collection, decks) or a
  bank-0 `sCurrentDuel`. What is proven is that the 32 KiB battery save survives a real
  navigation away and back byte for byte, with the duel record inside it decoding
  identically — not that the title screen offers to resume.
- **No long-running session.** The longest continuous run was about 20 minutes.
  Nothing is known about memory growth, or about leaving the tab open for hours.

## 8. Proposed catalog fragment

Not merged — `src/data/games.json` is the user's to edit. Field names follow
`~/decompfleet/shared/catalog-schema.md` (the real 41-record schema), not my guesses.

Four values I could not read off anything and had to choose — please check them:
`year` (this is the (U) ROM, released in North America in 2000; the game's own
copyright screen says 1998, which is the Japanese release), `shelf` (I used
`"decomp"`; the only example in the schema doc was `"cleanroom"`, which this is not),
`embedUrl` (depends where `web/` is hosted) and `complete` (see the note below).

```json
{
  "id": "pokemon-trading-card-game",
  "title": "Pokémon Trading Card Game",
  "aliases": [
    "poketcg",
    "Pokemon Trading Card Game",
    "Pokémon TCG",
    "Pokemon Card GB"
  ],
  "year": 2000,
  "genre": "Card Battler",
  "kind": "Disassembly",
  "mode": "files",
  "engine": "The real ROM, built byte-for-byte from the pret/poketcg disassembly, running on the libretro Gambatte core (EmulatorJS 4.2.3, WebAssembly)",
  "description": "Collect cards, build decks and duel your way to the Grand Masters.",
  "overview": "Pokémon Trading Card Game built from the pret/poketcg disassembly with rgbds 1.0.4, byte-identical to the retail cartridge, running in the browser on a WebAssembly Game Boy Color core. You supply your own ROM: it is read with FileReader, kept in the tab's memory and never uploaded, and the page recognises the cartridge by its SHA-1. Your collection, your decks and a duel in progress are kept in this browser's storage and survive a reload.",
  "source": "https://github.com/pret/poketcg",
  "engineLicense": "EmulatorJS is GPL-3.0 and the Gambatte core is GPL-2.0; the pret/poketcg disassembly is unlicensed research. See web/THIRD_PARTY.md.",
  "assetLicense": "No game data is distributed. Everything comes from the ROM you load.",
  "assetRequirements": "You supply your own Pokémon Trading Card Game ROM (1,048,576 bytes, sha1 0f8670a583255cff3e5b7ca71b5d7454d928fc48 — the same ROM this project's build produces). It is read in the browser and never uploaded.",
  "controls": [
    "Arrow keys — D-pad",
    "X — A",
    "Z — B",
    "Enter — Start",
    "Shift — Select",
    "Gamepad — standard mapping",
    "Touch — on-screen Game Boy pad on phones and tablets"
  ],
  "saveInstructions": "The game saves by itself as you play — your name, your collection, your decks and a duel in progress are written to the cartridge's battery save, which is kept in this browser's storage and survives a reload. Clearing site data removes it.",
  "limitations": [
    "Only the opening was play-tested: the intro, naming, the starter deck and the first turn of the tutorial duel against Sam. No duel was won and no opponent duel was ever started, so nothing past Dr. Mason's laboratory has been exercised.",
    "Saving was verified at the storage level — the battery save survives a reload byte for byte — but the test never played far enough for the game itself to offer a CONTINUE, so resuming from the title screen is untested.",
    "Audio is unverified: every test ran muted.",
    "Only tested in headless Chrome on Linux. Firefox, Safari, iOS, Android, gamepads and the on-screen controls are untested.",
    "EmulatorJS's own quick-save states are switched off so the ROM is never copied into browser storage; the cartridge's battery save is what persists.",
    "Link play — trading and link duels — is not possible in a single browser tab."
  ],
  "inputs": [
    "Gamepad",
    "Keyboard",
    "Touch"
  ],
  "downloadMB": null,
  "complete": true,
  "color": "#e8b923",
  "shelf": "decomp",
  "platform": "Game Boy Color",
  "launch": "embed",
  "touch": true,
  "embedUrl": "https://andrewnakas.github.io/poketcg/",
  "verification": {
    "status": "smoke-tested",
    "date": "2026-10-05",
    "browsers": ["Chrome 151 (headless, Linux)"],
    "scope": "The ROM builds byte-identical to the retail cartridge (sha1 verified against pret's rom.sha1 and against the staged dump). In the browser: loaded through the page's own file picker, ran at 60 fps, played the opening through naming and the starter deck into the tutorial duel in Dr. Mason's laboratory, then reloaded the page and read the battery save back. The duel was confirmed from the game's own checksummed duel record in SRAM, decoded with the symbol table of the build under test and cross-checked against live work RAM. Nothing past the first turn of that duel was played; audio was muted throughout.",
    "playthrough": false
  }
}
```

On `complete`: set to `true` because the ROM is byte-identical to the retail
cartridge — the *game* is complete by construction and there is no porting work left
undone. What is partial is my **testing**, which is why `verification.playthrough` is
`false` and the caveats are in `limitations`. If `complete` means "somebody finished
it in this runtime", set it to `false` — I did not.
