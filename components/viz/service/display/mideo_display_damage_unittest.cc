// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/*
 * [INPUT]: VizTestSuite 的共享任务环境、真实 Skia 像素绘制、Mideo 软件 Surface 提交与帧交付协议。
 * [OUTPUT]: 全不透明像素、nearest/linear 临时 tile 视图及 Surface/Alpha/Resize 行为验收。
 * [POS]: Viz 软件 Mideo 定向测试目标，保留原绘制和呈现协议的回归守卫。
 * [PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md。
 */

#include "components/viz/service/display/display.h"

#include <algorithm>
#include <array>

#include "base/containers/span.h"
#include "base/files/file.h"
#include "base/files/scoped_temp_dir.h"
#include "base/functional/bind.h"
#include "base/test/null_task_runner.h"
#include "components/viz/common/display/renderer_settings.h"
#include "components/viz/common/quads/compositor_frame.h"
#include "components/viz/common/surfaces/parent_local_surface_id_allocator.h"
#include "components/viz/service/display/display_client.h"
#include "components/viz/service/display/mideo_opaque_pixels.h"
#include "components/viz/service/display/overlay_processor_stub.h"
#include "components/viz/service/display/software_output_device.h"
#include "components/viz/service/frame_sinks/compositor_frame_sink_support.h"
#include "components/viz/service/frame_sinks/frame_sink_manager_impl.h"
#include "components/viz/test/compositor_frame_helpers.h"
#include "components/viz/test/fake_output_surface.h"
#include "gpu/command_buffer/service/scheduler.h"
#include "gpu/command_buffer/service/shared_image/shared_image_manager.h"
#include "gpu/command_buffer/service/sync_point_manager.h"
#include "testing/gtest/include/gtest/gtest.h"
#include "third_party/skia/include/core/SkBitmap.h"
#include "third_party/skia/include/core/SkCanvas.h"
#include "third_party/skia/include/core/SkColorSpace.h"
#include "third_party/skia/include/core/SkColorFilter.h"
#include "third_party/skia/include/core/SkPath.h"
#include "third_party/skia/include/core/SkShader.h"

namespace viz {
namespace {

// 此边界直接检查真实像素和输出，不替换 Skia，也不以背景颜色推断 Alpha。
class MideoOpaquePixelsTest : public testing::Test {
 protected:
  sk_sp<SkImage> SourceImage() {
    return SkImages::RasterFromPixmap(
        SkPixmap(SourceInfo(), pixels_.data(), 12), nullptr, nullptr);
  }

  sk_sp<SkImage> TileView(const SkImage* image,
                        const SkPaint& paint = SkPaint(),
                        bool clip_is_bw = true) {
    return MakeOpaqueMideoTileImage(
        image, SourceInfo(), SkRect::MakeWH(3, 2),
        SkRect::MakeXYWH(1, 1, 3, 2), SkMatrix::Translate(1, 1),
        SkSamplingOptions(SkFilterMode::kNearest), paint, clip_is_bw);
  }

  std::array<uint8_t, 192> DrawImage(const SkImage* image,
                                   const SkPaint& paint, int clip,
                                   SkFilterMode filter = SkFilterMode::kNearest,
                                   int translation = 1) {
    SkBitmap bitmap;
    bitmap.allocPixels(SourceInfo().makeWH(8, 6));
    bitmap.eraseARGB(255, 29, 53, 97);
    SkCanvas canvas(bitmap);
    if (clip == 1) {
      canvas.clipRect(SkRect::MakeXYWH(2, 1, 3, 3),
                      SkClipOp::kIntersect, false);
    } else if (clip == 2) {
      const SkPoint points[] = {{1, 1}, {5, 1}, {2, 5}};
      canvas.clipPath(SkPath::Polygon(points, true), SkClipOp::kIntersect,
                      false);
    }
    canvas.setMatrix(SkMatrix::Translate(translation, translation));
    canvas.drawImageRect(image, SkRect::MakeWH(3, 2),
                         SkRect::MakeXYWH(1, 1, 3, 2),
                         SkSamplingOptions(filter), &paint,
                         SkCanvas::kStrict_SrcRectConstraint);
    std::array<uint8_t, 192> output{};
    EXPECT_TRUE(bitmap.readPixels(bitmap.info(), output.data(), 32, 0, 0));
    return output;
  }

  SkImageInfo SourceInfo() const {
    return SkImageInfo::Make(3, 2, kBGRA_8888_SkColorType,
                             kPremul_SkAlphaType, SkColorSpace::MakeSRGB());
  }

  SkImageInfo TargetInfo() const {
    return SourceInfo().makeAlphaType(kUnpremul_SkAlphaType);
  }

  alignas(4) std::array<uint8_t, 24> pixels_{
      1, 37, 253, 255, 0, 255, 17, 255, 255, 0, 123, 255,
      64, 128, 192, 255, 7, 11, 13, 255, 239, 241, 251, 255};
};

TEST_F(MideoOpaquePixelsTest, userDrawsVerifiedOpaqueTilePixelsExactly) {
  // Given 实际非均匀 Premul 像素和非空目标背景，包含局部非AA剪裁。
  const auto source = SourceImage();
  ASSERT_TRUE(source);
  const auto original = pixels_;
  for (const auto blend : {SkBlendMode::kSrc, SkBlendMode::kSrcOver}) {
    SkPaint paint;
    paint.setBlendMode(blend);
    // When 同次读取创建只读视图，绘制参数保持相同。
    const auto view = TileView(source.get(), paint);
    ASSERT_TRUE(view);
    for (int clip : {0, 1, 2}) {
      // Then 完整目标每字节相同，原 Premul 源与像素不变。
      EXPECT_EQ(DrawImage(source.get(), paint, clip),
                DrawImage(view.get(), paint, clip));
    }
    EXPECT_EQ(source->alphaType(), kPremul_SkAlphaType);
    EXPECT_EQ(pixels_, original);
  }
}

TEST_F(MideoOpaquePixelsTest, userDrawsLinearOpaqueTilePixelsExactly) {
  // Given 高频不均匀源和非空背景，原采样为 linear，完整整数映射。
  const auto source = SourceImage();
  ASSERT_TRUE(source);
  const auto original = pixels_;
  for (const auto blend : {SkBlendMode::kSrc, SkBlendMode::kSrcOver}) {
    SkPaint paint;
    paint.setBlendMode(blend);
    for (int translation : {-1, 0, 1}) {
      // When 在本次读访问内创建视图，保留 linear 和正负整数平移。
      const auto view = MakeOpaqueMideoTileImage(
          source.get(), SourceInfo(), SkRect::MakeWH(3, 2),
          SkRect::MakeXYWH(1, 1, 3, 2),
          SkMatrix::Translate(translation, translation),
          SkSamplingOptions(SkFilterMode::kLinear), paint, true);
      ASSERT_TRUE(view);
      for (int clip : {0, 1, 2}) {
        // Then 整幅目标含边缘、剪裁及未绘制区域逐字节一致。
        EXPECT_EQ(DrawImage(source.get(), paint, clip,
                            SkFilterMode::kLinear, translation),
                  DrawImage(view.get(), paint, clip,
                            SkFilterMode::kLinear, translation));
      }
      EXPECT_EQ(source->alphaType(), kPremul_SkAlphaType);
      EXPECT_EQ(pixels_, original);
    }
  }
}

TEST_F(MideoOpaquePixelsTest, userRechecksEveryTileAlphaOnEachRead) {
  // Given 同一存储地址，前次读访问全部不透明，结束后释放只读图像。
  {
    const auto source = SourceImage();
    ASSERT_TRUE(source);
    ASSERT_TRUE(TileView(source.get()));
  }
  for (size_t index = 3; index < pixels_.size(); index += 4) {
    // When 下一次读取实际像素变为合法 Premul 半透明或全透明。
    const auto original = pixels_;
    pixels_[index - 3] = pixels_[index - 2] = pixels_[index - 1] = 0;
    for (uint8_t alpha : {uint8_t{254}, uint8_t{0}}) {
      pixels_[index] = alpha;
      // Then 新读访问必须拒绝，不复用同地址的旧 Alpha 证明。
      const auto source = SourceImage();
      ASSERT_TRUE(source);
      EXPECT_FALSE(TileView(source.get()));
      EXPECT_EQ(source->alphaType(), kPremul_SkAlphaType);
    }
    pixels_ = original;
  }
}

TEST_F(MideoOpaquePixelsTest, userKeepsTileFallbackOutsideVerifiedDrawBounds) {
  const auto image = SourceImage();
  ASSERT_TRUE(image);
  const auto original = pixels_;
  const auto reject = [&](const SkRect& src, const SkRect& dst,
                          const SkMatrix& matrix,
                          const SkSamplingOptions& sampling,
                          const SkPaint& paint, bool clip_is_bw) {
    EXPECT_FALSE(MakeOpaqueMideoTileImage(
        image.get(), SourceInfo(), src, dst, matrix, sampling, paint,
        clip_is_bw));
    EXPECT_EQ(pixels_, original);
  };
  const auto src = SkRect::MakeWH(3, 2);
  const auto dst = SkRect::MakeXYWH(1, 1, 3, 2);
  const auto matrix = SkMatrix::Translate(1, 1);
  const SkSamplingOptions nearest(SkFilterMode::kNearest);
  SkPaint paint;
  reject(src, dst, matrix, nearest, paint, false);
  reject(SkRect::MakeWH(2, 2), dst, matrix, nearest, paint, true);
  reject(src, SkRect::MakeXYWH(1.5f, 1, 3, 2), matrix, nearest, paint, true);
  reject(src, SkRect::MakeWH(6, 4), matrix, nearest, paint, true);
  reject(src, dst, SkMatrix::Translate(0.5f, 0), nearest, paint, true);
  reject(src, dst, SkMatrix::Scale(2, 2), nearest, paint, true);
  reject(src, dst, matrix, SkSamplingOptions(SkFilterMode::kLinear), paint,
         true);
  reject(src, dst, matrix,
         SkSamplingOptions(SkFilterMode::kNearest, SkMipmapMode::kNearest),
         paint, true);
  reject(src, dst, matrix, SkSamplingOptions(SkCubicResampler::Mitchell()),
         paint, true);
  reject(src, dst, matrix, SkSamplingOptions::Aniso(2), paint, true);
  paint.setAntiAlias(true);
  reject(src, dst, matrix, nearest, paint, true);
  paint.reset();
  paint.setAlpha(254);
  reject(src, dst, matrix, nearest, paint, true);
  paint.reset();
  paint.setBlendMode(SkBlendMode::kMultiply);
  reject(src, dst, matrix, nearest, paint, true);
  paint.reset();
  paint.setShader(SkShaders::Color(SK_ColorRED));
  reject(src, dst, matrix, nearest, paint, true);
  paint.reset();
  paint.setColorFilter(SkColorFilters::Blend(SK_ColorRED, SkBlendMode::kSrc));
  reject(src, dst, matrix, nearest, paint, true);
}

TEST_F(MideoOpaquePixelsTest, userRejectsUnknownTilePixelContracts) {
  const auto original = pixels_;
  EXPECT_FALSE(TileView(nullptr));
  for (auto info : {SourceInfo().makeColorSpace(nullptr),
                    SourceInfo().makeColorSpace(SkColorSpace::MakeSRGBLinear()),
                    SourceInfo().makeColorType(kRGBA_8888_SkColorType),
                    SourceInfo().makeAlphaType(kUnpremul_SkAlphaType)}) {
    const auto image = SkImages::RasterFromPixmap(
        SkPixmap(info, pixels_.data(), 12), nullptr, nullptr);
    ASSERT_TRUE(image);
    EXPECT_FALSE(TileView(image.get()));
  }
  const auto source = SourceImage();
  ASSERT_TRUE(source);
  for (auto target : {SourceInfo().makeColorSpace(nullptr),
                      SourceInfo().makeColorType(kRGBA_8888_SkColorType),
                      SourceInfo().makeAlphaType(kUnpremul_SkAlphaType),
                      SourceInfo().makeWH(0, 2)}) {
    EXPECT_FALSE(MakeOpaqueMideoTileImage(
        source.get(), target, SkRect::MakeWH(3, 2), SkRect::MakeWH(3, 2),
        SkMatrix::I(), SkSamplingOptions(), SkPaint(), true));
  }
  alignas(4) std::array<uint8_t, 32> padded{};
  std::copy_n(pixels_.begin(), 12, padded.begin());
  std::copy_n(pixels_.begin() + 12, 12, padded.begin() + 16);
  const auto padded_image = SkImages::RasterFromPixmap(
      SkPixmap(SourceInfo(), padded.data(), 16), nullptr, nullptr);
  ASSERT_TRUE(padded_image);
  EXPECT_FALSE(TileView(padded_image.get()));
  EXPECT_EQ(pixels_, original);
}

TEST_F(MideoOpaquePixelsTest, userCopiesEveryOpaquePixelWithoutChangingGuards) {
  // Given 非均匀、全不透明的真实 BGRA/Premul/sRGB 像素。
  const SkPixmap source(SourceInfo(), pixels_.data(), 12);
  std::array<uint8_t, 32> output;
  output.fill(0xA5);
  // When 输出到有前后边界的独立区域。
  ASSERT_TRUE(CopyOpaqueMideoPixels(
      source, TargetInfo(), base::span(output).subspan(4u, 24u), 12));
  // Then 每个字节与原始像素一致，区域以外不改动。
  EXPECT_TRUE(std::equal(pixels_.begin(), pixels_.end(), output.begin() + 4));
  EXPECT_EQ(output[0], 0xA5);
  EXPECT_EQ(output[3], 0xA5);
  EXPECT_EQ(output[28], 0xA5);
  EXPECT_EQ(output[31], 0xA5);
  std::array<uint8_t, 24> generic;
  ASSERT_TRUE(source.readPixels(TargetInfo(), generic.data(), 12));
  EXPECT_EQ(generic, pixels_);
}

TEST_F(MideoOpaquePixelsTest, userKeepsFallbackForAnyNonopaquePixel) {
  // Given 任意位置只有一个 Alpha=254 的像素，包括末行末像素。
  for (size_t index = 3; index < pixels_.size(); index += 4) {
    pixels_[index] = 254;
    const SkPixmap source(SourceInfo(), pixels_.data(), 12);
    std::array<uint8_t, 24> output;
    output.fill(0xA5);
    // When 尝试无损直拷。
    EXPECT_FALSE(CopyOpaqueMideoPixels(source, TargetInfo(), output, 12));
    // Then 保留通用 Alpha 转换，拒绝时不能先写出部分帧。
    for (uint8_t byte : output) {
      EXPECT_EQ(byte, 0xA5);
    }
    pixels_[index] = 255;
  }
}

TEST_F(MideoOpaquePixelsTest, userKeepsFallbackForTransparentPixel) {
  pixels_[23] = 0;
  const SkPixmap source(SourceInfo(), pixels_.data(), 12);
  std::array<uint8_t, 24> output{};
  EXPECT_FALSE(CopyOpaqueMideoPixels(source, TargetInfo(), output, 12));
}

TEST_F(MideoOpaquePixelsTest, userRejectsUnknownOrMismatchedPixelContracts) {
  std::array<uint8_t, 24> output;
  output.fill(0xA5);
  const auto reject = [&](const SkPixmap& source, const SkImageInfo& target,
                          base::span<uint8_t> destination, size_t row_bytes) {
    EXPECT_FALSE(CopyOpaqueMideoPixels(source, target, destination, row_bytes));
    for (uint8_t byte : output) {
      EXPECT_EQ(byte, 0xA5);
    }
  };
  const SkPixmap source(SourceInfo(), pixels_.data(), 12);
  reject(SkPixmap(SourceInfo(), nullptr, 12), TargetInfo(), output, 12);
  reject(SkPixmap(SourceInfo().makeColorSpace(nullptr), pixels_.data(), 12),
         TargetInfo(), output, 12);
  reject(SkPixmap(SourceInfo().makeColorType(kRGBA_8888_SkColorType),
                  pixels_.data(), 12), TargetInfo(), output, 12);
  reject(SkPixmap(SourceInfo().makeAlphaType(kOpaque_SkAlphaType),
                  pixels_.data(), 12), TargetInfo(), output, 12);
  reject(source, TargetInfo().makeWH(2, 2), output, 12);
  reject(source, TargetInfo().makeAlphaType(kPremul_SkAlphaType), output, 12);
  reject(source, TargetInfo().makeColorSpace(nullptr), output, 12);
  reject(source, TargetInfo(), base::span(output).first(23u), 12);
  reject(source, TargetInfo(), output, 8);
  reject(SkPixmap(SourceInfo(), pixels_.data(), 16), TargetInfo(), output, 12);
}

// 使用真实 Surface 提交和软件绘制，宿主输出仅使用既有软件测试适配器。
class DamageDevice : public SoftwareOutputDevice {
 public:
  gfx::Rect damage() const { return damage_rect_; }
};

class DamageClient : public DisplayClient {
 public:
  void DisplayOutputSurfaceLost() override {}
  void DisplayWillDrawAndSwap(bool, AggregatedRenderPassList*) override {}
  void DisplayDidDrawAndSwap() override {}
  void DisplayDidReceiveCALayerParams(gfx::CALayerParams) override {}
  void DisplayDidCompleteSwapWithSize(const gfx::Size&) override {}
  void DisplayAddChildWindowToBrowser(gpu::SurfaceHandle) override {}
  void SetWideColorEnabled(bool) override {}
};

class MideoDisplayDamageTest : public testing::Test {
 protected:
  void SetUp() override {
    auto device = std::make_unique<DamageDevice>();
    device_ = device.get();
    RendererSettings settings;
    settings.partial_swap_enabled = true;
    settings.auto_resize_output_surface = true;
    display_ = std::make_unique<Display>(
        &images_, &gpu_scheduler_, settings, &debug_settings_, sink_, nullptr,
        std::make_unique<FakeSoftwareOutputSurface>(std::move(device)),
        std::make_unique<OverlayProcessorStub>(), nullptr, task_runner_);
    display_->SetVisible(true);
    display_->Initialize(&client_, manager_.surface_manager());
    allocator_.GenerateId();
    child_allocator_.GenerateId();
    display_->SetLocalSurfaceId(allocator_.GetCurrentLocalSurfaceId(), 1.f);
    display_->Resize(gfx::Size(100, 100));
    Submit(old_token_, gfx::Rect(100, 100));
    Draw();
    ASSERT_EQ(device_->damage(), gfx::Rect(100, 100));
    ASSERT_TRUE(directory_.CreateUniqueTempDir());
  }

  void Submit(const base::UnguessableToken& token, const gfx::Rect& damage) {
    auto frame = CompositorFrameBuilder()
                     .AddRenderPass(RenderPassBuilder(gfx::Size(100, 100))
                                        .AddSolidColorQuad(gfx::Rect(100, 100),
                                                           SkColors::kBlue)
                                        .SetDamageRect(damage))
                     .Build();
    frame.metadata.mideo_frame_token = token;
    support_.SubmitCompositorFrame(allocator_.GetCurrentLocalSurfaceId(),
                                   std::move(frame));
  }

  void Draw() {
    DrawAndSwapParams params;
    params.expected_display_time = base::TimeTicks::Now();
    display_->DrawAndSwap(params);
  }

  void Arm() {
    Arm(SurfaceId(sink_, allocator_.GetCurrentLocalSurfaceId()));
  }

  void Arm(const SurfaceId& target) {
    base::File file(directory_.GetPath().AppendASCII("frame"),
                    base::File::FLAG_CREATE_ALWAYS | base::File::FLAG_READ |
                        base::File::FLAG_WRITE);
    ASSERT_TRUE(file.IsValid());
    ASSERT_TRUE(file.SetLength(100 * 100 * 4));
    ASSERT_TRUE(display_->ArmMideoFrame(
        target, target_token_,
        std::move(file), base::UnguessableToken::Create(), 0, gfx::Size(100, 100),
        base::BindOnce([](bool) {})));
  }

  SurfaceId ChildId() const {
    return SurfaceId(child_sink_, child_allocator_.GetCurrentLocalSurfaceId());
  }

  void SubmitChild(const base::UnguessableToken& token,
                   const gfx::Rect& damage) {
    auto frame = CompositorFrameBuilder()
                     .AddRenderPass(RenderPassBuilder(gfx::Size(100, 100))
                                        .AddSolidColorQuad(gfx::Rect(100, 100),
                                                           SkColors::kBlue)
                                        .SetDamageRect(damage))
                     .Build();
    frame.metadata.mideo_frame_token = token;
    child_support_.SubmitCompositorFrame(
        child_allocator_.GetCurrentLocalSurfaceId(), std::move(frame));
  }

  void SubmitEmbedded(const std::vector<SurfaceRange>& ranges,
                      const gfx::Rect& damage) {
    RenderPassBuilder pass(gfx::Size(100, 100));
    for (const auto& range : ranges) {
      pass.AddSurfaceQuad(gfx::Rect(100, 100), range);
    }
    pass.SetDamageRect(damage);
    support_.SubmitCompositorFrame(
        allocator_.GetCurrentLocalSurfaceId(),
        CompositorFrameBuilder().AddRenderPass(pass).Build());
  }

  void PrepareEmbedded() {
    SubmitChild(old_token_, gfx::Rect(100, 100));
    SubmitEmbedded({SurfaceRange(ChildId())}, gfx::Rect(100, 100));
    Draw();
    ASSERT_EQ(device_->damage(), gfx::Rect(100, 100));
  }

  const FrameSinkId sink_{61, 1};
  FrameSinkManagerImpl manager_{FrameSinkManagerImpl::InitParams()};
  CompositorFrameSinkSupport support_{nullptr, &manager_, sink_, true};
  const FrameSinkId child_sink_{61, 2};
  CompositorFrameSinkSupport child_support_{nullptr, &manager_, child_sink_,
                                          false};
  gpu::SharedImageManager images_;
  gpu::SyncPointManager sync_;
  gpu::Scheduler gpu_scheduler_{&sync_};
  scoped_refptr<base::NullTaskRunner> task_runner_ =
      base::MakeRefCounted<base::NullTaskRunner>();
  DebugRendererSettings debug_settings_;
  DamageClient client_;
  ParentLocalSurfaceIdAllocator allocator_;
  ParentLocalSurfaceIdAllocator child_allocator_;
  base::ScopedTempDir directory_;
  const base::UnguessableToken old_token_ = base::UnguessableToken::Create();
  const base::UnguessableToken target_token_ = base::UnguessableToken::Create();
  raw_ptr<DamageDevice> device_ = nullptr;
  std::unique_ptr<Display> display_;
};

TEST_F(MideoDisplayDamageTest, UnarrivedTokenKeepsNormalDamage) {
  // Given 已有完整旧帧，目标 token 尚未提交。
  Arm();
  // When 正常旧帧只提交局部损伤。
  const gfx::Rect partial(10, 10, 1, 1);
  Submit(old_token_, partial);
  Draw();
  // Then 不把不可交付的旧帧扩大为全帧重绘。
  EXPECT_EQ(device_->damage(), partial);
}

TEST_F(MideoDisplayDamageTest, ArrivedTokenStillReconstructsFullFrame) {
  Arm();
  Submit(target_token_, gfx::Rect(10, 10, 1, 1));
  Draw();
  // 目标匹配时仍必须完整重建软件画布，不能裁掉未损伤区域。
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
  EXPECT_EQ(device_->ReadbackForTesting().getColor(99, 99), SK_ColorBLUE);
}

TEST_F(MideoDisplayDamageTest, OrdinaryFrameKeepsNormalDamage) {
  const gfx::Rect partial(10, 10, 1, 1);
  Submit(old_token_, partial);
  Draw();
  EXPECT_EQ(device_->damage(), partial);
}

TEST_F(MideoDisplayDamageTest, EmbeddedOldTokenKeepsNormalDamage) {
  PrepareEmbedded();
  Arm(ChildId());
  const gfx::Rect partial(10, 10, 1, 1);
  SubmitChild(old_token_, partial);
  SubmitEmbedded({SurfaceRange(ChildId())}, partial);
  Draw();
  EXPECT_EQ(device_->damage(), partial);
}

TEST_F(MideoDisplayDamageTest, EmbeddedArrivedTokenReconstructsFullFrame) {
  PrepareEmbedded();
  Arm(ChildId());
  SubmitChild(target_token_, gfx::Rect(10, 10, 1, 1));
  SubmitEmbedded({SurfaceRange(ChildId())}, gfx::Rect(10, 10, 1, 1));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
  EXPECT_EQ(device_->ReadbackForTesting().getColor(99, 99), SK_ColorBLUE);
}

TEST_F(MideoDisplayDamageTest, UnknownTokenKeepsFullDamage) {
  Arm();
  Submit(base::UnguessableToken(), gfx::Rect(10, 10, 1, 1));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

TEST_F(MideoDisplayDamageTest, MissingTargetKeepsFullDamage) {
  Arm(ChildId());
  Submit(old_token_, gfx::Rect(10, 10, 1, 1));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

TEST_F(MideoDisplayDamageTest, MissingTokenFieldKeepsFullDamage) {
  Arm();
  auto frame = CompositorFrameBuilder()
                   .AddRenderPass(RenderPassBuilder(gfx::Size(100, 100))
                                      .AddSolidColorQuad(gfx::Rect(100, 100),
                                                         SkColors::kBlue)
                                      .SetDamageRect(gfx::Rect(10, 10, 1, 1)))
                   .Build();
  ASSERT_FALSE(frame.metadata.mideo_frame_token.has_value());
  support_.SubmitCompositorFrame(allocator_.GetCurrentLocalSurfaceId(),
                                 std::move(frame));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

TEST_F(MideoDisplayDamageTest, NewRootIdKeepsFullDamage) {
  Arm();
  allocator_.GenerateId();
  display_->SetLocalSurfaceId(allocator_.GetCurrentLocalSurfaceId(), 1.f);
  Submit(old_token_, gfx::Rect(10, 10, 1, 1));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

TEST_F(MideoDisplayDamageTest, NewEmbeddedIdKeepsFullDamage) {
  PrepareEmbedded();
  Arm(ChildId());
  child_allocator_.GenerateId();
  SubmitChild(old_token_, gfx::Rect(10, 10, 1, 1));
  SubmitEmbedded({SurfaceRange(ChildId())}, gfx::Rect(10, 10, 1, 1));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

TEST_F(MideoDisplayDamageTest, RangeFallbackKeepsFullDamage) {
  PrepareEmbedded();
  const SurfaceId old_id = ChildId();
  ParentLocalSurfaceIdAllocator new_embed;
  new_embed.GenerateId();
  const SurfaceId missing(child_sink_, new_embed.GetCurrentLocalSurfaceId());
  const SurfaceRange fallback(old_id, missing);
  SubmitEmbedded({fallback}, gfx::Rect(100, 100));
  Draw();
  Arm(old_id);
  SubmitChild(old_token_, gfx::Rect(10, 10, 1, 1));
  SubmitEmbedded({fallback}, gfx::Rect(10, 10, 1, 1));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

TEST_F(MideoDisplayDamageTest, MissingEmbeddedSurfaceKeepsFullDamage) {
  PrepareEmbedded();
  Arm(ChildId());
  ParentLocalSurfaceIdAllocator missing_allocator;
  missing_allocator.GenerateId();
  const SurfaceId missing(FrameSinkId(61, 3),
                          missing_allocator.GetCurrentLocalSurfaceId());
  SubmitEmbedded({SurfaceRange(ChildId()), SurfaceRange(missing)},
                 gfx::Rect(10, 10, 1, 1));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

TEST_F(MideoDisplayDamageTest, ChangedTopologyKeepsFullDamage) {
  PrepareEmbedded();
  Arm(ChildId());
  Submit(old_token_, gfx::Rect(10, 10, 1, 1));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

TEST_F(MideoDisplayDamageTest, MultipleTargetSurfacesKeepFullDamage) {
  PrepareEmbedded();
  const SurfaceId older = ChildId();
  child_allocator_.GenerateId();
  SubmitChild(old_token_, gfx::Rect(100, 100));
  const std::vector<SurfaceRange> ranges{SurfaceRange(older),
                                        SurfaceRange(ChildId())};
  SubmitEmbedded(ranges, gfx::Rect(100, 100));
  Draw();
  Arm(older);
  SubmitEmbedded(ranges, gfx::Rect(10, 10, 1, 1));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

TEST_F(MideoDisplayDamageTest, ResizeKeepsFullDamage) {
  Arm();
  display_->Resize(gfx::Size(101, 101));
  Submit(old_token_, gfx::Rect(10, 10, 1, 1));
  Draw();
  // Display 的输出尺寸变化时，不能依赖旧快照裁剪重建。
  EXPECT_EQ(device_->damage(), gfx::Rect(101, 101));
}

TEST_F(MideoDisplayDamageTest, QuadBudgetKeepsFullDamage) {
  Arm();
  RenderPassBuilder pass(gfx::Size(100, 100));
  for (size_t i = 0; i < 8193; ++i) {
    pass.AddSolidColorQuad(gfx::Rect(100, 100), SkColors::kBlue);
  }
  pass.SetDamageRect(gfx::Rect(10, 10, 1, 1));
  auto frame = CompositorFrameBuilder().AddRenderPass(pass).Build();
  frame.metadata.mideo_frame_token = old_token_;
  support_.SubmitCompositorFrame(allocator_.GetCurrentLocalSurfaceId(),
                                 std::move(frame));
  Draw();
  EXPECT_EQ(device_->damage(), gfx::Rect(100, 100));
}

}  // namespace
}  // namespace viz
