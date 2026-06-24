# Caliber — How It Works

_A complete technical guide to the product, the reconstruction pipeline, the science behind
each engine, and the capture and lighting practices that produce the best results._

---

## Table of contents

1. [What Caliber does](#1-what-caliber-does)
2. [The pipeline at a glance](#2-the-pipeline-at-a-glance)
3. [Engine 1 — Photogrammetry (local, free)](#3-engine-1--photogrammetry-local-free)
4. [Engine 2 — Generative reconstruction (cloud, bring-your-own-key)](#4-engine-2--generative-reconstruction-cloud-bring-your-own-key)
5. [Choosing the right engine (and why)](#5-choosing-the-right-engine-and-why)
6. [Real-world scale](#6-real-world-scale)
7. [Cleanup & print-prep](#7-cleanup--print-prep)
8. [Surface-of-revolution rebuild](#8-surface-of-revolution-rebuild)
9. [Capture best practices — video](#9-capture-best-practices--video)
10. [Capture best practices — photos](#10-capture-best-practices--photos)
11. [Lighting](#11-lighting)
12. [Surfaces & object suitability](#12-surfaces--object-suitability)
13. [Export & editing](#13-export--editing)
14. [Accuracy & expectations](#14-accuracy--expectations)
15. [Privacy & security](#15-privacy--security)
16. [Limitations & roadmap](#16-limitations--roadmap)
17. [Glossary](#17-glossary)

---

## 1. What Caliber does

Caliber converts ordinary photos or a phone video of a physical object into a clean,
watertight, correctly-scaled 3D model — a strong **first draft** you can print immediately
or refine in CAD. It removes the hardest, slowest step of digital fabrication: mapping an
object out and modelling it from a blank viewport.

The guiding principle: **the more and better the input, the better the result.** A careful
video orbit, good light, and one real measurement turn a rough capture into a printable part.

---

## 2. The pipeline at a glance

```
 capture / import
        │
        ▼
 reconstruct ─────────────┐
   • Local (photogrammetry)│  measures real geometry from overlapping views
   • Cloud (generative)    │  infers coherent geometry from one or more images
        │                  │
        ▼                  │
 clean + make watertight   │  weld, drop junk, fill holes, fix normals
        │                  │
        ▼                  │
 apply real-world scale    │  one measurement → true millimetres
        │                  │
        ▼                  │
 preview (3D) ─────────────┘
        │
        ▼
 export  →  STL · OBJ · PLY · 3MF
```

Every stage after capture runs locally on your machine. The only step that can leave your
device is the optional Cloud engine, which sends the photos you choose to a generative
provider using your own key.

---

## 3. Engine 1 — Photogrammetry (local, free)

**What it is.** The local engine uses Apple's Object Capture (RealityKit
`PhotogrammetrySession`) — a production photogrammetry pipeline accelerated on Apple Silicon.

**How photogrammetry works.** It *measures* geometry from many overlapping views:

1. **Feature detection & matching.** Distinctive points (corners, texture, edges) are found
   in each frame and matched across frames.
2. **Structure-from-Motion (SfM).** From those matches the system solves where the camera
   was for every frame and builds a sparse 3D point cloud.
3. **Multi-View Stereo (MVS).** With camera poses known, dense depth is computed and fused
   into a dense surface (mesh), then textured from the photos.

Because it triangulates real surface points seen from multiple angles, the result is
**dimensionally honest** and captures the object's true, asymmetric form (including the real
back, if you filmed it).

**What it needs to succeed.** Trackable surface detail, heavy overlap between frames, and
diffuse (non-reflective) surfaces. Its failure modes are physical, not bugs:

- **Low texture** (blank, glossy black panels): few features to match → holes, mush.
- **Specular / reflective surfaces:** highlights move as the camera moves, so the same
  point looks different from each angle and won't match → noise and collapse.
- **Transparent surfaces** (glass): no stable surface to reconstruct.
- **Symmetry + repetition:** if the left and right sides look nearly identical, the solver
  can confuse one side for the other and **fold them together** ("symmetry collapse").
- **Thin features** and deep concavities are easily lost.

This is why a glossy, symmetric model car is one of the hardest possible subjects for
photogrammetry, while a matte, textured, asymmetric object reconstructs beautifully.

---

## 4. Engine 2 — Generative reconstruction (cloud, bring-your-own-key)

**What it is.** A hosted neural image-to-3D model (e.g. Hunyuan3D, TRELLIS) that you reach
with your own API key. It is the engine for the objects photogrammetry can't do.

**How it works.** Instead of matching features between views, a network trained on large 3D
datasets has *learned what objects look like in 3D*. Given one or more images, it generates
a coherent 3D shape consistent with the picture. Because there's no cross-view feature
matching, it is **immune to the symmetry and gloss problems** that break photogrammetry — it
"recognises a car" and produces a whole car.

**The trade-offs, stated honestly:**

- **It infers unseen geometry.** Surfaces the camera didn't show (the back, the underside)
  are plausible guesses, not measurements. **Multi-view input** (front/back/left/right, and
  optionally top/bottom) dramatically reduces guessing and improves fidelity.
- **Photos only, up to six views.** The Cloud engine takes still photos, **not video**, and
  uses up to six named views. **Name your files** `front`, `back`, `left`, `right`, `top`,
  `bottom` so each photo lands on the right view (otherwise upload order is assumed). The
  count picks the model automatically: one photo → Hunyuan single-image; **2–4 sides →
  Hunyuan multi-view**; **5–6 views, or any set including top/bottom → TRELLIS**, which uses
  all of them.
- **No real-world scale.** Output is unit-scale; Caliber applies your measurement afterward.
- **It reconstructs everything in the frame** — including a hand holding the object. Always
  give it the object **alone**.

**Single vs multi-view.** One photo gives a recognisable model with an inferred back.
Several clean angles give a markedly better, more faithful model. Caliber routes multiple
views to a multi-image model automatically.

---

## 5. Choosing the right engine (and why)

| Object | Best engine | Reason |
|---|---|---|
| Matte, textured, round, or organic (bottle, tool, shoe, figurine, rock) | **Local photogrammetry** | Plenty of features; measured, to-scale, free, offline |
| Glossy, dark, metallic, transparent, or symmetric (a shiny car) | **Cloud generative** | Immune to gloss/symmetry collapse; coherent result |
| You already have a `.glb` / `.obj` / `.stl` | **Import & clean** | Skip reconstruction; just clean + scale |

Caliber's **Auto** mode inspects the input and routes for you. It also detects whether the
reconstructed object is a **surface of revolution** and, if so, rebuilds it cleanly (§8).

---

## 6. Real-world scale

Any single-camera reconstruction is inherently **scale-ambiguous**: a small object up close
and a large object far away project to exactly the same image, so the model comes out in
arbitrary units. The fix is one external reference.

Caliber asks for **one real measurement** — e.g. "the object is 105 mm tall" — and scales the
whole model so that dimension is exact in millimetres. For round objects you can pin the
**body diameter** (a more reliable anchor than total height), and pin **height
independently** if a scan is slightly stretched. The result carries true millimetre
dimensions into the slicer or CAD tool.

There are two ways to give that measurement:

- **Up front:** type a known dimension (and which axis it runs along) before building.
- **Two-point measurement (recommended):** build first, then **click two points on the 3D
  result** and type the real distance between them. Caliber measures the distance between
  the picked points and scales the whole model uniformly so it matches. Because you measure
  the exact feature you have a caliper reading for — the width of a boss, the span between two
  holes — this is more accurate than scaling by an axis-aligned bounding box, and you can
  re-measure against any feature until it's right.

A reference object captured in the same shot (a coin, a ruler, a printed marker) is an
equally valid way to supply that one measurement.

---

## 7. Cleanup & print-prep

Raw reconstructions are rarely print-ready. Caliber's prep stage runs automatically on every
result:

1. **Weld** near-duplicate vertices so the mesh is properly connected.
2. **Keep the largest connected piece** — removes floating fragments and stray junk (e.g. a
   hand fragment) while preserving the object.
3. **Fill holes** — closes open boundary loops so the mesh is **watertight** (required for
   reliable slicing).
4. **Fix normals / winding** and drop degenerate triangles.
5. **Apply the real-world scale** and set units to millimetres.
6. **Export** to STL, OBJ, PLY, or 3MF (3MF carries millimetre units natively).

---

## 8. Surface-of-revolution rebuild

Many everyday objects — bottles, cups, vases, knobs, wheels, lids — are **rotationally
symmetric**. For these, Caliber doesn't just smooth a noisy scan; it rebuilds clean geometry:

1. **Detect symmetry.** Align the object to its principal axis (PCA) and measure the radial
   spread within height slices. Low spread ⇒ a surface of revolution.
2. **Extract a profile.** For each height slice, take a robust outer radius — the silhouette
   curve `r(z)`.
3. **Denoise & simplify.** A median filter removes spikes; Ramer–Douglas–Peucker
   simplification collapses near-straight runs (a cylindrical body) to clean segments while
   keeping real steps (a neck, a cap).
4. **Orient & correct.** Put the wider end (base) at the bottom; clamp any base-contact lip
   so it can't be wider than the body; snap a cylindrical body to a single true radius.
5. **Revolve.** Sweep the cleaned profile 360° and cap both ends → a crisp, perfectly
   symmetric, watertight solid.

The effect: a blobby scanned bottle becomes a straight-walled cylinder with the correct neck
and a clean base — and, with measurements, exact diameters and height.

---

## 9. Capture best practices — video

Video is the easiest way to feed the **local photogrammetry** engine: it's just a dense set
of overlapping views. Quality of capture decides quality of result.

- **Put the object down.** Place it on a surface or turntable — **do not hold it.** A hand
  occludes the object (creating holes where it gripped) and adds moving geometry that
  confuses the solver.
- **Orbit slowly and fully.** Move all the way around the object in one smooth circle, keeping
  it centred and filling most of the frame.
- **Do at least two passes** at different heights: one roughly level with the object, one
  from above looking down. Then **re-orient** the object (lay it on its side, prop it up) and
  do another pass to capture the underside and anything previously hidden.
- **Maximise overlap.** Move slowly so consecutive frames share most of their content. Caliber
  samples ~80–150 frames; smooth, continuous motion tracks far better than jumps.
- **Avoid motion blur.** Good light + steady hands (or a tripod/turntable). Blur destroys the
  features the solver needs.
- **Texture the scene, not just the object.** A plain, blank background gives the solver
  nothing to anchor each viewpoint. A lightly textured surface (a patterned mat, a sheet of
  newspaper) and a couple of small distinct objects around the base **help localisation** and,
  importantly, **break symmetry** for symmetric subjects.

---

## 10. Capture best practices — photos

Photos are the input for the **generative (cloud)** engine, and the rules differ:

- **Object alone, no hand.** The model reconstructs whatever it sees. Place the object on a
  table; if you must hold it, mask/crop the hand out first.
- **Plain, uncluttered background.** It helps the model isolate the subject.
- **Give multiple angles.** Front, back, left, right — and top/bottom if you can. More views
  = a more faithful model and far less guessed geometry. A single photo still works, but its
  far side is inferred.
- **Fill the frame and keep it sharp.** High resolution, in focus, no motion blur.
- **Consistent object, consistent lighting** across the shots so the views agree.

For **symmetric objects on the photogrammetry path**, add a few **distinct asymmetric
markers** (numbered stickers, coloured tape — different on each side). This gives the matcher
unique features per side and prevents the two sides from collapsing into one. (Peel them off
afterward; you may want one more pass where they were.)

---

## 11. Lighting

Lighting is the single biggest controllable factor after coverage.

- **Soft and even.** Diffuse light from multiple directions (an overcast window, a softbox, a
  light tent, or bounced light) eliminates harsh shadows and — critically — **specular
  highlights** that move with the camera and wreck photogrammetry.
- **No hard direct light.** A single bright lamp or direct sun creates moving glints and deep
  shadows that both confuse reconstruction.
- **Avoid a single point source** reflecting off the object. If you can see a bright spot
  sliding across the surface as you move, the camera sees it too — and it won't match.
- **Keep it constant.** Don't change lighting between frames/photos; consistent appearance
  across views is what lets the views agree.
- **Matte the shine.** For glossy or metallic objects, a light dusting of a matte powder (dry
  shampoo, baby powder, chalk) or a removable anti-glare spray turns a reflective nightmare
  into a textured, reconstructable surface. This is a standard professional photogrammetry
  technique.

A simple, effective setup: object on a turntable inside or beside a white light tent (or
under soft, even room light), camera on a tripod, rotate the object (or orbit the camera)
through a full turn at two heights.

---

## 12. Surfaces & object suitability

| Surface / shape | Local (photogrammetry) | Cloud (generative) |
|---|---|---|
| Matte, textured (wood, fabric, printed parts, rock) | **Excellent** | Good |
| Smooth but matte (painted matte, plastic) | Good | Good |
| Glossy / metallic / reflective | Poor (matte it, or use Cloud) | **Good** |
| Transparent / glass | Very poor | Fair (inferred) |
| Symmetric & repetitive (cars, bottles by silhouette) | Poor unless markers added | **Good** |
| Very thin / wiry | Poor | Fair |
| Round / rotationally symmetric | Good → cleaned via revolve | Good → cleaned via revolve |

Rule of thumb: **if it's matte and textured, measure it locally; if it's shiny or symmetric,
generate it in the cloud.**

---

## 13. Export & editing

Export **STL** (printable), **OBJ** (universal mesh), **PLY**, or **3MF** (mm units). To
reshape a mesh result, import the OBJ into Blender, Fusion, SolidWorks, or ZBrush and save in
that tool's native format (`.f3d`/`.blend` can only be written by their own apps; the
**Import → edit → Save As** workflow covers it).

### Scan-to-CAD: editable parts & STEP (beta)

A reconstructed mesh is a shell of triangles — *no* CAD tool lets you grab a face and change
its radius, because there are no real faces or edges there. Caliber adds a step that, for
**mechanical / primitive-shaped** parts, recovers actual geometry:

1. **Segmentation.** A greedy RANSAC fit peels the mesh into the shapes it's actually made of
   — **planes, cylinders, cones/tapers, spheres** — and classifies each cylinder as a shaft
   or an inward-facing **hole (bore)**. Validators reject false fits (a flat disk masquerading
   as a sphere, a cylinder inscribed in a box), and a "prefer the simpler primitive" rule
   stops flat faces being swallowed by phantom curves.
2. **Editable parts (OBJ).** Each detected piece is written as its own named object, so the
   parts import as separate, individually-selectable bodies.
3. **STEP B-rep solids.** Each primitive becomes a real analytic solid — a true
   `CYLINDRICAL_SURFACE`, `CONICAL_SURFACE`, planar box, etc. — written as watertight,
   shared-topology AP214 STEP. Opened in Fusion/SolidWorks these are genuine **editable
   solids**: move a face, change a radius, combine them. The writer is pure Python (no CAD
   kernel bundled), so a real boolean **merge/cut** is a two-click step in your CAD tool
   (Combine → Join for bodies, Combine → Cut for holes).

**Honest scope.** This works where the object *is* primitives — a bottle (cylinders + a
taper), a bracket (planes + holes), a shaft. A **freeform** shape — a car body, a sculpted
tail-light housing — has no clean shapes to fit, so primitive-fitting produces nonsense;
Caliber measures how much of the surface is freeform and **refuses STEP** on those, pointing
you to the mesh instead. For freeform parts the correct workflow is **mesh-as-reference**:
import the accurate mesh, lock its scale, and rebuild the part on top of it in CAD — which is
how scan-to-CAD is done at every price point, including $20k+ tools that keep a human in the
loop. True automatic free-form surfacing (NURBS fitting) is the next frontier on the roadmap.

---

## 14. Accuracy & expectations

Caliber produces a **first draft**, not a finished, certified CAD model:

- For **round and simple** objects, the cleaned, measured result is often essentially final.
- For **complex** objects (a detailed car), expect a recognisable, printable model that you
  then refine for exact detail.
- **Dimensional accuracy** comes from your reference measurement plus the capture quality.
  Pin a diameter and a height for round parts to lock both.
- **Generated (cloud) geometry** is inferred on unseen surfaces — accurate where the camera
  saw, plausible where it didn't.

The product's value is starting from a correct-scale, watertight draft instead of a blank
viewport — not eliminating all refinement.

---

## 15. Privacy & security

Caliber is local-first. The app serves its UI on loopback (`127.0.0.1`) only and validates
request host/origin to block cross-site access. Local and Import workflows never leave your
machine. The Cloud engine sends only the photos you select, to the provider whose key you
supply; the key is stored only on your computer and is never transmitted anywhere else. There
is no telemetry. Full details and the pre-release checklist are in
[`SECURITY_AUDIT.md`](SECURITY_AUDIT.md).

---

## 16. Limitations & roadmap

**Today's limits**

- Photogrammetry can't reconstruct glossy/symmetric/transparent objects well (use Cloud).
- Generative output is inferred on unseen surfaces and arbitrary-scale until you measure it.
- The local engine currently targets macOS (Apple Object Capture).
- Front-end viewer assets load from a CDN (see the security checklist) pending local bundling.

**Shipped recently:** two-point scale picker; mix video + photos for local; video → Cloud
frame extraction; scan-to-CAD primitive detection with **editable-parts (OBJ)** and **STEP
B-rep** export; native `Caliber.app` / `.dmg`; per-launch security token and locally-vendored
viewer libraries.

**Roadmap**

- Windows/Linux local engine (COLMAP + AliceVision) alongside macOS Object Capture.
- More reverse-engineering: fillet/slot/countersink recognition and an auto-union of the
  detected solids, so more object classes come out crisp automatically.
- **Free-form surfacing** (NURBS fitting) so sculpted/organic parts get an editable surface
  body, not just a reference mesh.
- Developer-ID **signed &amp; notarised** builds (zero Gatekeeper prompts).

---

## 17. Glossary

- **Photogrammetry** — reconstructing 3D geometry by triangulating real surface points seen
  across many overlapping photos.
- **SfM (Structure-from-Motion)** — recovering camera positions and a sparse point cloud from
  feature matches.
- **MVS (Multi-View Stereo)** — densifying SfM into a full surface.
- **Generative image-to-3D** — a neural model that produces 3D geometry from images using
  learned shape priors.
- **Symmetry collapse** — a photogrammetry failure where near-identical sides are fused.
- **Surface of revolution** — a shape formed by rotating a profile curve around an axis.
- **Watertight** — a closed mesh with no holes, required for reliable 3D printing.
- **Specular highlight** — a moving bright reflection that breaks feature matching.
