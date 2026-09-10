import Foundation
import AppKit
import CoreGraphics
import CoreText
import ImageIO
import UniformTypeIdentifiers
import CryptoKit

func digest(_ url: URL) throws -> String {
    SHA256.hash(data: try Data(contentsOf: url)).map { String(format: "%02x", $0) }.joined()
}

struct Body: Codable {
    let name: String
    let xy: [Double]?
    let trajectory_xy: [[Double]]?
    let radius: Double?
}

struct Scene: Codable {
    let title: String
    let subtitle: String?
    let model: String
    let frame_label: String?
    let unit: String
    let mode: String
    let times_days: [Double]
    let dates: [String]?
    let prediction_xy: [[Double]]
    let reference_xy: [[Double]]
    let error_km: [Double]
    let context_bodies: [Body]?
    let limits: [Double]
    let amplification: String?
    let footer: String?
}

enum RenderError: Error, CustomStringConvertible {
    case message(String)
    var description: String { if case .message(let text) = self { return text }; return "render error" }
}

let width = 1280
let height = 800
let frameDelay = 0.06

func finite(_ x: Double, _ label: String) throws -> Double {
    guard x.isFinite else { throw RenderError.message("\(label) must be finite") }
    return x
}

func point(_ value: [Double], _ label: String) throws -> CGPoint {
    guard value.count == 2 else { throw RenderError.message("\(label) must contain exactly two values") }
    return CGPoint(x: try finite(value[0], "\(label)[0]"), y: try finite(value[1], "\(label)[1]"))
}

func color(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> CGColor {
    CGColor(red: r, green: g, blue: b, alpha: a)
}

func drawText(_ ctx: CGContext, _ text: String, _ x: CGFloat, _ y: CGFloat, _ size: CGFloat, _ textColor: CGColor, bold: Bool = false, maxWidth: CGFloat = 700, maxLines: Int = 3) {
    let font = NSFont(name: bold ? "Avenir Next Demi Bold" : "Avenir Next Regular", size: size) ?? NSFont.systemFont(ofSize: size, weight: bold ? .semibold : .regular)
    let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor(cgColor: textColor) ?? .white]
    var lines: [String] = []
    for paragraph in text.split(separator: "\n", omittingEmptySubsequences: false) {
        var current = ""
        for word in paragraph.split(separator: " ") {
            let candidate = current.isEmpty ? String(word) : current + " " + word
            let measure = NSAttributedString(string: candidate, attributes: attrs).size().width
            if measure > maxWidth && !current.isEmpty { lines.append(current); current = String(word) } else { current = candidate }
        }
        lines.append(current)
    }
    for (index, value) in lines.prefix(maxLines).enumerated() {
        let line = NSAttributedString(string: value, attributes: attrs)
        ctx.textPosition = CGPoint(x: x, y: y - CGFloat(index + 1) * size * 1.25)
        CTLineDraw(CTLineCreateWithAttributedString(line), ctx)
    }
}

func makeImage(_ draw: (CGContext) throws -> Void) throws -> CGImage {
    let colorSpace = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4, space: colorSpace, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        throw RenderError.message("could not create bitmap context")
    }
    ctx.setFillColor(color(0.025, 0.055, 0.12))
    ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
    try draw(ctx)
    guard let image = ctx.makeImage() else { throw RenderError.message("could not create image") }
    return image
}

func map(_ p: CGPoint, _ limits: [Double], _ rect: CGRect) -> CGPoint {
    let x = (p.x - limits[0]) / (limits[1] - limits[0])
    let y = (p.y - limits[2]) / (limits[3] - limits[2])
    return CGPoint(x: rect.minX + CGFloat(x) * rect.width, y: rect.minY + CGFloat(y) * rect.height)
}

func drawTrace(_ ctx: CGContext, _ values: [[Double]], _ end: Int, _ limits: [Double], _ rect: CGRect, _ stroke: CGColor, _ lineWidth: CGFloat) throws {
    guard !values.isEmpty else { return }
    ctx.saveGState(); ctx.addRect(rect); ctx.clip(); ctx.setStrokeColor(stroke); ctx.setLineWidth(lineWidth); ctx.setLineJoin(.round); ctx.setLineCap(.round)
    let count = min(end + 1, values.count)
    if count > 0 { ctx.move(to: map(try point(values[0], "trace"), limits, rect)) }
    if count > 1 { for i in 1..<count { ctx.addLine(to: map(try point(values[i], "trace"), limits, rect)) } }
    ctx.strokePath(); ctx.restoreGState()
}

