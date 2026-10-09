// Copyright 2021 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/*
 * [INPUT]: 真实软件 SharedImage、资源跨父子传输与 ScopedReadLockSkImage。
 * [OUTPUT]: 软件读像素及 nearest/linear 临时 opaque 视图的真实租约与归还验收。
 * [POS]: DisplayResourceProviderSoftware 契约测试，验证实际 GPU 服务资源生命周期。
 * [PROTOCOL]: 变更时更新此头部，然后检查 AGENTS.md。
 */

#include "components/viz/service/display/display_resource_provider_software.h"

#include <stddef.h>
#include <stdint.h>

#include <memory>
#include <set>
#include <unordered_map>
#include <utility>
#include <vector>

#include "base/check.h"
#include "base/functional/bind.h"
#include "base/functional/callback_helpers.h"
#include "base/memory/read_only_shared_memory_region.h"
#include "base/memory/ref_counted.h"
#include "base/memory/shared_memory_mapping.h"
#include "build/build_config.h"
#include "components/viz/client/client_resource_provider.h"
#include "components/viz/common/resources/release_callback.h"
#include "components/viz/common/resources/returned_resource.h"
#include "components/viz/service/display/mideo_opaque_pixels.h"
#include "components/viz/service/gl/gpu_service_impl.h"
#include "components/viz/test/test_in_process_context_provider.h"
#include "gpu/command_buffer/client/client_shared_image.h"
#include "gpu/command_buffer/client/shared_image_interface.h"
#include "gpu/command_buffer/service/scheduler.h"
#include "gpu/command_buffer/service/shared_image/shared_image_manager.h"
#include "testing/gmock/include/gmock/gmock.h"
#include "testing/gtest/include/gtest/gtest.h"
#include "third_party/skia/include/core/SkCanvas.h"
#include "ui/gfx/geometry/rect.h"

using testing::_;
using testing::ByMove;
using testing::DoAll;
using testing::Return;
using testing::SaveArg;

namespace viz {
namespace {

class MockReleaseCallback {
 public:
  MOCK_METHOD3(Released,
               void(scoped_refptr<gpu::ClientSharedImage> shared_image,
                    const gpu::SyncToken& token,
                    bool lost));
};

static void CollectResources(std::vector<ReturnedResource>* array,
                             std::vector<ReturnedResourceViz> returned) {
  for (auto& resource_viz : returned) {
    ReturnedResource resource{
        resource_viz.id,
        gpu::SharedImageExportResult::CreateForTesting(resource_viz.sync_token),
        std::move(resource_viz.release_fence), resource_viz.count,
        resource_viz.lost};
    array->emplace_back(std::move(resource));
  }
}

class DisplayResourceProviderSoftwareTest : public testing::Test {
 public:
  DisplayResourceProviderSoftwareTest() = default;

  ~DisplayResourceProviderSoftwareTest() override {
    child_resource_provider_->ShutdownAndReleaseAllResources();
  }

  void InitializeProvider() {
    auto context_provider = base::MakeRefCounted<TestInProcessContextProvider>(
        TestContextType::kRaster, /*support_locking=*/false);
    gpu::ContextResult result = context_provider->BindToCurrentSequence();
    CHECK_EQ(result, gpu::ContextResult::kSuccess);
    auto* gpu_service = context_provider->GpuService();
    child_context_provider_ = std::move(context_provider);

    resource_provider_ = std::make_unique<DisplayResourceProviderSoftware>(
        gpu_service->shared_image_manager(), gpu_service->gpu_scheduler());

    child_resource_provider_ = std::make_unique<ClientResourceProvider>();
  }

