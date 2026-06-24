#!/usr/bin/env python3
"""
caliber-prep — Caliber's print-prep step.

Takes a raw scan mesh (.obj from caliber-recon, or .stl) and produces a
clean, watertight, correctly-scaled .stl / .3mf ready for a slicer.

Pipeline:  load -> weld -> keep largest part (drop hand/float junk)
           -> fill holes -> optional smooth -> scale to your reference mm -> export

Scale: you must give ONE real-world reference, e.g. "the bottle is 205 mm tall":
       --ref-mm 205 --ref-axis longest   (or x | y | z)

Only dependency: numpy.   pip3 install numpy
"""

import sys, os, argparse, struct, zipfile, math, json
import numpy as np


# ---------------- loading ----------------

def load_obj(path):
    verts, faces = [], []
    with open(path, "r", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                idx = []
                for tok in line.split()[1:]:
                    v = tok.split("/")[0]
                    if v == "":
                        continue
                    iv = int(v)
                    idx.append(iv - 1 if iv > 0 else len(verts) + iv)
                # triangulate polygon fan
                for k in range(1, len(idx) - 1):
                    faces.append((idx[0], idx[k], idx[k + 1]))
    return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def load_stl(path):
    with open(path, "rb") as fh:
        data = fh.read()
    # try binary
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + n * 50 == len(data):
            tris = np.frombuffer(data, dtype=np.uint8, count=n * 50, offset=84).reshape(n, 50)
            v = np.zeros((n, 3, 3), dtype=np.float64)
            for j in range(3):
                v[:, j, :] = tris[:, 12 + j * 12: 24 + j * 12].copy().view("<f4").reshape(n, 3)
            verts = v.reshape(-1, 3)
            faces = np.arange(n * 3).reshape(n, 3)
            return verts, faces
    # ascii fallback
    verts, faces, cur = [], [], []
    for line in data.decode("ascii", "ignore").splitlines():
        s = line.split()
        if len(s) >= 4 and s[0] == "vertex":
            cur.append((float(s[1]), float(s[2]), float(s[3])))
            if len(cur) == 3:
                b = len(verts)
                verts += cur
                faces.append((b, b + 1, b + 2))
                cur = []
    return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)