func savePNG(_ image: CGImage, _ path: URL) throws {
    guard let dest = CGImageDestinationCreateWithURL(path as CFURL, UTType.png.identifier as CFString, 1, nil) else { throw RenderError.message("could not create PNG destination") }
    CGImageDestinationAddImage(dest, image, nil); guard CGImageDestinationFinalize(dest) else { throw RenderError.message("could not finalize PNG") }
}

func validate(_ scene: Scene) throws {
    guard scene.mode == "overview" || scene.mode == "zoom" else { throw RenderError.message("mode must be overview or zoom") }
    let n = scene.times_days.count
    guard n > 1, scene.prediction_xy.count == n, scene.reference_xy.count == n, scene.error_km.count == n else { throw RenderError.message("times, traces and errors must have equal length") }
    guard scene.limits.count == 4, scene.limits.allSatisfy({ $0.isFinite }), scene.limits[0] < scene.limits[1], scene.limits[2] < scene.limits[3] else { throw RenderError.message("limits must be four finite ordered values") }
    for i in 0..<n { _ = try finite(scene.times_days[i], "times_days[\(i)]"); if i > 0 && scene.times_days[i] <= scene.times_days[i-1] { throw RenderError.message("times_days must be strictly increasing") }; _ = try point(scene.prediction_xy[i], "prediction_xy[\(i)]"); _ = try point(scene.reference_xy[i], "reference_xy[\(i)]"); if try finite(scene.error_km[i], "error_km[\(i)]") < 0 { throw RenderError.message("error_km must be nonnegative") } }
    if let dates = scene.dates, dates.count != n { throw RenderError.message("dates must match times_days length") }
    for body in scene.context_bodies ?? [] {
        if let xy = body.xy { _ = try point(xy, "body \(body.name).xy") }
        if let path = body.trajectory_xy, path.count != n { throw RenderError.message("body \(body.name) trajectory length mismatch") }
        if let path = body.trajectory_xy { for p in path { _ = try point(p, "body trajectory") } }
        if let r = body.radius { if try finite(r, "body radius") < 0 { throw RenderError.message("body radius must be nonnegative") } }
    }
}

