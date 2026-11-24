// SPDX-License-Identifier: GPL-3.0-or-later
// Loader tests on the synthetic AArch64 ELF fixture (tests/data/fixture_aarch64.elf, facts in tests/data/fixture_info.txt).
#include <windows.h>

#include <cstring>
#include <fstream>
#include <vector>

#include "../src/loader/elf.h"
#include "nxrt/dispatch.h"
#include "nxrt/log.h"
#include "nxrt/memory.h"
#include "nxrt/module.h"
#include "test_framework.h"
NXTEST_GROUP(loader)

using namespace nxrt;

namespace {

const char* kFull = NXRT_TEST_DATA_DIR "/fixture_aarch64.elf";
const char* kNoSec = NXRT_TEST_DATA_DIR "/fixture_nosections.elf";
const char* kRelr = NXRT_TEST_DATA_DIR "/fixture_relr.elf";      // same object, packed relative relocations (DT_RELR)

// Facts from fixture_info.txt (module-relative ELF virtual addresses).
constexpr uint64_t kFnA = 0x2000, kFnB = 0x2008, kFnC = 0x2010, kCtorA = 0x2018, kCtorB = 0x2030, kDtorA = 0x2048;
constexpr uint64_t kValues = 0x51b0, kPtrToValues = 0x51c0, kFnTable = 0x51c8, kFnTableRo = 0x4030, kPtrImport = 0x51e0, kWeakPtr = 0x51e8;
constexpr uint64_t kInitFlags = 0x5210, kBssBlock = 0x5218, kGotDataImport = 0x4198, kGotMul = 0x41a0, kGotAdd = 0x5208, kTpRel = 0x41a8;

int g_init_flags = 0;
long g_host_data = 0x1234;

void host_ctor_a(::Ctx*) { g_init_flags |= 1; }
void host_ctor_b(::Ctx*) { g_init_flags |= 2; }
void host_dtor_a(::Ctx*) { g_init_flags |= 4; }
void host_hle_add(::Ctx* c) { reg(c, 0) = static_cast<uint32_t>(reg(c, 0) + reg(c, 1)); }
void host_fn_a(::Ctx* c) { reg(c, 0) = 11; }

void reset() {
    modules_unload_all();
    hle_clear();
    mem::shutdown();
    g_init_flags = 0;
    g_host_data = 0x1234;
}

uint64_t peek64(uint64_t a) { return *reinterpret_cast<uint64_t*>(a); }
uint32_t page_protect(uint64_t a) {
    MEMORY_BASIC_INFORMATION mbi{};
    VirtualQuery(reinterpret_cast<void*>(a), &mbi, sizeof mbi);
    return mbi.Protect;
}

Module* load_linked(const char* path = kFull) {
    module_provide_data("hle_data_import", reinterpret_cast<uint64_t>(&g_host_data));
    hle_register("hle_add", host_hle_add);
    std::string err;
    Module* m = module_load_file(path, {}, &err);
    if (!m) std::printf("    load error: %s\n", err.c_str());
    if (m && !module_link_all()) std::printf("    link failed\n");
    return m;
}

std::vector<uint8_t> read_file(const char* p) {
    std::ifstream f(p, std::ios::binary | std::ios::ate);
    std::vector<uint8_t> v(static_cast<size_t>(f.tellg()));
    f.seekg(0);
    f.read(reinterpret_cast<char*>(v.data()), static_cast<std::streamsize>(v.size()));
    return v;
}

void put_u32(std::vector<uint8_t>& v, size_t off, uint32_t value) {
    std::memcpy(v.data() + off, &value, sizeof value);
}

std::vector<uint8_t> minimal_nso(bool lz4 = false) {
    const uint32_t stored_size = lz4 ? 9 : 8;
    std::vector<uint8_t> v(0x100 + stored_size, 0);
    std::memcpy(v.data(), "NSO0", 4);
    put_u32(v, 0x0C, lz4 ? 1 : 0);
    put_u32(v, 0x10, 0x100);       // Eight decoded text bytes at image offset zero.
    put_u32(v, 0x14, 0);
    put_u32(v, 0x18, 8);
    put_u32(v, 0x20, 0x100 + stored_size);       // Empty rodata and data follow the text.
    put_u32(v, 0x24, 8);
    put_u32(v, 0x30, 0x100 + stored_size);
    put_u32(v, 0x34, 8);
    if (lz4) {
        put_u32(v, 0x60, stored_size);
        v[0x100] = 0x80;            // Final LZ4 sequence with eight literal zero bytes.
    }
    return v;
}

size_t dynamic_entry_offset(const std::vector<uint8_t>& image, int64_t tag) {
    elf::Ehdr eh{};
    if (image.size() < sizeof eh) return image.size();
    std::memcpy(&eh, image.data(), sizeof eh);
    if (eh.phoff > image.size() || uint64_t(eh.phnum) * sizeof(elf::Phdr) > image.size() - eh.phoff) return image.size();
    for (uint16_t i = 0; i < eh.phnum; i++) {
        elf::Phdr ph{};
        std::memcpy(&ph, image.data() + eh.phoff + uint64_t(i) * sizeof ph, sizeof ph);
        if (ph.type != elf::PT_DYNAMIC || ph.offset > image.size() || ph.filesz > image.size() - ph.offset) continue;
        for (uint64_t off = 0; off + sizeof(elf::Dyn) <= ph.filesz; off += sizeof(elf::Dyn)) {
            elf::Dyn dyn{};
            std::memcpy(&dyn, image.data() + ph.offset + off, sizeof dyn);
            if (dyn.tag == tag) return static_cast<size_t>(ph.offset + off);
            if (dyn.tag == elf::DT_NULL) break;
        }
    }
    return image.size();
}

size_t vaddr_file_offset(const std::vector<uint8_t>& image, uint64_t vaddr, size_t bytes) {
    elf::Ehdr eh{};
    if (image.size() < sizeof eh) return image.size();
    std::memcpy(&eh, image.data(), sizeof eh);
    if (eh.phoff > image.size() || uint64_t(eh.phnum) * sizeof(elf::Phdr) > image.size() - eh.phoff) return image.size();
    for (uint16_t i = 0; i < eh.phnum; i++) {
        elf::Phdr ph{};
        std::memcpy(&ph, image.data() + eh.phoff + uint64_t(i) * sizeof ph, sizeof ph);
        if (ph.type != elf::PT_LOAD || vaddr < ph.vaddr) continue;
        const uint64_t in_segment = vaddr - ph.vaddr;
        if (in_segment > ph.filesz || bytes > ph.filesz - in_segment || ph.offset > image.size() || in_segment > image.size() - ph.offset || bytes > image.size() - ph.offset - in_segment) continue;
        return static_cast<size_t>(ph.offset + in_segment);
    }
    return image.size();
}

}  // namespace

