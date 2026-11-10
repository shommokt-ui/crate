import AppKit

let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

let cyan = NSColor(calibratedRed: 1.0, green: 0.627, blue: 0.302, alpha: 1)
let violet = NSColor(calibratedRed: 0.949, green: 0.329, blue: 0.106, alpha: 1)

func stroke(_ path: NSBezierPath, width: CGFloat, alpha: CGFloat = 1) {
    // Clip to the stroked outline, then fill with the diagonal brand gradient.
    guard let context = NSGraphicsContext.current?.cgContext else { return }
    context.saveGState()
    context.setLineWidth(width)
    context.setLineCap(.round)
    context.setLineJoin(.round)
    context.addPath(path.cgPath)
    context.replacePathWithStrokedPath()
    context.clip()
    context.setAlpha(alpha)
    let gradient = NSGradient(starting: cyan, ending: violet)!
    gradient.draw(from: NSPoint(x: 160, y: 900), to: NSPoint(x: 860, y: 140), options: [])
    context.restoreGState()
}

func drawIcon(size: Int, filename: String) throws {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let transform = NSAffineTransform()
    transform.scale(by: CGFloat(size) / 1024)
    transform.concat()

    // Dark glassy tile with a soft inner glow and gradient hairline.
    let tile = NSBezierPath(roundedRect: NSRect(x: 48, y: 48, width: 928, height: 928), xRadius: 210, yRadius: 210)
    NSGradient(starting: NSColor(calibratedRed: 0.17, green: 0.10, blue: 0.07, alpha: 1),
               ending: NSColor(calibratedRed: 0.04, green: 0.03, blue: 0.02, alpha: 1))!
        .draw(in: tile, angle: -60)
    stroke(NSBezierPath(roundedRect: NSRect(x: 52, y: 52, width: 920, height: 920), xRadius: 206, yRadius: 206), width: 6, alpha: 0.5)

    // Crate: hexagon outline with the top "Y" edges, y-axis flipped from the SVG.
    func point(_ x: CGFloat, _ y: CGFloat) -> NSPoint { NSPoint(x: 48 + x * 1.8125 , y: 1024 - (48 + y * 1.8125)) }
    let hexagon = NSBezierPath()
    hexagon.move(to: point(256, 92)); hexagon.line(to: point(410, 176)); hexagon.line(to: point(410, 344))
    hexagon.line(to: point(256, 428)); hexagon.line(to: point(102, 344)); hexagon.line(to: point(102, 176)); hexagon.close()
    stroke(hexagon, width: 40)
    let top = NSBezierPath()
    top.move(to: point(102, 176)); top.line(to: point(256, 260)); top.line(to: point(410, 176))
    stroke(top, width: 26, alpha: 0.45)

    // Sound bars standing inside the crate.
    let bars: [(CGFloat, CGFloat, CGFloat)] = [(200, 318, 340), (228, 296, 362), (256, 270, 388), (284, 300, 358), (312, 320, 338)]
    for (x, from, to) in bars {
        let bar = NSBezierPath()
        bar.move(to: point(x, from)); bar.line(to: point(x, to))
        stroke(bar, width: 36)
    }
    NSGraphicsContext.restoreGraphicsState()
    try bitmap.representation(using: .png, properties: [:])!.write(to: directory.appendingPathComponent(filename))
}

for size in [16, 32, 128, 256, 512] {
    try drawIcon(size: size, filename: "icon_\(size)x\(size).png")
    try drawIcon(size: size * 2, filename: "icon_\(size)x\(size)@2x.png")
}
