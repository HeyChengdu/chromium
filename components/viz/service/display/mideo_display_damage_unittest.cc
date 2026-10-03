// Copyright 2026 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "components/viz/service/display/display.h"

#include "base/files/file.h"
#include "base/files/scoped_temp_dir.h"
#include "base/functional/bind.h"
#include "base/test/null_task_runner.h"
#include "base/test/task_environment.h"
#include "components/viz/common/display/renderer_settings.h"
#include "components/viz/common/quads/compositor_frame.h"
#include "components/viz/common/surfaces/parent_local_surface_id_allocator.h"
#include "components/viz/service/display/display_client.h"
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

namespace viz {
namespace {

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

  base::test::TaskEnvironment environment_;
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
