#!/usr/bin/env python3
"""
caliber_cad — turn detected primitives into editable STEP (AP214) B-rep solids.

Stage 2 of scan-to-CAD. The segmenter (caliber_prep.segment_primitives) tells us a
region IS a cylinder / box / etc. with exact parameters; this module emits each as a
real analytic solid in a STEP file. Opened in Fusion / SolidWorks those are genuine
editable solids (move a face, change a radius), not a mesh.

Pure Python, no dependencies beyond numpy — so it ships inside the app. It writes
watertight, shared-topology B-reps (every edge shared by exactly two faces) so the
geometry sews into a solid rather than loose surfaces.
"""
import math
import time
import numpy as np


def _f(x):
    return ("%.6f" % float(x)).rstrip("0").rstrip(".") or "0."

def _f3(p):
    return "%s,%s,%s" % (_f(p[0]), _f(p[1]), _f(p[2]))


class StepDoc:
    def __init__(self):
        self.lines = []
        self.n = 0
        self.solids = []
        # --- units + geometric context (mm) ---
        self.ctx = self._context()

    def add(self, body):
        self.n += 1
        self.lines.append("#%d=%s;" % (self.n, body))
        return self.n

    def _context(self):
        u_len = self.add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
        u_ang = self.add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
        u_sol = self.add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
        unc = self.add("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-06),#%d,"
                       "'distance_accuracy_value','')" % u_len)
        ctx = self.add("(GEOMETRIC_REPRESENTATION_CONTEXT(3)"
                       "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#%d))"
                       "GLOBAL_UNIT_ASSIGNED_CONTEXT((#%d,#%d,#%d))"
                       "REPRESENTATION_CONTEXT('Caliber','3D'))" % (unc, u_len, u_ang, u_sol))
        return ctx

    # ---- geometry primitives ----
    def point(self, p):    return self.add("CARTESIAN_POINT('',(%s))" % _f3(p))
    def direction(self, d): return self.add("DIRECTION('',(%s))" % _f3(np.asarray(d, float)))
    def vertex(self, p):   return self.add("VERTEX_POINT('',#%d)" % self.point(p))

    def axis2(self, origin, axis, ref):
        return self.add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)"
                        % (self.point(origin), self.direction(axis), self.direction(ref)))

    def line(self, p, d):
        d = np.asarray(d, float); L = float(np.linalg.norm(d)) or 1.0
        v = self.add("VECTOR('',#%d,%s)" % (self.direction(d / L), _f(L)))
        return self.add("LINE('',#%d,#%d)" % (self.point(p), v))

    def circle(self, center, axis, ref, r):
        return self.add("CIRCLE('',#%d,%s)" % (self.axis2(center, axis, ref), _f(r)))

    def plane(self, origin, normal, ref):
        return self.add("PLANE('',#%d)" % self.axis2(origin, normal, ref))

    def cyl_surface(self, base, axis, ref, r):
        return self.add("CYLINDRICAL_SURFACE('',#%d,%s)" % (self.axis2(base, axis, ref), _f(r)))

    def cone_surface(self, base, axis, ref, r, semi):
        return self.add("CONICAL_SURFACE('',#%d,%s,%s)" % (self.axis2(base, axis, ref), _f(r), _f(semi)))

    def edge_curve(self, v1, v2, curve):
        return self.add("EDGE_CURVE('',#%d,#%d,#%d,.T.)" % (v1, v2, curve))

    def oriented(self, edge, sense):
        return self.add("ORIENTED_EDGE('',*,*,#%d,%s)" % (edge, ".T." if sense else ".F."))

    def edge_loop(self, oriented_ids):
        return self.add("EDGE_LOOP('',(%s))" % ",".join("#%d" % e for e in oriented_ids))

    def face(self, loop, surface, sense=True):
        b = self.add("FACE_OUTER_BOUND('',#%d,.T.)" % loop)
        return self.add("ADVANCED_FACE('',(#%d),#%d,%s)" % (b, surface, ".T." if sense else ".F."))

    def solid(self, face_ids, name=""):
        shell = self.add("CLOSED_SHELL('',(%s))" % ",".join("#%d" % f for f in face_ids))
        sol = self.add("MANIFOLD_SOLID_BREP('%s',#%d)" % (name, shell))
        self.solids.append(sol)
        return sol

    def sphere_surface(self, center, axis, ref, r):
        return self.add("SPHERICAL_SURFACE('',#%d,%s)" % (self.axis2(center, axis, ref), _f(r)))

    # ---- serialise ----
    def dumps(self):
        # product / shape wrapper so CAD tools recognise it as a part
        shapes = ",".join("#%d" % s for s in self.solids)
        rep = self.add("ADVANCED_BREP_SHAPE_REPRESENTATION('Caliber',(%s),#%d)" % (shapes, self.ctx))
        appc = self.add("APPLICATION_CONTEXT('automotive design')")
        self.add("APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2010,#%d)" % appc)
        pctx = self.add("PRODUCT_CONTEXT('',#%d,'mechanical')" % appc)
        prod = self.add("PRODUCT('Caliber','Caliber','',(#%d))" % pctx)
        pdf = self.add("PRODUCT_DEFINITION_FORMATION('','',#%d)" % prod)
        pdc = self.add("PRODUCT_DEFINITION_CONTEXT('part definition',#%d,'design')" % appc)
        pd = self.add("PRODUCT_DEFINITION('design','',#%d,#%d)" % (pdf, pdc))
        pds = self.add("PRODUCT_DEFINITION_SHAPE('','',#%d)" % pd)
        self.add("SHAPE_DEFINITION_REPRESENTATION(#%d,#%d)" % (pds, rep))
        self.add("PRODUCT_RELATED_PRODUCT_CATEGORY('part','',(#%d))" % prod)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        header = ("ISO-10303-21;\nHEADER;\n"
                  "FILE_DESCRIPTION(('Caliber STEP export'),'2;1');\n"
                  "FILE_NAME('caliber.step','%s',(''),(''),'Caliber','Caliber','');\n"
                  "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n" % ts)
        return header + "\n".join(self.lines) + "\nENDSEC;\nEND-ISO-10303-21;\n"


