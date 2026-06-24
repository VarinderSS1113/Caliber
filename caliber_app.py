#!/usr/bin/env python3
"""
Caliber — local app.

The Caliber pipeline server. It drives the whole flow locally — capture/import ->
reconstruct -> clean + scale -> preview -> export a printable STL — orchestrating
the three tools you built:

  • caliber-recon   (Apple Object Capture, free local photogrammetry)   [macOS]
  • caliber_gen.py  (fal.ai generative, the paid cloud tier)            [needs FAL_KEY]
  • caliber_prep.py (clean / watertight / scale; reads obj/stl/glb)     [needs numpy]

Normal use is the native app — double-click Caliber.app, which opens Caliber in its
own window (see caliber_desktop.py). For development you can also run the server
directly and use it in a browser:

  python3 caliber_app.py     ->     http://127.0.0.1:8765

The server binds loopback only. No dependencies beyond numpy (for prep).
"""
import os, sys, json, time, uuid, struct, threading, webbrowser, subprocess, tempfile, re, atexit, shutil, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Packaged .app launches with an ASCII locale; make our own stdout/stderr UTF-8 tolerant
# so Unicode in log lines never raises (subprocess decoding is handled in run_cmd).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
HOST = "127.0.0.1"                      # loopback only — never exposed to the network
SESSION = tempfile.mkdtemp(prefix="caliber-app-")
UP = {}       # uploaded files:  id -> {path,name,ext}
RESULTS = {}  # result files:    id -> path
LOCK = threading.Lock()                # guards UP / RESULTS / KEY across worker threads

# Per-launch secret. The UI is handed this token and must echo it on every /api call
# (header or ?t=). Any other local process or web page that reaches the loopback port
# without it is rejected — defence-in-depth on top of the Host/Origin checks.
TOKEN = secrets.token_urlsafe(24)

MAX_UPLOAD = 1024 * 1024 * 1024        # 1 GB cap per upload (videos); prevents memory/disk DoS
MAX_JSON   = 4 * 1024 * 1024           # 4 MB cap for control requests
MAX_FILES  = 1000                      # cap stored uploads per session

# 3D-preview libraries are served locally (see /vendor/). If a copy isn't bundled yet
# (e.g. running from source before vendoring), the server redirects to the pinned CDN so
# the preview still works in development; the shipped app bundles them and never calls out.
VENDOR_CDN = {
    "three.min.js":  "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js",
    "STLLoader.js":  "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js",
}
def vendor_dir():
    for d in (os.path.join(os.environ.get("RESOURCEPATH", ""), "vendor"),
              os.path.join(APP_DIR, "assets", "vendor")):
        if d and os.path.isdir(d):
            return d
    return os.path.join(APP_DIR, "assets", "vendor")

# clean temp working files on exit (uploaded photos, intermediate meshes, results)
atexit.register(lambda: shutil.rmtree(SESSION, ignore_errors=True))

def safe_ext(name):
    """Return a sanitised lowercase extension (no path separators / odd chars)."""
    e = os.path.splitext(name or "")[1].lower()
    return re.sub(r"[^a-z0-9.]", "", e)[:8]

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".webm", ".avi", ".mkv", ".3gp"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
MESH_EXT  = {".glb", ".obj", ".stl", ".ply"}


# ----------------------------- tool discovery -----------------------------
#
# prep and gen are imported as modules and run in-process, so the app works the
# same when frozen into a .app bundle (where you can't spawn `python script.py`).
# recon is a native binary (the Swift Object Capture engine), so it stays a
# subprocess — we just need to find it inside the bundle or next to the app.

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

try:
    import caliber_prep as PREPMOD
except Exception as e:
    PREPMOD = None
    print("note: print-prep module unavailable:", e)

try:
    import caliber_gen as GENMOD
except Exception as e:
    GENMOD = None
    print("note: cloud-engine module unavailable:", e)

try:
    import caliber_cad as CADMOD          # STEP / B-rep export (Stage 2 scan-to-CAD)
except Exception as e:
    CADMOD = None
    print("note: STEP module unavailable:", e)

PREP = PREPMOD is not None
GEN = GENMOD is not None

def find_recon():
    cands = [
        os.path.join(APP_DIR, "caliber-recon"),
        os.path.join(os.environ.get("RESOURCEPATH", ""), "caliber-recon"),   # inside a py2app .app
        os.path.join(APP_DIR, "caliber-recon", ".build", "release", "caliber-recon"),
        os.path.join(APP_DIR, ".build", "release", "caliber-recon"),
    ]
    for c in cands:
        if c and os.path.exists(c) and os.access(c, os.X_OK):
            return os.path.abspath(c)
    return None

RECON = find_recon()

# bring-your-own fal.ai key — the app is free & open-source; users pay fal directly.
# stored locally in the user's home, not in the shipped folder.
CONFIG_DIR = os.path.expanduser("~/.caliber")
CONFIG = os.path.join(CONFIG_DIR, "config.json")

def load_key():
    try:
        with open(CONFIG) as f:
            return (json.load(f).get("fal_key") or "").strip()
    except Exception:
        return (os.environ.get("FAL_KEY") or "").strip()

def set_key(k):
    global KEY
    with LOCK:
        KEY = (k or "").strip()
        body = KEY
    try:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        # Create owner-only from the start (avoid a world-readable window before chmod).
        fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"fal_key": body}, f)
    except Exception as e:
        print("warning: could not save key file securely:", e)

KEY = load_key()


# ----------------------------- pipeline -----------------------------

class Err(Exception):
    pass

def sp(name):
    return os.path.join(SESSION, name)

def run_cmd(cmd, log, env=None):
    log("$ " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    # Decode the engine's output as UTF-8 explicitly. The native engine prints Unicode
    # (…, →, ✅); in the packaged .app the default locale is ASCII, so without this the
    # reader would crash on the first non-ASCII byte. errors="replace" never throws.
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace",
                         env=env or os.environ.copy())
    out = []
    for line in p.stdout:
        line = line.rstrip()
        out.append(line)
        log(line)
    p.wait()
    if p.returncode != 0:
        tail = next((l for l in reversed(out) if l.strip()), "")
        raise Err("engine exited %d%s" % (p.returncode, (": " + tail) if tail else ""))
    return "\n".join(out)

def stl_stats(path):
    with open(path, "rb") as f:
        d = f.read()
    n = struct.unpack_from("<I", d, 80)[0]
    import numpy as np
    tr = np.frombuffer(d, np.uint8, count=n * 50, offset=84).reshape(n, 50)
    v = np.zeros((n, 3, 3))
    for k in range(3):
        v[:, k, :] = tr[:, 12 + k * 12:24 + k * 12].copy().view("<f4").reshape(n, 3)
    V = v.reshape(-1, 3)
    ext = V.max(0) - V.min(0)
    return {"triangles": int(n), "size_mm": [round(float(x), 1) for x in ext]}

