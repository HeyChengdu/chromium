// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/*
 * [INPUT]: Skia 可读像素、目标 BGRA 格式、有界输出区域及同次读锁内绘制参数。
 * [OUTPUT]: 全不透明无损写入结果与待验收的临时绘制视图接口；拒绝不改源或目标。
 * [POS]: 软件 Mideo 像素优化边界，通用 readPixels 与原 tile 绘制均由调用者保留。
 * [PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md。
 */
#ifndef COMPONENTS_VIZ_SERVICE_DISPLAY_MIDEO_OPAQUE_PIXELS_H_
#define COMPONENTS_VIZ_SERVICE_DISPLAY_MIDEO_OPAQUE_PIXELS_H_

#include <cstdint>

#include "base/containers/span.h"
#include "third_party/skia/include/core/SkColorSpace.h"
#include "third_party/skia/include/core/SkImage.h"
#include "third_party/skia/include/core/SkMatrix.h"
#include "third_party/skia/include/core/SkPaint.h"
#include "third_party/skia/include/core/SkPixmap.h"
#include "third_party/skia/include/core/SkSamplingOptions.h"

namespace viz {

// 待验收接口：只允许同次 SharedImage 读锁内创建、使用并销毁，不缓存视图。
// 先以拒绝实现运行真实像素合同红灯；尚未接入 SoftwareRenderer。
inline sk_sp<SkImage> MakeOpaqueMideoTileImage(
    const SkImage* image,
    const SkImageInfo& target,
    const SkRect& source_rect,
    const SkRect& destination_rect,
    const SkMatrix& matrix,
    const SkSamplingOptions& sampling,
    const SkPaint& paint,
    bool clip_is_bw) {
  return nullptr;
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
