// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/*
 * [INPUT]: Skia 可读像素、混合模式完整定义、目标 BGRA 格式及同次读锁内绘制参数。
 * [OUTPUT]: 实像素核验后的无损写入结果与整数映射 nearest/linear 同次读锁视图；拒绝不改源或目标。
 * [POS]: 软件 Mideo 像素优化边界，通用 readPixels 与原 tile 绘制均由调用者保留。
 * [PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md。
 */
#ifndef COMPONENTS_VIZ_SERVICE_DISPLAY_MIDEO_OPAQUE_PIXELS_H_
#define COMPONENTS_VIZ_SERVICE_DISPLAY_MIDEO_OPAQUE_PIXELS_H_

#include <cmath>
#include <cstdint>

#include "base/containers/span.h"
#include "third_party/skia/include/core/SkBlendMode.h"
#include "third_party/skia/include/core/SkColorSpace.h"
#include "third_party/skia/include/core/SkImage.h"
#include "third_party/skia/include/core/SkMatrix.h"
#include "third_party/skia/include/core/SkPaint.h"
#include "third_party/skia/include/core/SkPixmap.h"
#include "third_party/skia/include/core/SkSamplingOptions.h"

namespace viz {

// 只允许同次 SharedImage 读锁内创建、同步绘制并销毁，不缓存视图或 Alpha。
inline sk_sp<SkImage> MakeOpaqueMideoTileImage(
    const SkImage* image,
    const SkImageInfo& target,
    const SkRect& source_rect,
    const SkRect& destination_rect,
    const SkMatrix& matrix,
    const SkSamplingOptions& sampling,
    const SkPaint& paint,
    bool clip_is_bw) {
  const auto integer = [](SkScalar value) {
    // 限定精确整数范围，避免组合平移超出 Skia sprite 的整数坐标域。
    return std::isfinite(value) && std::abs(value) <= (1 << 20) &&
           value == std::floor(value);
  };
  const auto blend = paint.asBlendMode();
  if (!image || !clip_is_bw || paint.isAntiAlias() ||
      paint.getAlphaf() != 1.f || !blend ||
      (*blend != SkBlendMode::kSrc && *blend != SkBlendMode::kSrcOver) ||
      paint.getShader() || paint.getColorFilter() || paint.getMaskFilter() ||
      paint.getImageFilter() || sampling.useCubic || sampling.maxAniso != 0 ||
      (sampling.filter != SkFilterMode::kNearest &&
       sampling.filter != SkFilterMode::kLinear) ||
      sampling.mipmap != SkMipmapMode::kNone ||
      (matrix.getType() & ~SkMatrix::kTranslate_Mask) ||
      !integer(matrix.getTranslateX()) || !integer(matrix.getTranslateY()) ||
      !destination_rect.isFinite() ||
      !integer(destination_rect.left()) || !integer(destination_rect.top()) ||
      !integer(destination_rect.right()) ||
      !integer(destination_rect.bottom()) || target.width() <= 0 ||
      target.height() <= 0 || target.colorType() != kBGRA_8888_SkColorType ||
      target.alphaType() != kPremul_SkAlphaType || !target.colorSpace() ||
      !target.colorSpace()->isSRGB()) {
    return nullptr;
  }
  SkPixmap source;
  if (!image->peekPixels(&source) || !source.addr() || source.width() <= 0 ||
      source.height() <= 0 || source.width() > (1 << 20) ||
      source.height() > (1 << 20) ||
      source.colorType() != kBGRA_8888_SkColorType ||
      source.alphaType() != kPremul_SkAlphaType || !source.colorSpace() ||
      !source.colorSpace()->isSRGB() ||
      !SkColorSpace::Equals(source.colorSpace(), target.colorSpace()) ||
      source.rowBytes() != source.info().minRowBytes() ||
      reinterpret_cast<uintptr_t>(source.addr()) % alignof(uint32_t) != 0 ||
      source_rect != SkRect::MakeWH(source.width(), source.height()) ||
      destination_rect.width() != source.width() ||
      destination_rect.height() != source.height() ||
      !source.computeIsOpaque()) {
    return nullptr;
  }
  // 原图像及 SharedImage 的 Premul 契约不变；别名不承担像素租约。
  const SkPixmap opaque(source.info().makeAlphaType(kOpaque_SkAlphaType),
                        source.addr(), source.rowBytes());
  return SkImages::RasterFromPixmap(opaque, nullptr, nullptr);
}

// 所有格式和容量检查均在扫描前完成，Alpha 检查均在写入前完成。
inline bool CopyOpaqueMideoPixels(const SkPixmap& source,
                                 const SkImageInfo& target,
                                 base::span<uint8_t> destination,
                                 size_t row_bytes) {
  if (!source.addr() || target.width() <= 0 || target.height() <= 0 ||
      source.width() != target.width() || source.height() != target.height() ||
      source.colorType() != kBGRA_8888_SkColorType ||
      target.colorType() != kBGRA_8888_SkColorType ||
      source.alphaType() != kPremul_SkAlphaType ||
      target.alphaType() != kUnpremul_SkAlphaType ||
      !source.colorSpace() || !target.colorSpace() ||
      !source.colorSpace()->isSRGB() || !target.colorSpace()->isSRGB() ||
      !SkColorSpace::Equals(source.colorSpace(), target.colorSpace()) ||
      row_bytes != target.minRowBytes() || source.rowBytes() != row_bytes ||
      reinterpret_cast<uintptr_t>(source.addr()) % alignof(uint32_t) != 0 ||
      destination.size() / row_bytes != static_cast<size_t>(target.height()) ||
      destination.size() % row_bytes != 0) {
    return false;
  }
  // 不信任 opaque 元数据；逐个验证实际 Alpha，任一透明像素保留通用回退。
  if (!source.computeIsOpaque()) {
    return false;
  }
  // 已验证全 Alpha=255，只改变这次只读视图；原画布仍为 Premul。
  // 固定 Skia 的同格式、同颜色空间、无 Alpha 转换路径执行 SkRectMemcpy。
  const SkPixmap opaque(source.info().makeAlphaType(kOpaque_SkAlphaType),
                        source.addr(), source.rowBytes());
  return opaque.readPixels(target, destination.data(), row_bytes);
}

}  // namespace viz

#endif  // COMPONENTS_VIZ_SERVICE_DISPLAY_MIDEO_OPAQUE_PIXELS_H_
