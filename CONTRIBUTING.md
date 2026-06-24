# Contributing to Caliber

Thanks for your interest in improving Caliber. It's free and open-source (MIT), and
contributions are welcome.

## Reporting bugs & requesting features

Please use the feedback form:
**https://docs.google.com/forms/d/e/1FAIpQLSde7zEqapYnMvW7v8M67SwK0krQA3UnaRefzA4YcxNhdIJh2Q/viewform**

When reporting a reconstruction issue, it helps to include: the **object type** (matte/shiny,
size), the **engine** used (Local / Cloud), and how it was captured (video vs photos, how many).

## Running from source

Caliber is mostly Python (standard library + numpy). No build step is needed to develop:

```bash
pip3 install numpy pywebview        # pywebview only for the native window
python3 caliber_desktop.py          # native window
python3 caliber_app.py              # or use it in a browser at http://127.0.0.1:8765
```

The optional Cloud engine needs `fal-client` and your own fal.ai key (entered in the UI).
The local engine is a small Swift tool — see `engines/macos-objectcapture/README.md`.

## Project layout

| File | Role |
|---|---|
| `caliber_desktop.py` | native-window launcher (pywebview) — app entry point |
| `caliber_app.py` | local loopback server: UI + pipeline orchestration |
| `caliber_prep.py` | mesh cleanup, watertight repair, scaling, primitive detection |
| `caliber_cad.py` | scan-to-CAD: primitives → editable STEP B-rep solids |
| `caliber_gen.py` | generative cloud reconstruction (bring-your-own-key) |
| `engines/macos-objectcapture/` | the macOS local engine (Swift / Object Capture) |
| `packaging/` | py2app config + `build_dmg.sh` to produce `Caliber.app` / `Caliber.dmg` |
| `docs/` | the website (`index.html`, `capture.html`, `examples.html`) + whitepaper + audit |

## Building the macOS app

```bash
# one-time, on a Mac (Xcode needed only to include the local engine)
bash packaging/build_dmg.sh          # or double-click "Build Caliber.dmg.command"
```

See `docs/BUILD.md` for signing and notarisation.

## Pull requests

- Keep changes focused and described clearly.
- For reconstruction/geometry changes, test on a couple of **real** objects (not just
  synthetic shapes) and say which.
- Follow responsible disclosure in `SECURITY.md` for anything security-related.

## Code of conduct

Be respectful and constructive. That's it.