NXTEST(map_and_symbols) {
    reset();
    Module* m = load_linked();
    REQUIRE(m);
    CHECK_EQ(m->base(), mem::kMainBase);
    CHECK_EQ(m->name(), std::string("fixture_aarch64.elf"));
    CHECK(m->size() >= 0x5400);
    CHECK_EQ(m->entry(), m->base() + 0x2060);
    CHECK_EQ(m->find_symbol("fn_a"), m->base() + kFnA);
    CHECK_EQ(m->find_symbol("fn_c"), m->base() + kFnC);
    CHECK_EQ(m->find_symbol("values"), m->base() + kValues);
    CHECK_EQ(m->find_symbol("nonexistent"), 0u);
    CHECK_EQ(module_find_symbol("init_flags"), m->base() + kInitFlags);
    CHECK_EQ(module_at(m->base() + 0x2100), m);
    CHECK(module_at(m->base() + m->size() + 0x100000) == nullptr);
    CHECK(m->describe(m->base() + kFnB + 2).find("fn_b+0x2") == 0);
    CHECK(m->linked());
    reset();
}

NXTEST(data_and_relative_relocations) {
    reset();
    Module* m = load_linked();
    REQUIRE(m);
    const uint64_t b = m->base();
    CHECK_EQ(*reinterpret_cast<int*>(b + kValues + 4), 2);                  // .data bytes copied
    CHECK_EQ(peek64(b + kPtrToValues), b + kValues + 8);                      // &values[2]
    for (int i = 0; i < 3; i++) CHECK_EQ(peek64(b + kFnTable + 8 * i), b + kFnA + 8 * i);
    CHECK_EQ(peek64(b + kFnTableRo + 0), b + kFnC);
    CHECK_EQ(peek64(b + kFnTableRo + 16), b + kFnA);
    CHECK_EQ(std::memcmp(reinterpret_cast<void*>(b + 0x1130), "NXRT-FIXTURE", 13), 0);   // .rodata
    for (int i = 0; i < 64; i++) CHECK_EQ(*reinterpret_cast<int*>(b + kBssBlock + 4 * i), 0);   // .bss zero
    reset();
}