def _ref_perp(axis):
    axis = np.asarray(axis, float); axis = axis / (np.linalg.norm(axis) + 1e-12)
    t = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    r = np.cross(axis, t); return r / (np.linalg.norm(r) + 1e-12)


def add_planar_solid(doc, points, faces, name=""):
    """Add a closed planar B-rep. `points` is an (N,3) array; `faces` is a list of
    vertex-index loops, each ordered CCW as seen from OUTSIDE the solid. Edges are
    shared between faces so the shell is watertight."""
    points = np.asarray(points, float)
    vid = {}
    def V(i):
        if i not in vid: vid[i] = doc.vertex(points[i])
        return vid[i]
    edge = {}      # (min,max) -> (edge_id, low_index)  canonical direction low->high
    def E(a, b):
        key = (min(a, b), max(a, b))
        if key not in edge:
            lo, hi = key
            ln = doc.line(points[lo], points[hi] - points[lo])
            edge[key] = (doc.edge_curve(V(lo), V(hi), ln), lo)
        return edge[key]
    face_ids = []
    for loop in faces:
        oriented = []
        for k in range(len(loop)):
            a, b = loop[k], loop[(k + 1) % len(loop)]
            eid, lo = E(a, b)
            oriented.append(doc.oriented(eid, sense=(a == lo)))
        el = doc.edge_loop(oriented)
        p = points[loop]
        # Newell normal (outward, since loop is CCW from outside)
        nrm = np.zeros(3)
        for k in range(len(p)):
            c, n = p[k], p[(k + 1) % len(p)]
            nrm += np.cross(c, n)
        nrm = nrm / (np.linalg.norm(nrm) + 1e-12)
        surf = doc.plane(p[0], nrm, _ref_perp(nrm))
        face_ids.append(doc.face(el, surf))
    return doc.solid(face_ids, name)


def add_box(doc, center, axes, half, name="box"):
    """axes: 3x3 unit row vectors; half: 3 half-extents."""
    center = np.asarray(center, float); axes = np.asarray(axes, float); half = np.asarray(half, float)
    pts = []
    idx = {}
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                idx[(i, j, k)] = len(pts)
                pts.append(center + (2*i-1)*half[0]*axes[0] + (2*j-1)*half[1]*axes[1] + (2*k-1)*half[2]*axes[2])
    pts = np.array(pts)
    def q(*c): return idx[c]
    # six faces, each CCW seen from outside
    faces = [
        [q(0,0,0), q(0,0,1), q(0,1,1), q(0,1,0)],   # -x
        [q(1,0,0), q(1,1,0), q(1,1,1), q(1,0,1)],   # +x
        [q(0,0,0), q(1,0,0), q(1,0,1), q(0,0,1)],   # -y
        [q(0,1,0), q(0,1,1), q(1,1,1), q(1,1,0)],   # +y
        [q(0,0,0), q(0,1,0), q(1,1,0), q(1,0,0)],   # -z
        [q(0,0,1), q(1,0,1), q(1,1,1), q(0,1,1)],   # +z
    ]
    return add_planar_solid(doc, pts, faces, name)


