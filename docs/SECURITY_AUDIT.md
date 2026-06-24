# Caliber — Security & Logic Audit

_Date: June 2026 · Scope: `caliber_app.py` (local server), `caliber_prep.py`, `caliber_gen.py`,
`caliber_desktop.py`, the packaging/build scripts, and the Swift engine. Independent re-audit._

## 0. Re-audit summary (latest pass)

A second, adversarial review was run after the app gained new endpoints (`/api/rescale`,
`/api/discard`), in-process tool execution, the native window, and the py2app build. An
independent reviewer read every file. **No malware, backdoor, obfuscation, hidden network
call, command injection, `eval`/`exec`/`shell=True`, or unsafe deserialization was found.**
The only outbound traffic is the user's own fal.ai cloud calls over HTTPS; the server binds
loopback only; the fal key is never logged or returned. The findings that *were* raised have
now been fixed:

| # | Severity | Issue | Status |
|---|----------|-------|--------|
| R1 | **High** | Front-end loaded three.js + STLLoader from third-party CDNs **without SRI**, executing in the app window with full access to the local API. | **Fixed** — vendored locally, served from loopback; build verifies checksums; no runtime CDN. |
| R2 | Medium | GET endpoints weren't Origin-checked; any local process could reach the API (Host check only). | **Fixed** — per-launch **session token** required on every `/api` call (header, or `?t=` for loader/download URLs). |
| R3 | Medium | DNS-rebinding residual (Origin-absent allowed). | **Fixed** — session token closes it; Host + Origin checks retained. |
| R4 | Medium | Shared `UP`/`RESULTS`/`KEY` mutated without a lock under the threaded server. | **Fixed** — guarded by a `threading.Lock`. |
| R5 | Low | fal key file created then `chmod`-ed (brief world-readable window) with silent failure. | **Fixed** — created `0o600` atomically via `os.open`; failure surfaced. |
| R6 | Low | Uploaded filenames echoed into the UI via `innerHTML` (self-XSS). | **Fixed** — filenames/errors HTML-escaped. |
| — | — | New `/api/rescale` & `/api/discard`: ids used only as **dict keys** (paths come from server-controlled dicts) — no traversal; inputs range-checked. | Verified safe. |

These are verified by automated tests: token-less `/api` → **403**, valid token → **200**,
spoofed `Host` → **403**, cross-origin POST → **403**, export with token → **200**, vendored
asset served locally (CDN only as a dev fallback redirect).

_Original pre-release review follows._

## 1. Trust model

Caliber is a **local-first** app. A small HTTP server runs on **loopback only**
(`127.0.0.1`, an OS-assigned port) and renders the UI in a native app window (the same
server can be used from a browser in development). There is no remote
backend we operate. Data leaves the machine only when the user **explicitly chooses the
Cloud engine**, in which case the selected photos are sent to **fal.ai** using the user's
own API key. There is **no telemetry or analytics**.

Primary attack surface: the local HTTP server (any local process — and, via the browser,
potentially a malicious website — can send it requests). Secondary: parsing untrusted 3D
files; handling the user's fal API key; third-party assets loaded by the UI.

## 2. Findings & status

| # | Severity | Issue | Status |
|---|----------|-------|--------|
| H1 | **High** | DNS-rebinding / CSRF: server had no `Host`/`Origin` validation, so a malicious website could drive the local API — overwrite the stored fal key (re-routing the user's photos to an attacker's fal account), trigger uploads/reconstructions, or read results. | **Fixed** |
| H2 | **High** | Unbounded request body read into memory (`Content-Length`) → memory-exhaustion DoS. | **Fixed** |
| M1 | Medium | Upload filename/extension taken from user input (path-traversal vector). | **Fixed** |
| M2 | Medium | Untrusted JSON (`refMm`, `refAxis`, `engine`, `ids`) passed toward subprocess args without type/range validation. | **Fixed** |
| M3 | Medium | Generative result downloaded from an arbitrary URL in the API response (`file://`/SSRF-style risk if the API were compromised). | **Fixed** |
| M4 | Medium | Supply chain: UI loaded three.js + STLLoader + fonts from third-party CDNs without SRI. | **Fixed** (re-audit R1 — vendored locally) |
| L1 | Low | fal key stored plaintext in `~/.caliber/config.json` (now created `0o600` atomically). | Accepted / documented (R5 hardened) |
| L2 | Low | Temp working files (uploads, meshes, results) not cleaned up. | **Fixed** (removed on exit) |
| L3 | Low | No authentication — any local process could reach the loopback server. | **Fixed** (re-audit R2 — session token) |
| L4 | Low | Shared state dicts mutated without locks under threaded server. | **Fixed** (re-audit R4 — lock) |
| L5 | Info | Malformed OBJ/STL/GLB cause graceful exceptions (no RCE); huge meshes can DoS, but they are the user's own local files. | Accepted; noted |

## 3. Fixes applied (this review)