NXTEST(packed_relative_relocations) {
    // SDK 20 modules keep their RELATIVE relocations in DT_RELR (addresses and bitmaps, addend in the slot). The layout differs from
    // the full fixture, so the addresses come from the symbols.
    reset();
    Module* m = load_linked(kRelr);
    REQUIRE(m);
    const uint64_t values = m->find_symbol("values"), fn_a = m->find_symbol("fn_a"), fn_b = m->find_symbol("fn_b"), fn_c = m->find_symbol("fn_c");
    REQUIRE(values && fn_a && fn_b && fn_c);
    CHECK_EQ(peek64(m->find_symbol("ptr_to_values")), values + 8);
    const uint64_t t = m->find_symbol("fn_table"), ro = m->find_symbol("fn_table_ro");
    CHECK_EQ(peek64(t), fn_a);
    CHECK_EQ(peek64(t + 8), fn_b);
    CHECK_EQ(peek64(t + 16), fn_c);
    CHECK_EQ(peek64(ro), fn_c);
    CHECK_EQ(peek64(ro + 16), fn_a);
    const RelocStats& s = m->stats();
    CHECK_EQ(s.unhandled, 0u);
    CHECK_EQ(s.out_of_range, 0u);
    CHECK_EQ(s.relative, 11u);                 // the same 11 RELATIVE relocations as the RELA fixture
    reset();
}

NXTEST(wrapping_relocation_targets_are_rejected) {
    reset();
    const uint64_t max = UINT64_MAX;
    const std::vector<uint8_t> rela_fixture = read_file(kFull);
    const size_t rela_dynamic = dynamic_entry_offset(rela_fixture, elf::DT_RELA);
    REQUIRE(rela_dynamic != rela_fixture.size());
    elf::Dyn rela_tag{};
    std::memcpy(&rela_tag, rela_fixture.data() + rela_dynamic, sizeof rela_tag);
    const size_t rela_file = vaddr_file_offset(rela_fixture, rela_tag.val, sizeof(elf::Rela));
    REQUIRE(rela_file != rela_fixture.size());
    for (uint64_t delta = 0; delta < 8; delta++) {
        std::vector<uint8_t> image = rela_fixture;
        const uint64_t target = max - delta;
        std::memcpy(image.data() + rela_file, &target, sizeof target);
        Module* m = module_load_memory(image.data(), image.size());
        REQUIRE(m);
        CHECK(module_link_all());
        CHECK_EQ(m->stats().out_of_range, 1u);
        reset();
    }

    std::vector<uint8_t> relr_fixture = read_file(kRelr);
    const size_t relr_dynamic = dynamic_entry_offset(relr_fixture, elf::DT_RELR);
    const size_t relr_size_dynamic = dynamic_entry_offset(relr_fixture, elf::DT_RELRSZ);
    REQUIRE(relr_dynamic != relr_fixture.size() && relr_size_dynamic != relr_fixture.size());
    elf::Dyn relr_tag{}, relr_size_tag{};
    std::memcpy(&relr_tag, relr_fixture.data() + relr_dynamic, sizeof relr_tag);
    std::memcpy(&relr_size_tag, relr_fixture.data() + relr_size_dynamic, sizeof relr_size_tag);
    relr_size_tag.val = sizeof(uint64_t);       // isolate the direct entry from its following bitmap
    std::memcpy(relr_fixture.data() + relr_size_dynamic, &relr_size_tag, sizeof relr_size_tag);
    const size_t relr_file = vaddr_file_offset(relr_fixture, relr_tag.val, sizeof(uint64_t));
    REQUIRE(relr_file != relr_fixture.size());
    const uint64_t relr_targets[] = {max - 7, max - 5, max - 3, max - 1};  // RELR direct entries have bit zero clear
    for (uint64_t target : relr_targets) {
        std::vector<uint8_t> image = relr_fixture;
        std::memcpy(image.data() + relr_file, &target, sizeof target);
        Module* m = module_load_memory(image.data(), image.size());
        REQUIRE(m);
        CHECK(module_link_all());
        CHECK_EQ(m->stats().out_of_range, 1u);
        reset();
    }
}

