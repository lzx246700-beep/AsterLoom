// Read-only CoreGraphics pixel access for original scientific JPEG sources.
// No output image, rescaling, enhancement, AI generation or source modification.
import Foundation
import ImageIO
import CoreGraphics
guard CommandLine.arguments.count == 2 else { exit(2) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(3) }
let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
let orientation = properties?[kCGImagePropertyOrientation] as? Int ?? 1
guard orientation == 1, image.width > 0, image.height > 0,
      image.width * image.height <= 6000000 else { exit(4) }
let stride = image.width * 4
var bytes = [UInt8](repeating: 0, count: stride * image.height)
let success = bytes.withUnsafeMutableBytes { buffer -> Bool in
    guard let context = CGContext(data: buffer.baseAddress, width: image.width, height: image.height,
        bitsPerComponent: 8, bytesPerRow: stride, space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue) else { return false }
    context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
    return true
}
guard success else { exit(5) }
let result: [String: Any] = ["width":image.width,"height":image.height,"bpp":4,
    "rgba_base64":Data(bytes).base64EncodedString(),"backend":"macOS ImageIO/CoreGraphics source decode",
    "source_modified":false,"exif_orientation":orientation]
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:result,options:[.sortedKeys]))
