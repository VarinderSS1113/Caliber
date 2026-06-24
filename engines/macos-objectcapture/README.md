# caliber-recon — macOS local engine

Caliber's free, offline reconstruction engine for macOS. It turns a video (or a folder of
images) into a textured 3D mesh using Apple's **Object Capture** (RealityKit
`PhotogrammetrySession`), running on the Mac's own GPU / Neural Engine — no cloud, no key.

Best for **matte, textured, and round** objects. Shiny / dark / symmetric objects (e.g. a
glossy car) are better served by the Cloud engine.

## Requirements

- Apple Silicon Mac (M1 or newer), macOS 13+.
- Xcode (or the Xcode command-line tools).

## Build

```bash
swift build -c release
```

Then place the binary next to the app:

```bash
cp .build/release/caliber-recon ../../caliber-recon
```

## Use (standalone)

```bash
# from a video → a Quick-Look-able .usdz
.build/release/caliber-recon car.mov car.usdz --detail full --frames 100

# from a folder of images → an .obj (mesh + texture)
.build/release/caliber-recon ./frames part.obj --detail medium
```

- `<input>`  a video (`.mov` / `.mp4`) or a folder of images.
- `<output>` `.usdz` (Quick Look preview), or `.obj` / `.ply` / `.stl` (the tool converts via ModelIO).
- `--detail` `preview | reduced | medium | full | raw` (default `medium`).
- `--frames` how many frames to sample from a video (default `80`).

## Capture tips

Photogrammetry measures real surfaces, so capture quality decides the result:

- Orbit the object slowly and fully; do a second pass from a higher angle. Lots of overlap.
- Even, diffuse light — no harsh reflections.
- Matte, textured objects work best. Shiny, metallic, or transparent surfaces reconstruct
  poorly; a light matte dusting and soft lighting help a lot.
- Keep the object filling most of the frame; avoid motion blur.

The mesh it produces is not yet scaled or print-prepped — feed it to `caliber_prep.py`
(`--auto --ref-mm <length>`) to clean, scale, and export a printable file.