NXTEST(last_relocation_slot_remains_valid) {
    reset();
    std::vector<uint8_t> image = read_file(kFull);
    const size_t rela_dynamic = dynamic_entry_offset(image, elf::DT_RELA);
    REQUIRE(rela_dynamic != image.size());
    elf::Dyn rela_tag{};
    std::memcpy(&rela_tag, image.data() + rela_dynamic, sizeof rela_tag);
    const size_t rela_file = vaddr_file_offset(image, rela_tag.val, sizeof(elf::Rela));
    REQUIRE(rela_file != image.size());
    Module* probe = module_load_memory(image.data(), image.size());
    REQUIRE(probe && probe->size() >= sizeof(uint64_t));
    const uint64_t target = probe->size() - sizeof(uint64_t);
    reset();

    std::memcpy(image.data() + rela_file, &target, sizeof target);
    Module* m = module_load_memory(image.data(), image.size());
    REQUIRE(m);
    CHECK(module_link_all());
    CHECK_EQ(m->stats().out_of_range, 0u);
    CHECK_EQ(peek64(m->base() + target), m->base() + kCtorA);
    reset();
}

NXTEST(import_resolution_order) {
    reset();
    Module* m = load_linked();
    REQUIRE(m);
    const uint64_t b = m->base();
    CHECK_EQ(peek64(b + kPtrImport), reinterpret_cast<uint64_t>(&g_host_data));    // host data
    CHECK_EQ(peek64(b + kGotDataImport), reinterpret_cast<uint64_t>(&g_host_data));
    CHECK_EQ(peek64(b + kGotAdd), hle_address("hle_add"));                         // HLE handler (JUMP_SLOT)
    CHECK(hle_lookup("hle_add") == host_hle_add);
    CHECK_EQ(peek64(b + kGotMul), hle_address("hle_mul"));                         // no handler: logging stub
    CHECK(hle_lookup("hle_mul") == nullptr);
    CHECK_EQ(peek64(b + kWeakPtr), 0u);                                            // undefined weak -> null
    const RelocStats& s = m->stats();
    CHECK_EQ(s.total, 17u);
    CHECK_EQ(s.relative, 11u);
    CHECK_EQ(s.abs64, 2u);
    CHECK_EQ(s.glob_dat, 2u);
    CHECK_EQ(s.jump_slot, 1u);
    CHECK_EQ(s.tls, 1u);
    CHECK_EQ(s.unhandled, 0u);
    CHECK_EQ(s.to_host_data, 2u);
    CHECK_EQ(s.to_hle, 1u);
    CHECK_EQ(s.to_stub, 1u);
    CHECK_EQ(s.to_weak_null, 1u);
    reset();
}

NXTEST(call_through_got_runs_host_code) {
    reset();
    Module* m = load_linked();
    REQUIRE(m);
    ::Ctx c{};
    reg(&c, 0) = 40;
    reg(&c, 1) = 2;
    dispatch_call(&c, peek64(m->base() + kGotAdd));       // the PLT slot the lifted code would call through
    CHECK_EQ(reg(&c, 0), 42u);
    CHECK_EQ(hle_calls(hle_address("hle_add")), 1u);
    reg(&c, 0) = 99;
    dispatch_call(&c, peek64(m->base() + kGotMul));       // logging stub: returns 0 and counts
    CHECK_EQ(reg(&c, 0), 0u);
    CHECK_EQ(hle_calls(hle_address("hle_mul")), 1u);
    CHECK_EQ(hle_stub_count(), 1u);
    reset();
}

NXTEST(register_functions_and_indirect_call_through_table) {
    reset();
    Module* m = load_linked();
    REQUIRE(m);
    const FuncEntry tab[] = {{m->base() + kFnA, host_fn_a}, {0x1000, host_fn_a} /* outside the image: rejected */, {m->base() + kFnB, nullptr}};
    CHECK_EQ(module_register_functions(m, tab, 3), 1u);
    CHECK_EQ(fn_count(), 1u);
    ::Ctx c{};
    dispatch_call(&c, peek64(m->base() + kFnTable));      // fn_table[0] == fn_a
    CHECK_EQ(reg(&c, 0), 11u);
    reset();
}

NXTEST(init_and_fini_arrays) {
    reset();
    Module* m = load_linked();
    REQUIRE(m);
    REQUIRE(m->init_array().size() == 2);
    CHECK_EQ(m->init_array()[0], m->base() + kCtorA);
    CHECK_EQ(m->init_array()[1], m->base() + kCtorB);
    REQUIRE(m->fini_array().size() == 1);
    CHECK_EQ(m->fini_array()[0], m->base() + kDtorA);
    const FuncEntry tab[] = {{m->base() + kCtorA, host_ctor_a}, {m->base() + kCtorB, host_ctor_b}, {m->base() + kDtorA, host_dtor_a}};
    CHECK_EQ(module_register_functions(m, tab, 3), 3u);
    ::Ctx c{};
    CHECK(module_run_init(m, &c));
    CHECK_EQ(g_init_flags, 3);
    CHECK(module_run_fini(m, &c));
    CHECK_EQ(g_init_flags, 7);
    reset();
}