def run_tool(mod, argv, log, env_extra=None):
    """Run a Caliber CLI tool (prep/gen) in-process by calling its main() with a
    synthesised argv, capturing its output. Works inside a frozen .app, where
    spawning a separate python interpreter isn't possible."""
    import io, contextlib
    saved_argv = sys.argv
    saved_env = {}
    if env_extra:
        for k, v in env_extra.items():
            saved_env[k] = os.environ.get(k)
            os.environ[k] = v
    buf = io.StringIO()
    log("$ " + os.path.basename(argv[0]) + " " + " ".join(argv[1:]))
    rc = None
    try:
        sys.argv = list(argv)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = mod.main()
    except SystemExit as e:                       # argparse / sys.exit() inside the tool
        rc = e.code if isinstance(e.code, int) else (0 if not e.code else 1)
    finally:
        sys.argv = saved_argv
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    out = buf.getvalue()
    for line in out.splitlines():
        log(line.rstrip())
    if rc not in (None, 0):
        raise Err("step failed: " + (out.strip().splitlines() or ["exit %s" % rc])[-1])
    return out

def native_env():
    """Environment for spawning the native engine. A frozen .app exports DYLD_*/PYTHON*
    vars that point into the bundle; if the native Swift binary inherits them it loads the
    wrong libraries and Object Capture (RealityKit) fails. Strip them so it links against
    the system frameworks, exactly as it does when run from a normal shell."""
    env = os.environ.copy()
    for k in list(env):
        if k.startswith("DYLD_") or k in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONNOUSERSITE"):
            env.pop(k, None)
    return env

def run_recon(inputs, out_obj, log):
    if not RECON:
        raise Err("Local engine (Object Capture) not found. It runs on macOS — build caliber-recon "
                  "and place it next to this app. (You can still use Import or Cloud.)")
    if isinstance(inputs, str):
        inputs = [inputs]
    try:
        run_cmd([RECON] + list(inputs) + [out_obj, "--detail", "full", "--frames", "150"],
                log, env=native_env())
    except Err as e:
        # Keep the engine's real message in the headline; add capture guidance after it.
        raise Err("Local reconstruction failed — %s. Apple Object Capture needs many OVERLAPPING "
                  "photos (each a small step from the last) or a slow orbit VIDEO of a matte, "
                  "textured object — a few spread-out angles (front/back/left/right…) don't overlap "
                  "enough to solve. See the log for the exact engine error, or use the Cloud engine." % e)
    if not os.path.exists(out_obj) or os.path.getsize(out_obj) < 200:
        raise Err("Local reconstruction finished but wrote no usable mesh (the engine exported an "
                  "empty file). See the log for details; try the Cloud engine if it persists.")

VIEWS = ["front", "back", "left", "right", "top", "bottom"]

def assign_views(files):
    """Map photos to views. Honour filenames that name a view (front/back/left/right/
    top/bottom); fill any remaining views with the rest in upload order. Cloud uses up
    to 6 views, so extras beyond that are dropped."""
    assigned = {}
    leftover = []
    for f in files:
        nm = (f.get("name") or "").lower()
        v = next((v for v in VIEWS if v in nm), None)
        if v and v not in assigned:
            assigned[v] = f["path"]
        else:
            leftover.append(f["path"])
    unused = [v for v in VIEWS if v not in assigned]
    for v, p in zip(unused, leftover):       # front gets filled first, so --front is always set
        assigned[v] = p
    return assigned

def extract_cloud_frames(video_path, k, log):
    """Pull the k sharpest, well-spread frames from a video (via the local engine binary)
    so a video can feed the Cloud generative engine — which itself only takes photos.
    TRELLIS multi-view uses unordered images, so no view labels are needed."""
    if not RECON:
        raise Err("To use a video with the Cloud engine, Caliber pulls still frames with the local "
                  "engine — but caliber-recon isn't built. Build it, or upload 4–6 photos for Cloud.")
    import glob
    outdir = sp("cloudframes_%s" % uuid.uuid4().hex[:8])
    os.makedirs(outdir, exist_ok=True)
    run_cmd([RECON, "--export-frames", str(k), video_path, outdir], log, env=native_env())
    paths = sorted(glob.glob(os.path.join(outdir, "view_*.jpg")))
    if not paths:
        raise Err("Couldn't pull frames from the video. Try uploading photos for Cloud instead.")
    return [{"path": p, "name": os.path.basename(p), "ext": ".jpg", "kind": "image"} for p in paths]

def run_gen(files, out_glb, log):
    if not GENMOD:
        raise Err("Cloud engine module not available in this build.")
    if not KEY:
        raise Err("Cloud engine needs your fal.ai API key — paste it into the app (free account at fal.ai; "
                  "you pay fal directly, a few cents per model).")
    if len(files) > 6:
        log("note: Cloud uses up to 6 views — the first 6 photos (by name/order) are used.")
    assigned = assign_views(files[:64])
    argv = ["caliber_gen"]
    for v in VIEWS:
        if v in assigned:
            argv += ["--" + v, assigned[v]]
    argv += ["-o", out_glb]
    run_tool(GENMOD, argv, log, env_extra={"FAL_KEY": KEY})

def run_prep(model, out_stl, ref_mm, ref_axis, log):
    if not PREPMOD:
        raise Err("Print-prep module not available in this build.")
    argv = ["caliber_prep", model, out_stl, "--auto"]
    if ref_mm:
        argv += ["--ref-mm", str(ref_mm), "--ref-axis", ref_axis or "longest"]
    txt = run_tool(PREPMOD, argv, log)
    st = {}
    m = re.search(r"watertight:\s*(\w+)", txt);    st["watertight"] = (m.group(1) == "yes") if m else None
    return st

def run_pipeline(ids, engine, ref_mm, ref_axis, log):
    files = [UP[i] for i in ids if i in UP]
    if not files:
        raise Err("No files uploaded.")
    exts = [f["ext"] for f in files]
    has_video = any(e in VIDEO_EXT for e in exts)
    images = [f for f in files if f["ext"] in IMAGE_EXT]
    out_stl = sp("result_%s.stl" % uuid.uuid4().hex[:8])

    if len(files) == 1 and files[0]["ext"] in MESH_EXT:
        model = files[0]["path"]
        log("Imported %s — cleaning + scaling." % files[0]["name"])
    else:
        # Auto sends a small photo set to Cloud (if a key is set); a video, lots of photos,
        # or explicit Local all go to local Object Capture.
        want_cloud = (engine == "cloud") or (
            engine == "auto" and not has_video and bool(KEY) and 1 <= len(images) <= 8)
        if want_cloud:
            cloud_views = list(images)                     # uploaded photos (labels honoured)
            if has_video and len(cloud_views) < 6:
                vid = next(f for f in files if f["ext"] in VIDEO_EXT)
                need = 6 - len(cloud_views)
                log("Pulling %d sharp frame(s) from the video for Cloud…" % need)
                cloud_views += extract_cloud_frames(vid["path"], need, log)
            if not cloud_views:
                raise Err("Cloud needs photos, or a video to pull frames from.")
            model = sp("gen.glb")
            log("Cloud reconstruction (generative) from %d view(s)…" % len(cloud_views))
            run_gen(cloud_views, model, log)
        else:
            # Local reconstruction from EVERYTHING provided — video frames and photos
            # together, for more coverage and detail. The engine merges them.
            local_inputs = [f["path"] for f in files if f["ext"] in VIDEO_EXT or f["ext"] in IMAGE_EXT]
            if not local_inputs:
                raise Err("Local reconstruction needs a video and/or photos.")
            nvid = sum(1 for f in files if f["ext"] in VIDEO_EXT)
            if nvid and images:
                log("Local reconstruction (Object Capture) from %d video(s) + %d photo(s)…" % (nvid, len(images)))
            elif nvid:
                log("Local reconstruction (Object Capture) from video…")
            else:
                if engine == "auto" and len(images) <= 8:
                    log("note: few photos and no Cloud key — trying Local with %d. It can still "
                        "work; more overlapping angles help if it doesn't." % len(images))
                elif len(images) < 12:
                    log("note: trying Local with %d photo(s). Fewer can still solve; add more "
                        "overlapping angles if it doesn't come out (or use Cloud)." % len(images))
                log("Local reconstruction (Object Capture) from %d photo(s)…" % len(images))
            model = sp("recon.obj")
            run_recon(local_inputs, model, log)

    st = run_prep(model, out_stl, ref_mm, ref_axis, log)
    st.update(stl_stats(out_stl))
    rid = uuid.uuid4().hex
    with LOCK:
        RESULTS[rid] = out_stl
    return rid, st