func render(_ scene: Scene, _ index: Int) throws -> CGImage {
    let plot = CGRect(x: 70, y: 100, width: 820, height: 590)
    let side = CGRect(x: 930, y: 100, width: 290, height: 590)
    return try makeImage { ctx in
        var offscreenBodies: [String] = []
        ctx.setFillColor(color(0.04, 0.09, 0.18)); ctx.fill(plot)
        ctx.setStrokeColor(color(0.18, 0.31, 0.42)); ctx.setLineWidth(1); ctx.stroke(plot)
        for fraction in stride(from: 0.0, through: 1.0, by: 0.25) {
            let x = plot.minX + plot.width * CGFloat(fraction), y = plot.minY + plot.height * CGFloat(fraction)
            ctx.setStrokeColor(color(0.12, 0.22, 0.31, 0.8)); ctx.move(to: CGPoint(x: x, y: plot.minY)); ctx.addLine(to: CGPoint(x: x, y: plot.maxY)); ctx.move(to: CGPoint(x: plot.minX, y: y)); ctx.addLine(to: CGPoint(x: plot.maxX, y: y)); ctx.strokePath()
        }
        try drawTrace(ctx, scene.reference_xy, index, scene.limits, plot, color(0.25, 0.88, 0.88), 3.8)
        try drawTrace(ctx, scene.prediction_xy, index, scene.limits, plot, color(1.0, 0.48, 0.18), 1.8)
        ctx.saveGState(); ctx.addRect(plot); ctx.clip()
        for body in scene.context_bodies ?? [] {
            let bodyPoint: CGPoint?
            if let path = body.trajectory_xy, index < path.count { bodyPoint = try point(path[index], "body trajectory") }
            else if let xy = body.xy { bodyPoint = try point(xy, "body xy") }
            else { bodyPoint = nil }
            if let p = bodyPoint {
                let q = map(p, scene.limits, plot); let radius: CGFloat = 4.5
                guard plot.insetBy(dx: radius, dy: radius).contains(q) else {
                    offscreenBodies.append(body.name)
                    continue
                }
                ctx.setFillColor(color(0.95, 0.80, 0.25, 0.85)); ctx.fillEllipse(in: CGRect(x: q.x-radius, y: q.y-radius, width: radius*2, height: radius*2))
                drawText(ctx, body.name, min(q.x + 7, plot.maxX - 125), min(q.y + 15, plot.maxY - 3), 12, color(0.95, 0.88, 0.55), bold: true, maxWidth: 120, maxLines: 2)
            }
        }
        ctx.restoreGState()
        for (p, c, label, radius) in [(scene.reference_xy[index], color(0.25,0.88,0.88), "reference", 6.0), (scene.prediction_xy[index], color(1.0,0.48,0.18), "prediction", 4.0)] {
            let q = map(try point(p, label), scene.limits, plot); ctx.setFillColor(c); ctx.fillEllipse(in: CGRect(x:q.x-radius,y:q.y-radius,width:radius*2,height:radius*2))
        }
        drawText(ctx, scene.title, 70, 770, 27, .white, bold: true, maxWidth: 1150, maxLines: 1)
        drawText(ctx, scene.subtitle ?? "", 70, 733, 14, color(0.63,0.77,0.84), maxWidth: 1150, maxLines: 2)
        drawText(ctx, "x [\(scene.unit)]", 410, 82, 13, color(0.55,0.7,0.77))
        ctx.saveGState(); ctx.translateBy(x: 12, y: plot.midY); ctx.rotate(by: .pi/2)
        drawText(ctx, "y [\(scene.unit)]", -25, 0, 12, color(0.55,0.7,0.77)); ctx.restoreGState()
        for fraction in stride(from: 0.0, through: 1.0, by: 0.25) {
            let xv = scene.limits[0] + fraction * (scene.limits[1] - scene.limits[0]); let yv = scene.limits[2] + fraction * (scene.limits[3] - scene.limits[2])
            drawText(ctx, String(format: "%.3g", xv), plot.minX + plot.width * CGFloat(fraction) - 12, 98, 10, color(0.48,0.63,0.7))
            drawText(ctx, String(format: "%.3g", yv), 38, plot.minY + plot.height * CGFloat(fraction) + 4, 10, color(0.48,0.63,0.7))
        }
        let scaleUnits = (scene.limits[1] - scene.limits[0]) / 4.0; let scalePixels = plot.width / 4.0
        ctx.setStrokeColor(color(0.8,0.9,0.92)); ctx.setLineWidth(3); ctx.move(to: CGPoint(x: plot.maxX - scalePixels - 25, y: plot.minY + 25)); ctx.addLine(to: CGPoint(x: plot.maxX - 25, y: plot.minY + 25)); ctx.strokePath()
        drawText(ctx, String(format: "%.3g %@", scaleUnits, scene.unit), plot.maxX - scalePixels - 25, plot.minY + 18, 11, color(0.8,0.9,0.92))
        ctx.setFillColor(color(0.06,0.12,0.22)); ctx.fill(side); ctx.setStrokeColor(color(0.18,0.31,0.42)); ctx.stroke(side)
        drawText(ctx, scene.model, 955, 670, 16, .white, bold: true, maxWidth: 245, maxLines: 3)
        drawText(ctx, scene.frame_label ?? "Frame", 955, 595, 13, color(0.62,0.76,0.82), maxWidth: 245, maxLines: 3)
        let progress = Double(index) / Double(scene.times_days.count - 1); let dateText = scene.dates?[index] ?? String(format: "t = %.2f d", scene.times_days[index])
        let displayedDate = dateText.replacingOccurrences(of: " ", with: "\n", range: dateText.range(of: " "))
        drawText(ctx, displayedDate, 955, 530, 21, color(0.25,0.88,0.88), bold: true, maxWidth: 245, maxLines: 2)
        drawText(ctx, String(format: "%.1f%% complete", progress*100), 955, 468, 13, color(0.68,0.78,0.84))
        let errorLabel = scene.error_km[index] < 1.0
            ? String(format: "3D error %.6g m", scene.error_km[index] * 1000.0)
            : String(format: "3D error %.6g km", scene.error_km[index])
        drawText(ctx, errorLabel, 955, 435, 17, color(1.0,0.58,0.28), bold: true, maxWidth: 245, maxLines: 2)
        ctx.setFillColor(color(0.25,0.88,0.88)); ctx.fill(CGRect(x:955,y:368,width:35,height:4)); drawText(ctx, "Horizons reference", 1005, 380, 13, color(0.25,0.88,0.88), maxWidth: 200)
        ctx.setFillColor(color(1.0,0.48,0.18)); ctx.fill(CGRect(x:955,y:338,width:35,height:4)); drawText(ctx, "Prediction", 1005, 350, 13, color(1.0,0.62,0.35), maxWidth: 200)
        if let amp = scene.amplification { drawText(ctx, amp, 955, 310, 13, color(0.78,0.85,0.88), maxWidth: 245, maxLines: 3) }
        let contextNote = offscreenBodies.isEmpty ? "" : offscreenBodies.joined(separator: ", ") + " outside view. "
        drawText(ctx, contextNote + "Bodies schematic; marker size is not physical.", 955, 240, 11, color(0.62,0.74,0.79), maxWidth: 245, maxLines: 4)
        drawText(ctx, scene.footer ?? "Coordinates and traces supplied by the prepared input.", 955, 178, 11, color(0.48,0.62,0.7), maxWidth: 245, maxLines: 4)
    }
}