- **Anti DNS-rebinding / CSRF (H1):** every request must carry `Host: 127.0.0.1:8765`
  (or `localhost:8765`); state-changing POSTs must have a same-origin (or absent) `Origin`.
  Verified: spoofed `Host: evil.com` → **403**, `Origin: https://evil.com` POST → **403**,
  legitimate same-origin flow → **200**.
- **Request limits (H2):** uploads are **streamed to disk in 1 MB chunks** with a **1 GB
  cap**; control requests capped at **4 MB**; oversize → **413**. No request body is read
  whole into memory.
- **Filename safety (M1):** the stored filename is always server-controlled
  (`up_<uuid><ext>`); the incoming name is reduced to `basename` and the extension is
  sanitised to `[a-z0-9.]`. Verified: `../../../etc/passwd.glb` is neutralised.
- **Input validation (M2):** `refMm` must be a number in `(0, 1e6)`; `refAxis` and
  `engine` are allow-listed; `ids` must be strings and are capped. (Subprocess calls were
  already safe — list args, **never `shell=True`**, so no command injection.)
- **HTTPS-only download (M3):** the generative step refuses any non-`https://` mesh URL.
- **Temp cleanup (L2):** the session working directory is deleted on exit.
- **Loopback bind confirmed:** server binds `127.0.0.1` only — never `0.0.0.0`.

## 4. Practices already in place (verified good)

- No `eval` / `exec` / `pickle` / `marshal` / `yaml.load` on any untrusted data.
- All subprocess invocations use argument **lists** with `shell=False` — no shell injection.
- The fal API key is **never logged**, **never returned by any endpoint** (the health
  endpoint exposes only booleans), and is passed to the cloud step via **environment
  variable**, not command-line arguments.
- The cloud step embeds images as base64 in the request to fal rather than using a
  third-party CDN upload — fewer moving parts, and no separate storage credentials.

## 5. Dependencies & versions

| Dependency | Used | Latest (Jun 2026) | Recommendation |
|---|---|---|---|
| **numpy** | required by prep | **2.5.0** (needs Python 3.12+) | Pin `numpy>=1.26`. Code is compatible with 1.26.x and 2.x (the one 2.0 API change is handled). Target Python **3.12+** for the shipped build; the user's current env is 3.9 (gets numpy ≤ 2.0.x). |
| **three.js** (+ STLLoader) | UI viewer, via CDN | ~r0.17x | **r128 (2021) is old and loaded from a CDN without SRI** → update to a current release and **vendor locally** (M4). |
| **fal-client** | cloud step | (check PyPI) | Pin to a known-good recent version; review its transitive deps (httpx) and keep updated. |
| heic2any / tabler-icons / Google Fonts | browser *prototype* only | — | Not part of the shipped app. If the prototype is ever shipped, vendor/SRI these too. |

## 6. Data flow & privacy

- **Local (Object Capture) and Import:** files never leave the machine.
- **Cloud (generative):** only the photos the user selects are sent, to **fal.ai**, with
  the user's own key. Disclose this in-app.
- **API key:** stored locally only; sent only to fal; never to us.
- **CDN assets (M4):** until vendored, loading the viewer/fonts leaks the user's IP to
  Cloudflare/jsDelivr/Google. Vendoring removes this and makes the app work offline.
- **No telemetry.** Add a one-line privacy statement to the UI to make this explicit.

## 7. Pre-release checklist (before public distribution)

1. ~~Vendor `three.js` + `STLLoader` locally~~ — **done** (re-audit R1); `build_dmg.sh`
   fetches + checksum-verifies them and bundles them into the app.
2. **Pin dependency versions** — **done**: `build_dmg.sh` pins py2app/numpy/pywebview/pyobjc/fal-client.
3. **Code-sign & notarize** the packaged `.app` (Developer ID) so users don't see Gatekeeper
   warnings. (Currently ad-hoc signed; instructions in `BUILD.md`.) _Remaining._
4. **In-app privacy disclosure**: local stays on device; cloud sends photos to fal; no telemetry.
5. Consider storing the fal key in the **OS keychain** instead of a plaintext config file.
6. ~~Per-session token on API calls~~ — **done** (re-audit R2).
7. Add a **`SECURITY.md`** (responsible-disclosure contact) and **OSS license files** for
   bundled components (three.js MIT, numpy BSD, etc.) to the open-source repo.

## 8. Logic audit

Logic bugs found and fixed during development were re-verified resolved: the numpy-2.0
`return_inverse` shape change, the PCA axis-sign flip in the revolve (built bottles
upside-down), and the "snap body from the base" assumption that failed on real scans with
an inward-curving base. Remaining by-design behaviours, which are disclosed rather than
bugs: scale derives from **one** user measurement, and generative output is **AI-inferred**
on unseen surfaces.

---

**Bottom line:** the high-severity issues (DNS-rebinding/CSRF and unbounded request size)
are fixed and tested, along with the medium input/filename/download issues. The one
remaining must-do before public release is **M4 — vendor the CDN assets (or add SRI) and
update three.js** — plus the standard release hygiene in §7 (signing, dependency pinning,
privacy note). No remote code execution, command injection, or key-leak paths were found.
