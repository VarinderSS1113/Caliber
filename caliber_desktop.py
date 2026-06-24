#!/usr/bin/env python3
"""
Caliber — native desktop launcher.

Opens Caliber in its own application window (Apple WKWebView on macOS) instead of
a browser tab, and shows no terminal. It starts the same loopback-only server that
powers the app, runs it on a background thread, and renders the UI in a native
window. Closing the window quits the app.

This is what Caliber.app launches. You can also run it directly:

    pip3 install pywebview
    python3 caliber_desktop.py

Requires: pywebview (native window) and numpy (print-prep).
"""
import os
import shutil
import sys
import threading

import caliber_app


class Bridge:
    """JS ↔ Python bridge for actions a webview can't do on its own. The native
    web view ignores HTML <a download>, so the UI calls save_export() to pop a real
    macOS Save dialog and write the chosen file. Only ever touches files Caliber
    already produced for the given result id."""

    def __init__(self):
        self._webview = None

    def save_export(self, rid, fmt):
        try:
            import webview
            fmt = (fmt or "stl").lower()
            if fmt not in ("stl", "obj", "ply", "3mf", "parts", "step"):
                return {"ok": False, "error": "unsupported format"}
            try:
                src = caliber_app.make_export(rid, fmt)      # build the export file
            except Exception as e:
                return {"ok": False, "error": "%s" % e}       # e.g. "too organic for STEP"
            if not src or not os.path.exists(src):
                return {"ok": False, "error": "no such result"}
            ext = "obj" if fmt == "parts" else fmt
            win = self._webview or (webview.windows[0] if webview.windows else None)
            dest = win.create_file_dialog(
                webview.SAVE_DIALOG, save_filename="caliber_model." + ext
            )
            if not dest:
                return {"cancelled": True}
            dest = dest[0] if isinstance(dest, (list, tuple)) else dest
            shutil.copyfile(src, dest)
            return {"ok": True, "path": dest}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def main():
    try:
        import webview          # pywebview — uses the OS's native web view
    except ImportError:
        sys.stderr.write(
            "Caliber's window needs pywebview. Install it with:\n"
            "    pip3 install pywebview\n"
            "Then launch Caliber again.\n"
        )
        return 1

    # Start the local server on a free loopback port; the window owns the UI.
    srv, url = caliber_app.serve(open_browser=False, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    bridge = Bridge()
    bridge._webview = webview.create_window(
        "Caliber",
        url,
        width=1180,
        height=860,
        min_size=(960, 680),
        js_api=bridge,
    )
    try:
        webview.start()         # blocks on the native UI loop until the window closes
    finally:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
