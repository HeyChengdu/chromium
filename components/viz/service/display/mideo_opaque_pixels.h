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

#include "base/containers/span.h"
#include "third_party/skia/include/core/SkPixmap.h"

namespace viz {

// 红灯阶段只建立可执行契约，不接入运行时，也不假装已实现快速路径。
inline bool CopyOpaqueMideoPixels(const SkPixmap&,
                                 const SkImageInfo&,
                                 base::span<uint8_t>,
                                 size_t) {
  return false;
}

}  // namespace viz

#endif  // COMPONENTS_VIZ_SERVICE_DISPLAY_MIDEO_OPAQUE_PIXELS_H_