def add_cylinder(doc, base, axis, radius, height, name="cylinder"):
    """A capped cylinder solid: bottom + top circle edges joined by a seam line,
    lateral cylindrical face + two planar caps."""
    base = np.asarray(base, float); axis = np.asarray(axis, float)
    axis = axis / (np.linalg.norm(axis) + 1e-12)
    ref = _ref_perp(axis)
    top = base + axis * height
    pb = base + ref * radius          # seam point bottom
    pt = top + ref * radius           # seam point top
    vb = doc.vertex(pb); vt = doc.vertex(pt)
    circ_b = doc.circle(base, axis, ref, radius)
    circ_t = doc.circle(top, axis, ref, radius)
    eb = doc.edge_curve(vb, vb, circ_b)        # closed circle edges
    et = doc.edge_curve(vt, vt, circ_t)
    seam = doc.edge_curve(vb, vt, doc.line(pb, axis * height))
    # lateral surface loop
    lat_loop = doc.edge_loop([doc.oriented(seam, True), doc.oriented(et, True),
                              doc.oriented(seam, False), doc.oriented(eb, False)])
    lat = doc.face(lat_loop, doc.cyl_surface(base, axis, ref, radius))
    # bottom cap (normal -axis), top cap (normal +axis)
    bot_loop = doc.edge_loop([doc.oriented(eb, True)])
    bot = doc.face(bot_loop, doc.plane(base, -axis, ref))
    top_loop = doc.edge_loop([doc.oriented(et, False)])
    cap = doc.face(top_loop, doc.plane(top, axis, ref))
    return doc.solid([lat, bot, cap], name)


def add_cone(doc, base, axis, r1, r2, height, name="cone"):
    """A truncated cone (frustum) solid: bottom + top circles joined by a slanted seam,
    a conical lateral face, and two planar caps."""
    base = np.asarray(base, float); axis = np.asarray(axis, float)
    axis = axis / (np.linalg.norm(axis) + 1e-12)
    if r1 > r2:                                     # orient so radius grows along +axis
        base = base + axis * height; axis = -axis; r1, r2 = r2, r1
    r1 = max(r1, 1e-4)
    import math
    ref = _ref_perp(axis)
    top = base + axis * height
    semi = math.atan2(r2 - r1, height)
    pb = base + ref * r1; pt = top + ref * r2
    vb = doc.vertex(pb); vt = doc.vertex(pt)
    eb = doc.edge_curve(vb, vb, doc.circle(base, axis, ref, r1))
    et = doc.edge_curve(vt, vt, doc.circle(top, axis, ref, r2))
    seam = doc.edge_curve(vb, vt, doc.line(pb, pt - pb))
    surf = doc.cone_surface(base, axis, ref, r1, semi)
    lat = doc.face(doc.edge_loop([doc.oriented(seam, True), doc.oriented(et, True),
                                  doc.oriented(seam, False), doc.oriented(eb, False)]), surf)
    bot = doc.face(doc.edge_loop([doc.oriented(eb, True)]), doc.plane(base, -axis, ref))
    cap = doc.face(doc.edge_loop([doc.oriented(et, False)]), doc.plane(top, axis, ref))
    return doc.solid([lat, bot, cap], name)


def add_sphere(doc, center, radius, name="dome", nlon=18, nlat=12):
    """A faceted sphere solid (UV tessellation). Hand-written analytic spheres are
    degenerate at the poles and risk breaking the whole STEP; a fine facet sphere is a
    guaranteed-valid solid that stands in for a dome/cap and is still editable in Fusion."""
    center = np.asarray(center, float)
    pts = []
    for j in range(1, nlat):
        phi = math.pi * j / nlat
        for i in range(nlon):
            th = 2 * math.pi * i / nlon
            pts.append(center + radius * np.array([math.sin(phi) * math.cos(th),
                                                   math.sin(phi) * math.sin(th), math.cos(phi)]))
    north = len(pts); pts.append(center + np.array([0, 0, radius]))
    south = len(pts); pts.append(center + np.array([0, 0, -radius]))
    pts = np.array(pts)

    def ring(j, i): return j * nlon + (i % nlon)
    faces = []
    for j in range(nlat - 2):
        for i in range(nlon):
            faces.append([ring(j, i), ring(j, i + 1), ring(j + 1, i + 1), ring(j + 1, i)])
    for i in range(nlon):
        faces.append([north, ring(0, i + 1), ring(0, i)])
        faces.append([south, ring(nlat - 2, i), ring(nlat - 2, i + 1)])
    # ensure every loop winds CCW as seen from outside (normal points away from centre)
    fixed = []
    for loop in faces:
        q = pts[loop]
        nrm = np.zeros(3)
        for k in range(len(q)):
            nrm += np.cross(q[k], q[(k + 1) % len(q)])
        if nrm @ (q.mean(0) - center) < 0:
            loop = loop[::-1]
        fixed.append(loop)
    return add_planar_solid(doc, pts, fixed, name)


