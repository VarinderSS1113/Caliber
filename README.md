<p align="center">
  <img src="assets/caliber-icon-blue.png" width="96" alt="Caliber">
</p>

<h1 align="center">Caliber</h1>

<p align="center"><em>Photos or video in. A printable, correctly-sized 3D model out.</em></p>

---

Caliber turns photos or a video of a real object into a clean, watertight, **dimensionally
accurate** 3D model you can print or edit — skipping the slowest part of fabrication:
modelling the thing from a blank viewport. It is **free and open-source**, runs entirely on
your own machine, and stores nothing on anyone's servers.

## Highlights

- **Two reconstruction engines, one workflow.**
  - **Local (free):** Apple Object Capture photogrammetry — excellent for matte, textured,
    or round objects, fully offline. **Mix a video and photos together** for more coverage.
  - **Cloud (bring-your-own-key):** hosted generative reconstruction for the hard cases —
    glossy, dark, or symmetric objects that photogrammetry can't handle. Feed it labeled
    photos, or **a video — Caliber pulls the sharpest frames** and routes them to the model.
- **Real-world scale.** Enter one measurement, **or pick two points on the finished model**,
  and it comes out correct in millimetres.
- **Automatic cleanup.** De-junks, fills holes, makes the mesh watertight, and — for round
  objects — rebuilds a crisp surface of revolution.
- **Scan-to-CAD (beta).** For mechanical/primitive parts, Caliber detects the shapes —
  planes, cylinders, cones/tapers, holes, domes — and exports them as **editable parts** or
  as a **STEP B-rep solid** you can modify in Fusion/SolidWorks (not just a frozen mesh).
- **Export anywhere:** STL, OBJ, PLY, 3MF, **OBJ split into editable parts**, and **STEP**.
  Import the OBJ into Fusion or Blender to edit freely and save in their native formats.
- **A real desktop app.** Ships as a signed-able `Caliber.app` in a drag-to-Applications
  `.dmg` — its own name and icon, no terminal, no browser tab.
- **Local-first & private.** Nothing leaves your computer unless you explicitly use the
  Cloud engine, in which case your selected photos go to the provider you bring a key for.
  The loopback server is guarded by a per-launch token and Host/Origin checks; **no telemetry.**

## How it works (full whitepaper)

For everything — how both engines reconstruct geometry, the science behind them, how scale
and cleanup work, and detailed **capture & lighting best practices** — read the
**[whitepaper](docs/WHITEPAPER.md)**.

## Quick capture tips

The result is only as good as the capture. The one rule that matters is **overlap** — many
frames that each share surface with the next. In short:

- **Local (video):** put the object **on a surface** (a turntable, lazy-susan, or stool) and
  spin it slowly — ~30s per lap — at two heights. Matte, well-lit objects only. You can drop
  **a video and photos together** for extra coverage.
- **Cloud (photos):** the object **alone**, plain background, a few clean angles. **Name the
  files** `front`/`back`/`left`/`right`/`top`/`bottom`. (Cloud can also pull frames from a
  video for you.)
- **Lighting:** soft and diffuse — no hard light or moving glints. **Matte any shine** (powder,
  dry shampoo) — or matte it digitally with an AI image tool — for glossy/metallic objects.
- **Scale:** measure the object once, or pick two points on the result, so it's true mm.

There's a full visual **[capture guide](https://VarinderSS1113.github.io/caliber/capture.html)**
(and the [whitepaper](docs/WHITEPAPER.md) for the science).

## Install & run

**macOS — install from `Caliber.dmg`.** Open the disk image, drag **Caliber** into your
**Applications** folder, and launch it like any other app. It opens in its own window
(its own name and icon — no browser, no terminal) and has Python and all dependencies
built in, so there's nothing else to install.

> First launch from an unsigned build: right-click **Caliber** ▸ **Open** once to clear
> Gatekeeper. (A signed + notarised build skips this — see [`docs/BUILD.md`](docs/BUILD.md).)
> Windows app: coming soon.

**Build the installer (publisher).** A Mac app must be compiled on a Mac. Double-click
**`Build Caliber.dmg.command`** once on macOS (Python 3 required; Xcode only if you want
the free local engine included) and it produces `Caliber.dmg`. Full details, plus signing
and notarisation, are in [`docs/BUILD.md`](docs/BUILD.md).

**Run from source (developers).** The app is a thin native window over a loopback-only
server, so you can also run it directly:

