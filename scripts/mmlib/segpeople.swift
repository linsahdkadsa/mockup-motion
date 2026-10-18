// 用 macOS 自带的人像分割（Vision）给底片逐帧抠出完整的人（衣服和背景颜色接近时，帧间差分会漏掉，这个不会）。
// 用法：segpeople jobs.json
// jobs.json：[{"in": "plate/f_000.jpg", "out": "tmp/v_000.png", "rects": [[x, y, w, h], ...]}, ...]
//   rects 是要分割的区域（原图像素，左上角为原点）；空数组表示整张图。
// 输出：和原图一样大的灰度 PNG，白 = 人。每处理 10 帧打印一行进度。
import Foundation
import Vision
import CoreImage
import ImageIO
import UniformTypeIdentifiers

struct Job: Decodable { let `in`: String; let out: String; let rects: [[Double]] }

let jobs = try JSONDecoder().decode([Job].self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
let ctx = CIContext(options: [.cacheIntermediates: false])
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = .accurate
req.outputPixelFormat = kCVPixelFormatType_OneComponent8

for (n, job) in jobs.enumerated() {
  autoreleasepool {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: job.in) as CFURL, nil),
          let full = CGImageSourceCreateImageAtIndex(src, 0, nil) else { print("SKIP \(job.in)"); return }
    let W = full.width, H = full.height
    var acc = CIImage(color: CIColor.black).cropped(to: CGRect(x: 0, y: 0, width: W, height: H))
    let rects = job.rects.isEmpty ? [[0, 0, Double(W), Double(H)]] : job.rects
    for r in rects {
      let crop = CGRect(x: r[0], y: r[1], width: r[2], height: r[3]).integral.intersection(CGRect(x: 0, y: 0, width: W, height: H))
      guard crop.width > 16, crop.height > 16, let img = full.cropping(to: crop) else { continue }
      do { try VNImageRequestHandler(cgImage: img, options: [:]).perform([req]) } catch { continue }
      guard let pb = req.results?.first?.pixelBuffer else { continue }
      let m = CIImage(cvPixelBuffer: pb)
      let sx = Double(img.width) / m.extent.width, sy = Double(img.height) / m.extent.height
      // CoreImage 原点在左下：把裁剪区放回整图的对应位置
      let placed = m.transformed(by: CGAffineTransform(scaleX: sx, y: sy))
        .transformed(by: CGAffineTransform(translationX: crop.minX, y: Double(H) - crop.maxY))
      acc = placed.applyingFilter("CIMaximumCompositing", parameters: [kCIInputBackgroundImageKey: acc])
    }
    let gray = CGColorSpaceCreateDeviceGray()
    guard let cg = ctx.createCGImage(acc.cropped(to: CGRect(x: 0, y: 0, width: W, height: H)), from: CGRect(x: 0, y: 0, width: W, height: H), format: .L8, colorSpace: gray),
          let dst = CGImageDestinationCreateWithURL(URL(fileURLWithPath: job.out) as CFURL, UTType.png.identifier as CFString, 1, nil) else { print("FAIL \(job.out)"); return }
    CGImageDestinationAddImage(dst, cg, nil)
    CGImageDestinationFinalize(dst)
    if n % 10 == 0 || n == jobs.count - 1 { print("\(n + 1)/\(jobs.count)"); fflush(stdout) }
  }
}
