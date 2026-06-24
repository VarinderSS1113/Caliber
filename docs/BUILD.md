# Building the Caliber installer (for the publisher)

This is the one-time step that turns the source into a real macOS app and a
`Caliber.dmg` installer. **End users never do this** — they just open the `.dmg`
you produce, drag Caliber to Applications, and launch it. No terminal, nothing to
install: Python and every dependency are frozen inside the app, and it launches as
**Caliber** with its own icon.

A Mac app can only be compiled on a Mac, so this build has to run on macOS — it
can't be produced on Linux/CI without a macOS runner.

## Build it

Requirements: macOS 13+, Python 3 (from [python.org](https://www.python.org/downloads/)).
Xcode is needed **only** to include the free local engine; without it you still get
a fully working Cloud + Import app.

Double-click **`Build Caliber.dmg.command`** (or run `bash packaging/build_dmg.sh`).
It will:

1. create a throwaway build environment and install `py2app`, `numpy`, `pywebview`,
   `pyobjc`, `fal-client`;
2. compile the local engine (`caliber-recon`) if Xcode is present;
3. freeze everything into `dist/Caliber.app` with the Caliber name + icon;
4. ad-hoc code-sign it so it runs;
5. package `Caliber.dmg`.

Upload `Caliber.dmg` to your GitHub release — the website's "Download for macOS"
button already points at `releases/latest`.

## Signing & notarisation (optional but recommended)

The ad-hoc signature lets the app run, but on *other* people's Macs Gatekeeper will
still warn "unidentified developer" (they can right-click ▸ **Open** once to bypass).
To make it install with **zero** warnings — exactly like any app downloaded from the
web — sign and notarise with an Apple Developer ID (Apple Developer Program, $99/yr):

```bash
# 1. Sign with your Developer ID Application certificate
codesign --force --deep --options runtime \
  --sign "Developer ID Application: Your Name (TEAMID)" dist/Caliber.app

# 2. Notarise (submit to Apple, wait for approval)
xcrun notarytool submit Caliber.dmg \
  --apple-id "you@example.com" --team-id TEAMID --password APP_SPECIFIC_PW --wait

# 3. Staple the ticket so it verifies offline
xcrun stapler staple Caliber.dmg
```

After stapling, anyone can open `Caliber.dmg` and run Caliber with no warning at all.

## What ends up in the app

```
Caliber.app/
  Contents/
    Info.plist            name = Caliber, icon = AppIcon, identifier app.caliber.desktop
    MacOS/Caliber         the launcher (frozen Python runtime)
    Resources/
      caliber-recon       native local engine (if Xcode was present at build time)
      …                   numpy, pywebview/pyobjc, fal-client, and the Caliber code
```

At runtime the app starts the loopback-only server and renders the UI in its own
native window — no browser, no terminal, no system Python required.