NXTEST(init_with_unregistered_entry_is_skipped_not_a_crash) {
    reset();
    Module* m = load_linked();
    REQUIRE(m);
    ::Ctx c{};
    CHECK(!module_run_init(m, &c));       // nothing registered: reported, never dispatched
    CHECK_EQ(g_init_flags, 0);
    reset();
}

NXTEST(segment_protections) {
    reset();
    Module* m = load_linked();
    REQUIRE(m);
    const uint64_t b = m->base();
    CHECK_EQ(page_protect(b + 0x0), (uint32_t)PAGE_READONLY);          // headers / rodata
    CHECK_EQ(page_protect(b + 0x2000), (uint32_t)PAGE_EXECUTE_READ);   // text
    CHECK_EQ(page_protect(b + 0x4000), (uint32_t)PAGE_READONLY);       // RELRO, read-only once relocated
    CHECK_EQ(page_protect(b + 0x5000), (uint32_t)PAGE_READWRITE);      // data + bss
    mem::VmInfo v = mem::vm_query(b + 0x2000);
    CHECK_EQ(v.state, (uint32_t)mem::kMemCode);
    CHECK_EQ(mem::vm_query(b + 0x5000).state, (uint32_t)mem::kMemCodeData);
    reset();
}

NXTEST(tls_segment_and_tprel) {
    reset();
    Module* m = load_linked();
    REQUIRE(m);
    const TlsInfo& t = m->tls();
    CHECK_EQ(t.file_size, 0x20u);
    CHECK_EQ(t.mem_size, 0x40u);
    CHECK_EQ(t.tp_offset, 16u);
    CHECK_EQ(t.init_addr, m->base() + 0x4000);
    CHECK_EQ(*reinterpret_cast<int*>(t.init_addr), 7);               // tls_a initial value
    CHECK_EQ(peek64(m->base() + kTpRel), 16u);                         // tls_a: offset 0 from the block start + tp_offset
    CHECK_EQ(module_tls_template().mem_size, 0x40u);
    reset();
}

NXTEST(image_without_section_headers) {
    reset();
    Module* full = load_linked();
    REQUIRE(full);
    size_t nsyms = full->symbols().size();
    uint64_t fn_c = full->find_symbol("fn_c") - full->base();
    reset();
    Module* m = load_linked(kNoSec);
    REQUIRE(m);
    CHECK_EQ(m->symbols().size(), nsyms);
    CHECK_EQ(m->find_symbol("fn_c") - m->base(), fn_c);
    CHECK_EQ(m->stats().total, 17u);
    CHECK_EQ(m->init_array().size(), 2u);
    reset();
}

NXTEST(explicit_base_and_two_modules) {
    reset();
    module_provide_data("hle_data_import", reinterpret_cast<uint64_t>(&g_host_data));
    hle_register("hle_add", host_hle_add);
    LoadOptions o1, o2;
    o1.name = "main";
    o1.base = mem::kMainBase;
    o2.name = "sdk";
    o2.base = mem::kSdkBase;
    std::string err;
    Module* a = module_load_file(kFull, o1, &err);
    Module* b = module_load_file(kFull, o2, &err);
    REQUIRE(a && b);
    CHECK(module_link_all());
    CHECK_EQ(b->base(), mem::kSdkBase);
    CHECK_EQ(a->find_symbol("fn_a") + (mem::kSdkBase - mem::kMainBase), b->find_symbol("fn_a"));
    CHECK_EQ(module_by_name("sdk"), b);
    CHECK_EQ(modules().size(), 2u);
    CHECK_EQ(module_at(mem::kSdkBase + 0x2000), b);
    // a third module overlapping the first is refused
    LoadOptions o3;
    o3.base = mem::kMainBase;
    CHECK(module_load_file(kFull, o3, &err) == nullptr);
    CHECK(err.find("overlaps") != std::string::npos || err.find("range in use") != std::string::npos);
    reset();
}

NXTEST(default_bases_are_sequential) {
    reset();
    Module* a = module_load_file(kFull);
    Module* b = module_load_file(kFull);
    REQUIRE(a && b);
    CHECK_EQ(a->base(), mem::kMainBase);
    CHECK(b->base() >= mem::kSdkBase);
    CHECK_EQ(b->base() % mem::kAllocGranularity, 0u);
    reset();
}