def load_glb(path):
    """Minimal binary glTF (.glb) reader — for meshes from generative models (Hunyuan/Tripo/Meshy)."""
    data = open(path, "rb").read()
    if struct.unpack_from("<I", data, 0)[0] != 0x46546C67:
        raise SystemExit("Not a valid .glb file.")
    length = struct.unpack_from("<I", data, 8)[0]
    off, J, B = 12, None, None
    while off < length:
        clen, ctype = struct.unpack_from("<II", data, off); off += 8
        chunk = data[off:off + clen]; off += clen
        if ctype == 0x4E4F534A:
            J = json.loads(chunk.decode("utf-8"))
        elif ctype == 0x004E4942:
            B = chunk
    if J is None or B is None:
        raise SystemExit("GLB has no embedded mesh data.")
    def acc(ai):
        a = J["accessors"][ai]; bv = J["bufferViews"][a["bufferView"]]
        bo = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        nc = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        dt = {5120: "<i1", 5121: "<u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}[a["componentType"]]
        return np.frombuffer(B, dtype=dt, count=a["count"] * nc, offset=bo).reshape(a["count"], nc)
    V, Fc, o = [], [], 0
    for m in J.get("meshes", []):
        for p in m["primitives"]:
            if "POSITION" not in p.get("attributes", {}):
                continue
            pos = acc(p["attributes"]["POSITION"]).astype(np.float64)
            idx = acc(p["indices"]).reshape(-1).astype(np.int64) if "indices" in p else np.arange(len(pos), dtype=np.int64)
            V.append(pos); Fc.append(idx.reshape(-1, 3) + o); o += len(pos)
    if not V:
        raise SystemExit("GLB has no triangle data.")
    return np.vstack(V), np.vstack(Fc)


def load_mesh(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        return load_obj(path)
    if ext == ".stl":
        return load_stl(path)
    if ext == ".glb":
        return load_glb(path)
    raise SystemExit(f"Unsupported input '{ext}'. Use .obj, .stl, or .glb (from a generative model).")


# ---------------- cleanup ----------------

def weld(verts, faces, tol=1e-5):
    """Merge near-duplicate vertices so the mesh is properly connected."""
    if len(verts) == 0:
        return verts, faces
    scale = 1.0 / max(tol, 1e-9)
    keys = np.round(verts * scale).astype(np.int64)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = np.asarray(inv).reshape(-1)            # numpy 2.0 returns odd shapes; force 1-D
    newverts = np.zeros((len(uniq), 3), dtype=np.float64)
    newverts[inv[::-1]] = verts[::-1]            # keep first-occurrence position
    newfaces = inv[faces]                        # remap face indices
    good = (newfaces[:, 0] != newfaces[:, 1]) & (newfaces[:, 1] != newfaces[:, 2]) & (newfaces[:, 0] != newfaces[:, 2])
    return newverts, newfaces[good]


def largest_component(verts, faces):
    """Union-find over shared vertices; keep the biggest connected piece."""
    n = len(verts)
    parent = np.arange(n)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for f in faces:
        union(f[0], f[1]); union(f[1], f[2])
    roots = np.array([find(i) for i in range(n)])
    face_root = roots[faces[:, 0]]
    uniq, counts = np.unique(face_root, return_counts=True)
    keep_root = uniq[np.argmax(counts)]
    keep = face_root == keep_root
    faces2 = faces[keep]
    used = np.unique(faces2)
    remap = -np.ones(n, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return verts[used], remap[faces2], int(uniq.size)


def boundary_loops(faces):
    """Edges used by exactly one triangle, ordered into closed loops."""
    from collections import defaultdict
    edge_count = defaultdict(int)
    for a, b, c in faces:
        for e in ((a, b), (b, c), (c, a)):
            edge_count[(min(e), max(e))] += 1
    # directed boundary edges (keep orientation from the single owning face)
    bnd = []
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            if edge_count[(min(u, v), max(u, v))] == 1:
                bnd.append((u, v))
    nxt = {}
    for u, v in bnd:
        nxt.setdefault(u, []).append(v)
    loops, used = [], set()
    for u0, v0 in bnd:
        if (u0, v0) in used:
            continue
        loop = [u0]
        cur = v0
        used.add((u0, v0))
        steps = 0
        while cur != u0 and steps < 100000:
            loop.append(cur)
            nbrs = nxt.get(cur, [])
            nb = None
            for w in nbrs:
                if (cur, w) not in used:
                    nb = w; break
            if nb is None:
                break
            used.add((cur, nb))
            cur = nb
            steps += 1
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def fill_holes(verts, faces):
    loops = boundary_loops(faces)
    if not loops:
        return verts, faces, 0
    new = list(map(tuple, faces.tolist()))
    filled = 0
    for loop in loops:
        # fan triangulate around the loop centroid for non-planar robustness
        cen = verts[loop].mean(axis=0)
        ci = len(verts)
        verts = np.vstack([verts, cen])
        for k in range(len(loop)):
            a = loop[k]; b = loop[(k + 1) % len(loop)]
            new.append((a, b, ci))
        filled += 1
    return verts, np.asarray(new, dtype=np.int64), filled


def _median1d(a, w):
    if w < 3:
        return a
    h = w // 2
    out = a.copy()
    for i in range(len(a)):
        lo, hi = max(0, i - h), min(len(a), i + h + 1)
        out[i] = np.median(a[lo:hi])
    return out


def _avg1d(a, w):
    if w < 2:
        return a
    k = np.ones(w) / w
    return np.convolve(np.pad(a, w // 2, mode="edge"), k, mode="same")[w // 2: w // 2 + len(a)]


def symmetry_score(verts, n_slices=80):
    """How rotationally symmetric is this object about its principal axis?
    Returns the median (radial std / mean) across height slices.
    ~0 = a clean surface of revolution; larger = not round."""
    c = verts.mean(0)
    X = verts - c
    w, V = np.linalg.eigh(X.T @ X)
    axis = V[:, int(np.argmax(w))]
    axis = axis / np.linalg.norm(axis)
    z = X @ axis
    r = np.linalg.norm(X - np.outer(z, axis), axis=1)
    zmin, zmax = z.min(), z.max()
    H = zmax - zmin
    if H <= 0:
        return 1.0
    b = np.clip(((z - zmin) / H * (n_slices - 1)).astype(int), 0, n_slices - 1)
    ratios = []
    for i in range(n_slices):
        rr = r[b == i]
        if rr.size > 8 and rr.mean() > 1e-6:
            ratios.append(rr.std() / rr.mean())
    return float(np.median(ratios)) if ratios else 1.0


def _rdp_profile(z, r, eps):
    """Ramer-Douglas-Peucker on the radius profile r(z): collapses near-straight
    runs (a cylindrical body) to single segments while keeping real steps (neck, cap)."""
    n = len(z)
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        dz = z[i1] - z[i0]
        if dz == 0:
            dev = np.abs(r[i0 + 1:i1] - r[i0])
        else:
            chord = r[i0] + (r[i1] - r[i0]) * (z[i0 + 1:i1] - z[i0]) / dz
            dev = np.abs(r[i0 + 1:i1] - chord)
        if dev.size == 0:
            continue
        k = int(np.argmax(dev))
        if dev[k] > eps:
            idx = i0 + 1 + k
            keep[idx] = True
            stack.append((i0, idx)); stack.append((idx, i1))
    return keep


def revolve(verts, axis_choice="auto", n_slices=220, n_seg=160, pct=92.0, simplify_frac=0.05,
            snap_body=False, ref_dia=None, cap_dia=None, body_top_frac=None):
    """Rebuild a rotationally-symmetric object (bottle, cup, vase, knob, lid, wheel)
    as a clean surface of revolution: find the axis, extract a radius profile, revolve it.
    Removes scan lumpiness and restores crisp edges; output is watertight by construction."""
    c = verts.mean(0)
    X = verts - c
    if axis_choice == "auto":
        # principal axis via PCA (the long axis of an upright bottle)
        w, V = np.linalg.eigh(X.T @ X)
        axis = V[:, int(np.argmax(w))]
    else:
        axis = {"x": np.array([1.0, 0, 0]), "y": np.array([0, 1.0, 0]), "z": np.array([0, 0, 1.0])}[axis_choice]
    axis = axis / np.linalg.norm(axis)
    tmp = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(axis, tmp); e1 /= np.linalg.norm(e1)
    e2 = np.cross(axis, e1)

    z = X @ axis
    radial = X - np.outer(z, axis)
    r = np.linalg.norm(radial, axis=1)
    zmin, zmax = z.min(), z.max()
    H = zmax - zmin
    if H <= 0:
        raise SystemExit("Revolve: object has no length along its axis.")

    # radius profile: robust outer radius per height slice
    N = n_slices
    b = np.clip(((z - zmin) / H * (N - 1)).astype(int), 0, N - 1)
    prof = np.full(N, np.nan)
    for i in range(N):
        rr = r[b == i]
        if rr.size:
            prof[i] = np.percentile(rr, pct)
    valid = ~np.isnan(prof)
    if valid.sum() < 2:
        raise SystemExit("Revolve: not enough points to build a profile.")
    prof = np.interp(np.arange(N), np.where(valid)[0], prof[valid])
    prof = _median1d(prof, 3)      # kill spikes first
    zc = np.linspace(0, 1, N) * H
    keep = _rdp_profile(zc, prof, max(simplify_frac * float(prof.max()), 1e-9))
    ks = np.where(keep)[0]
    prof = np.interp(np.arange(N), ks, prof[ks])   # straight body, crisp neck/cap steps
    prof = np.clip(prof, 0.0, None)

    # PCA axis sign is arbitrary: orient so the wider end (the base) sits at the bottom
    if float(np.mean(prof[:N // 2])) < float(np.mean(prof[N // 2:])):
        prof = prof[::-1].copy()

    # the base of a scan usually grows a flared lip (the bottom edge + table contact). It
    # should never be wider than the body just above it -> clamp it down. Bottom only, and
    # radius-only (height is preserved, so scaling stays correct). The top is left alone
    # because it may be a legitimately-wider cap.
    k = max(2, int(0.06 * N))
    if N > 4 * k:
        body_ref = float(np.median(prof[k:2 * k]))
        prof[:k] = np.minimum(prof[:k], body_ref)

    # --- optional dimension constraints (your real measurements) ---
    s = None
    if snap_body or ref_dia is not None or cap_dia is not None:
        # body radius = the most common radius across the profile (the body occupies the most
        # slices), which is robust to a base flare, neck, or cap inflating the estimate
        pos = prof[prof > 1e-6]
        if pos.size:
            hist, edges = np.histogram(pos, bins=40)
            bi = int(np.argmax(hist))
            dom = 0.5 * (edges[bi] + edges[bi + 1])
            near = pos[np.abs(pos - dom) < (edges[1] - edges[0]) * 1.5]
            bodyR = float(np.median(near)) if near.size else float(dom)
        else:
            bodyR = float(prof.max())
        if snap_body:
            if body_top_frac is not None:               # you told us exactly where the taper starts
                top = int(np.clip(body_top_frac, 0.05, 0.98) * N)
                prof[:top + 1] = bodyR
            else:
                # snap every slice near the dominant body radius to it -> a true straight
                # cylinder, while leaving the neck, cap, and the narrow base edge untouched.
                near = np.abs(prof - bodyR) < 0.10 * bodyR
                prof[near] = bodyR
                # nothing on a bottle/cup should be WIDER than its body -> clamp any
                # remaining base-edge flare down to the body radius.
                prof = np.minimum(prof, bodyR)
        if ref_dia is not None:                         # scale so body diameter == ref_dia
            s = (ref_dia / 2.0) / bodyR
            if cap_dia is not None:                     # (optional) hard-pin the cap diameter
                prof[int(0.86 * N):] = (cap_dia / 2.0) / s

    # --- build upright (axis -> +Y), base on the y=0 plane, ready to print ---
    zpos = np.linspace(0, 1, N) * H
    ang = 2 * np.pi * np.arange(n_seg) / n_seg
    cj, sj = np.cos(ang), np.sin(ang)
    V = np.zeros((N * n_seg + 2, 3))
    for i in range(N):
        seg = slice(i * n_seg, (i + 1) * n_seg)
        V[seg, 0] = prof[i] * cj
        V[seg, 1] = zpos[i]
        V[seg, 2] = prof[i] * sj
    cb = N * n_seg          # bottom centre
    ct = N * n_seg + 1      # top centre
    V[cb] = (0.0, 0.0, 0.0)
    V[ct] = (0.0, H, 0.0)

    F = []
    for i in range(N - 1):
        a0 = i * n_seg; a1 = (i + 1) * n_seg
        for j in range(n_seg):
            jn = (j + 1) % n_seg
            F.append((a0 + j, a0 + jn, a1 + jn))
            F.append((a0 + j, a1 + jn, a1 + j))
    for j in range(n_seg):                      # bottom cap
        jn = (j + 1) % n_seg
        F.append((cb, j, jn))
    base = (N - 1) * n_seg
    for j in range(n_seg):                      # top cap
        jn = (j + 1) % n_seg
        F.append((ct, base + jn, base + j))

    if s is not None:
        V = V * s          # uniform scale to mm so the body diameter matches your measurement
    return V, np.asarray(F, dtype=np.int64)


def laplacian_smooth(verts, faces, iters, lam=0.5):
    if iters <= 0:
        return verts
    from collections import defaultdict
    adj = defaultdict(set)
    for a, b, c in faces:
        adj[a].update((b, c)); adj[b].update((a, c)); adj[c].update((a, b))
    nbr = [np.array(sorted(adj[i]), dtype=np.int64) if adj[i] else np.array([], dtype=np.int64)
           for i in range(len(verts))]
    v = verts.copy()
    for _ in range(iters):
        nv = v.copy()
        for i in range(len(v)):
            if len(nbr[i]):
                nv[i] = v[i] + lam * (v[nbr[i]].mean(axis=0) - v[i])
        v = nv
    return v


# ---------------- stats & export ----------------

def mesh_stats(verts, faces):
    boundary = len(boundary_loops(faces))
    tris = verts[faces]
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()
    vol = np.abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
    ext = verts.max(0) - verts.min(0)
    return dict(verts=len(verts), tris=len(faces), holes=boundary,
                watertight=boundary == 0, area=area, volume=vol, ext=ext)


def write_stl(path, verts, faces):
    tris = verts[faces].astype("<f4")
    n = len(faces)
    nrm = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(nrm, axis=1, keepdims=True); ln[ln == 0] = 1
    nrm = (nrm / ln).astype("<f4")
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", n))
        for i in range(n):
            f.write(nrm[i].tobytes())
            f.write(tris[i].tobytes())
            f.write(b"\0\0")


def write_obj(path, verts, faces):
    """Wavefront OBJ — imported by Blender, Fusion, MeshLab, ZBrush, everything."""
    with open(path, "w") as f:
        f.write("# Caliber\n")
        for v in verts:
            f.write("v %.5f %.5f %.5f\n" % (v[0], v[1], v[2]))
        for t in faces:
            f.write("f %d %d %d\n" % (t[0] + 1, t[1] + 1, t[2] + 1))


def write_ply(path, verts, faces):
    """Stanford PLY (ascii) — imported by Blender, MeshLab, CloudCompare."""
    with open(path, "w") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write("element vertex %d\nproperty float x\nproperty float y\nproperty float z\n" % len(verts))
        f.write("element face %d\nproperty list uchar int vertex_indices\nend_header\n" % len(faces))
        for v in verts:
            f.write("%.5f %.5f %.5f\n" % (v[0], v[1], v[2]))
        for t in faces:
            f.write("3 %d %d %d\n" % (int(t[0]), int(t[1]), int(t[2])))


def write_3mf(path, verts, faces):
    vx = "".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in verts)
    tx = "".join(f'<triangle v1="{int(a)}" v2="{int(b)}" v3="{int(c)}"/>' for a, b, c in faces)
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources><object id="1" type="model"><mesh>'
             f'<vertices>{vx}</vertices><triangles>{tx}</triangles>'
             '</mesh></object></resources>'
             '<build><item objectid="1"/></build></model>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


# ============================================================================
#  Primitive segmentation (Stage 1 of scan-to-CAD)
#
#  Greedy RANSAC: repeatedly fit the best plane / cylinder / sphere to the faces
#  still unassigned, peel those off as a named "part", repeat. Whatever is left
#  (organic / unfittable) becomes one "residual" part. numpy only. The result is
#  an OBJ where each part is a separate selectable object, plus a list of the
#  shapes found with dimensions.
# ============================================================================

def _tri_geom(verts, faces):
    v = verts[faces]                                   # (F,3,3)
    cents = v.mean(axis=1)
    cr = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    ln = np.linalg.norm(cr, axis=1)
    areas = 0.5 * ln
    norms = cr / np.where(ln[:, None] > 1e-12, ln[:, None], 1.0)
    return cents, norms, areas

def _closest_point_two_lines(p1, d1, p2, d2):
    d1 = d1 / (np.linalg.norm(d1) + 1e-12); d2 = d2 / (np.linalg.norm(d2) + 1e-12)
    a = d1 @ d1; b = d1 @ d2; c = d2 @ d2; w0 = p1 - p2
    den = a * c - b * b
    if abs(den) < 1e-9:
        return (p1 + p2) * 0.5
    d = d1 @ w0; e = d2 @ w0
    t = (b * e - c * d) / den; s = (a * e - b * d) / den
    return 0.5 * ((p1 + t * d1) + (p2 + s * d2))

def _fit_circle_2d(P):
    x = P[:, 0]; y = P[:, 1]
    A = np.column_stack([x, y, np.ones(len(P))])
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx = sol[0] / 2.0; cy = sol[1] / 2.0
    r = math.sqrt(max(0.0, sol[2] + cx * cx + cy * cy))
    return np.array([cx, cy]), r

def _basis_perp(axis):
    a = axis / (np.linalg.norm(axis) + 1e-12)
    t = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(a, t); u /= (np.linalg.norm(u) + 1e-12)
    w = np.cross(a, u)
    return a, u, w

def _ransac_plane(idx, C, N, A, dist_tol, ang, rng, iters=60):
    best = None
    for _ in range(iters):
        s = idx[rng.integers(len(idx))]
        n0 = N[s]; d0 = n0 @ C[s]
        m = (np.abs(C[idx] @ n0 - d0) < dist_tol) & (np.abs(N[idx] @ n0) > ang)
        sc = A[idx][m].sum()
        if best is None or sc > best[0]:
            best = (sc, n0, d0)
    if best is None:
        return None
    _, n0, d0 = best
    inl = idx[(np.abs(C[idx] @ n0 - d0) < dist_tol) & (np.abs(N[idx] @ n0) > ang)]
    if len(inl) < 4:
        return None
    pts = C[inl]; ctr = pts.mean(0)                    # refine via PCA
    _, V = np.linalg.eigh(np.cov((pts - ctr).T))
    n = V[:, 0]; d = n @ ctr
    _, u, w = _basis_perp(n)
    pu = (pts - ctr) @ u; pw = (pts - ctr) @ w
    dims = (float(pu.max() - pu.min()), float(pw.max() - pw.min()))
    return {"type": "plane", "faces": inl, "score": float(A[inl].sum()),
            "params": {"normal": n, "point": ctr, "size": dims}}

def _ransac_sphere(idx, C, N, A, dist_tol, ang, rng, diag, iters=80):
    best = None
    for _ in range(iters):
        s1, s2 = idx[rng.integers(len(idx))], idx[rng.integers(len(idx))]
        if s1 == s2:
            continue
        c = _closest_point_two_lines(C[s1], N[s1], C[s2], N[s2])
        r = 0.5 * (np.linalg.norm(C[s1] - c) + np.linalg.norm(C[s2] - c))
        if r < dist_tol or r > diag:
            continue
        dvec = C[idx] - c; dist = np.linalg.norm(dvec, axis=1)
        rad = dvec / np.where(dist[:, None] > 1e-9, dist[:, None], 1.0)
        m = (np.abs(dist - r) < dist_tol) & (np.abs(np.sum(rad * N[idx], axis=1)) > ang)
        sc = A[idx][m].sum()
        if best is None or sc > best[0]:
            best = (sc, c, r)
    if best is None:
        return None
    _, c, r = best
    dvec = C[idx] - c; dist = np.linalg.norm(dvec, axis=1)
    rad = dvec / np.where(dist[:, None] > 1e-9, dist[:, None], 1.0)
    inl = idx[(np.abs(dist - r) < dist_tol) & (np.abs(np.sum(rad * N[idx], axis=1)) > ang)]
    if len(inl) < 8:
        return None
    # reject flat/bipolar regions masquerading as a sphere. A real sphere patch spreads
    # its surface directions across 2D; flat disks (or two parallel caps) concentrate them
    # along one axis -> the dominant eigenvalue of the radial-direction covariance is high.
    rdir = (C[inl] - c); rdir = rdir / (np.linalg.norm(rdir, axis=1)[:, None] + 1e-12)
    if np.linalg.eigvalsh(np.cov(rdir.T))[-1] > 0.75:
        return None
    return {"type": "sphere", "faces": inl, "score": float(A[inl].sum()),
            "params": {"center": c, "radius": float(r)}}

def _cyl_inliers(idx, C, N, axis, apt, r, dist_tol, ang):
    rel = C[idx] - apt
    along = rel @ axis
    radial = rel - np.outer(along, axis)
    dist = np.linalg.norm(radial, axis=1)
    rn = radial / np.where(dist[:, None] > 1e-9, dist[:, None], 1.0)
    perp = np.abs(N[idx] @ axis) < 0.30
    return (np.abs(dist - r) < dist_tol) & perp & (np.abs(np.sum(rn * N[idx], axis=1)) > ang)

def _ransac_cylinder(idx, C, N, A, dist_tol, ang, rng, diag, iters=140):
    best = None
    for _ in range(iters):
        s = rng.choice(idx, size=3, replace=False)
        axis = np.cross(N[s[0]], N[s[1]])
        if np.linalg.norm(axis) < 0.2:
            continue
        a, u, w = _basis_perp(axis)
        P2 = np.column_stack([(C[s] - C[s[0]]) @ u, (C[s] - C[s[0]]) @ w])
        try:
            ctr2, r = _fit_circle_2d(P2)
        except Exception:
            continue
        if not (r > dist_tol) or r > 1e6:
            continue
        apt = C[s[0]] + ctr2[0] * u + ctr2[1] * w
        m = _cyl_inliers(idx, C, N, a, apt, r, dist_tol, ang)
        sc = A[idx][m].sum()
        if best is None or sc > best[0]:
            best = (sc, a, apt, r)
    if best is None:
        return None
    _, axis, apt, r = best
    inl = idx[_cyl_inliers(idx, C, N, axis, apt, r, dist_tol, ang)]
    if len(inl) < 8:
        return None
    _, V = np.linalg.eigh(np.cov(N[inl].T))            # refine axis = min-variance normal dir
    axis = V[:, 0] / (np.linalg.norm(V[:, 0]) + 1e-12)
    a, u, w = _basis_perp(axis)
    P2 = np.column_stack([(C[inl] - C[inl[0]]) @ u, (C[inl] - C[inl[0]]) @ w])
    ctr2, r = _fit_circle_2d(P2)
    apt = C[inl[0]] + ctr2[0] * u + ctr2[1] * w
    inl = idx[_cyl_inliers(idx, C, N, axis, apt, r, dist_tol, ang)]
    if len(inl) < 8:
        return None
    # validate it's really a cylinder: normals must be perpendicular to the axis, and
    # the surface must wrap a good arc around it (a flat patch fails both).
    if r > 0.75 * diag or np.mean(np.abs(N[inl] @ axis)) > 0.25:
        return None
    rel = C[inl] - apt
    rad = rel - np.outer(rel @ axis, axis)
    a, u, w = _basis_perp(axis)
    angles = np.sort(np.arctan2(rad @ w, rad @ u))
    gaps = np.diff(np.concatenate([angles, [angles[0] + 2 * np.pi]]))
    coverage = 2 * np.pi - gaps.max()                 # arc actually spanned
    if coverage < math.radians(110):
        return None
    # the surface must fill its arc, not just graze a few tangent lines (a cylinder
    # inscribed in a box touches 4 walls and would otherwise pass the coverage test).
    occ = (np.histogram(angles, bins=36, range=(-np.pi, np.pi))[0] > 0).sum()
    if occ / max(1, round(coverage / (2 * np.pi) * 36)) < 0.6:
        return None
    along = rel @ axis
    # outward-facing surface = a shaft/boss; inward-facing = a hole (bore).
    rln = np.linalg.norm(rad, axis=1)
    rdir = rad / np.where(rln[:, None] > 1e-9, rln[:, None], 1.0)
    outward = bool(np.mean(np.sum(rdir * N[inl], axis=1)) >= 0)
    return {"type": "cylinder", "faces": inl, "score": float(A[inl].sum()),
            "params": {"axis": axis, "point": apt, "radius": float(r),
                       "length": float(along.max() - along.min()), "outward": outward}}

def _axis_point(pts, axis):
    """A point on the axis line: circle-fit a thin cross-section band (one slice has a
    single radius even on a cone), in the plane perpendicular to the axis."""
    a, u, w = _basis_perp(axis)
    t = pts @ a
    mid = np.abs(t - np.median(t)) < (t.max() - t.min()) * 0.15 + 1e-9
    band = pts[mid] if mid.sum() >= 4 else pts
    ctr2, _ = _fit_circle_2d(np.column_stack([band @ u, band @ w]))
    return ctr2[0] * u + ctr2[1] * w

def _ransac_cone(idx, C, N, A, dist_tol, ang, rng, diag, iters=160):
    """Cone / truncated cone (frustum) — for tapers, shoulders, chamfers. A revolved
    surface whose radius varies linearly along the axis: r(t) = a + b*t. Detected via
    the fact that every surface normal makes the SAME angle with the axis (n·axis = c0)."""
    best = None
    for _ in range(iters):
        s = rng.choice(idx, size=3, replace=False)
        axis = np.cross(N[s[0]] - N[s[1]], N[s[0]] - N[s[2]])
        na = np.linalg.norm(axis)
        if na < 1e-3:
            continue
        axis = axis / na
        c0 = float(np.mean(N[s] @ axis))
        if abs(c0) < 0.12 or abs(c0) > 0.97:      # ~cylinder (0) or ~flat (1): not a cone
            continue
        d = N[idx] @ axis
        m0 = np.abs(d - c0) < 0.12
        if m0.sum() < 8:
            continue
        apt = _axis_point(C[idx][m0], axis)
        rel = C[idx] - apt; t = rel @ axis
        radial = rel - np.outer(t, axis); r = np.linalg.norm(radial, axis=1)
        try:
            b_, a_ = np.polyfit(t[m0], r[m0], 1)
        except Exception:
            continue
        if abs(b_) < 0.05:                         # negligible taper -> a cylinder
            continue
        rn = radial / np.where(r[:, None] > 1e-9, r[:, None], 1.0)
        pred = a_ + b_ * t
        m = (np.abs(r - pred) < dist_tol) & (np.abs(d - c0) < 0.12) & \
            (np.abs(np.sum(rn * N[idx], axis=1)) > 0.5) & (pred > 0)
        sc = float(A[idx][m].sum())
        if best is None or sc > best[0]:
            best = (sc, axis, c0)
    if best is None:
        return None
    _, axis, c0 = best
    inl0 = idx[np.abs(N[idx] @ axis - c0) < 0.12]
    if len(inl0) < 10:
        return None
    _, V = np.linalg.eigh(np.cov(N[inl0].T))       # refine axis = min-variance normal dir
    ax2 = V[:, 0] / (np.linalg.norm(V[:, 0]) + 1e-12)
    axis = ax2 if (ax2 @ axis) >= 0 else -ax2
    c0 = float(np.mean(N[inl0] @ axis))
    apt = _axis_point(C[inl0], axis)
    rel = C[idx] - apt; t = rel @ axis
    radial = rel - np.outer(t, axis); r = np.linalg.norm(radial, axis=1)
    m = np.abs(N[idx] @ axis - c0) < 0.12
    b_, a_ = np.polyfit(t[m], r[m], 1)
    if abs(b_) < 0.05:
        return None
    rn = radial / np.where(r[:, None] > 1e-9, r[:, None], 1.0)
    pred = a_ + b_ * t
    inl = idx[(np.abs(r - pred) < dist_tol) & (np.abs(N[idx] @ axis - c0) < 0.12) &
              (np.abs(np.sum(rn * N[idx], axis=1)) > 0.5) & (pred > 0)]
    if len(inl) < 10:
        return None
    rel_i = C[inl] - apt; t_i = rel_i @ axis
    rad_i = rel_i - np.outer(t_i, axis)
    a, u, w = _basis_perp(axis)
    angles = np.sort(np.arctan2(rad_i @ w, rad_i @ u))
    gaps = np.diff(np.concatenate([angles, [angles[0] + 2 * np.pi]]))
    if (2 * np.pi - gaps.max()) < math.radians(150):   # a real taper wraps fully around
        return None
    tmin, tmax = float(t_i.min()), float(t_i.max())
    r1, r2 = a_ + b_ * tmin, a_ + b_ * tmax
    if min(r1, r2) < 0 or max(r1, r2) > diag:
        return None
    return {"type": "cone", "faces": inl, "score": float(A[inl].sum()),
            "params": {"axis": axis, "base": apt + axis * tmin,
                       "r1": float(r1), "r2": float(r2), "length": float(tmax - tmin)}}

def segment_primitives(verts, faces, max_parts=16, min_frac=0.012, seed=0, verbose=False):
    """Greedily peel planes/cylinders/cones/spheres off the mesh. Returns parts:
    {type, faces (global indices), params, score}."""
    faces = np.asarray(faces)
    C, N, A = _tri_geom(verts, faces)
    F = len(faces)
    diag = float(np.linalg.norm(verts.max(0) - verts.min(0))) or 1.0
    dist_tol = 0.012 * diag
    ang = math.cos(math.radians(22))
    rng = np.random.default_rng(seed)
    remaining = np.ones(F, bool)
    min_faces = max(8, int(min_frac * F))
    parts = []
    for _ in range(max_parts):
        idx = np.where(remaining)[0]
        if len(idx) < min_faces:
            break
        cands = [_ransac_plane(idx, C, N, A, dist_tol, ang, rng),
                 _ransac_cylinder(idx, C, N, A, dist_tol, ang, rng, diag),
                 _ransac_cone(idx, C, N, A, dist_tol, ang, rng, diag),
                 _ransac_sphere(idx, C, N, A, dist_tol, ang, rng, diag)]
        cands = [c for c in cands if c and len(c["faces"]) >= min_faces]
        if not cands:
            break
        best = max(cands, key=lambda c: c["score"])
        # Prefer the simpler primitive: if a plane explains nearly as much as the best
        # curved fit, take the plane. Stops flat faces being swallowed by a phantom
        # cylinder/sphere (e.g. a cylinder inscribed in a box touching its walls).
        planes = [c for c in cands if c["type"] == "plane"]
        if planes:
            bp = max(planes, key=lambda c: c["score"])
            if bp is not best and bp["score"] >= 0.6 * best["score"]:
                best = bp
        remaining[best["faces"]] = False
        parts.append(best)
        if verbose:
            print("  found", best["type"], len(best["faces"]), "faces")
    res = np.where(remaining)[0]
    if len(res):
        parts.append({"type": "residual", "faces": res, "score": float(A[res].sum()), "params": {}})
    return parts

def describe_segments(parts):
    counts = {}
    lines = []
    for p in parts:
        t = p["type"]; counts[t] = counts.get(t, 0) + 1
        pr = p["params"]
        label = "%s_%d" % (t, counts[t])
        if t == "plane":
            w, h = pr["size"]; desc = "flat face ~%.1f x %.1f mm" % (w, h)
        elif t == "cylinder":
            kind = "cylinder" if pr.get("outward", True) else "HOLE (bore)"
            desc = "%s dia %.1f x %.1f mm long" % (kind, 2 * pr["radius"], pr["length"])
        elif t == "cone":
            desc = "taper dia %.1f -> %.1f x %.1f mm long" % (2 * pr["r1"], 2 * pr["r2"], pr["length"])
        elif t == "sphere":
            desc = "sphere dia %.1f mm" % (2 * pr["radius"])
        else:
            desc = "freeform region (%d triangles)" % len(p["faces"])
        lines.append("  - %-12s %s" % (label, desc))
    head = "Detected %d part(s): " % len(parts) + ", ".join(
        "%d %s" % (n, k + ("s" if n > 1 else "")) for k, n in counts.items())
    return head + "\n" + "\n".join(lines)

def write_obj_groups(path, verts, faces, parts):
    """One OBJ, shared vertices, each part its own `o <label>` object so Blender/Fusion
    import them as separate selectable pieces."""
    faces = np.asarray(faces)
    counts = {}
    with open(path, "w") as f:
        f.write("# Caliber - segmented parts\n")
        for v in verts:
            f.write("v %.5f %.5f %.5f\n" % (v[0], v[1], v[2]))
        for p in parts:
            t = p["type"]; counts[t] = counts.get(t, 0) + 1
            f.write("o %s_%d\ng %s_%d\n" % (t, counts[t], t, counts[t]))
            for fi in p["faces"]:
                a, b, c = faces[fi]
                f.write("f %d %d %d\n" % (a + 1, b + 1, c + 1))


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser(description="Caliber print-prep: scan mesh -> clean, scaled, printable STL/3MF")
    ap.add_argument("input", help="input mesh (.obj or .stl)")
    ap.add_argument("output", help="output (.stl or .3mf)")
    ap.add_argument("--auto", action="store_true",
                    help="auto pipeline: clean everything, auto-detect a surface of revolution and "
                         "rebuild it cleanly when confident, else keep a clean mesh. No shape-specific flags.")
    ap.add_argument("--ref-mm", type=float, default=None, help="real length of the reference axis, in mm")
    ap.add_argument("--ref-axis", default="longest", choices=["longest", "x", "y", "z"],
                    help="which bounding-box axis the reference measures (default longest)")
    ap.add_argument("--smooth", type=int, default=0, help="Laplacian smoothing iterations (default 0)")
    ap.add_argument("--no-fill", action="store_true", help="don't fill holes")
    ap.add_argument("--keep-all", action="store_true", help="keep all parts (default: keep largest only)")
    ap.add_argument("--revolve", action="store_true",
                    help="rebuild as a clean surface of revolution (bottles, cups, vases, knobs, lids, wheels)")
    ap.add_argument("--revolve-axis", default="auto", choices=["auto", "x", "y", "z"],
                    help="axis of symmetry for --revolve (default auto/PCA)")
    ap.add_argument("--segments", type=int, default=160, help="angular resolution for --revolve (default 160)")
    ap.add_argument("--slices", type=int, default=220, help="profile resolution for --revolve (default 220)")
    ap.add_argument("--simplify-frac", type=float, default=0.05,
                    help="--revolve feature tolerance (default 0.05). Lower keeps more curve detail; "
                         "too high merges real features (neck/cap) into the body. Sweet spot ~0.03-0.06.")
    ap.add_argument("--snap-body", action="store_true",
                    help="(with --revolve) force the body to a perfectly constant diameter")
    ap.add_argument("--ref-dia", type=float, default=None,
                    help="(with --revolve) scale so the body diameter equals this many mm — "
                         "a better reference than total height for round objects")
    ap.add_argument("--cap-dia", type=float, default=None,
                    help="(with --revolve, needs --ref-dia) pin the cap/top diameter to this many mm")
    ap.add_argument("--ref-height", type=float, default=None,
                    help="(with --revolve) pin TOTAL height to this many mm, independent of diameter")
    ap.add_argument("--body-top", type=float, default=None,
                    help="(with --revolve --snap-body, needs --ref-height) height in mm where the body stops "
                         "and the taper/neck begins; the real shoulder/neck/cap above it is preserved")
    ap.add_argument("--shapes", action="store_true",
                    help="detect geometric parts (planes/cylinders/spheres) and write a grouped OBJ where "
                         "each part is a separate object you can edit individually, plus a shapes report")
    args = ap.parse_args()

    print(f"Loading {args.input} …")
    verts, faces = load_mesh(args.input)
    if len(faces) == 0:
        raise SystemExit("No triangles found in input.")
    print(f"  raw: {len(verts):,} verts, {len(faces):,} tris")

    verts, faces = weld(verts, faces)
    if not args.keep_all:
        verts, faces, ncomp = largest_component(verts, faces)
        print(f"  kept largest of {ncomp} part(s) -> {len(faces):,} tris (dropped floaters/hand junk)")

    if args.auto:
        score = symmetry_score(verts)
        if score < 0.08:
            args.revolve = True
            args.snap_body = True      # straighten a cylindrical body automatically (safe: only long near-constant runs)
            print(f"  auto: rotationally symmetric (score {score:.3f}) -> clean revolve + straighten body")
        else:
            print(f"  auto: not a surface of revolution (score {score:.3f}) -> clean mesh")

    if args.revolve:
        btf = (args.body_top / args.ref_height) if (args.body_top and args.ref_height) else None
        if args.body_top and not args.ref_height:
            print("  note: --body-top needs --ref-height to convert mm; using auto-detect instead")
        verts, faces = revolve(verts, args.revolve_axis, args.slices, args.segments,
                               simplify_frac=args.simplify_frac, snap_body=args.snap_body,
                               ref_dia=args.ref_dia, cap_dia=args.cap_dia, body_top_frac=btf)
        print(f"  revolved into a clean surface of revolution -> {len(faces):,} tris (crisp, symmetric, watertight)")
    elif not args.no_fill:
        verts, faces, nfill = fill_holes(verts, faces)
        print(f"  filled {nfill} hole(s)")

    if args.smooth > 0:
        verts = laplacian_smooth(verts, faces, args.smooth)
        print(f"  smoothed x{args.smooth}")

    # scale
    if args.revolve and args.ref_dia is not None:
        verts = verts - verts.min(0)              # already in mm (scaled inside revolve); drop to origin
        msg = f"  scaled so body diameter = {args.ref_dia} mm"
        if args.cap_dia is not None:
            msg += f", cap diameter = {args.cap_dia} mm"
        print(msg)
    elif args.ref_mm is not None:
        ext = verts.max(0) - verts.min(0)
        axis = int(np.argmax(ext)) if args.ref_axis == "longest" else {"x": 0, "y": 1, "z": 2}[args.ref_axis]
        cur = ext[axis]
        if cur <= 0:
            raise SystemExit("Reference axis has zero size; cannot scale.")
        factor = args.ref_mm / cur
        verts = (verts - verts.min(0)) * factor   # also drops to origin-positive, slicer-friendly
        print(f"  scaled so axis {'xyz'[axis]} = {args.ref_mm} mm (factor {factor:.4f})")
    else:
        print("  WARNING: no --ref-mm / --ref-dia given; output is NOT scaled to real millimetres.")

    if args.revolve and args.ref_height is not None:     # pin total height (axial scale, keeps diameter)
        ext = verts.max(0) - verts.min(0)
        if ext[1] > 0:
            fy = args.ref_height / ext[1]
            verts[:, 1] = (verts[:, 1] - verts[:, 1].min()) * fy
            print(f"  scaled height -> {args.ref_height} mm (axial factor {fy:.4f})")

    st = mesh_stats(verts, faces)
    ext = st["ext"]
    print("\nResult:")
    print(f"  triangles : {st['tris']:,}")
    print(f"  watertight: {'yes' if st['watertight'] else 'no (' + str(st['holes']) + ' open loop(s))'}")
    scaled = (args.ref_mm is not None) or (args.revolve and args.ref_dia is not None)
    print(f"  size      : {ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} {'mm' if scaled else 'units'}")
    if args.revolve and scaled:
        print(f"  diameter  : {max(ext[0], ext[2]):.1f} mm (widest / body)")
        print(f"  height    : {ext[1]:.1f} mm (total)")
    if scaled:
        print(f"  volume    : {st['volume']/1000.0:.1f} cm^3  (approx)")

    ext_out = os.path.splitext(args.output)[1].lower()
    if args.shapes:
        # Geometric segmentation: write each detected part as its own OBJ object + a report.
        if ext_out != ".obj":
            args.output = os.path.splitext(args.output)[0] + ".obj"
            print("  --shapes writes a grouped OBJ; using", args.output)
        print("\nDetecting geometric parts…")
        parts = segment_primitives(verts, faces)
        report = describe_segments(parts)
        print(report)
        write_obj_groups(args.output, verts, faces, parts)
        with open(os.path.splitext(args.output)[0] + ".shapes.txt", "w") as fh:
            fh.write(report + "\n")
        print(f"\nWrote {args.output} (+ .shapes.txt)  ✅")
    elif ext_out == ".stl":
        write_stl(args.output, verts, faces)
        print(f"\nWrote {args.output}  ✅")
    elif ext_out == ".3mf":
        write_3mf(args.output, verts, faces)
        print(f"\nWrote {args.output}  ✅")
    elif ext_out == ".obj":
        write_obj(args.output, verts, faces)
        print(f"\nWrote {args.output}  ✅")
    elif ext_out == ".ply":
        write_ply(args.output, verts, faces)
        print(f"\nWrote {args.output}  ✅")
    elif ext_out in (".step", ".stp"):
        import caliber_cad
        print("\nDetecting geometric parts for STEP…")
        parts = segment_primitives(verts, faces)
        print(describe_segments(parts))
        print(caliber_cad.primitives_to_step(verts, faces, parts, args.output))
        print(f"Wrote {args.output}  ✅")
    else:
        raise SystemExit("Output must be .stl, .3mf, .obj, .ply, or .step")


if __name__ == "__main__":
    main()
