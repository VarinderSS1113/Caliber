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
- **Mesh → CAD solid body.** Turn any watertight scan into a real **STEP solid body** (not a
  mesh) that opens in Fusion/SolidWorks — flat areas become clean editable faces, like
  Fusion's "Convert Mesh". For genuinely mechanical parts there's also an *experimental*
  idealized-primitive path (true cylinders, holes) for crisp input.
- **Export anywhere:** STL, OBJ, PLY, 3MF, a **STEP solid body**, plus *(experimental)* OBJ
  editable parts and idealized-primitive STEP. Import the OBJ into Fusion or Blender to edit
  freely and save in their native formats.
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

> **First launch — "Caliber can't be opened" (unsigned app).** Caliber is open-source and
> **not yet notarised** (Apple charges $99/yr for a Developer ID; that's on the roadmap), so
> macOS blocks the first launch. It's safe to open — here's the one-time bypass:
>
> 1. Double-click **Caliber**. You'll see a warning that it can't be opened — click
>    **Done** / **Cancel** to dismiss it.
> 2. Open **System Settings ▸ Privacy & Security**.
> 3. Scroll down to the **Security** section. You'll see a line like
>    *"Caliber was blocked to protect your Mac."* Click **Open Anyway**.
> 4. Confirm with **Open Anyway** (and Touch ID / password). Caliber launches and macOS
>    remembers it — you won't be asked again.
>
> (A signed + notarised build will skip all of this — see [`docs/BUILD.md`](docs/BUILD.md).)
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

## How to use

A full run takes one capture and a few clicks:

1. **Capture the object.** The result is only as good as the input — the one rule is
   **overlap**. For the free Local engine, put the object on a turntable/stool and take a
   **slow orbit video** (~30s) or many photos, each a small step from the last, of a
   **matte, well-lit** object. For shiny/dark/symmetric objects use the **Cloud** engine
   with a few clean labeled photos (`front`/`back`/`left`/`right`/`top`/`bottom`). See the
   visual **[capture guide](https://VarinderSS1113.github.io/caliber/capture.html)**.

2. **Drop the files in and pick an engine.** Drag your video/photos (or an existing
   `.obj`/`.stl`/`.glb`) onto the app. Leave it on **Auto** and Caliber suggests the right
   engine, or choose **Local** / **Cloud** / **Import** yourself. (Cloud needs your own
   provider API key, pasted once into the Cloud Key field.)

3. **Build.** Click **Build**. Caliber reconstructs the object, cleans it, fills holes, and
   makes it watertight, then shows a 3D preview. If it's not right, you can **Build again**
   or tweak the capture — there's a one-click **Clear** to start over.

4. **Set real-world scale.** The model starts at unit-scale. Enter one known measurement
   (e.g. total height in mm), **or pick two points** on the preview and type the real
   distance between them — the whole model rescales to true millimetres.

5. **Export.** Choose a format and **Download**:
   - **STL / OBJ / PLY / 3MF** — for 3D printing or editing in any tool.
   - **STEP · solid body (from mesh)** — turns the mesh into a real **CAD solid body** (not
     a mesh) that opens in Fusion/SolidWorks; flat areas become clean editable faces. Works
     on any watertight object. *(Install the geometry kernel — `pip install cadquery` — to
     also merge flat regions; otherwise you get a valid faceted solid.)*
   - **OBJ · editable parts** / **STEP · idealized primitives** *(experimental)* — for
     **clean mechanical parts** (flats, holes, true cylinders), Caliber can fit idealized
     shapes. This needs *crisp* input — a machined/printed part or a CAD-exported mesh — and
     is not meant for smooth generative or organic scans.

> **Which CAD export?** If you just want a **solid body** you can edit in Fusion, use
> **STEP · solid body**. The **idealized primitives** path only produces clean cylinders on
> genuinely prismatic, low-noise parts. For freeform shapes (a car panel, a tail-light
> housing) the right workflow is **mesh-as-reference**: export the OBJ, lock its scale, and
> rebuild the part on top of it in CAD.

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

## Scan-to-CAD

A reconstructed mesh is a shell of triangles — a CAD tool opens it as a "mesh body", not a
solid you can push/pull. Caliber offers two ways to get real CAD geometry:

**1. STEP · solid body (from mesh) — works on anything.** This is the equivalent of Fusion's
"Convert Mesh": Caliber takes the watertight mesh and turns it into a genuine B-rep **solid
body** that *follows the surface*. It opens in Fusion/SolidWorks as a **Solid** (not a mesh)
that you can cut, shell, and combine. Flat regions are **merged into clean single faces** you
can edit directly; curved regions stay faceted (still one solid, just more faces). Works on
any watertight object — bottle, bracket, housing.

> Install the geometry kernel — `pip install cadquery` — to enable the flat-face merge
> (`UnifySameDomain`), exactly like Fusion's prismatic conversion. Without it you still get a
> valid faceted solid.

**2. STEP · idealized primitives *(experimental)* — clean mechanical parts only.** For parts
that genuinely *are* made of primitives, Caliber can detect the geometry — **planes,
cylinders, cones/tapers, holes** — and write each as a true analytic solid (a real Ø8
cylinder, a real bore). With the kernel it boolean-joins/cuts them into one stitched solid;
**OBJ · editable parts** writes them as separate named objects.

> Honest limit: idealized primitives only work on **crisp** input — a machined/printed part
> or a CAD-exported mesh, low-noise with sharp edges. On **smooth generative or organic
> scans** (or freeform shapes like a car panel or tail-light housing) there are no true
> primitives to find, so this path is unreliable — use **STEP · solid body** instead, or the
> **mesh-as-reference** workflow: export the OBJ, lock its scale, and rebuild the part on top
> of it in CAD.

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