  ResourceId AllocateAndFillSoftwareResource(
      MockReleaseCallback& release_callback,
      const gfx::Size& size,
      const uint32_t value) {
    auto* shared_image_interface =
        child_context_provider_->SharedImageInterface();
    auto shared_image =
        shared_image_interface->CreateSharedImageForSoftwareCompositor(
            {SinglePlaneFormat::kBGRA_8888, size, gfx::ColorSpace(),
             gpu::SHARED_IMAGE_USAGE_CPU_WRITE_ONLY,
             "DisplayResourceProviderSoftwareTest"});
    auto mapping = shared_image->Map();

    uint32_t* ptr =
        reinterpret_cast<uint32_t*>(mapping->GetMemoryForPlane(0).data());
    base::span<uint32_t> span =
        UNSAFE_BUFFERS(base::span(ptr, static_cast<uint32_t>(size.GetArea())));

    std::fill(span.begin(), span.end(), value);

    auto transferable_resource = TransferableResource::Make(
        shared_image, TransferableResource::ResourceSource::kTileRasterTask,
        shared_image_interface->GenVerifiedSyncToken());

    auto callback = base::BindOnce(&MockReleaseCallback::Released,
                                   base::Unretained(&release_callback),
                                   std::move(shared_image));

    return child_resource_provider_->ImportResource(
        std::move(transferable_resource), std::move(callback));
  }

