// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/*
 * [INPUT]: Skia 可读像素、目标 BGRA 图像格式及有界共享输出区域。
 * [OUTPUT]: 严格确认全不透明后无损写入的结果；拒绝时不改动目标区域。
 * [POS]: 软件 Mideo 导出的像素优化边界，通用 readPixels 仍由调用者保留。
 * [PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md。
 */
#ifndef COMPONENTS_VIZ_SERVICE_DISPLAY_MIDEO_OPAQUE_PIXELS_H_
#define COMPONENTS_VIZ_SERVICE_DISPLAY_MIDEO_OPAQUE_PIXELS_H_

#include <cstdint>

#include "base/containers/span.h"
#include "third_party/skia/include/core/SkColorSpace.h"
#include "third_party/skia/include/core/SkPixmap.h"

namespace viz {

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
