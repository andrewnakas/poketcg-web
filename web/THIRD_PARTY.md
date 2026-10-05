# Third-party runtime in this page

- **EmulatorJS 4.2.3** (`data/`), GPL-3.0 — https://github.com/EmulatorJS/EmulatorJS
  (licence text: `data/LICENSE.EmulatorJS`). Vendored unmodified; the page patches
  one of its behaviours at run time from `index.html` rather than editing it.
- **libretro Gambatte core** (`data/cores/gambatte-wasm.data`,
  `data/cores/gambatte-legacy-wasm.data`), GPL-2.0 — source
  https://github.com/libretro/gambatte-libretro, built by the EmulatorJS project and
  published at `cdn.emulatorjs.org/4.2.3/data/cores/`.
  sha256 `ad67c7bf57f8f8b62606048e6ea498afac5b5abc76ad8de5f9dfc2a6719374bb` (wasm)
  sha256 `940a7381d51223c0be0c0f98d1a988d42cc98d03c27b392cbd29cb9f357b5f62` (legacy-wasm)

  Both variants ship on purpose. EmulatorJS decides which one to load at run time
  from `data/cores/reports/<core>.json`; that file is not published for this core at
  this version, so the `-legacy` (WebGL1) build is what actually loads, and the
  non-legacy one is kept so a future core report flipping `defaultWebGL2` does not
  404.

`index.html` and `no-phone-home.js` are this project's own code.

**No ROM, and nothing extracted from a ROM, is distributed here.** The page runs
whatever Game Boy Color ROM the person loads from their own disk. It is read with
`FileReader`, handed to the core as an in-memory `File`, and never sent anywhere —
the CSP in `index.html` pins `connect-src` to `'self' blob: data:`, so the browser
itself refuses any cross-origin request, and `no-phone-home.js` refuses and logs one
at run time as well.
