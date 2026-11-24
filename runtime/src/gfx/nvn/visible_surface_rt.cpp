// SPDX-License-Identifier: GPL-3.0-or-later
#include "visible_surface_rt.h"
#include "nvn_gpu_internal.h"
#include "nxrt/log.h"
#include <algorithm>
#include <array>
#include <cstring>

namespace nxrt::nvn::be {
namespace {
#include "extras/visible_surface_shaders.inc"
using gpu::device;
constexpr uint32_t kColumns = 96, kRows = 54, kTriangles = 2 * kColumns * kRows;
constexpr auto kAddress = VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
constexpr auto kBuildInput =
    VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR;
constexpr auto kBuildStage =
    VK_PIPELINE_STAGE_ACCELERATION_STRUCTURE_BUILD_BIT_KHR;
constexpr auto kBuildRead = VK_ACCESS_ACCELERATION_STRUCTURE_READ_BIT_KHR;
constexpr auto kBuildWrite = VK_ACCESS_ACCELERATION_STRUCTURE_WRITE_BIT_KHR;

struct Params {
  int size[2], color_base[2];
  float depth_base[2], depth_ratio[2];
  int reversed;
};
static_assert(sizeof(Params) == 36);
VkDeviceAddress address(const gfx::Buffer &b) {
  VkBufferDeviceAddressInfo info{VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO};
  info.buffer = b.buffer;
  return vkGetBufferDeviceAddress(device().device(), &info);
}
void sync(VkCommandBuffer cb, VkPipelineStageFlags from, VkAccessFlags writes,
          VkPipelineStageFlags to, VkAccessFlags reads) {
  VkMemoryBarrier barrier{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
  barrier.srcAccessMask = writes;
  barrier.dstAccessMask = reads;
  vkCmdPipelineBarrier(cb, from, to, 0, 1, &barrier, 0, nullptr, 0, nullptr);
}
bool pipeline(const uint32_t *code, size_t bytes, VkPipelineLayout layout,
              VkPipeline *result) {
  const VkDevice dev = device().device();
  VkShaderModuleCreateInfo sci{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
  sci.codeSize = bytes;
  sci.pCode = code;
  VkShaderModule module{};
  if (vkCreateShaderModule(dev, &sci, nullptr, &module) != VK_SUCCESS)
    return false;
  VkComputePipelineCreateInfo ci{
      VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
  ci.layout = layout;
  ci.stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO};
  ci.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
  ci.stage.module = module;
  ci.stage.pName = "main";
  const VkResult r =
      vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &ci, nullptr, result);
  vkDestroyShaderModule(dev, module, nullptr);
  return r == VK_SUCCESS;
}
} // namespace

VkAccelerationStructureGeometryKHR VisibleSurfaceRt::geometry(bool top) const {
  VkAccelerationStructureGeometryKHR g{
      VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR};
  g.flags = VK_GEOMETRY_OPAQUE_BIT_KHR;
  if (top) {
    g.geometryType = VK_GEOMETRY_TYPE_INSTANCES_KHR;
    g.geometry.instances = {
        VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_INSTANCES_DATA_KHR};
    g.geometry.instances.data.deviceAddress = address(instance_);
  } else {
    g.geometryType = VK_GEOMETRY_TYPE_TRIANGLES_KHR;
    auto &triangles = g.geometry.triangles;
    triangles = {
        VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_TRIANGLES_DATA_KHR};
    triangles.vertexFormat = VK_FORMAT_R32G32B32_SFLOAT;
    triangles.vertexStride = 16;
    triangles.maxVertex = kTriangles * 3 - 1;
    triangles.indexType = VK_INDEX_TYPE_NONE_KHR;
    triangles.vertexData.deviceAddress = address(vertices_);
  }
  return g;
}
bool VisibleSurfaceRt::make_accel(
    Accel &a, VkAccelerationStructureBuildGeometryInfoKHR &b,
    uint32_t primitives) {
  vkGetAccelerationStructureBuildSizesKHR(
      device().device(), VK_ACCELERATION_STRUCTURE_BUILD_TYPE_DEVICE_KHR, &b,
      &primitives, &a.sizes);
  if (device().create_buffer(
          a.sizes.accelerationStructureSize,
          VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_STORAGE_BIT_KHR | kAddress,
          gfx::MemoryUse::GpuOnly, &a.storage,
          "visible scene acceleration structure") != VK_SUCCESS)
    return false;
  VkAccelerationStructureCreateInfoKHR ci{
      VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_CREATE_INFO_KHR};
  ci.buffer = a.storage.buffer;
  ci.size = a.sizes.accelerationStructureSize;
  ci.type = b.type;
  return vkCreateAccelerationStructureKHR(device().device(), &ci, nullptr,
                                          &a.handle) == VK_SUCCESS;
}
void VisibleSurfaceRt::build(VkCommandBuffer cb, bool top) {
  const auto geom = geometry(top);
  VkAccelerationStructureBuildGeometryInfoKHR info{
      VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_GEOMETRY_INFO_KHR};
  info.type = top ? VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR
                  : VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR;
  info.flags = VK_BUILD_ACCELERATION_STRUCTURE_PREFER_FAST_BUILD_BIT_KHR;
  info.mode = VK_BUILD_ACCELERATION_STRUCTURE_MODE_BUILD_KHR;
  info.dstAccelerationStructure = top ? top_.handle : bottom_.handle;
  info.geometryCount = 1;
  info.pGeometries = &geom;
  info.scratchData.deviceAddress = scratch_addr_;
  VkAccelerationStructureBuildRangeInfoKHR range{top ? 1u : kTriangles, 0, 0,
                                                 0};
  const auto *ranges = &range;
  vkCmdBuildAccelerationStructuresKHR(cb, 1, &info, &ranges);
}
bool VisibleSurfaceRt::init() {
  const VkDevice dev = device().device();
  auto buffer = [](gfx::Buffer &target, VkDeviceSize size,
                   VkBufferUsageFlags usage, gfx::MemoryUse memory) {
    return device().create_buffer(size, usage, memory, &target,
                                  "visible scene rays") == VK_SUCCESS;
  };
  if (!buffer(vertices_, kTriangles * 3 * 16,
              VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | kBuildInput | kAddress,
              gfx::MemoryUse::GpuOnly) ||
      !buffer(instance_, sizeof(VkAccelerationStructureInstanceKHR),
              kBuildInput | kAddress, gfx::MemoryUse::Upload) ||
      !buffer(counts_, 16,
              VK_BUFFER_USAGE_STORAGE_BUFFER_BIT |
                  VK_BUFFER_USAGE_TRANSFER_SRC_BIT |
                  VK_BUFFER_USAGE_TRANSFER_DST_BIT,
              gfx::MemoryUse::GpuOnly))
    return false;
  for (bool top : {false, true}) {
    const auto g = geometry(top);
    VkAccelerationStructureBuildGeometryInfoKHR b{
        VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_GEOMETRY_INFO_KHR};
    b.type = top ? VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR
                 : VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR;
    b.flags = VK_BUILD_ACCELERATION_STRUCTURE_PREFER_FAST_BUILD_BIT_KHR;
    b.geometryCount = 1;
    b.pGeometries = &g;
    if (!make_accel(top ? top_ : bottom_, b, top ? 1u : kTriangles))
      return false;
  }
  VkPhysicalDeviceAccelerationStructurePropertiesKHR props{
      VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_PROPERTIES_KHR};
  VkPhysicalDeviceProperties2 p{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
                                &props};
  vkGetPhysicalDeviceProperties2(device().physical(), &p);
  const VkDeviceSize align =
      props.minAccelerationStructureScratchOffsetAlignment;
  if (!buffer(scratch_,
              std::max(bottom_.sizes.buildScratchSize,
                       top_.sizes.buildScratchSize) +
                  align,
              VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | kAddress,
              gfx::MemoryUse::GpuOnly))
    return false;
  scratch_addr_ = (address(scratch_) + align - 1) & ~(align - 1);
  VkAccelerationStructureDeviceAddressInfoKHR addr{
      VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_DEVICE_ADDRESS_INFO_KHR};
  addr.accelerationStructure = bottom_.handle;
  VkAccelerationStructureInstanceKHR inst{};
  for (int i = 0; i < 3; ++i)
    inst.transform.matrix[i][i] = 1.0f;
  inst.mask = 0xff;
  inst.flags = VK_GEOMETRY_INSTANCE_TRIANGLE_FACING_CULL_DISABLE_BIT_KHR;
  inst.accelerationStructureReference =
      vkGetAccelerationStructureDeviceAddressKHR(dev, &addr);
  std::memcpy(instance_.mapped, &inst, sizeof inst);
  device().flush_buffer(instance_);
  VkSamplerCreateInfo sci{VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
  sci.magFilter = sci.minFilter = VK_FILTER_NEAREST;
  sci.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
  sci.addressModeU = sci.addressModeV = sci.addressModeW =
      VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
  if (vkCreateSampler(dev, &sci, nullptr, &sampler_) != VK_SUCCESS)
    return false;
  const VkDescriptorType types[] = {
      VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,
      VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
      VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR,
      VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,
      VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
      VK_DESCRIPTOR_TYPE_STORAGE_BUFFER};
  std::array<VkDescriptorSetLayoutBinding, 6> bindings{};
  for (uint32_t i = 0; i < bindings.size(); ++i)
    bindings[i] = {i, types[i], 1, VK_SHADER_STAGE_COMPUTE_BIT, nullptr};
  VkDescriptorSetLayoutCreateInfo dci{
      VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
  dci.flags = VK_DESCRIPTOR_SET_LAYOUT_CREATE_PUSH_DESCRIPTOR_BIT_KHR;
  dci.bindingCount = static_cast<uint32_t>(bindings.size());
  dci.pBindings = bindings.data();
  if (vkCreateDescriptorSetLayout(dev, &dci, nullptr, &set_) != VK_SUCCESS)
    return false;
  VkPushConstantRange range{VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(Params)};
  VkPipelineLayoutCreateInfo lci{VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
  lci.setLayoutCount = 1;
  lci.pSetLayouts = &set_;
  lci.pushConstantRangeCount = 1;
  lci.pPushConstantRanges = &range;
  return vkCreatePipelineLayout(dev, &lci, nullptr, &layout_) == VK_SUCCESS &&
         pipeline(kMesh, sizeof(kMesh), layout_, &mesh_) &&
         pipeline(kReflect, sizeof(kReflect), layout_, &reflect_);
}

bool VisibleSurfaceRt::record(gpu::Frame &f, const extras::TaauFrame &t,
                              const gfx::Image &output) {
  if (!tried_) {
    tried_ = true;
    ready_ = init();
    if (!ready_) {
      destroy();
      NXLOG_W("nvn.rt", "Visible-surface ray-query pass could not initialize; "
                        "preserving the game's input.");
    }
  }
  if (!ready_)
    return false;
  const VkCommandBuffer cb = f.cb;
  Params pc{{int(t.in_size[0]), int(t.in_size[1])},
            {int(t.in_base[0]), int(t.in_base[1])},
            {t.depth_base[0], t.depth_base[1]},
            {t.depth_ratio[0], t.depth_ratio[1]},
            t.depth_inverted ? 1 : 0};
  // The previous composite, upscaler, or AS build can still be reading
  // resources from an earlier submission.
  sync(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
       VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT,
       VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
       VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT);
  vkCmdFillBuffer(cb, counts_.buffer, 0, 16, 0);
  sync(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,
       VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
       VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_SHADER_READ_BIT);
  VkDescriptorImageInfo depth{sampler_, t.depth, t.depth_layout},
      color{sampler_, t.color, t.color_layout},
      dest{VK_NULL_HANDLE, output.view, VK_IMAGE_LAYOUT_GENERAL};
  VkDescriptorBufferInfo vertices{vertices_.buffer, 0, VK_WHOLE_SIZE},
      stats{counts_.buffer, 0, 16};
  VkWriteDescriptorSetAccelerationStructureKHR as{
      VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET_ACCELERATION_STRUCTURE_KHR};
  as.accelerationStructureCount = 1;
  as.pAccelerationStructures = &top_.handle;
  std::array<VkWriteDescriptorSet, 6> w{};
  for (uint32_t i = 0; i < w.size(); ++i) {
    w[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
    w[i].dstBinding = i;
    w[i].descriptorCount = 1;
  }
  w[0].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
  w[0].pImageInfo = &depth;
  w[1].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
  w[1].pBufferInfo = &vertices;
  w[2].descriptorType = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
  w[2].pNext = &as;
  w[3].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
  w[3].pImageInfo = &color;
  w[4].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
  w[4].pImageInfo = &dest;
  w[5].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
  w[5].pBufferInfo = &stats;
  vkCmdPushDescriptorSetKHR(cb, VK_PIPELINE_BIND_POINT_COMPUTE, layout_, 0, 2,
                            w.data());
  vkCmdPushConstants(cb, layout_, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof pc,
                     &pc);
  vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, mesh_);
  vkCmdDispatch(cb, (kColumns + 7) / 8, (kRows + 7) / 8, 1);
  sync(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_WRITE_BIT,
       kBuildStage, VK_ACCESS_SHADER_READ_BIT | kBuildRead);
  build(cb, false);
  sync(cb, kBuildStage, kBuildWrite, kBuildStage, kBuildRead | kBuildWrite);
  build(cb, true);
  sync(cb, kBuildStage, kBuildWrite, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
       kBuildRead);
  vkCmdPushDescriptorSetKHR(cb, VK_PIPELINE_BIND_POINT_COMPUTE, layout_, 0,
                            static_cast<uint32_t>(w.size()), w.data());
  vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, reflect_);
  vkCmdDispatch(cb, (pc.size[0] + 7) / 8, (pc.size[1] + 7) / 8, 1);
  sync(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_WRITE_BIT,
       VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_ACCESS_MEMORY_READ_BIT);
  if (frames_++ % 120 == 0) {
    gfx::Buffer readback;
    if (device().create_buffer(16, VK_BUFFER_USAGE_TRANSFER_DST_BIT,
                               gfx::MemoryUse::Readback, &readback,
                               "ray sample counts") == VK_SUCCESS) {
      VkBufferCopy copy{0, 0, 16};
      vkCmdCopyBuffer(cb, counts_.buffer, readback.buffer, 1, &copy);
      sync(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,
           VK_PIPELINE_STAGE_HOST_BIT, VK_ACCESS_HOST_READ_BIT);
      f.defer([readback]() mutable {
        device().invalidate_buffer(readback);
        const auto *n = static_cast<const uint32_t *>(readback.mapped);
        NXLOG_I("nvn.rt",
                "Visible-surface rays: %u traced, %u triangle hits, %u changed "
                "colours, %u tiles with scene depth (one sample per 8x8 tile)",
                n[0], n[1], n[2], n[3]);
        device().destroy_buffer(readback);
      });
    }
  }
  return true;
}
void VisibleSurfaceRt::destroy() {
  if (!tried_) return;
  const VkDevice dev = device().device();
  if (reflect_)
    vkDestroyPipeline(dev, reflect_, nullptr);
  if (mesh_)
    vkDestroyPipeline(dev, mesh_, nullptr);
  if (layout_)
    vkDestroyPipelineLayout(dev, layout_, nullptr);
  if (set_)
    vkDestroyDescriptorSetLayout(dev, set_, nullptr);
  if (sampler_)
    vkDestroySampler(dev, sampler_, nullptr);
  reflect_ = mesh_ = VK_NULL_HANDLE;
  layout_ = VK_NULL_HANDLE;
  set_ = VK_NULL_HANDLE;
  sampler_ = VK_NULL_HANDLE;
  for (Accel *a : {&top_, &bottom_}) {
    if (a->handle)
      vkDestroyAccelerationStructureKHR(dev, a->handle, nullptr);
    a->handle = VK_NULL_HANDLE;
    if (a->storage)
      device().destroy_buffer(a->storage);
  }
  for (gfx::Buffer *b : {&vertices_, &instance_, &scratch_, &counts_})
    if (*b)
      device().destroy_buffer(*b);
  ready_ = false;
}
} // namespace nxrt::nvn::be
