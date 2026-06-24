import Foundation
import RealityKit
import AVFoundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import ModelIO

// caliber-recon — Caliber's macOS reconstruction engine.
// Turns a phone video (or a folder of images) into a textured 3D mesh
// using Apple's Object Capture (RealityKit PhotogrammetrySession), on your own GPU.

@main
struct CaliberRecon {

    static func main() async {
        let args = CommandLine.arguments
        // Frame-picker mode (for the Cloud engine): pull the K sharpest, well-spread frames
        // from a video and write them out — no reconstruction.
        //   caliber-recon --export-frames <K> <video> <outdir>
        if let ei = args.firstIndex(of: "--export-frames") {
            exportFrames(args, flagIndex: ei)   // exits
        }
        guard args.count >= 3 else { printUsage(); exit(2) }

        // Parse: every positional arg is an input except the LAST, which is the output.
        // Inputs may freely mix videos, image files, and image folders — they're all
        // merged into one image set, so you can use a video AND photos together.
        var detail: PhotogrammetrySession.Request.Detail = .medium
        var frameCount = 80
        var positionals: [String] = []
        var i = 1
        while i < args.count {
            switch args[i] {
            case "--detail": if i + 1 < args.count { detail = parseDetail(args[i + 1]); i += 1 }
            case "--frames": if i + 1 < args.count { frameCount = Int(args[i + 1]) ?? frameCount; i += 1 }
            default: positionals.append(args[i])
            }
            i += 1
        }
        guard positionals.count >= 2 else { printUsage(); exit(2) }
        let outputPath = positionals.removeLast()
        let inputPaths = positionals
        let outputURL = URL(fileURLWithPath: outputPath)

        guard PhotogrammetrySession.isSupported else {
            fail("Object Capture isn't supported on this Mac. It needs Apple Silicon (M1 or newer) or an Intel Mac with a 4GB+ VRAM GPU, on macOS 13+.")
        }

        // Merge every input into one working folder: videos -> sampled frames; image
        // files and folders -> copied. Object Capture then reconstructs from all of them.
        let imageExts: Set<String> = ["jpg", "jpeg", "png", "heic", "heif", "tif", "tiff"]
        let videoExts: Set<String> = ["mov", "mp4", "m4v", "avi", "mkv", "webm", "3gp"]
        let imagesURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("caliber-frames-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: imagesURL, withIntermediateDirectories: true)

        var total = 0
        for (k, p) in inputPaths.enumerated() {
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: p, isDirectory: &isDir) else {
                fail("Input not found: \(p)")
            }
            let url = URL(fileURLWithPath: p)
            let ext = url.pathExtension.lowercased()
            if isDir.boolValue {
                let items = (try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? []
                for it in items where imageExts.contains(it.pathExtension.lowercased()) {
                    let dst = imagesURL.appendingPathComponent(String(format: "in%02d_", k) + it.lastPathComponent)
                    try? FileManager.default.copyItem(at: it, to: dst); total += 1
                }
            } else if videoExts.contains(ext) {
                do {
                    let n = try extractFrames(from: url, count: frameCount, to: imagesURL,
                                              prefix: String(format: "vid%02d_", k))
                    log("Extracted \(n) frames from \(url.lastPathComponent)")
                    total += n
                } catch {
                    fail("Could not extract frames from \(url.lastPathComponent): \(error.localizedDescription)")
                }
            } else if imageExts.contains(ext) {
                let dst = imagesURL.appendingPathComponent(String(format: "in%02d_", k) + url.lastPathComponent)
                try? FileManager.default.copyItem(at: url, to: dst); total += 1
            } else {
                log("Skipping unsupported input: \(url.lastPathComponent)")
            }
        }
        guard total >= 2 else {
            fail("Not enough images to reconstruct (got \(total)). Provide a video and/or several photos.")
        }
        log("Using \(total) images from \(inputPaths.count) input(s)")

        do {
            var config = PhotogrammetrySession.Configuration()
            config.sampleOrdering = .sequential        // video frames are already ordered
            config.featureSensitivity = .high          // better for low-texture surfaces

            // Object Capture only writes USD. Reconstruct to a temp .usdz, then convert
            // to whatever extension the user asked for (.obj / .ply / .stl / .usdz).
            let tmpModel = FileManager.default.temporaryDirectory
                .appendingPathComponent("caliber-model-\(UUID().uuidString).usdz")

            let session = try PhotogrammetrySession(input: imagesURL, configuration: config)
            let request = PhotogrammetrySession.Request.modelFile(url: tmpModel, detail: detail)

            log("Reconstructing at detail \(detail). This can take a few minutes on first run…")
            try session.process(requests: [request])

            var lastPct = -1
            for try await output in session.outputs {
                switch output {
                case .requestProgress(_, let fraction):
                    let pct = Int(fraction * 100)
                    if pct != lastPct && pct % 5 == 0 { log("  \(pct)%"); lastPct = pct }
                case .requestComplete(_, let result):
                    if case .modelFile(let url) = result { log("Wrote model: \(url.path)") }
                case .requestError(_, let error):
                    fail("Reconstruction error: \(error.localizedDescription)")
                case .processingComplete:
                    do {
                        try convertModel(from: tmpModel, to: outputURL)
                        try? FileManager.default.removeItem(at: tmpModel)
                        log("Done ✅  → \(outputURL.path)")
                        log("Tip: .usdz previews in Quick Look; .obj feeds caliber-prep.")
                        exit(0)
                    } catch {
                        fail("Export failed: \(error.localizedDescription)")
                    }
                case .invalidSample(let id, let reason):
                    log("  skipped sample \(id): \(reason)")
                case .processingCancelled:
                    fail("Processing was cancelled.")
                default:
                    break
                }
            }
        } catch {
            fail("Session failed: \(error.localizedDescription)")
        }
    }

    // MARK: - Video → frames (AVFoundation)

    static func extractFrames(from videoURL: URL, count: Int, to dir: URL, prefix: String = "") throws -> Int {
        let asset = AVURLAsset(url: videoURL)
        let gen = AVAssetImageGenerator(asset: asset)
        gen.appliesPreferredTrackTransform = true
        gen.requestedTimeToleranceBefore = .zero
        gen.requestedTimeToleranceAfter = .zero

        let durationSec = CMTimeGetSeconds(asset.duration)
        guard durationSec.isFinite, durationSec > 0 else { throw Err.msg("video has zero or unknown duration") }

        var written = 0
        for k in 0..<max(1, count) {
            let t = durationSec * (Double(k) + 0.5) / Double(count)
            let time = CMTime(seconds: t, preferredTimescale: 600)
            if let cg = try? gen.copyCGImage(at: time, actualTime: nil) {
                let url = dir.appendingPathComponent(prefix + String(format: "frame_%04d.jpg", k))
                try writeJPEG(cg, to: url)
                written += 1
            }
        }
        guard written > 0 else { throw Err.msg("no frames could be read from the video") }
        return written
    }

    // MARK: - Frame picker for the Cloud engine (sharpest, well-spread frames)

    // Sharpness via mean squared gradient on a 64×64 grayscale downscale. Higher = sharper.
    static func sharpness(_ cg: CGImage) -> Double {
        let w = 64, h = 64
        var buf = [UInt8](repeating: 0, count: w * h)
        let cs = CGColorSpaceCreateDeviceGray()
        guard let ctx = CGContext(data: &buf, width: w, height: h, bitsPerComponent: 8,
                                  bytesPerRow: w, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return 0 }
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        var s = 0.0
        for y in 1..<(h - 1) {
            for x in 1..<(w - 1) {
                let i = y * w + x
                let gx = Int(buf[i + 1]) - Int(buf[i - 1])
                let gy = Int(buf[i + w]) - Int(buf[i - w])
                s += Double(gx * gx + gy * gy)
            }
        }
        return s
    }

    static func exportFrames(_ args: [String], flagIndex ei: Int) -> Never {
        guard ei + 3 < args.count, let k = Int(args[ei + 1]) else {
            fail("usage: caliber-recon --export-frames <K> <video> <outdir>")
        }
        let videoURL = URL(fileURLWithPath: args[ei + 2])
        let outDir = URL(fileURLWithPath: args[ei + 3])
        try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

        let asset = AVURLAsset(url: videoURL)
        let gen = AVAssetImageGenerator(asset: asset)
        gen.appliesPreferredTrackTransform = true
        gen.requestedTimeToleranceBefore = .zero
        gen.requestedTimeToleranceAfter = .zero
        let dur = CMTimeGetSeconds(asset.duration)
        guard dur.isFinite, dur > 0 else { fail("video has zero or unknown duration") }

        // Sample several candidates per output slot, keep the sharpest in each time window.
        let K = max(1, k)
        let perWindow = 4
        var written = 0
        for slot in 0..<K {
            let lo = dur * Double(slot) / Double(K)
            let hi = dur * Double(slot + 1) / Double(K)
            var best: CGImage? = nil
            var bestScore = -1.0
            for c in 0..<perWindow {
                let t = lo + (hi - lo) * (Double(c) + 0.5) / Double(perWindow)
                if let cg = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil) {
                    let sc = sharpness(cg)
                    if sc > bestScore { bestScore = sc; best = cg }
                }
            }
            if let img = best {
                let url = outDir.appendingPathComponent(String(format: "view_%02d.jpg", slot))
                do { try writeJPEG(img, to: url); print(url.path); written += 1 } catch {}
            }
        }
        guard written > 0 else { fail("could not read frames from the video") }
        log("Exported \(written) frame(s) to \(outDir.path)")
        exit(0)
    }

