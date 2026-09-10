import Foundation
import ImageIO
import CryptoKit

struct AuditError: Error { let message: String }
func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw AuditError(message: message) }
}
func digest(_ url: URL) throws -> String {
    SHA256.hash(data: try Data(contentsOf: url)).map { String(format: "%02x", $0) }.joined()
}

func verify(_ argument: String) throws {
    let folder = URL(fileURLWithPath: argument)
    guard let info = try JSONSerialization.jsonObject(with: Data(contentsOf: folder.appendingPathComponent("metadata.json"))) as? [String:Any],
          let sceneFile = info["scene_file"] as? String,
          let checkpoints = info["checkpoint_sha256"] as? [String:String] else {
        throw AuditError(message: "current render provenance required; rerender")
    }
    let sceneURL = folder.deletingLastPathComponent().appendingPathComponent(sceneFile)
    let gifURL = folder.appendingPathComponent("trajectory_animation.gif")
    try require(try digest(sceneURL) == info["scene_sha256"] as? String, "scene hash mismatch; stale render")
    try require(try digest(gifURL) == info["gif_sha256"] as? String, "GIF hash mismatch")
    for (name, expected) in checkpoints {
        try require(try digest(folder.appendingPathComponent(name)) == expected, "PNG checkpoint hash mismatch")
    }
    guard let source = CGImageSourceCreateWithURL(gifURL as CFURL, nil),
          let scene = try JSONSerialization.jsonObject(with: Data(contentsOf: sceneURL)) as? [String:Any],
          let times = scene["times_days"] as? [Double] else {
        throw AuditError(message: "GIF or source scene unreadable")
    }
    let n = CGImageSourceGetCount(source)
    try require(n == info["frame_count"] as? Int && n == times.count, "frame count mismatch")
    for i in 0..<n {
        guard let props = CGImageSourceCopyPropertiesAtIndex(source,i,nil) as? [CFString:Any],
              let gif = props[kCGImagePropertyGIFDictionary] as? [CFString:Any],
              let delay = gif[kCGImagePropertyGIFDelayTime] as? Double else {
            throw AuditError(message: "frame properties unreadable")
        }
        try require(props[kCGImagePropertyPixelWidth] as? Int == 1280 && props[kCGImagePropertyPixelHeight] as? Int == 800, "frame dimensions mismatch")
        try require(abs(delay - 0.06) < 1e-8, "incorrect frame delay")
    }
    guard let global = CGImageSourceCopyProperties(source,nil) as? [CFString:Any],
          let gif = global[kCGImagePropertyGIFDictionary] as? [CFString:Any] else {
        throw AuditError(message: "global GIF properties unreadable")
    }
    try require(gif[kCGImagePropertyGIFLoopCount] as? Int == 0, "GIF loop mismatch")
    let size = try FileManager.default.attributesOfItem(atPath: gifURL.path)[.size] as! NSNumber
    print("verified \(folder.path): \(n) frames, 1280x800, 0.06 s/frame, infinite loop, hashes match, \(size) bytes")
}

do {
    try require(CommandLine.arguments.count > 1, "usage: verify_animation_media scene_output_folder ...")
    for argument in CommandLine.arguments.dropFirst() { try verify(argument) }
} catch let error as AuditError {
    fputs("media audit failed: \(error.message)\n", stderr); exit(2)
} catch {
    fputs("media audit failed: \(error)\n", stderr); exit(2)
}
