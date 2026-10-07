// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/*
 * [INPUT]: VizTestSuite 的共享任务环境、SoftwareOutputDevice 和真实 Skia 画布。
 * [OUTPUT]: Mideo 显式启用后 Alpha、Resize 及普通设备隔离的像素验收。
 * [POS]: Viz 软件输出设备回归合同，由独立 Mideo 目标与 Viz 单测目标消费。
 * [PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md。
 */

#include "components/viz/service/display/software_output_device.h"

#include "testing/gtest/include/gtest/gtest.h"
#include "third_party/skia/include/core/SkBitmap.h"
#include "third_party/skia/include/core/SkCanvas.h"
#include "third_party/skia/include/core/SkColorSpace.h"

namespace viz {

TEST(SoftwareOutputDeviceMideoTest, OptInPreservesAlphaAndSurvivesResize) {
  SoftwareOutputDevice device;
  EXPECT_FALSE(device.EnableMideoFrameOutput());
  device.Resize(gfx::Size(2, 2), 1.f);
  auto* canvas = device.BeginPaint(gfx::Rect(2, 2));
  ASSERT_TRUE(canvas);
  EXPECT_EQ(canvas->imageInfo().alphaType(), kOpaque_SkAlphaType);
  EXPECT_FALSE(canvas->imageInfo().colorSpace());
  device.EndPaint();

  ASSERT_TRUE(device.EnableMideoFrameOutput());
  canvas = device.BeginPaint(gfx::Rect(2, 2));
  ASSERT_TRUE(canvas);
  EXPECT_EQ(canvas->imageInfo().alphaType(), kPremul_SkAlphaType);
  ASSERT_TRUE(canvas->imageInfo().colorSpace());
  EXPECT_TRUE(canvas->imageInfo().colorSpace()->isSRGB());
  canvas->clear(SkColorSetARGB(128, 255, 0, 0));
  device.EndPaint();

  // 连续请求不得重新分配或清空已绘制内容。
  ASSERT_TRUE(device.EnableMideoFrameOutput());
  auto bitmap = device.ReadbackForTesting();
  EXPECT_EQ(bitmap.getColor(0, 0), SkColorSetARGB(128, 255, 0, 0));
  device.Resize(gfx::Size(3, 3), 1.f);
  canvas = device.BeginPaint(gfx::Rect(3, 3));
  ASSERT_TRUE(canvas);
  EXPECT_EQ(canvas->imageInfo().alphaType(), kPremul_SkAlphaType);
  ASSERT_TRUE(canvas->imageInfo().colorSpace());
  EXPECT_TRUE(canvas->imageInfo().colorSpace()->isSRGB());
  canvas->clear(SK_ColorTRANSPARENT);
  device.EndPaint();
  EXPECT_EQ(device.ReadbackForTesting().getColor(0, 0), SK_ColorTRANSPARENT);

  // 导出模式属于该设备，不能污染另一个普通渲染会话。
  SoftwareOutputDevice ordinary;
  ordinary.Resize(gfx::Size(2, 2), 1.f);
  canvas = ordinary.BeginPaint(gfx::Rect(2, 2));
  ASSERT_TRUE(canvas);
  EXPECT_EQ(canvas->imageInfo().alphaType(), kOpaque_SkAlphaType);
  EXPECT_FALSE(canvas->imageInfo().colorSpace());
  ordinary.EndPaint();
}

}  // namespace viz