    // MARK: - Convert the USD result to the requested format (obj/ply/stl/usdz)

    static func convertModel(from src: URL, to dst: URL) throws {
        let ext = dst.pathExtension.lowercased()
        if FileManager.default.fileExists(atPath: dst.path) {
            try? FileManager.default.removeItem(at: dst)
        }
        if ext == "usdz" || ext == "usd" || ext == "usdc" || ext == "usda" {
            try FileManager.default.copyItem(at: src, to: dst)
            return
        }
        guard MDLAsset.canExportFileExtension(ext) else {
            throw Err.msg("can't export “.\(ext)”. Use .usdz, .obj, .ply, or .stl")
        }
        let asset = MDLAsset(url: src)
        try asset.export(to: dst)
    }

    static func writeJPEG(_ image: CGImage, to url: URL) throws {
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else {
            throw Err.msg("could not create image destination")
        }
        CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality as String: 0.9] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { throw Err.msg("could not finalize jpeg") }
    }

    // MARK: - Helpers

    static func parseDetail(_ s: String) -> PhotogrammetrySession.Request.Detail {
        switch s.lowercased() {
        case "preview": return .preview
        case "reduced": return .reduced
        case "medium":  return .medium
        case "full":    return .full
        case "raw":     return .raw
        default:        return .medium
        }
    }

    static func log(_ s: String) { print(s); fflush(stdout) }

    static func fail(_ s: String) -> Never {
        FileHandle.standardError.write(("Error: " + s + "\n").data(using: .utf8)!)
        exit(1)
    }

    static func printUsage() {
        print("""
        caliber-recon — videos and/or photos → 3D mesh, via Apple Object Capture

        Usage:
          caliber-recon <input...> <output> [--detail LEVEL] [--frames N]

          <input...>  one or more inputs, freely mixed: video files (.mov/.mp4…),
                      image files, and/or folders of images. All are merged into one
                      image set — so a video AND photos can be used together.
          <output>    output mesh path — .usdz (Quick Look) or .obj/.ply/.stl
          --detail    preview | reduced | medium | full | raw    (default: medium)
          --frames    frames to sample from each video            (default: 80)

        Examples:
          caliber-recon car.mov car.usdz --detail full --frames 100
          caliber-recon clip.mov front.jpg back.jpg ./more_photos part.obj --detail full
        """)
    }

    enum Err: Error { case msg(String) }
}