def make_export(rid, fmt):
    """Produce an export file for a result in the requested format; return its path
    (or None). STL is the stored file; obj/ply/3mf are written via the prep module.
    Shared by the HTTP /api/export endpoint and the native save dialog."""
    src = RESULTS.get(rid)
    if not src or not os.path.exists(src):
        return None
    fmt = (fmt or "stl").lower()
    if fmt == "stl":
        return src
    if not PREPMOD:
        return None
    V, F = PREPMOD.load_mesh(src)
    if fmt == "parts":
        # geometric segmentation: each detected primitive as its own OBJ object
        out = sp("export_%s_parts.obj" % rid[:8])
        parts = PREPMOD.segment_primitives(V, F)
        PREPMOD.write_obj_groups(out, V, F, parts)
        return out
    if fmt == "step":
        # editable B-rep solids (cylinders / boxes) for Fusion / SolidWorks
        if not CADMOD:
            raise Err("STEP export module not available in this build.")
        out = sp("export_%s.step" % rid[:8])
        parts = PREPMOD.segment_primitives(V, F)
        CADMOD.primitives_to_step(V, F, parts, out)   # raises ValueError if too organic
        return out
    out = sp("export_%s.%s" % (rid[:8], fmt))
    if fmt == "obj":   PREPMOD.write_obj(out, V, F)
    elif fmt == "ply": PREPMOD.write_ply(out, V, F)
    elif fmt == "3mf": PREPMOD.write_3mf(out, V, F)
    else:              return None
    return out


