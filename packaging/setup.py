"""
py2app build config for Caliber.

Produces a self-contained `Caliber.app` — its own name and icon, with Python and
every dependency (numpy, pywebview/pyobjc, fal-client) frozen inside, plus the
native local engine binary if it was built. The app launches as "Caliber" (not
"Python") and needs nothing installed on the user's machine.

Don't run this directly — use  build_dmg.sh  (or the double-click
"Build Caliber.dmg.command"), which sets up the build environment, compiles the
local engine, runs this, and packages the installer.

    python packaging/setup.py py2app
"""
import os
from setuptools import setup

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# caliber_desktop.py is the entry point (starts the loopback server + native window).
APP = [os.path.join(ROOT, "caliber_desktop.py")]

# Bundle the native Object Capture engine as a resource if it has been built.
RECON = os.path.join(ROOT, "caliber-recon")
RESOURCES = [RECON] if os.path.exists(RECON) else []

# Bundle the vendored 3D-preview libraries (downloaded by build_dmg.sh) so the app
# serves them locally and loads no third-party JS at runtime.
VENDOR = os.path.join(ROOT, "assets", "vendor")
if os.path.isdir(VENDOR):
    RESOURCES.append(VENDOR)

OPTIONS = {
    "iconfile": os.path.join(ROOT, "assets", "AppIcon.icns"),
    "resources": RESOURCES,
    # caliber_app imports these; webview + fal_client are imported lazily, so name
    # them explicitly to make sure py2app freezes them in.
    "packages": ["numpy", "webview", "fal_client"],
    "includes": ["caliber_app", "caliber_prep", "caliber_gen", "caliber_cad"],
    "plist": {
        "CFBundleName": "Caliber",
        "CFBundleDisplayName": "Caliber",
        "CFBundleIdentifier": "app.caliber.desktop",
        "CFBundleVersion": "1.0",
        "CFBundleShortVersionString": "1.0",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.graphics-design",
        # No dock-less / agent mode: it's a normal windowed app.
        "LSUIElement": False,
    },
}

setup(
    name="Caliber",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