```bash
pip3 install numpy pywebview      # pywebview is only needed for the native window
python3 caliber_desktop.py        # native window
python3 caliber_app.py            # or browser at http://127.0.0.1:8765
```

## How it works

```
capture/import ─▶ reconstruct ─▶ clean + make watertight ─▶ scale ─▶ preview ─▶ export
                    │
                    ├── Local  : engines/macos-objectcapture  (free, offline)
                    └── Cloud  : your generative provider key  (paid, opt-in)
```

| Input | Engine | Cost |
|---|---|---|
| an existing `.glb` / `.obj` / `.stl` | **Import & clean** | free |
| video / photos of a matte or round object | **Local** (Object Capture) | free |
| photos of a shiny / symmetric / complex object | **Cloud** (generative) | you pay the provider |

Pick **Auto** and Caliber routes for you.

## Components

| File | Role |
|---|---|
| `caliber_desktop.py` | native-window launcher (pywebview) — the app's entry point |
| `caliber_app.py` | the local server — UI + pipeline orchestration (Python stdlib only) |
| `caliber_prep.py` | cleanup, watertight repair, real-world scaling, primitive detection, mesh export |
| `caliber_cad.py` | scan-to-CAD: turns detected primitives into editable **STEP** B-rep solids |
| `caliber_gen.py` | generative cloud reconstruction (bring your own API key) |
| `engines/macos-objectcapture/` | the macOS local engine (Swift / Object Capture; also pulls Cloud frames from video) |
| `packaging/` + `Build Caliber.dmg.command` | one-time build of `Caliber.app` + `Caliber.dmg` |
| `assets/AppIcon.icns` | the app icon |

## Building the macOS local engine

The local engine is a small Swift command-line tool. On a Mac with Xcode:

```bash
cd engines/macos-objectcapture
swift build -c release
cp .build/release/caliber-recon ../../caliber-recon   # place next to the app
```

See `engines/macos-objectcapture/README.md`. (Windows/Linux local engine via
COLMAP/AliceVision is on the roadmap; until then, those platforms use Import or Cloud.)

## Bring-your-own-key (Cloud engine)

The app is free; the optional Cloud engine uses **your own** API key from a hosted
generative-3D provider. Create an account, add credit, and paste the key into the app's
Cloud Key field. It is stored only on your computer and used only when you choose the Cloud
engine. You pay the provider directly, per generation.

## Scan-to-CAD (beta)

A reconstructed mesh is a shell of triangles — no CAD tool lets you grab a face and change a
radius on it. Caliber can take the next step on **mechanical / primitive-shaped** parts:

- It detects the geometry — **planes, cylinders, cones/tapers, holes (bores), domes** — and
  reports each with dimensions.
- **OBJ · editable parts** writes each detected piece as its own named object so you can
  select and edit them individually in Blender/Fusion.
- **STEP · editable solid** writes real B-rep solids (true cylinders, cones, boxes, holes)
  that open in Fusion/SolidWorks as genuine editable geometry. Solids are named
  (`cylinder_1`, `hole_1`, `taper_1`, `dome_1`); to merge/cut them, use **Combine → Join**
  for bodies and **Combine → Cut** for holes (a real boolean needs the CAD tool's kernel).

Honest limits: this works where the object *is* made of primitives. A **freeform** shape (a
car body, a sculpted housing) has no clean shapes to fit, so STEP refuses it and tells you to
use the mesh instead. For those, the right workflow is **mesh-as-reference**: import the
mesh, lock its scale, and rebuild the part on top of it.

## Export & editing

Export formats: **STL, OBJ, PLY, 3MF**, plus **OBJ (editable parts)** and **STEP**. Import
the OBJ into Blender, Fusion, SolidWorks, ZBrush, or any 3D tool to reshape it and save in
that tool's native format. (`.f3d`/`.blend` are proprietary and can only be written by
Fusion/Blender themselves — the standard Import → edit → Save As workflow covers this.)

## Security & privacy

Caliber runs a server on loopback (`127.0.0.1`) only, validates request host/origin to
block cross-site access, streams uploads with size limits, sanitises inputs, and never logs
or transmits your API key except to your chosen provider. See
[`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md) and [`SECURITY.md`](SECURITY.md).

## Contributing

Issues and pull requests are welcome. Please run the app locally and describe the object
type when reporting reconstruction quality, and follow responsible disclosure in
`SECURITY.md` for any security report.

## License

[MIT](LICENSE).