func main() throws {
    guard CommandLine.arguments.count == 3 else { throw RenderError.message("usage: render_trajectory_animation.swift input.json output_folder") }
    let input = URL(fileURLWithPath: CommandLine.arguments[1]); let output = URL(fileURLWithPath: CommandLine.arguments[2]); try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
    let scene = try JSONDecoder().decode(Scene.self, from: Data(contentsOf: input)); try validate(scene)
    let gifURL = output.appendingPathComponent("trajectory_animation.gif")
    guard let dest = CGImageDestinationCreateWithURL(gifURL as CFURL, UTType.gif.identifier as CFString, scene.times_days.count, nil) else { throw RenderError.message("could not create GIF destination") }
    let properties: [CFString: Any] = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: 0]]
    CGImageDestinationSetProperties(dest, properties as CFDictionary)
    var checkpoints: [Int: String] = [:]
    for i in 0..<scene.times_days.count {
        let image = try render(scene, i)
        let frameProps: [CFString: Any] = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: frameDelay]]
        CGImageDestinationAddImage(dest, image, frameProps as CFDictionary)
        if i == 0 || i == scene.times_days.count/2 || i == scene.times_days.count-1 { let name = i == 0 ? "frame_000.png" : (i == scene.times_days.count-1 ? "frame_end.png" : "frame_mid.png"); try savePNG(image, output.appendingPathComponent(name)); checkpoints[i] = name }
    }
    guard CGImageDestinationFinalize(dest) else { throw RenderError.message("could not finalize GIF") }
    var checkpointHashes: [String: String] = [:]
    for name in checkpoints.values { checkpointHashes[name] = try digest(output.appendingPathComponent(name)) }
    let metadata: [String: Any] = ["schema_version": 2, "frame_count": scene.times_days.count, "width": width, "height": height, "delay_seconds": frameDelay, "loop_count": 0, "gif": "trajectory_animation.gif", "checkpoints": checkpoints.reduce(into: [String:String]()) { $0[String($1.key)] = $1.value }, "scene_file": input.lastPathComponent, "scene_sha256": try digest(input), "gif_sha256": try digest(gifURL), "checkpoint_sha256": checkpointHashes, "renderer_binary_sha256": try digest(URL(fileURLWithPath: CommandLine.arguments[0]))]
    let data = try JSONSerialization.data(withJSONObject: metadata, options: [.prettyPrinted, .sortedKeys]); try data.write(to: output.appendingPathComponent("metadata.json"), options: .atomic)
    print("rendered \(scene.times_days.count) frames at \(width)x\(height) to \(output.path)")
}

do { try main() } catch { fputs("error: \(error)\n", stderr); exit(2) }
