#!/bin/bash
#
# Build Caliber.app and a drag-to-Applications Caliber.dmg.
#
# Run this ONCE on a Mac (you, as the publisher). It produces a real, self-contained
# app — Python and all dependencies frozen inside — and an installer you can keep or
# hand to anyone. After this, installing/launching Caliber needs no terminal at all.
#
# Requirements: macOS 13+, Python 3 (python.org). Xcode is needed only to include the
# free local engine; without it you still get a working Cloud + Import app.
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
echo "▸ Caliber — building the macOS app + installer"
echo "  repo: $ROOT"

# ── 1. Python build environment ───────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || {
    echo "✗ Python 3 not found. Install it from https://www.python.org/downloads/ and re-run."
    exit 1
}
VENV="$ROOT/.build-venv"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip wheel
echo "▸ Installing build dependencies…"
# Known-good minimum versions (installable across Python 3.10–3.13). For a fully
# reproducible release build, freeze these to exact ==versions with hashes once you've
# confirmed them on your machine (see docs/SECURITY_AUDIT.md §7).
python -m pip install --quiet \
    "py2app>=0.28" "numpy>=1.26" "pywebview>=5.0" "pyobjc>=10.0" "fal-client>=0.5"

# ── Vendor the 3D-preview libraries so the app loads NO third-party JS at runtime ──
#    (removes the only remote-code-execution surface; the app works fully offline).
echo "▸ Vendoring three.js + STLLoader (with checksum verification)…"
VDIR="$ROOT/assets/vendor"; mkdir -p "$VDIR"
fetch_verify () {  # url  dest  expected_sha256 (or "PIN" to print-and-pin on first run)
    curl -fsSL "$1" -o "$2"
    got="$(shasum -a 256 "$2" | awk '{print $1}')"
    if [ "$3" = "PIN" ]; then
        echo "  ⓘ $(basename "$2") sha256 = $got"
        echo "    → paste this into build_dmg.sh to lock it for future builds."
    elif [ "$got" != "$3" ]; then
        echo "✗ Checksum mismatch for $2"; echo "  expected $3"; echo "  got      $got"; exit 1
    fi
}
# First build prints the hashes; replace "PIN" with the printed sha256 to enforce
# verification on every later build (recommended before distributing).
fetch_verify "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js" \
    "$VDIR/three.min.js"  "PIN"
fetch_verify "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js" \
    "$VDIR/STLLoader.js"  "PIN"
echo "  vendored into assets/vendor/"

# ── 2. Local engine (Apple Object Capture) — optional, needs Xcode ────────────
if command -v swift >/dev/null 2>&1; then
    echo "▸ Compiling the local engine (caliber-recon)…"
    ( cd engines/macos-objectcapture && swift build -c release )
    cp engines/macos-objectcapture/.build/release/caliber-recon "$ROOT/caliber-recon"
    chmod +x "$ROOT/caliber-recon"
    echo "  local engine: included"
else
    echo "  ! Xcode/swift not found — building Cloud + Import only (local engine can be added later)."
fi

# ── 3. Freeze into Caliber.app ────────────────────────────────────────────────
echo "▸ Freezing Caliber.app with py2app…"
rm -rf build dist
python packaging/setup.py py2app >/dev/null

# ── 4. Ad-hoc code-sign so it launches locally ────────────────────────────────
#     (For zero Gatekeeper warnings on other machines, sign + notarise with an
#      Apple Developer ID — see docs/BUILD.md.)
echo "▸ Signing (ad-hoc)…"
codesign --force --deep --sign - "dist/Caliber.app" || true

# ── 5. Package the drag-to-Applications installer ─────────────────────────────
echo "▸ Building Caliber.dmg…"
STAGE="$(mktemp -d)/Caliber"
mkdir -p "$STAGE"
cp -R "dist/Caliber.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$ROOT/Caliber.dmg"
hdiutil create -volname "Caliber" -srcfolder "$STAGE" -ov -format UDZO "$ROOT/Caliber.dmg" >/dev/null

echo ""
echo "✅ Done."
echo "   • App:       $ROOT/dist/Caliber.app   (drag to /Applications)"
echo "   • Installer: $ROOT/Caliber.dmg         (open, drag Caliber → Applications)"
echo "   Upload Caliber.dmg to your GitHub release; the website button links to it."
