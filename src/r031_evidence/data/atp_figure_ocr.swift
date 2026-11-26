// Local macOS Vision adapter. OCR tokens are review candidates, never truth.
import Foundation
import Vision
import ImageIO
import CryptoKit

guard CommandLine.arguments.count == 2 else { exit(2) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(3) }
func recognize(_ image: CGImage) throws -> [[String: Any]] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US"]
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return ["text": candidate.string, "ocr_confidence": candidate.confidence,
                "bbox": [box.minX, box.minY, box.width, box.height]]
    }
}

// Rotated labels are common on scientific x-axes. Keep this as a separate
// candidate view: its boxes are in rotated-image coordinates, never assigned
// to a panel, arm, dose, or value by this OCR adapter.
let angleDegrees = -45.0
let radians = angleDegrees * Double.pi / 180.0
let outWidth = Int(ceil(abs(Double(image.width) * cos(radians)) +
                        abs(Double(image.height) * sin(radians))))
let outHeight = Int(ceil(abs(Double(image.width) * sin(radians)) +
                         abs(Double(image.height) * cos(radians))))
guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
      let context = CGContext(data: nil, width: outWidth, height: outHeight,
                              bitsPerComponent: 8, bytesPerRow: outWidth * 4,
                              space: colorSpace,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(4) }
context.clear(CGRect(x: 0, y: 0, width: outWidth, height: outHeight))
context.translateBy(x: CGFloat(outWidth) / 2, y: CGFloat(outHeight) / 2)
context.rotate(by: CGFloat(radians))
context.draw(image, in: CGRect(x: -CGFloat(image.width) / 2,
                               y: -CGFloat(image.height) / 2,
                               width: CGFloat(image.width), height: CGFloat(image.height)))
guard let rotatedImage = context.makeImage(), let pixels = context.data else { exit(5) }
let pixelCount = outWidth * outHeight * 4
let pixelData = Data(bytes: pixels, count: pixelCount)
let rotatedPixelSHA256 = SHA256.hash(data: pixelData).map { String(format: "%02x", $0) }.joined()
let tokens = try recognize(image)
let rotatedTokens = try recognize(rotatedImage)
let result: [String: Any] = ["tokens": tokens, "width": image.width, "height": image.height,
    "coordinate_basis": "normalized image coordinates; origin bottom-left",
    "rotated_views": [["angle_degrees": angleDegrees,
                        "coordinate_basis": "normalized rotated-image coordinates; origin bottom-left",
                        "width": rotatedImage.width, "height": rotatedImage.height,
                        "rgba_pixel_sha256": rotatedPixelSHA256,
                        "tokens": rotatedTokens,
                        "state": "ROTATED_TEXT_CANDIDATES_PANEL_AND_ARM_UNASSIGNED"]],
    "backend": "macOS Vision VNRecognizeTextRequest accurate; original and -45 degree rotated views; language correction disabled",
    "os_version": ProcessInfo.processInfo.operatingSystemVersionString]
let bytes = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
FileHandle.standardOutput.write(bytes)
