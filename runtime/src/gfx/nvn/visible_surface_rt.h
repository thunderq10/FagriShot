// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "extras/taau.h"
#include "nxrt/gfx.h"

namespace nxrt::nvn::be::gpu {
struct Frame;
}
namespace nxrt::nvn::be {
// One optional ray-query scene per DLSS network. Its resources retire with that
// network's GPU work.
class VisibleSurfaceRt {
public:
  bool record(gpu::Frame &frame, const extras::TaauFrame &inputs,
              const gfx::Image &output);
  void destroy();

private:
  struct Accel {
    VkAccelerationStructureKHR handle = VK_NULL_HANDLE;
    gfx::Buffer storage;
    VkAccelerationStructureBuildSizesInfoKHR sizes{
        VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_SIZES_INFO_KHR};
  };
  bool init();
  bool make_accel(Accel &accel,
                  VkAccelerationStructureBuildGeometryInfoKHR &build,
                  uint32_t primitives);
  VkAccelerationStructureGeometryKHR geometry(bool top) const;
  void build(VkCommandBuffer cb, bool top);
  bool tried_ = false, ready_ = false;
  uint32_t frames_ = 0;
  VkDeviceAddress scratch_addr_ = 0;
  Accel bottom_, top_;
  gfx::Buffer vertices_, instance_, scratch_, counts_;
  VkDescriptorSetLayout set_ = VK_NULL_HANDLE;
  VkPipelineLayout layout_ = VK_NULL_HANDLE;
  VkPipeline mesh_ = VK_NULL_HANDLE, reflect_ = VK_NULL_HANDLE;
  VkSampler sampler_ = VK_NULL_HANDLE;
};
} // namespace nxrt::nvn::be