def _oriented_box(verts):
    """Oriented bounding box of a point set via PCA. Returns (center, axes3x3, half3)."""
    verts = np.asarray(verts, float)
    c = verts.mean(0)
    _, V = np.linalg.eigh(np.cov((verts - c).T))
    axes = V.T[::-1]                                # largest variance first
    proj = (verts - c) @ axes.T
    lo, hi = proj.min(0), proj.max(0)
    center = c + ((lo + hi) / 2) @ axes
    half = (hi - lo) / 2
    return center, axes, half


def primitives_to_step(verts, faces, parts, out_path):
    """Build a STEP file from detected primitives. Cylinders become cylinder solids;
    a box-like object (mostly planes) becomes a box solid. Returns a short summary."""
    verts = np.asarray(verts, float); faces = np.asarray(faces)

    # How much of the surface is actually clean primitives vs freeform "residual"?
    # A bottle/bracket is ~all primitives; a car body is ~all freeform. Don't hand the
    # user a jumble of false cylinders forced onto a sculpted shape.
    total_area = sum(p.get("score", 0.0) for p in parts) or 1.0
    prim_area = sum(p.get("score", 0.0) for p in parts if p["type"] != "residual")
    coverage = prim_area / total_area
    if coverage < 0.55:
        raise ValueError(
            "This object is mostly freeform (only %d%% of it fits clean shapes) — a car body, a "
            "tail-light housing, anything sculpted. STEP would just be a jumble of false cylinders. "
            "Export the mesh (OBJ/STL) instead and rebuild on it in CAD; STEP suits mechanical/"
            "primitive parts (flat faces, holes, cylinders, tapers)." % round(coverage * 100))

    doc = StepDoc()
    plane_parts = [p for p in parts if p["type"] == "plane"]
    cyl_parts = [p for p in parts if p["type"] == "cylinder"]
    cone_parts = [p for p in parts if p["type"] == "cone"]
    sph_parts = [p for p in parts if p["type"] == "sphere"]
    bodies, holes = [], []
    nC = nH = nT = nD = 0

    for p in cyl_parts:
        pr = p["params"]; axis = np.asarray(pr["axis"], float)
        cents = verts[faces[p["faces"]]].mean(axis=1)
        along = (cents - pr["point"]) @ (axis / (np.linalg.norm(axis) + 1e-12))
        base = pr["point"] + axis * along.min()
        if pr.get("outward", True):
            nC += 1
            add_cylinder(doc, base, axis, pr["radius"], pr["length"], "cylinder_%d" % nC)
            bodies.append("cylinder Ø%.1f" % (2 * pr["radius"]))
        else:                                          # inward-facing surface = a bore/hole
            nH += 1
            add_cylinder(doc, base, axis, pr["radius"], pr["length"], "hole_%d" % nH)
            holes.append("hole Ø%.1f" % (2 * pr["radius"]))

    for p in cone_parts:
        pr = p["params"]; nT += 1
        add_cone(doc, pr["base"], pr["axis"], pr["r1"], pr["r2"], pr["length"], "taper_%d" % nT)
        bodies.append("taper Ø%.1f→%.1f" % (2 * pr["r1"], 2 * pr["r2"]))

    for p in sph_parts:
        pr = p["params"]; nD += 1
        add_sphere(doc, pr["center"], pr["radius"], "dome_%d" % nD)
        bodies.append("dome Ø%.1f" % (2 * pr["radius"]))

    # An all-flat object with no curved parts → a box.
    if plane_parts and not cyl_parts and not cone_parts and not sph_parts:
        used = np.unique(faces[np.concatenate([p["faces"] for p in plane_parts])])
        c, axes, half = _oriented_box(verts[used])
        add_box(doc, c, axes, half, "box")
        bodies.append("box %.1f×%.1f×%.1f" % tuple(2 * half))

    if not doc.solids:
        raise ValueError("No primitive solids could be built (object is too organic for STEP). "
                         "Export the mesh OBJ instead.")
    with open(out_path, "w") as f:
        f.write(doc.dumps())

    s = "Wrote %d solid(s)." % len(doc.solids)
    if coverage < 0.7:
        s += (" Heads-up: ~%d%% of this object is freeform, so only its mechanical bits are in the "
              "STEP — keep the mesh for the sculpted parts." % round((1 - coverage) * 100))
    if bodies:
        s += " Bodies: " + ", ".join(bodies) + "."
    if holes:
        s += " Holes (cut these): " + ", ".join(holes) + "."
    s += ("  In Fusion: select the body solids -> Combine -> Join to merge them; then select a "
          "hole solid + the body -> Combine -> Cut to bore each hole.")
    return s