# ----------------------------- http server -----------------------------

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # --- anti DNS-rebinding / CSRF: only accept requests that came to the loopback host,
    #     and reject browser requests whose Origin isn't this app. A malicious website
    #     (even one resolving its domain to 127.0.0.1) is blocked on both checks. ---
    def _host_ok(self):
        h = self.headers.get("Host", "")
        return h in ("%s:%d" % (HOST, PORT), "localhost:%d" % PORT)

    def _origin_ok(self):
        o = self.headers.get("Origin")
        if not o:
            return True
        try:
            return urlparse(o).hostname in (HOST, "localhost")
        except Exception:
            return False

    def _token_ok(self, u):
        # The UI echoes the per-launch token on every /api call (header, or ?t= for
        # download/loader URLs that can't set headers). Constant-time compare.
        t = self.headers.get("X-Caliber-Token") or parse_qs(u.query).get("t", [""])[0]
        return secrets.compare_digest(t or "", TOKEN)

    def _serve_vendor(self, u):
        name = os.path.basename(u.path)
        if name not in VENDOR_CDN:                       # only known assets
            return self._send(404, {"error": "not found"})
        local = os.path.join(vendor_dir(), name)
        if os.path.exists(local):
            with open(local, "rb") as f:
                return self._send(200, f.read(), "application/javascript")
        # Not vendored (dev/source run): redirect to the pinned CDN copy.
        self.send_response(302)
        self.send_header("Location", VENDOR_CDN[name])
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        u = urlparse(self.path)
        if u.path == "/" or u.path == "/index.html":
            page = HTML.replace("__CALIBER_TOKEN__", TOKEN)
            return self._send(200, page, "text/html; charset=utf-8")
        if u.path.startswith("/vendor/"):
            return self._serve_vendor(u)
        # everything else is API and requires the per-launch token
        if not u.path.startswith("/api/") or not self._token_ok(u):
            return self._send(403, {"error": "forbidden"})
        if u.path == "/api/health":
            return self._send(200, {"recon": bool(RECON), "gen": bool(GEN),
                                    "fal_key": bool(KEY), "prep": bool(PREP)})
        if u.path == "/api/export":
            q = parse_qs(u.query)
            rid = q.get("id", [""])[0]
            fmt = q.get("fmt", ["stl"])[0].lower()
            if fmt not in ("stl", "obj", "ply", "3mf", "parts", "step"):
                return self._send(400, {"error": "unsupported format"})
            try:
                out = make_export(rid, fmt)
            except Exception as e:
                return self._send(500, {"error": str(e)})
            if not out or not os.path.exists(out):
                return self._send(404, {"error": "no such result"})
            with open(out, "rb") as f:
                return self._send(200, f.read(), "application/octet-stream")
        if u.path.startswith("/api/file/"):
            rid = u.path.rsplit("/", 1)[-1]
            path = RESULTS.get(rid) or (UP[rid]["path"] if rid in UP else None)
            if path and os.path.exists(path):
                with open(path, "rb") as f:
                    data = f.read()
                return self._send(200, data, "application/octet-stream")
            return self._send(404, {"error": "not found"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden host"})
        if not self._origin_ok():
            return self._send(403, {"error": "cross-origin request blocked"})
        u = urlparse(self.path)
        if not self._token_ok(u):
            return self._send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length", 0) or 0)

        if u.path == "/api/upload":
            if n <= 0 or n > MAX_UPLOAD:
                return self._send(413, {"error": "upload empty or too large"})
            if len(UP) >= MAX_FILES:
                return self._send(429, {"error": "too many files this session"})
            q = parse_qs(u.query)
            name = os.path.basename(q.get("name", ["file"])[0])     # strip any path
            ext = safe_ext(name)
            fid = uuid.uuid4().hex
            path = sp("up_%s%s" % (fid, ext))                       # filename is server-controlled
            remaining, ok = n, True
            with open(path, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        ok = False; break
                    f.write(chunk); remaining -= len(chunk)
            if not ok:
                return self._send(400, {"error": "upload interrupted"})
            kind = ("video" if ext in VIDEO_EXT else "image" if ext in IMAGE_EXT
                    else "mesh" if ext in MESH_EXT else "other")
            with LOCK:
                UP[fid] = {"path": path, "name": name, "ext": ext, "kind": kind}
            return self._send(200, {"id": fid, "name": name, "kind": kind})

        # control endpoints carry small JSON only
        if n > MAX_JSON:
            return self._send(413, {"error": "request too large"})
        try:
            req = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception:
            return self._send(400, {"error": "bad json"})

        if u.path == "/api/key":
            set_key(req.get("key", ""))
            return self._send(200, {"ok": True, "set": bool(KEY)})

        if u.path == "/api/run":
            ref = req.get("refMm")
            try:
                ref = float(ref) if ref is not None else None
                if ref is not None and not (0 < ref < 1e6):
                    ref = None
            except Exception:
                ref = None
            axis = req.get("refAxis", "longest")
            if axis not in ("longest", "x", "y", "z"):
                axis = "longest"
            engine = req.get("engine", "auto")
            if engine not in ("auto", "local", "cloud"):
                engine = "auto"
            ids = [i for i in req.get("ids", []) if isinstance(i, str)][:MAX_FILES]
            logs = []
            try:
                rid, st = run_pipeline(ids, engine, ref, axis, logs.append)
                return self._send(200, {"ok": True, "resultId": rid, "stats": st, "log": logs})
            except Err as e:
                return self._send(200, {"ok": False, "error": str(e), "log": logs})
            except Exception as e:
                # Local app: surface the real reason (and a trace in the log) so problems
                # are diagnosable instead of a blank "Unexpected error".
                import traceback
                for line in traceback.format_exc().splitlines():
                    logs.append(line)
                return self._send(200, {"ok": False,
                                        "error": "%s: %s" % (type(e).__name__, e),
                                        "log": logs})

        if u.path == "/api/rescale":
            # Two-point measurement: the UI sends the distance it measured between two
            # picked points (in current model units) and the real distance the user typed.
            # We scale the whole result uniformly so that measured distance == real mm.
            rid = req.get("id", "")
            src = RESULTS.get(rid)
            if not src or not os.path.exists(src):
                return self._send(404, {"ok": False, "error": "no such result"})
            if not PREPMOD:
                return self._send(500, {"ok": False, "error": "rescale unavailable (prep not loaded)"})
            try:
                measured = float(req.get("measured"))
                real_mm = float(req.get("realMm"))
            except Exception:
                return self._send(400, {"ok": False, "error": "need numeric measured + realMm"})
            if not (measured > 0 and 0 < real_mm < 1e7):
                return self._send(400, {"ok": False, "error": "measurement out of range"})
            factor = real_mm / measured
            if not (1e-6 < factor < 1e6):
                return self._send(400, {"ok": False, "error": "resulting scale out of range"})
            try:
                import numpy as np
                V, F = PREPMOD.load_mesh(src)
                V = np.asarray(V, dtype=float) * factor
                PREPMOD.write_stl(src, V, F)            # overwrite so every export follows the new scale
                st = {"watertight": None}
                st.update(stl_stats(src))
                return self._send(200, {"ok": True, "stats": st, "factor": factor})
            except Exception as e:
                return self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

        if u.path == "/api/discard":
            # Clear a result (and its file) so the user can start over cleanly.
            rid = req.get("id", "")
            with LOCK:
                src = RESULTS.pop(rid, None)
            if src:
                try:
                    if os.path.exists(src):
                        os.remove(src)
                except Exception:
                    pass
            return self._send(200, {"ok": True})

        return self._send(404, {"error": "not found"})


def _make_server(want_port):
    """Bind the loopback server, falling back to an OS-assigned free port if busy."""
    try:
        return ThreadingHTTPServer((HOST, want_port), H)
    except OSError:
        return ThreadingHTTPServer((HOST, 0), H)


def serve(open_browser=True, port=None):
    """Start the Caliber server on loopback and return (server, url).

    Used both by `python3 caliber_app.py` (browser mode) and by the native
    desktop launcher (caliber_desktop.py), which renders the URL in its own
    window instead of a browser.
    """
    global PORT
    if not PREP:
        print("WARNING: print-prep module (caliber_prep) could not be imported")
    srv = _make_server(PORT if port is None else port)
    PORT = srv.server_address[1]            # keep the global in sync for the Host check
    url = "http://%s:%d" % (HOST, PORT)
    print("Caliber running at", url)
    print("  local engine (Object Capture):", "ready" if RECON else "not found (macOS build)")
    print("  cloud engine (fal generative):", "ready" if (GEN and KEY) else "paste your fal.ai key in the UI to enable")
    print("  print-prep:", "ready" if PREP else "MISSING")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    return srv, url


def main():
    srv, _ = serve(open_browser=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Caliber</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Space+Grotesk:wght@500&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--ink:#15191F;--paper:#fff;--surface:#F7F8FA;--surf2:#fff;--bd:#E6E9ED;--tx:#15191F;--t2:#5F6873;--t3:#8C95A0;--sig:#2D6BE3;--sigbg:#EAF1FC;--ok:#2F8F5B;--okbg:#E7F3EC;--mono:'JetBrains Mono',monospace}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--tx);font-family:'Inter',system-ui,sans-serif;font-size:15px}
.mono{font-family:var(--mono)}
header{background:var(--paper);border-bottom:.5px solid var(--bd);padding:12px 22px;display:flex;align-items:center;gap:10px;position:sticky;top:0;z-index:5}
.wm{font-family:'Space Grotesk';font-weight:500;letter-spacing:.12em;font-size:16px}
.hlinks{margin-left:auto;display:flex;align-items:center;gap:16px;font-size:13px}
.hlinks a{color:var(--t2);text-decoration:none}
.hlinks a:hover{color:var(--tx)}
.hlinks a.sup{color:var(--sig);font-weight:500}
.ghbtn{display:inline-flex;align-items:center;gap:6px;border:.5px solid var(--bd);border-radius:8px;padding:6px 11px;color:var(--tx)!important}
.ghbtn:hover{border-color:var(--t3)}
#engines{font-size:11px;color:var(--t3)}
@media(max-width:680px){.hlinks .hl-collapse{display:none}}
footer{border-top:.5px solid var(--bd);margin-top:48px;padding:22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--t3);max-width:980px;margin-left:auto;margin-right:auto}
footer a{color:var(--t2);text-decoration:none}
footer a:hover{color:var(--tx)}
footer .sp{flex:1}
footer .wm{font-size:14px}
.wrap{max-width:980px;margin:0 auto;padding:24px 22px 80px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.card{background:var(--surf2);border:.5px solid var(--bd);border-radius:14px;padding:18px}
h1{font-family:'Space Grotesk';font-weight:500;font-size:24px;margin:4px 0 2px}
.sub{color:var(--t2);font-size:14px;margin:0 0 18px}
.lab{font-size:11px;letter-spacing:.05em;color:var(--t3);margin:0 0 8px}
.drop{border:1.5px dashed #D5DAE0;border-radius:12px;padding:26px;text-align:center;cursor:pointer;transition:.12s}
.drop.hot{border-color:var(--sig);background:var(--sigbg)}
.drop h3{font-family:'Space Grotesk';font-weight:500;margin:0 0 4px}.drop p{color:var(--t2);font-size:13px;margin:0}
.files{display:flex;flex-direction:column;gap:6px;margin-top:12px}
.file{display:flex;align-items:center;gap:8px;font-size:13px;background:var(--surface);border:.5px solid var(--bd);border-radius:8px;padding:7px 10px}
.file .x{margin-left:auto;color:var(--t3);cursor:pointer}
.row{display:flex;align-items:center;gap:10px;margin:12px 0}
.seg{display:flex;background:var(--surface);border:.5px solid var(--bd);border-radius:18px;padding:2px;font-size:12px}
.seg button{border:none;background:transparent;color:var(--t2);border-radius:16px;padding:6px 13px;cursor:pointer}
.seg button.on{background:var(--sig);color:#fff}
input,select{font-family:inherit;font-size:14px;border:.5px solid var(--bd);border-radius:8px;padding:9px 10px;background:#fff;color:var(--tx)}
input.mono{font-family:var(--mono)}
.btn{display:inline-flex;align-items:center;gap:8px;font-size:14px;border-radius:9px;padding:11px 18px;border:.5px solid var(--sig);background:var(--sig);color:#fff;cursor:pointer}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.ghost{background:transparent;color:var(--tx);border-color:#D5DAE0}
.btn.ghost.on{background:var(--sig);color:#fff;border-color:var(--sig)}
#viewport{width:100%;height:300px;background:var(--ink);border-radius:10px;touch-action:none}
#viewport.measuring{cursor:crosshair}
#viewport canvas{display:block;border-radius:10px}
.pill{font-family:var(--mono);font-size:11px;padding:4px 10px;border-radius:16px;background:var(--okbg);color:var(--ok)}
.logbox{font-family:var(--mono);font-size:11.5px;color:var(--t2);background:#0E1116;color:#AEB6BF;border-radius:10px;padding:12px;max-height:170px;overflow:auto;white-space:pre-wrap;margin-top:12px}
.errbox{margin-top:12px;border-radius:10px;padding:12px 14px;background:#FCEDEC;border:.5px solid #F1C6C2;color:#9B2C22;font-size:13px;line-height:1.45}
.errbox b{color:#7A211A}
.errbox .lnk{color:var(--sig);cursor:pointer;text-decoration:underline}
.notebox{position:relative;margin-top:12px;border-radius:10px;padding:11px 30px 11px 13px;background:var(--sigbg);border:.5px solid #CFE0FA;color:var(--tx);font-size:13px;line-height:1.5}
.notebox .lnk{color:var(--sig);cursor:pointer;text-decoration:underline;font-weight:500}
.notebox .x{position:absolute;top:8px;right:10px;color:var(--t3);cursor:pointer;font-size:12px}
.notebox .x:hover{color:var(--tx)}
.hide{display:none}
.status{font-size:12px;color:var(--t3)}
.status.warn{color:#9B2C22;background:#FCEDEC;border:.5px solid #F1C6C2;border-radius:8px;padding:8px 10px}
.status b{color:var(--tx)}
.dimrow{display:flex;justify-content:space-between;font-size:13px;padding:4px 0;border-bottom:.5px solid var(--bd)}
.dimrow .mono{color:var(--tx)}
</style></head><body>
<header>
 <svg width="20" height="20" viewBox="50 50 140 140" fill="#2D6BE3"><rect x="64" y="64" width="12" height="112" rx="6"/><rect x="64" y="58" width="40" height="12" rx="6"/><rect x="64" y="170" width="40" height="12" rx="6"/><rect x="164" y="64" width="12" height="112" rx="6"/><rect x="136" y="58" width="40" height="12" rx="6"/><rect x="136" y="170" width="40" height="12" rx="6"/><circle cx="120" cy="120" r="14"/></svg>
 <span class="wm">CALIBER</span>
 <span class="hlinks">
   <span class="status mono hl-collapse" id="engines"></span>
   <a class="hl-collapse" href="https://t.me/va_rinder" target="_blank" rel="noopener">Contact</a>
   <a class="sup" href="https://ko-fi.com/singhvarinder" target="_blank" rel="noopener">♥ Support</a>
   <a class="ghbtn" href="https://github.com/VarinderSS1113/caliber" target="_blank" rel="noopener"><svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>GitHub</a>
 </span>
</header>
<div class="wrap">
 <h1>Photos or video in. A printable model out.</h1>
 <p class="sub">Add a video, photos, or an existing 3D file. Caliber reconstructs, cleans, scales, and exports a watertight STL.</p>
 <div class="grid">
  <div class="card">
   <div class="lab">1 · INPUT</div>
   <div class="drop" id="drop">
     <h3>Drop files here</h3>
     <p>video (.mov/.mp4) · photos (.jpg/.png/.heic) · or a 3D file (.glb/.obj/.stl)</p>
   </div>
   <input id="file" type="file" multiple class="hide">
   <div class="files" id="files"></div>

   <div class="lab" style="margin-top:18px">2 · ENGINE</div>
   <div class="row">
     <div class="seg" id="engine">
       <button data-e="auto" class="on">Auto</button>
       <button data-e="local">Local (free)</button>
       <button data-e="cloud">Cloud (paid)</button>
     </div>
   </div>
   <p class="status" id="engineHint">Auto: video/matte → local Object Capture; shiny/complex photos → cloud generative; a 3D file → just clean &amp; scale.</p>

   <div class="lab" style="margin-top:14px">CLOUD KEY <span style="color:var(--t3)">— bring your own (the app is free; you pay fal directly)</span></div>
   <div class="row">
     <input id="falKey" type="password" placeholder="fal.ai API key" style="flex:1">
     <button class="btn ghost" id="saveKey">Save</button>
     <span class="status" id="keyStatus"></span>
   </div>
   <p class="status">Free account at <a href="https://fal.ai" target="_blank" style="color:var(--sig)">fal.ai</a> → add a few dollars of credit. Stored only on this computer; needed only for the Cloud engine.</p>

   <div class="row" style="margin-top:18px">
     <button class="btn" id="build" disabled>Build model</button>
     <button class="btn ghost" id="startOver">Start over</button>
     <span class="status" id="working"></span>
   </div>
   <div class="errbox hide" id="err"></div>
   <div class="logbox hide" id="log"></div>
  </div>

  <div class="card">
   <div class="row" style="justify-content:space-between;align-items:center">
     <div class="lab" style="margin:0">RESULT</div>
     <button class="btn ghost hide" id="clearResult" style="padding:6px 12px;font-size:13px">✕ Clear</button>
   </div>
   <div id="empty" class="status" style="padding:24px 0;text-align:center">Your model will appear here.</div>
   <div id="result" class="hide">
     <div id="viewport"></div>
     <div class="notebox hide" id="resultNote">
       <b>This is a first draft.</b> If it isn't accurate, <span class="lnk" id="noteRebuild">build again</span>
       with more or better photos/video — or <b>export</b> it (OBJ opens in Blender, Fusion, etc.) and
       touch it up by hand. You can also pick two points below to set exact scale.
       <span class="x" id="noteClose" title="dismiss">✕</span>
     </div>
     <div class="row" style="justify-content:space-between;margin-top:12px">
       <span style="display:flex;gap:8px;align-items:center"><span class="pill" id="wt">watertight</span><span class="status" id="dlMsg"></span></span>
       <span style="display:flex;gap:8px;align-items:center">
         <select id="fmt"><option value="stl">STL</option><option value="obj">OBJ</option><option value="ply">PLY</option><option value="3mf">3MF</option><option value="parts">OBJ · editable parts (beta)</option><option value="step">STEP · editable solid · mechanical parts (beta)</option></select>
         <button class="btn ghost" id="dl">Download</button>
       </span>
     </div>
     <p class="status" id="fmtHint">For Fusion (.f3d) or Blender (.blend): download OBJ → File ▸ Import → edit → Save As in that app.</p>
     <div id="dims" style="margin-top:10px"></div>

     <div class="lab" style="margin-top:16px">SET SCALE <span style="color:var(--t3)">— make it true to size (two ways)</span></div>
     <div class="row">
       <button class="btn ghost" id="mBtn">📏 Pick two points</button>
       <span class="status" id="mReadout">Click two points on the model and enter the real distance between them.</span>
     </div>
     <div class="row hide" id="mApplyRow" style="margin-top:8px">
       <span class="status">that distance is</span>
       <input id="mReal" class="mono" type="number" min="0" step="0.1" placeholder="mm" style="width:96px">
       <span class="status">mm</span>
       <button class="btn" id="mApply">Apply</button>
       <button class="btn ghost" id="mClear">Reset</button>
     </div>
     <div class="status" style="margin:12px 0 8px;text-align:center">— or set a known dimension —</div>
     <div class="row">
       <select id="axSel"><option value="longest">longest side</option><option value="x">width</option><option value="y">height</option><option value="z">depth</option></select>
       <span class="status">is</span>
       <input id="axReal" class="mono" type="number" min="0" step="0.1" placeholder="mm" style="width:96px">
       <span class="status">mm</span>
       <button class="btn" id="axApply">Apply</button>
       <span class="status" id="axMsg"></span>
     </div>
   </div>
  </div>
 </div>
</div>
<script src="/vendor/three.min.js"></script>
<script src="/vendor/STLLoader.js"></script>
<script>
const $=s=>document.querySelector(s);
const TOK="__CALIBER_TOKEN__";
// Every API call carries the per-launch token (header); URLs used as <a>/loader src carry ?t=
function J(path,opts){ opts=opts||{}; opts.headers=Object.assign({'X-Caliber-Token':TOK}, opts.headers||{}); return fetch(path,opts); }
function tparam(path){ return path+(path.includes('?')?'&':'?')+'t='+encodeURIComponent(TOK); }
function fileURL(rid){ return tparam('/api/file/'+encodeURIComponent(rid))+'&_='+Date.now(); }
function esc(s){ return (s==null?'':''+s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
let UP=[], engine="auto";
function refreshHealth(){
  return J('/api/health').then(r=>r.json()).then(h=>{
    $('#engines').textContent = `local ${h.recon?'✓':'—'} · cloud ${h.gen&&h.fal_key?'✓':'—'} · prep ${h.prep?'✓':'✗'}`;
    if(h.fal_key && !$('#falKey').value){ $('#falKey').placeholder='•••••• key saved'; $('#keyStatus').textContent='saved ✓'; }
    return h;
  });
}
refreshHealth();
$('#saveKey').onclick=async()=>{
  const k=$('#falKey').value.trim();
  const r=await J('/api/key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:k})});
  const j=await r.json(); $('#keyStatus').textContent=j.set?'saved ✓':'cleared'; $('#falKey').value=''; refreshHealth();
};
const drop=$('#drop'), fileInput=$('#file');
drop.onclick=()=>fileInput.click();
['dragenter','dragover'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hot')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hot')}));
drop.addEventListener('drop',ev=>addFiles(ev.dataTransfer.files));
fileInput.onchange=()=>addFiles(fileInput.files);
async function addFiles(list){
  for(const f of list){
    const r=await J('/api/upload?name='+encodeURIComponent(f.name),{method:'POST',body:f});
    const j=await r.json(); UP.push(j);
  }
  renderFiles();
}
function renderFiles(){
  $('#files').innerHTML=UP.map((f,i)=>`<div class="file"><span class="mono">${esc(f.kind)}</span> ${esc(f.name)}<span class="x" onclick="rm(${i})">✕</span></div>`).join('');
  $('#build').disabled = UP.length===0;
  analyzeFiles();
}
window.rm=i=>{UP.splice(i,1);renderFiles();};
// Look at the dropped files + chosen engine and steer the user to the right path.
function analyzeFiles(){
  const hint=$('#engineHint');
  const vids=UP.filter(f=>f.kind==='video').length;
  const imgs=UP.filter(f=>f.kind==='image').length;
  const meshes=UP.filter(f=>f.kind==='mesh').length;
  let msg, warn=false;
  if(UP.length===0){
    msg='Auto: a video or matte object → Local (free); a few photos of a shiny/complex object → Cloud; an existing 3D file → just clean &amp; scale.';
  } else if(meshes && UP.length===1){
    msg='3D file detected → Caliber will just <b>clean &amp; scale</b> it. No engine needed.';
  } else if(vids){
    if(engine==='cloud'){
      const extra=Math.max(0,6-imgs);
      msg='Cloud + video → Caliber pulls the <b>'+extra+' sharpest frame'+(extra!==1?'s':'')+'</b> from your video'
        +(imgs?(' to top up your '+imgs+' photo'+(imgs>1?'s':'')):'')
        +' and reconstructs with <b>TRELLIS</b>. Best for shiny/symmetric objects a video alone can\'t scan. (Needs the local engine built.)';
    } else {
      msg='Video detected → <b>Local</b> engine. Orbit slowly all the way around a <b>matte, well-lit</b> object on a surface.';
      if(imgs) msg+=' Your '+imgs+' photo'+(imgs>1?'s':'')+' will be <b>combined</b> with the video for extra detail.';
    }
    if(meshes) msg+=' (Loose 3D files are ignored alongside a video.)';
  } else if(imgs){
    if(engine==='cloud'){
      const names=UP.filter(f=>f.kind==='image').map(f=>(f.name||'').toLowerCase());
      const hasTB=names.some(n=>n.includes('top')||n.includes('bottom'));
      const route = imgs<=1?'Hunyuan (single image)':(hasTB||imgs>4)?'TRELLIS (uses all views)':'Hunyuan (multi-view)';
      const tip='<b>Tip: name your photos first</b> — front, back, left, right (+ top, bottom). 4 named sides → <b>Hunyuan</b>; 5–6 or with top/bottom → <b>TRELLIS</b>. Without names, upload order is used.';
      if(imgs>6){ warn=true; msg='Cloud uses up to <b>6 views</b> — extras are ignored. '+tip; }
      else msg='Cloud: '+imgs+' photo'+(imgs>1?'s':'')+' → <b>'+route+'</b>. '+tip;
    } else if(engine==='local'){
      if(imgs<12){ msg='Local matches <b>overlapping</b> shots — a few spread-out angles (front/back/left/right…) don\'t overlap enough to solve. Best input is a <b>slow orbit video</b>, or ~30+ photos each a <b>small step</b> from the last. Those labeled views are ideal for <b>Cloud</b>, not Local.'; }
      else msg='Local: '+imgs+' photos — make sure they <b>overlap</b> (each a small step from the last) around a <b>matte, well-lit</b> object. A slow orbit <b>video</b> is the easiest way to get that.';
    } else { // auto
      if(imgs<=8) msg='Auto: with '+imgs+' photo'+(imgs>1?'s':'')+', Caliber uses <b>Cloud</b> if your fal key is set (ideal: 4–6 angles — <b>name them</b> front/back/left/right/top/bottom; 4 → Hunyuan, 6 → TRELLIS), else tries Local.';
      else msg='Auto: '+imgs+' photos → <b>Local</b> photogrammetry. Matte, well-lit objects only; shiny/dark ones fail — pick Cloud for those.';
    }
  }
  hint.innerHTML=msg;
  hint.classList.toggle('warn', warn);
}
$('#engine').onclick=e=>{const b=e.target.closest('button');if(!b)return;engine=b.dataset.e;[...$('#engine').children].forEach(x=>x.classList.toggle('on',x===b));analyzeFiles();};
function hideErr(){ $('#err').classList.add('hide'); $('#err').innerHTML=''; }
function showErr(headline, hint){
  const e=$('#err');
  e.innerHTML='<b>'+headline+'</b>'+(hint?'<br>'+hint:'')
    +' &nbsp;<span class="lnk" id="errLog">show details</span>';
  e.classList.remove('hide');
  const lg=$('#errLog'); if(lg) lg.onclick=()=>$('#log').classList.toggle('hide');
}
// Turn a raw engine error into something a person can act on.
function friendlyError(msg){
  const m=(msg||'').toLowerCase();
  if(m.includes('object capture')||m.includes('local reconstruction'))
    return ['Local reconstruction didn\'t work.',
      'The free local engine needs lots of overlapping, sharp photos (or a slow orbit video) of a <i>matte</i> object on a surface. For shiny, dark, symmetric, or few-photo subjects, switch the engine to <b>Cloud</b>.'];
  if(m.includes('fal')||m.includes('cloud engine')||m.includes('key')||m.includes('balance')||m.includes('locked'))
    return ['The Cloud engine couldn\'t run.',
      'Check that your fal.ai key is saved and has credit (a few dollars). You pay fal directly, per model.'];
  if(m.includes('no files'))
    return ['Nothing to build yet.','Add a video, some photos, or a 3D file first.'];
  return ['Something went wrong building the model.', esc(msg)];
}
$('#build').onclick=async()=>{
  hideErr();
  $('#build').disabled=true; $('#working').textContent='Working… (reconstruction can take a minute)';
  const log=$('#log'); log.classList.add('hide'); log.textContent='';
  try{
    const res=await J('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ids:UP.map(f=>f.id),engine})});
    const j=await res.json();
    log.textContent=(j.log||[]).join('\n');
    if(!j.ok){ const [h,hint]=friendlyError(j.error); showErr(h,hint); return; }
    showResult(j);
  }catch(err){
    showErr('Couldn\'t reach the Caliber engine.','The app may have stopped, or the file was too large. Try again, or quit and reopen Caliber.');
  }finally{
    $('#working').textContent=''; $('#build').disabled = UP.length===0;
  }
};
async function clearResult(){
  if(RID){ try{ await J('/api/discard',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:RID})}); }catch(e){} }
  RID=null; resetMeasure(true); VIEW=null;
  $('#viewport').innerHTML=''; $('#dims').innerHTML='';
  $('#result').classList.add('hide'); $('#empty').classList.remove('hide');
  $('#clearResult').classList.add('hide'); $('#resultNote').classList.add('hide');
}
$('#clearResult').onclick=clearResult;
$('#startOver').onclick=async()=>{
  await clearResult();
  UP=[]; renderFiles(); hideErr();
  $('#log').classList.add('hide'); $('#log').textContent='';
};
let RID=null;
// Download: in the native app, use a real Save dialog (the webview ignores <a download>);
// in a browser, fall back to a normal download link.
async function doDownload(){
  if(!RID) return;
  const fmt=$('#fmt').value;
  const ext=(fmt==='parts')?'obj':fmt;     // "parts" is a grouped .obj
  $('#dlMsg').textContent='';
  if(window.pywebview && window.pywebview.api && window.pywebview.api.save_export){
    $('#dlMsg').textContent='Saving…';
    try{
      const r=await window.pywebview.api.save_export(RID, fmt);
      if(r && r.ok) $('#dlMsg').textContent='Saved ✓';
      else if(r && r.cancelled) $('#dlMsg').textContent='';
      else $('#dlMsg').textContent='Save failed'+(r&&r.error?': '+r.error:'');
    }catch(e){ $('#dlMsg').textContent='Save failed'; }
    return;
  }
  const a=document.createElement('a');
  a.href=tparam('/api/export?id='+encodeURIComponent(RID)+'&fmt='+fmt);
  a.download='caliber_model.'+ext; document.body.appendChild(a); a.click(); a.remove();
}
$('#dl').onclick=doDownload;
$('#noteClose').onclick=()=>$('#resultNote').classList.add('hide');
$('#noteRebuild').onclick=()=>{ $('#resultNote').classList.add('hide'); $('#build').scrollIntoView({behavior:'smooth',block:'center'}); };
let curSize=[], scaled=false;   // current bounding-box [x,y,z]; whether the user has set a real scale yet
function updateDims(s){
  s=s||{}; const d=s.size_mm||[]; curSize=d;
  let html = (d.length?`<div class="dimrow"><span style="color:var(--t2)">Size</span><span class="mono">${d[0]} × ${d[1]} × ${d[2]} mm</span></div>`:'')
     + (s.triangles?`<div class="dimrow"><span style="color:var(--t2)">Triangles</span><span class="mono">${s.triangles.toLocaleString()}</span></div>`:'');
  if(!scaled) html += `<div class="status" style="margin-top:7px;color:var(--sig)">⚲ Unit-scale — set a real dimension below to make it true to size.</div>`;
  $('#dims').innerHTML = html;
}
function showResult(j){
  hideErr();
  $('#empty').classList.add('hide'); $('#result').classList.remove('hide');
  $('#clearResult').classList.remove('hide');
  $('#resultNote').classList.remove('hide');
  RID=j.resultId;
  $('#dlMsg').textContent=''; $('#axMsg').textContent='';
  const s=j.stats||{};
  $('#wt').textContent = s.watertight ? 'watertight ✓' : 'check mesh';
  scaled=false;                       // every fresh build starts unit-scale
  updateDims(s);
  loadSTL(fileURL(RID));
}
// ---- 3D preview + two-point measurement ----
let VIEW=null, measureMode=false, picks=[], markers=[], lineObj=null, measuredDist=0;

function resetMeasure(turnOff){
  if(VIEW){ markers.forEach(s=>VIEW.mesh.remove(s)); if(lineObj) VIEW.mesh.remove(lineObj); }
  markers=[]; lineObj=null; picks=[]; measuredDist=0;
  $('#mApplyRow').classList.add('hide'); $('#mReal').value='';
  if(turnOff){ measureMode=false; $('#mBtn').classList.remove('on');
    $('#viewport').classList.remove('measuring');
    $('#mReadout').textContent='Turn this on, then click two points on the model (drag still rotates).'; }
}
function addMarker(local){
  const s=new THREE.Mesh(new THREE.SphereGeometry(VIEW.markerR,16,16),
                         new THREE.MeshBasicMaterial({color:0x2D6BE3}));
  s.position.copy(local); VIEW.mesh.add(s); markers.push(s);
}
function drawLine(){
  if(lineObj) VIEW.mesh.remove(lineObj);
  const geo=new THREE.BufferGeometry().setFromPoints([picks[0],picks[1]]);
  lineObj=new THREE.Line(geo,new THREE.LineBasicMaterial({color:0x2D6BE3}));
  VIEW.mesh.add(lineObj);
}
function tryPick(e){
  if(!VIEW) return;
  const rect=VIEW.ren.domElement.getBoundingClientRect();
  const ndc=new THREE.Vector2(((e.clientX-rect.left)/rect.width)*2-1, -((e.clientY-rect.top)/rect.height)*2+1);
  const ray=new THREE.Raycaster(); ray.setFromCamera(ndc,VIEW.cam);
  const hits=ray.intersectObject(VIEW.mesh,true);
  if(!hits.length) return;
  if(picks.length>=2) resetMeasure(false);                 // start a fresh measurement
  const local=VIEW.mesh.worldToLocal(hits[0].point.clone());// rotation-invariant model coords
  picks.push(local); addMarker(local);
  if(picks.length===1){ $('#mReadout').textContent='Good — now click the second point.'; }
  else {
    drawLine();
    measuredDist=picks[0].distanceTo(picks[1]);
    $('#mReadout').textContent='Measured '+measuredDist.toFixed(2)+' units —';
    $('#mApplyRow').classList.remove('hide'); $('#mReal').focus();
  }
}
$('#mBtn').onclick=()=>{
  measureMode=!measureMode;
  $('#mBtn').classList.toggle('on',measureMode);
  $('#viewport').classList.toggle('measuring',measureMode);
  if(measureMode){ resetMeasure(false); $('#mReadout').textContent='Click two points on the model (drag still rotates).'; }
  else resetMeasure(true);
};
$('#mClear').onclick=()=>{ resetMeasure(false); $('#mReadout').textContent='Click two points on the model (drag still rotates).'; };
$('#mApply').onclick=async()=>{
  const real=parseFloat($('#mReal').value);
  if(!(real>0)){ $('#mReadout').textContent='Enter the real distance in mm (a positive number).'; return; }
  if(!(measuredDist>0)){ $('#mReadout').textContent='Pick two points first.'; return; }
  const r=await J('/api/rescale',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:RID,measured:measuredDist,realMm:real})});
  const j=await r.json();
  if(!j.ok){ $('#mReadout').textContent='Scale failed: '+j.error; return; }
  scaled=true; updateDims(j.stats);
  resetMeasure(true);
  loadSTL(fileURL(RID));            // reload at the new scale
};
// Method 2 — scale by a known bounding-box dimension.
$('#axApply').onclick=async()=>{
  const real=parseFloat($('#axReal').value);
  if(!(real>0)){ $('#axMsg').textContent='enter mm'; return; }
  const d=curSize||[];
  const sel=$('#axSel').value;
  const measured = (sel==='longest') ? Math.max(d[0]||0,d[1]||0,d[2]||0) : (d[{x:0,y:1,z:2}[sel]]||0);
  if(!(measured>0)){ $('#axMsg').textContent='no size yet'; return; }
  $('#axMsg').textContent='scaling…';
  const r=await J('/api/rescale',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:RID,measured,realMm:real})});
  const j=await r.json();
  if(!j.ok){ $('#axMsg').textContent='failed: '+j.error; return; }
  scaled=true; updateDims(j.stats); $('#axMsg').textContent='✓'; $('#axReal').value='';
  resetMeasure(true); loadSTL(fileURL(RID));
};

function loadSTL(url){
  resetMeasure(true);
  const host=$('#viewport'); host.innerHTML='';
  const w=host.clientWidth,h=host.clientHeight;
  const ren=new THREE.WebGLRenderer({antialias:true}); ren.setPixelRatio(Math.min(2,devicePixelRatio)); ren.setSize(w,h); host.appendChild(ren.domElement);
  const sc=new THREE.Scene(); const cam=new THREE.PerspectiveCamera(45,w/h,0.1,8000);
  sc.add(new THREE.AmbientLight(0xffffff,.6)); const d1=new THREE.DirectionalLight(0xffffff,.85); d1.position.set(1,2,1.5); sc.add(d1);
  const grp=new THREE.Group(); sc.add(grp);
  new THREE.STLLoader().load(url,g=>{
    g.computeVertexNormals(); g.center();
    const m=new THREE.Mesh(g,new THREE.MeshStandardMaterial({color:0x5b86c9,roughness:.7,metalness:.02}));
    grp.add(m);
    g.computeBoundingSphere(); const r=g.boundingSphere.radius||50; let dist=r*3, rx=-0.5, ry=0.6, drag=false, lx,ly, dnx=0, dny=0;
    const dom=ren.domElement;
    dom.addEventListener('pointerdown',e=>{drag=true;lx=e.clientX;ly=e.clientY;dnx=e.clientX;dny=e.clientY});
    addEventListener('pointerup',e=>{ const wasClick=Math.abs(e.clientX-dnx)+Math.abs(e.clientY-dny)<5; drag=false; if(measureMode && wasClick) tryPick(e); });
    addEventListener('pointermove',e=>{if(!drag)return;ry+=(e.clientX-lx)*.01;rx+=(e.clientY-ly)*.01;rx=Math.max(-1.5,Math.min(1.5,rx));lx=e.clientX;ly=e.clientY});
    dom.addEventListener('wheel',e=>{e.preventDefault();dist*=e.deltaY>0?1.1:0.9},{passive:false});
    VIEW={ren,cam,grp,mesh:m,markerR:r*0.025};
    (function loop(){requestAnimationFrame(loop);grp.rotation.x=rx;grp.rotation.y=ry;cam.position.set(0,dist*.35,dist);cam.lookAt(0,0,0);ren.render(sc,cam);})();
  });
}
</script>
<footer>
  <svg width="18" height="18" viewBox="50 50 140 140" fill="#2D6BE3"><rect x="64" y="64" width="12" height="112" rx="6"/><rect x="64" y="58" width="40" height="12" rx="6"/><rect x="64" y="170" width="40" height="12" rx="6"/><rect x="164" y="64" width="12" height="112" rx="6"/><rect x="136" y="58" width="40" height="12" rx="6"/><rect x="136" y="170" width="40" height="12" rx="6"/><circle cx="120" cy="120" r="14"/></svg>
  <span class="wm">CALIBER</span>
  <span>Precision, made effortless.</span>
  <span class="sp"></span>
  <a href="https://docs.google.com/forms/d/e/1FAIpQLSde7zEqapYnMvW7v8M67SwK0krQA3UnaRefzA4YcxNhdIJh2Q/viewform?usp=dialog" target="_blank" rel="noopener">Request a feature</a>
  <a href="https://docs.google.com/forms/d/e/1FAIpQLSde7zEqapYnMvW7v8M67SwK0krQA3UnaRefzA4YcxNhdIJh2Q/viewform?usp=dialog" target="_blank" rel="noopener">Report a bug</a>
  <a href="https://t.me/va_rinder" target="_blank" rel="noopener">Telegram</a>
  <a href="https://github.com/VarinderSS1113/caliber" target="_blank" rel="noopener">GitHub</a>
  <a href="https://github.com/VarinderSS1113/caliber/blob/main/docs/WHITEPAPER.md" target="_blank" rel="noopener">Whitepaper</a>
</footer>
</body></html>"""

if __name__ == "__main__":
    main()
