# Changelog

All notable changes to Caliber. Dates are approximate.

## 1.1 — June 2026

### Added
- **Scan-to-CAD (beta).** Detects planes, cylinders, cones/tapers, holes (bores), and domes
  and exports them as **editable parts (OBJ)** or real **STEP B-rep solids** editable in
  Fusion/SolidWorks. Refuses STEP on mostly-freeform objects (use the mesh instead).
- **STEP** and **OBJ editable-parts** export formats (alongside STL/OBJ/PLY/3MF).
- **Two-point measurement** — click two points on the result to set exact real-world scale;
  plus a known-dimension option (longest side / width / height / depth).
- **Native macOS app** — ships as `Caliber.app` in a drag-to-Applications `.dmg`, own name
  and icon, no terminal or browser tab (pywebview + py2app).
- **Mix video + photos** for the local engine; feed a **video to the Cloud engine** and it
  pulls the sharpest frames automatically.
- **Smart capture guidance** in the UI (file-aware engine tips, Cloud photo-naming for
  Hunyuan/TRELLIS) and a full visual **capture guide** page.
- Native **Save dialog** for downloads, **Clear / Start-over**, plain-language errors, and a
  **"first draft"** nudge after each build.
- Website **Examples** gallery and **Capture guide** pages; feature/bug feedback links.

### Changed
- Reconstruction tools run **in-process** (freeze-safe) instead of spawning subprocesses.
- Scale is set **after** building (on the result), not before.

### Security
- Per-launch **session token** required on all API calls (plus Host/Origin checks).
- 3D-preview libraries **vendored locally** (no CDN at runtime); pinned build dependencies.
- fal.ai key stored owner-only (`0600`); thread-safe shared state; output HTML-escaped.

### Fixed
- Local-engine output decoded as UTF-8 (fixed a crash in the packaged app on engine logs).

## 1.0 — June 2026

- Local photogrammetry engine (Apple Object Capture) and generative Cloud engine (BYO key).
- Auto engine routing; one-measurement real-world scaling; watertight cleanup & hole-filling.
- Surface-of-revolution rebuild for round objects; live 3D preview.
- Export to STL, OBJ, PLY, 3MF; import & clean existing `.glb`/`.obj`/`.stl`.
- Loopback-only server with Host/Origin validation; full whitepaper & capture guide.
