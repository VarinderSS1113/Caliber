#!/usr/bin/env python3
"""
caliber-gen — Caliber's generative (cloud) reconstruction tier.

Sends 1-4 clean object photos to a hosted image-to-3D model (fal.ai Hunyuan3D) and
downloads a GLB mesh. Use this for the objects photogrammetry can't handle — glossy,
dark, symmetric, or otherwise low-texture (e.g. a shiny model car). The more angles
you give it, the better the result (multi-view rebuilds the real back, not a guess).

Then run the GLB through caliber-prep for cleanup + real-world scale + STL/3MF.

------------------------------------------------------------------------------
Setup (one time):
  1. Create a free account at  https://fal.ai   (gives starter credits)
  2. Copy your API key from the dashboard
  3.  export FAL_KEY="your-key-here"
  4.  pip3 install fal-client
------------------------------------------------------------------------------

Capture tips: photograph the object ALONE (on a table, no hand), plain background,
even light. Give front / back / left / right for the best reconstruction.

Usage:
  # all six views (best — auto-uses TRELLIS so top/bottom are included):
  python3 caliber_gen.py --front f.jpg --back b.jpg --left l.jpg --right r.jpg \
                         --top t.jpg --bottom u.jpg -o car.glb
  # four sides:
  python3 caliber_gen.py --front f.jpg --back b.jpg --left l.jpg --right r.jpg -o car.glb
  # single image (quick):
  python3 caliber_gen.py --front photo.jpg -o car.glb

Then:
  caliber_prep car.glb car.stl --auto --ref-mm <real length in mm>

NOTE: fal occasionally renames model fields. If a call errors on an unknown field,
open the model's API tab on fal.ai and adjust the field names flagged below.
"""
import os, sys, argparse, urllib.request, base64, mimetypes


def main():
    ap = argparse.ArgumentParser(description="Generative image-to-3D via fal.ai (Hunyuan3D)")
    ap.add_argument("--front", required=True, help="front (or main) photo")
    ap.add_argument("--back", help="back photo")
    ap.add_argument("--left", help="left-side photo")
    ap.add_argument("--right", help="right-side photo")
    ap.add_argument("--top", help="top / roof photo")
    ap.add_argument("--bottom", help="bottom / underside photo")
    ap.add_argument("--model", choices=["auto", "hunyuan", "trellis"], default="auto",
                    help="auto (default): Hunyuan for <=4 sides, TRELLIS when top/bottom or 5-6 views are given")
    ap.add_argument("-o", "--output", default="model.glb", help="output .glb path")
    args = ap.parse_args()

    if not os.environ.get("FAL_KEY"):
        sys.exit('No fal.ai key. Run:  export FAL_KEY="your-key"   (free signup at https://fal.ai)')
    try:
        import fal_client
    except ImportError:
        sys.exit("Missing client. Run:  pip3 install fal-client")

    order = ["front", "back", "left", "right", "top", "bottom"]
    imgs = {k: getattr(args, k) for k in order if getattr(args, k)}
    for k, p in imgs.items():
        if not os.path.exists(p):
            sys.exit(f"{k} image not found: {p}")

    # Embed images as base64 data URIs instead of uploading to fal's CDN — this avoids
    # the storage/auth/token step (which can 403) and works with fal's image_url inputs.
    def to_data_uri(path):
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
        with open(path, "rb") as fh:
            return f"data:{mime};base64," + base64.b64encode(fh.read()).decode()
    print(f"Encoding {len(imgs)} image(s)…")
    total_mb = sum(os.path.getsize(p) for p in imgs.values()) / 1e6
    if total_mb > 18:
        print(f"  heads-up: {total_mb:.0f} MB of images — if the request fails on size, shrink them first:")
        print("    sips -Z 1600 ~/Desktop/*.jpg")
    urls = {k: to_data_uri(p) for k, p in imgs.items()}

    def on_update(update):
        for log in (getattr(update, "logs", None) or []):
            msg = log.get("message") if isinstance(log, dict) else None
            if msg:
                print("  " + msg)

    n = len(urls)
    has_tb = ("top" in urls) or ("bottom" in urls)
    # routing: TRELLIS takes an arbitrary list of views (so it can use top/bottom and 5-6 shots);
    # Hunyuan's multi-view endpoint only takes the four named sides.
    use_trellis = (args.model == "trellis") or (args.model == "auto" and (has_tb or n > 4))

    if n == 1 and args.model != "trellis":
        model = "fal-ai/hunyuan3d/v2"                       # single-image endpoint
        arguments = {"input_image_url": urls["front"]}      # <- verify field name on fal if it errors
        how = "single image"
    elif use_trellis:
        model = "fal-ai/trellis/multi"                     # arbitrary multi-view (uses all 6)
        arguments = {"image_urls": [urls[k] for k in order if k in urls],
                     "multiimage_algo": "stochastic"}        # <- verify field names on fal if it errors
        how = f"{n} views (TRELLIS multi)"
    else:
        model = "fal-ai/hunyuan3d/v2/multi-view"           # 2-4 named sides
        arguments = {"front_image_url": urls["front"]}      # <- field names to verify on fal if it errors
        if "back" in urls:  arguments["back_image_url"] = urls["back"]
        if "left" in urls:  arguments["left_image_url"] = urls["left"]
        if "right" in urls: arguments["right_image_url"] = urls["right"]
        ignored = [k for k in ("top", "bottom") if k in urls]
        if ignored:
            print(f"  note: Hunyuan multi-view ignores {ignored}; pass --model trellis to use all views")
        how = f"{n} sides (Hunyuan multi-view)"

    print(f"Reconstructing with {model}  ({how})…")
    print("This runs on a cloud GPU — usually under a minute.")
    result = fal_client.subscribe(model, arguments=arguments, with_logs=True, on_queue_update=on_update)

    # locate the GLB url in the response (covers the common field names)
    url = None
    if isinstance(result, dict):
        for key in ("model_mesh", "model_glb", "mesh", "glb"):
            v = result.get(key)
            if isinstance(v, dict) and v.get("url"):
                url = v["url"]; break
            if isinstance(v, str) and v.startswith("http"):
                url = v; break
    if not url:
        sys.exit("Couldn't find a mesh URL in the result. Full response:\n" + repr(result))
    if not str(url).lower().startswith("https://"):
        sys.exit("Refusing to download from a non-https URL: %s" % str(url)[:80])

    print(f"Downloading mesh → {args.output}")
    urllib.request.urlretrieve(url, args.output)
    print(f"Done ✅  {args.output}")
    print(f"Next:  caliber_prep {args.output} model.stl --auto --ref-mm <real length in mm>")


if __name__ == "__main__":
    main()