 protected:
  scoped_refptr<RasterContextProvider> child_context_provider_;
  std::unique_ptr<DisplayResourceProviderSoftware> resource_provider_;
  std::unique_ptr<ClientResourceProvider> child_resource_provider_;
};

TEST_F(DisplayResourceProviderSoftwareTest, ReadSoftwareResources) {
  InitializeProvider();

  gfx::Size size(64, 64);
  const uint32_t kBadBeef = 0xbadbeef;

  MockReleaseCallback release_callback;
  ResourceId resource_id =
      AllocateAndFillSoftwareResource(release_callback, size, kBadBeef);

  // Transfer resources to the parent.
  std::vector<TransferableResource> send_to_parent;
  std::vector<ReturnedResource> returned_to_child;
  int child_id = resource_provider_->CreateChild(
      base::BindRepeating(&CollectResources, &returned_to_child), SurfaceId());

  CHECK(child_context_provider_);
  child_resource_provider_->PrepareSendToParent(
      {resource_id}, &send_to_parent,
      child_context_provider_->SharedImageInterface());
  resource_provider_->ReceiveFromChild(child_id, send_to_parent);

  // In DisplayResourceProvider's namespace, use the mapped resource id.
  std::unordered_map<ResourceId, ResourceId, ResourceIdHasher> resource_map =
      resource_provider_->GetChildToParentMap(child_id);
  ResourceId mapped_resource_id = resource_map[resource_id];
  {
    SkBitmap dstBitmap;
    dstBitmap.allocPixels(SkImageInfo::Make(size.width(), size.height(),
                                            kBGRA_8888_SkColorType,
                                            kPremul_SkAlphaType));

    DisplayResourceProviderSoftware::ScopedReadLockSkImage lock(
        resource_provider_.get(), mapped_resource_id);
    const SkImage* sk_image = lock.sk_image();
    bool result = sk_image->readPixels(nullptr, dstBitmap.pixmap(),
                                       /*srcX=*/0, /*srcY=*/0);

    EXPECT_TRUE(result);
    EXPECT_EQ(sk_image->width(), size.width());
    EXPECT_EQ(sk_image->height(), size.height());
    EXPECT_EQ(*dstBitmap.getAddr32(16, 16), kBadBeef);
  }

  EXPECT_EQ(0u, returned_to_child.size());
  // Transfer resources back from the parent to the child. Set no resources as
  // being in use.
  resource_provider_->DeclareUsedResourcesFromChild(child_id, ResourceIdSet());
  EXPECT_EQ(1u, returned_to_child.size());
  child_resource_provider_->ReceiveReturnsFromParent(
      std::move(returned_to_child));

  EXPECT_CALL(release_callback, Released(_, _, false));
  child_resource_provider_->RemoveImportedResource(resource_id);
}

TEST_F(DisplayResourceProviderSoftwareTest,
       userKeepsRealTileLeaseUntilSynchronousDrawEnds) {
  InitializeProvider();
  auto* interface = child_context_provider_->SharedImageInterface();
  std::vector<ReturnedResource> returned;
  const int child = resource_provider_->CreateChild(
      base::BindRepeating(&CollectResources, &returned), SurfaceId());
  auto released = std::make_shared<int>(0);
  for (SkFilterMode filter : {SkFilterMode::kNearest, SkFilterMode::kLinear}) {
    for (uint32_t value : {0xff112233u, 0u}) {
      // Given 真实软件 SharedImage；第二次资源是全透明像素，不能复用旧证明。
      const gfx::Size size(3, 2);
      auto shared_image = interface->CreateSharedImageForSoftwareCompositor(
          {SinglePlaneFormat::kBGRA_8888, size, gfx::ColorSpace::CreateSRGB(),
           gpu::SHARED_IMAGE_USAGE_CPU_WRITE_ONLY, "MideoTileLeaseTest"});
      {
        auto mapping = shared_image->Map();
        ASSERT_TRUE(mapping);
        SkPixmap pixels(SkImageInfo::Make(3, 2, kBGRA_8888_SkColorType,
                                        kPremul_SkAlphaType,
                                        SkColorSpace::MakeSRGB()),
                        mapping->GetMemoryForPlane(0).data(), 12);
        ASSERT_TRUE(pixels.erase(value));
      }
      const auto resource = child_resource_provider_->ImportResource(
          TransferableResource::Make(
              shared_image, TransferableResource::ResourceSource::kTileRasterTask,
              interface->GenVerifiedSyncToken()),
          base::BindOnce(
              [](std::shared_ptr<int> count, const gpu::SyncToken&, bool lost) {
                EXPECT_FALSE(lost);
                ++*count;
              },
              released));
      std::vector<TransferableResource> sent;
      child_resource_provider_->PrepareSendToParent({resource}, &sent, interface);
      resource_provider_->ReceiveFromChild(child, sent);
      const auto parent_resource =
          resource_provider_->GetChildToParentMap(child).at(resource);
      const int released_before = *released;
      {
        DisplayResourceProviderSoftware::ScopedReadLockSkImage lock(
            resource_provider_.get(), parent_resource);
        ASSERT_TRUE(lock.valid());
        SkBitmap target;
        target.allocPixels(SkImageInfo::Make(
            3, 2, kBGRA_8888_SkColorType, kPremul_SkAlphaType,
            SkColorSpace::MakeSRGB()));
        target.eraseColor(SK_ColorTRANSPARENT);
        SkPaint paint;
        const SkSamplingOptions sampling(filter);
        const auto rect = SkRect::MakeWH(3, 2);
        const auto view = MakeOpaqueMideoTileImage(
            lock.sk_image(), target.info(), rect, rect, SkMatrix::I(), sampling,
            paint, true);
        EXPECT_EQ(bool(view), value != 0);
        EXPECT_EQ(lock.sk_image()->alphaType(), kPremul_SkAlphaType);
        // When 消费者不再使用资源，实际读锁内归还仍必须延后。
        resource_provider_->DeclareUsedResourcesFromChild(child, ResourceIdSet());
        EXPECT_TRUE(returned.empty());
        EXPECT_EQ(*released, released_before);
        SkCanvas canvas(target);
        canvas.drawImageRect(view ? view.get() : lock.sk_image(), rect, rect,
                             sampling, &paint, SkCanvas::kStrict_SrcRectConstraint);
        // Then 同步绘制完整像素正确，视图在读锁之前销毁。
        for (int y = 0; y < 2; ++y) {
          for (int x = 0; x < 3; ++x) {
            EXPECT_EQ(*target.getAddr32(x, y), value);
          }
        }
      }
      ASSERT_EQ(returned.size(), 1u);
      child_resource_provider_->ReceiveReturnsFromParent(std::move(returned));
      returned.clear();
      child_resource_provider_->RemoveImportedResource(resource);
      EXPECT_EQ(*released, released_before + 1);
    }
  }
}

}  // namespace
}  // namespace viz