NXTEST(symbol_override_beats_everything) {
    reset();
    hle_register("weak_missing", host_fn_a);
    module_override_symbol("weak_missing");
    Module* m = load_linked();
    REQUIRE(m);
    CHECK_EQ(peek64(m->base() + kWeakPtr), hle_address("weak_missing"));
    CHECK_EQ(m->stats().to_weak_null, 0u);
    reset();
}

NXTEST(malformed_images_are_rejected) {
    reset();
    std::vector<uint8_t> good = read_file(kFull);
    REQUIRE(good.size() > 0x5000);
    std::string err;
    CHECK(module_load_memory(good.data(), 10, {}, &err) == nullptr);                 // too short
    {
        auto v = good;
        v[0] = 'X';
        CHECK(module_load_memory(v.data(), v.size(), {}, &err) == nullptr);          // bad magic
        CHECK(err.find("ELF") != std::string::npos);
    }
    {
        auto v = good;
        v[18] = 62;                                                                    // e_machine = x86-64
        CHECK(module_load_memory(v.data(), v.size(), {}, &err) == nullptr);
        CHECK(err.find("AArch64") != std::string::npos);
    }
    {
        auto v = good;
        uint64_t phoff = 0xFFFFFFFFFFFF0000ull;                                        // program headers outside the file
        std::memcpy(&v[32], &phoff, 8);
        CHECK(module_load_memory(v.data(), v.size(), {}, &err) == nullptr);
    }
    {
        auto v = good;
        v.resize(0x3000);                                                              // PT_LOAD data past the end of the file
        CHECK(module_load_memory(v.data(), v.size(), {}, &err) == nullptr);
        CHECK(err.find("outside the file") != std::string::npos);
    }
    {
        LoadOptions o;
        o.max_image_size = 0x1000;                                                     // oversize image
        CHECK(module_load_memory(good.data(), good.size(), o, &err) == nullptr);
        CHECK(err.find("exceeds") != std::string::npos);
    }
    CHECK(module_load_file("no/such/file.elf", {}, &err) == nullptr);
    CHECK(modules().empty());                                                          // nothing half-loaded survived
    // fuzz: corrupting any single byte of the headers or dynamic section must never crash the loader
    for (size_t i = 0; i < 0x400; i++) {
        auto v = good;
        v[i] ^= 0xFF;
        Module* m = module_load_memory(v.data(), v.size(), {}, &err);
        if (m) module_link_all();
        modules_unload_all();
    }
    for (size_t i = 0x4040; i < 0x4190; i++) {          // dynamic section
        auto v = good;
        v[i] ^= 0xA5;
        Module* m = module_load_memory(v.data(), v.size(), {}, &err);
        if (m) module_link_all();
        modules_unload_all();
    }
    reset();
}

NXTEST(well_formed_nso_segments_load) {
    reset();
    std::vector<uint8_t> nso = minimal_nso();
    std::string err;
    Module* m = module_load_nso_memory(nso.data(), nso.size(), {}, &err);
    REQUIRE(m);
    CHECK_EQ(m->size(), 0x1000u);
    reset();
    nso = minimal_nso(true);
    m = module_load_nso_memory(nso.data(), nso.size(), {}, &err);
    REQUIRE(m);
    CHECK_EQ(m->size(), 0x1000u);
    reset();
}

NXTEST(wrapped_nso_segment_span_is_rejected) {
    reset();
    std::vector<uint8_t> nso = minimal_nso(true);
    put_u32(nso, 0x14, 0x1001);
    put_u32(nso, 0x18, 0xFFFFFFFFu);   // the old 32-bit end calculation wrapped to 0x1000
    put_u32(nso, 0x60, 2);
    nso[0x100] = 0x10;                 // one literal byte, formerly copied beyond the undersized image
    nso[0x101] = 0x41;
    put_u32(nso, 0x20, 0x102);
    put_u32(nso, 0x24, 0x1000);
    put_u32(nso, 0x28, 0);
    put_u32(nso, 0x30, 0x102);
    put_u32(nso, 0x34, 0x1000);
    put_u32(nso, 0x38, 0);
    std::string err;
    CHECK(module_load_nso_memory(nso.data(), nso.size(), {}, &err) == nullptr);
    CHECK(err.find("overlap") != std::string::npos);
    CHECK(modules().empty());
    reset();
}
