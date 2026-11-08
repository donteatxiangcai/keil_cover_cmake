#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keil2cmake.py —— 把 Keil MDK (uVision) 工程转换成 CMake 工程 (Arm Compiler 6)

从 .uvprojx 里读出 Keil 的全部工程信息, 生成:
    <输出目录>/CMakeLists.txt
    <输出目录>/CMakePresets.json
    <输出目录>/cmake/toolchain-armclang.cmake
    <输出目录>/cmake/<target>.sct          (分散加载文件)

用法:
    python keil2cmake.py 路径/工程.uvprojx
    python keil2cmake.py 工程.uvprojx -o D:/out --target NAND --build

依赖: 只要 Python 3.8+ 标准库; 能真正编译还需要本机装有 Keil MDK (含 ARMCLANG) 和 CMake+Ninja。
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
#  Keil 选项编号 -> 实际参数 的映射表 (来自 uVision 的 Target 选项)
# ---------------------------------------------------------------------------
# <Optim> 1..4  ->  -O0..-O3  (实测: 2 == -O1)
OPTIM_MAP = {"1": "-O0", "2": "-O1", "3": "-O2", "4": "-O3"}

# <v6Lang>  AC6 的 C 语言标准
V6LANG_MAP = {
    "0": ("99", False),   # default -> 不显式指定, 按 c99 处理
    "1": ("90", False),   # c90
    "2": ("90", True),    # gnu90
    "3": ("99", False),   # c99
    "4": ("99", True),    # gnu99
    "5": ("11", False),   # c11
    "6": ("11", True),    # gnu11
}

# Keil FileType -> 我们的分类
FILE_C, FILE_ASM, FILE_OBJ, FILE_LIB, FILE_TXT = "1", "2", "3", "4", "5"


def log(msg):
    if _LOG_HOOK is not None:
        try:
            _LOG_HOOK(msg)
            return
        except Exception:
            pass
    print(msg, flush=True)


_LOG_HOOK = None


def set_logger(fn):
    """GUI 用: 把 log() 的输出接到自己的回调里 (传 None 恢复打印)"""
    global _LOG_HOOK
    _LOG_HOOK = fn


def _init_console():
    """输出被重定向(管道/文件)时统一用 UTF-8, 避免中文乱码"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  工具链定位
# ---------------------------------------------------------------------------
def find_armclang_bin():
    """返回 Keil ARMCLANG\\bin 目录, 找不到返回 None"""
    cands = []
    for env in ("ARMCLANG_BIN_DIR", "ARMCLANG_BIN", "KEIL_MDK_PATH", "KEIL_PATH"):
        v = os.environ.get(env)
        if v:
            cands.append(os.path.join(v, "ARMCLANG", "bin") if env.startswith("KEIL") else v)

    if os.name == "nt":
        try:
            import winreg
            for hive, key in (
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Keil\Products\MDK"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Keil\Products\MDK"),
                (winreg.HKEY_CURRENT_USER, r"Software\Keil\Products\MDK"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        p = winreg.QueryValueEx(k, "Path")[0]
                        cands.append(os.path.join(p, "ARMCLANG", "bin"))
                except OSError:
                    pass
        except ImportError:
            pass

    cands += [
        r"C:\Keil_v5\ARM\ARMCLANG\bin",
        r"D:\Keil_v5\ARM\ARMCLANG\bin",
        r"E:\Keil_v5\ARM\ARMCLANG\bin",
        r"C:\Keil\ARM\ARMCLANG\bin",
        r"D:\Program Files\keil\ARM\ARMCLANG\bin",
        r"C:\Program Files\keil\ARM\ARMCLANG\bin",
        r"C:\Program Files\ARM\ARMCLANG\bin",
        "/opt/arm/armclang/bin",
    ]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "armclang.exe")) or \
           c and os.path.isfile(os.path.join(c, "armclang")):
            return c
    return None


# ---------------------------------------------------------------------------
#  uvprojx 解析
# ---------------------------------------------------------------------------
def txt(node, path, default=""):
    if node is None:
        return default
    v = node.findtext(path)
    return default if v is None else v


def parse_int(s, default=0):
    s = (s or "").strip()
    if not s:
        return default
    try:
        return int(s, 0)
    except ValueError:
        return default


def parse_project(uvprojx, target_name=None):
    try:
        tree = ET.parse(uvprojx)
    except ET.ParseError:
        with open(uvprojx, "rb") as f:
            raw = f.read().decode("utf-8", "replace")
        tree = ET.ElementTree(ET.fromstring(raw))
    root = tree.getroot()

    targets = root.findall("./Targets/Target")
    if not targets:
        raise SystemExit("错误: %s 里找不到 <Target>" % uvprojx)

    tgt = None
    if target_name:
        for t in targets:
            if txt(t, "TargetName") == target_name:
                tgt = t
                break
        if tgt is None:
            names = ", ".join(txt(t, "TargetName") for t in targets)
            raise SystemExit("错误: 没有名为 %r 的 Target, 可选: %s" % (target_name, names))
    else:
        tgt = targets[0]

    p = {}
    p["uvprojx"] = os.path.abspath(uvprojx)
    p["dir"] = os.path.dirname(p["uvprojx"])
    p["target"] = txt(tgt, "TargetName", "app")
    p["device"] = txt(tgt, "TargetOption/TargetCommonOption/Device", "")
    p["vendor"] = txt(tgt, "TargetOption/TargetCommonOption/Vendor", "")
    p["cpu_str"] = txt(tgt, "TargetOption/TargetCommonOption/Cpu", "")
    p["output_name"] = txt(tgt, "TargetOption/TargetCommonOption/OutputName", p["target"]) or p["target"]
    p["output_dir"] = txt(tgt, "TargetOption/TargetCommonOption/OutputDirectory", "")
    p["create_hex"] = txt(tgt, "TargetOption/TargetCommonOption/CreateHexFile", "0") == "1"

    # 编译器版本: pArmCC = AC6, pCCUsed = AC5
    p["armcc"] = txt(tgt, "pArmCC", "")
    p["cc_used"] = txt(tgt, "pCCUsed", "")
    p["use_ac6"] = txt(tgt, "uAC6", "0") == "1"

    # ---- C 编译选项 ----
    cads = tgt.find("TargetOption/TargetArmAds/Cads")
    aads = tgt.find("TargetOption/TargetArmAds/Aads")
    lads = tgt.find("TargetOption/TargetArmAds/LDads")

    p["defines"] = [d.strip() for d in txt(cads, "VariousControls/Define").split(",") if d.strip()]
    p["undefines"] = [d.strip() for d in txt(cads, "VariousControls/Undefine").split(",") if d.strip()]
    p["includes"] = [i.strip() for i in txt(cads, "VariousControls/IncludePath").split(";") if i.strip()]
    p["misc_c"] = txt(cads, "VariousControls/MiscControls", "").strip()
    p["misc_asm"] = txt(aads, "VariousControls/MiscControls", "").strip()
    p["misc_ld"] = txt(lads, "Misc", "").strip()

    p["optim"] = txt(cads, "Optim", "2").strip()
    p["v6lang"] = txt(cads, "v6Lang", "3").strip()
    p["one_elf_section"] = txt(cads, "OneElfS", "1") == "1"
    p["short_en"] = txt(cads, "vShortEn", "1") == "1"
    p["short_wch"] = txt(cads, "vShortWch", "1") == "1"
    p["lto"] = txt(cads, "v6Lto", "0") == "1"

    # ---- 内存布局 ----
    regions = []
    for node in tgt.findall("TargetOption/TargetArmAds/ArmAdsMisc/OnChipMemories/*"):
        tag = node.tag
        start = parse_int(txt(node, "StartAddress"), 0)
        size = parse_int(txt(node, "Size"), 0)
        if size <= 0:
            continue
        if tag in ("IROM", "IROM2"):
            regions.append(("ROM", tag, start, size))
        elif tag in ("IRAM", "IRAM2", "XRAM"):
            regions.append(("RAM", tag, start, size))
    # OnChipMemories 为空的旧工程, 回退到 OCR_RVCT
    if not regions:
        for tag, kind in (("OCR_RVCT4", "ROM"), ("OCR_RVCT9", "RAM"), ("OCR_RVCT10", "RAM")):
            node = tgt.find("TargetOption/TargetArmAds/ArmAdsMisc/OnChipMemories/" + tag)
            if node is None:
                continue
            size = parse_int(txt(node, "Size"), 0)
            if size <= 0:
                continue
            regions.append((kind, tag, parse_int(txt(node, "StartAddress"), 0), size))
    p["regions"] = regions

    # ---- 分散加载文件 ----
    p["scatter_user"] = txt(lads, "ScatterFile", "").strip()
    p["use_mem_layout"] = txt(lads, "umfTarg", "1") == "1"

    # ---- 源文件 ----
    groups = []
    others = []
    for g in tgt.findall("./Groups/Group"):
        gname = txt(g, "GroupName", "")
        files = []
        for f in g.findall("./Files/File"):
            raw = txt(f, "FilePath", "")
            ftype = txt(f, "FileType", "1").strip()
            if not raw:
                continue
            ap = os.path.normpath(os.path.join(p["dir"], raw.replace("\\", "/")))
            item = (ap, ftype)
            if ftype in (FILE_C, FILE_ASM):
                files.append(item)
            elif ftype in (FILE_OBJ, FILE_LIB):
                others.append((ap, ftype, gname))
        if files:
            groups.append((gname, files))
    p["groups"] = groups
    p["others"] = others

    # ---- CPU / FPU ----
    m = re.search(r'CPUTYPE\("([^"]+)"\)', p["cpu_str"])
    p["core"] = m.group(1) if m else ""
    if not p["core"]:
        m = re.search(r'CPUTYPE\(([^)]+)\)', p["cpu_str"])
        p["core"] = m.group(1).strip('"') if m else ""
    fm = re.search(r'\bFPU(\d)\b', p["cpu_str"])
    p["fpu"] = int(fm.group(1)) if fm else 0   # 0=无, 2=单精度, 3=双精度
    return p


# ---------------------------------------------------------------------------
#  CPU / FPU -> 各工具的参数
# ---------------------------------------------------------------------------
def cpu_flags(p):
    """返回 (编译器 -mcpu/-mfpu 串, armasm --cpu/--fpu 串, armlink --cpu 串)"""
    core = (p["core"] or "Cortex-M4").strip()
    low = core.lower()
    cpu = core

    if low.startswith("cortex-m7"):
        cpu = "cortex-m7"
    elif low.startswith("cortex-m4"):
        cpu = "cortex-m4"
    elif low.startswith("cortex-m33"):
        cpu = "cortex-m33"
    elif low.startswith("cortex-m3"):
        cpu = "cortex-m3"
    elif low.startswith("cortex-m0+"):
        cpu = "cortex-m0plus"
    elif low.startswith("cortex-m0"):
        cpu = "cortex-m0"
    elif low.startswith("cortex-m23"):
        cpu = "cortex-m23"
    else:
        cpu = low

    fpu = p["fpu"]
    cflags = ["-mcpu=%s" % cpu]
    link_cpu = core
    asm_fpu = ""

    if fpu == 3:      # 双精度
        cflags += ["-mfpu=fpv5-d16", "-mfloat-abi=hard"]
        link_cpu = core + ".fp.dp"
        asm_fpu = "FPv5_D16"
    elif fpu == 2:    # 单精度
        mfpu = "fpv5-sp-d16" if cpu in ("cortex-m7", "cortex-m33") else "fpv4-sp-d16"
        cflags += ["-mfpu=%s" % mfpu, "-mfloat-abi=hard"]
        link_cpu = core + ".fp.sp"
        asm_fpu = "FPv5-SP" if cpu in ("cortex-m7", "cortex-m33") else "FPv4-SP"
    else:             # 无 FPU
        link_cpu = core + ".no_fp" if cpu in ("cortex-m4", "cortex-m7") else core

    asm_cpu = core if core else "Cortex-M4"
    asm_flags = "--cpu=%s" % asm_cpu
    if asm_fpu:
        asm_flags += " --fpu=%s" % asm_fpu
        asm_flags += " --apcs=/hardfp"
    else:
        asm_flags += " --apcs=/softfp"

    return " ".join(cflags), asm_flags, "--cpu=%s" % link_cpu


# ---------------------------------------------------------------------------
#  生成分散加载文件
# ---------------------------------------------------------------------------
def find_keil_scatter(p):
    """工程编译过一次后, Keil 会在输出目录留下自动生成的 .sct, 直接拿来用最准"""
    if p["scatter_user"]:
        f = os.path.normpath(os.path.join(p["dir"], p["scatter_user"].replace("\\", "/")))
        if os.path.isfile(f):
            return f, "工程里指定"
    if not p["output_dir"]:
        return None, ""
    out = os.path.normpath(os.path.join(p["dir"], p["output_dir"].replace("\\", "/")))
    if not os.path.isdir(out):
        return None, ""
    cands = sorted(glob.glob(os.path.join(out, "*.sct")))
    if not cands:
        return None, ""
    for c in cands:
        if os.path.splitext(os.path.basename(c))[0] == p["output_name"]:
            return c, "Keil 自动生成"
    return cands[0], "Keil 自动生成"


def gen_scatter(p):
    roms = [r for r in p["regions"] if r[0] == "ROM"]
    rams = [r for r in p["regions"] if r[0] == "RAM"]
    if not roms:
        roms = [("ROM", "IROM", 0x08000000, 0x100000)]
    if not rams:
        rams = [("RAM", "IRAM", 0x20000000, 0x30000)]

    rom = roms[0]
    lines = []
    lines.append("; *************************************************************")
    lines.append("; *** Scatter-Loading Description File (generated by keil2cmake) ***")
    lines.append("; *************************************************************")
    lines.append("")
    lines.append("LR_IROM1 0x%08X 0x%08X  {    ; load region size_region" % (rom[2], rom[3]))
    lines.append("  ER_IROM1 0x%08X 0x%08X  {  ; load address = execution address" % (rom[2], rom[3]))
    lines.append("   *.o (RESET, +First)")
    lines.append("   *(InRoot$$Sections)")
    lines.append("   .ANY (+RO)")
    lines.append("   .ANY (+XO)")
    lines.append("  }")
    for i, r in enumerate(rams, 1):
        lines.append("  RW_IRAM%d 0x%08X 0x%08X  {  ; RW data" % (i, r[2], r[3]))
        lines.append("   .ANY (+RW +ZI)")
        lines.append("  }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  模板
# ---------------------------------------------------------------------------
TOOLCHAIN_TMPL = r'''# =============================================================================
#  Arm Compiler 6 (Keil MDK / ARMCLANG) toolchain file
#  由 keil2cmake.py 根据 .uvprojx 的 Target 设置生成
#    器件: {device}
#    CPU : {core}  FPU: {fpu_desc}
#
#  用法:
#    cmake -S . -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE=<本文件>
#  如果 Keil 不在默认路径, 加 -DARMCLANG_BIN_DIR=".../ARM/ARMCLANG/bin"
# =============================================================================
cmake_minimum_required(VERSION 3.22)

# CMake 3.22+ 原生支持 ARMClang; CMP0123=NEW 表示 cpu/fpu 选项由工程显式给出
cmake_policy(SET CMP0123 NEW)

set(CMAKE_SYSTEM_NAME      Generic)
set(CMAKE_SYSTEM_PROCESSOR {processor})

# 裸机链接需要 scatter 文件, try_compile 只做静态库检查
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

if(NOT ARMCLANG_BIN_DIR AND DEFINED $ENV{{ARMCLANG_BIN_DIR}})
  set(ARMCLANG_BIN_DIR "$ENV{{ARMCLANG_BIN_DIR}}")
endif()
if(NOT ARMCLANG_BIN_DIR)
  find_program(ARMCLANG_EXECUTABLE armclang.exe
    HINTS
      "{keil_bin}"
      "D:/Program Files/keil/ARM/ARMCLANG/bin"
      "C:/Keil_v5/ARM/ARMCLANG/bin"
  )
  if(NOT ARMCLANG_EXECUTABLE)
    message(FATAL_ERROR "未找到 armclang.exe, 请用 -DARMCLANG_BIN_DIR=<Keil>/ARM/ARMCLANG/bin 指定")
  endif()
  get_filename_component(ARMCLANG_BIN_DIR "${{ARMCLANG_EXECUTABLE}}" DIRECTORY)
endif()
file(TO_CMAKE_PATH "${{ARMCLANG_BIN_DIR}}" ARMCLANG_BIN_DIR)

set(CMAKE_C_COMPILER   "${{ARMCLANG_BIN_DIR}}/armclang.exe" CACHE FILEPATH "" FORCE)
set(CMAKE_ASM_COMPILER "${{ARMCLANG_BIN_DIR}}/armasm.exe"   CACHE FILEPATH "" FORCE)
set(CMAKE_LINKER       "${{ARMCLANG_BIN_DIR}}/armlink.exe"  CACHE FILEPATH "" FORCE)
set(CMAKE_AR           "${{ARMCLANG_BIN_DIR}}/armar.exe"    CACHE FILEPATH "" FORCE)
set(CMAKE_FROMELF      "${{ARMCLANG_BIN_DIR}}/fromelf.exe"  CACHE FILEPATH "" FORCE)

set(CMAKE_C_FLAGS_INIT   "{c_cpu_flags} ")
set(CMAKE_ASM_FLAGS_INIT "{asm_cpu_flags} --diag_suppress=A1950W ")
set(CMAKE_EXE_LINKER_FLAGS_INIT "{link_cpu_flags} ")

# 优化等级 (来自 Keil 的 Optimization 设置, CMake 自带的 -O3 会被这里的 cache 覆盖)
if(NOT DEFINED CMAKE_C_FLAGS_RELEASE)
  set(CMAKE_C_FLAGS_RELEASE        "{opt_release}" CACHE STRING "" FORCE)
  set(CMAKE_C_FLAGS_RELWITHDEBINFO "{opt_release} -g" CACHE STRING "" FORCE)
  set(CMAKE_C_FLAGS_MINISIZEREL    "-Os" CACHE STRING "" FORCE)
  set(CMAKE_C_FLAGS_DEBUG          "-O0 -g" CACHE STRING "" FORCE)
endif()
'''

CMAKE_TMPL = r'''# =============================================================================
#  {target}  ({device})
#  由 keil2cmake.py 从 {uvprojx_name} 自动生成, 工具链: Arm Compiler 6
#
#    cmake --preset {preset_name}
#    cmake --build --preset {preset_name}
#
#  产物: build/{build_dir}/{output_name}.axf / .hex / .bin / .axf.map
# =============================================================================
cmake_minimum_required(VERSION 3.22)

if(NOT CMAKE_TOOLCHAIN_FILE)
  message(FATAL_ERROR "请指定工具链文件: -DCMAKE_TOOLCHAIN_FILE=cmake/toolchain-armclang.cmake")
endif()

project({project_name} LANGUAGES C ASM)

# armasm 的规则和 armclang 不同(没有 -c, 输出用 -o)
if(CMAKE_ASM_COMPILER MATCHES "armasm")
  set(CMAKE_ASM_COMPILE_OBJECT "<CMAKE_ASM_COMPILER> <FLAGS> -o <OBJECT> <SOURCE>")
endif()

set(CMAKE_C_STANDARD {c_std})
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS {c_ext})

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE "Release" CACHE STRING "" FORCE)
endif()

set(ROOT_DIR ${{CMAKE_CURRENT_SOURCE_DIR}})

set(APP_SOURCES
{sources}
)

add_executable(${{PROJECT_NAME}} ${{APP_SOURCES}})

target_compile_options(${{PROJECT_NAME}} PRIVATE
    $<$<COMPILE_LANGUAGE:C>:-Wno-invalid-source-encoding>
    $<$<COMPILE_LANGUAGE:C>:-Wno-deprecated-non-prototype>{extra_c_flags}
)

target_compile_definitions(${{PROJECT_NAME}} PRIVATE
{defines}
)

target_include_directories(${{PROJECT_NAME}} PRIVATE
{includes}
)

set_target_properties(${{PROJECT_NAME}} PROPERTIES SUFFIX "{suffix}")
target_link_options(${{PROJECT_NAME}} PRIVATE
    --scatter=${{ROOT_DIR}}/{scatter_rel}
    --strict{extra_ld_flags}
    --info=sizes,totals,unused,veneers,summarysizes
)

# 生成 hex / bin (fromelf)
if(CMAKE_FROMELF)
  add_custom_command(TARGET ${{PROJECT_NAME}} POST_BUILD
      COMMAND ${{CMAKE_FROMELF}} --i32combined --output
              ${{CMAKE_CURRENT_BINARY_DIR}}/{output_name}.hex $<TARGET_FILE:${{PROJECT_NAME}}>
      COMMAND ${{CMAKE_FROMELF}} --bin --output
              ${{CMAKE_CURRENT_BINARY_DIR}}/{output_name}.bin $<TARGET_FILE:${{PROJECT_NAME}}>
      COMMENT "fromelf: 生成 hex/bin"
      VERBATIM)
elseif(CMAKE_OBJCOPY)
  add_custom_command(TARGET ${{PROJECT_NAME}} POST_BUILD
      COMMAND ${{CMAKE_OBJCOPY}} -O ihex   $<TARGET_FILE:${{PROJECT_NAME}}> ${{CMAKE_CURRENT_BINARY_DIR}}/{output_name}.hex
      COMMAND ${{CMAKE_OBJCOPY}} -O binary $<TARGET_FILE:${{PROJECT_NAME}}> ${{CMAKE_CURRENT_BINARY_DIR}}/{output_name}.bin
      VERBATIM)
endif()
'''

PRESETS_TMPL = r'''{{
  "version": 3,
  "cmakeMinimumRequired": {{ "major": 3, "minor": 22, "patch": 0 }},
  "configurePresets": [
    {{
      "name": "{preset_name}",
      "displayName": "{target} - Arm Compiler 6 (Release)",
      "generator": "Ninja",
      "binaryDir": "${{sourceDir}}/build/{build_dir}",
      "cacheVariables": {{
        "CMAKE_TOOLCHAIN_FILE": "${{sourceDir}}/cmake/toolchain-armclang.cmake",
        "CMAKE_BUILD_TYPE": "Release"
      }}
    }}
  ],
  "buildPresets": [
    {{ "name": "{preset_name}", "configurePreset": "{preset_name}" }}
  ]
}}
'''


# ---------------------------------------------------------------------------
#  工具函数
# ---------------------------------------------------------------------------
def cmake_path(target_abs, base_dir):
    """生成 CMake 里可用的路径: 同盘符用 ${ROOT_DIR}/相对路径, 跨盘符用绝对路径"""
    target_abs = os.path.normpath(target_abs)
    try:
        r = os.path.relpath(target_abs, base_dir).replace("\\", "/")
        if r.startswith("../"):
            return '"${ROOT_DIR}/%s"' % r
        return '"${ROOT_DIR}/%s"' % r
    except ValueError:
        return '"%s"' % target_abs.replace("\\", "/")


def c_quote(s):
    return '"%s"' % s.replace("\\", "/")


def is_gnu_asm(path):
    try:
        with open(path, "rb") as f:
            data = f.read(60000).decode("utf-8", "replace")
    except OSError:
        return False
    if re.search(r"^\s*AREA\s", data, re.M) or re.search(r"^\s*EXPORT\s", data, re.M):
        return False
    return bool(re.search(r"\.syntax\s+unified|^\s*\.section\s", data, re.M))


# ---------------------------------------------------------------------------
#  主流程
# ---------------------------------------------------------------------------
def build_project(uvprojx, outdir, target=None, do_build=False, force=False):
    p = parse_project(uvprojx, target)
    outdir = os.path.abspath(outdir)

    if not p["use_ac6"] and "ARMCLANG" not in p["armcc"].upper():
        log("警告: 该 Target 用的是 Arm Compiler 5 (AC5), 本脚本按 AC6 生成;")
        log("      AC5 的启动文件/库需要单独处理, 请确认工程里用的是 AC6。")

    keil_bin = find_armclang_bin()
    if keil_bin:
        log("Keil ARMCLANG: %s" % keil_bin)
    else:
        log("警告: 没找到 Keil 的 ARMCLANG 目录, toolchain 文件里会用默认路径, 可后期改。")
        keil_bin = "D:/Program Files/keil/ARM/ARMCLANG/bin"

    c_cpu, asm_cpu, link_cpu = cpu_flags(p)
    opt = OPTIM_MAP.get(p["optim"], "-O1")
    c_std, c_ext_use = V6LANG_MAP.get(p["v6lang"], ("99", False))
    c_ext = "ON" if c_ext_use else "OFF"
    fpu_desc = {0: "无", 2: "单精度", 3: "双精度"}[p["fpu"]]

    # ---- 输出目录 ----
    os.makedirs(outdir, exist_ok=True)
    cmake_dir = os.path.join(outdir, "cmake")
    os.makedirs(cmake_dir, exist_ok=True)

    target_cmake = os.path.join(outdir, "CMakeLists.txt")
    if os.path.exists(target_cmake) and not force:
        raise SystemExit("错误: %s 已存在。确认覆盖请加 --force" % target_cmake)

    # ---- 分散加载文件 ----
    sct_src, sct_kind = find_keil_scatter(p)
    sct_name = "%s.sct" % p["output_name"]
    sct_dst = os.path.join(cmake_dir, sct_name)
    if sct_src:
        shutil.copyfile(sct_src, sct_dst)
        sct_rel = "cmake/%s" % sct_name
        log("分散加载文件: %s (%s)" % (sct_src, sct_kind))
    else:
        with open(sct_dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(gen_scatter(p))
        sct_rel = "cmake/%s" % sct_name
        log("分散加载文件: 由内存布局生成 -> cmake/%s" % sct_name)
        log("  (建议先用 Keil 编译一次, 脚本会直接复用 Keil 生成的 .sct, 更准确)")

    # ---- toolchain 文件 ----
    tc_path = os.path.join(cmake_dir, "toolchain-armclang.cmake")
    tc = TOOLCHAIN_TMPL.format(
        device=p["device"], core=p["core"], fpu_desc=fpu_desc,
        processor=(p["core"] or "Cortex-M4").lower(),
        keil_bin=keil_bin.replace("\\", "/"),
        c_cpu_flags=c_cpu, asm_cpu_flags=asm_cpu, link_cpu_flags=link_cpu,
        opt_release=opt,
    )
    with open(tc_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(tc)

    # ---- CMakeLists.txt ----
    src_lines = []
    for gname, files in p["groups"]:
        src_lines.append("  # ---- %s ----" % (gname or "未命名分组"))
        for ap, ftype in files:
            if ftype == FILE_ASM and is_gnu_asm(ap):
                log("警告: %s 看起来是 GNU 语法汇编, armasm 可能无法汇编" % ap)
            src_lines.append("  " + cmake_path(ap, outdir))
    sources = "\n".join(src_lines)

    def_lines = "\n".join("    " + d for d in p["defines"]) or "    # (无)"
    inc_lines = "\n".join(
        "    " + cmake_path(os.path.normpath(os.path.join(p["dir"], i.replace("\\", "/"))), outdir)
        for i in p["includes"]) or "    # (无)"

    extra_c = ""
    if p["one_elf_section"]:
        extra_c += "\n    $<$<COMPILE_LANGUAGE:C>:-ffunction-sections>"
    if p["short_en"]:
        extra_c += "\n    $<$<COMPILE_LANGUAGE:C>:-fshort-enums>"
    if p["short_wch"]:
        extra_c += "\n    $<$<COMPILE_LANGUAGE:C>:-fshort-wchar>"
    if p["lto"]:
        extra_c += "\n    $<$<COMPILE_LANGUAGE:C>:-flto>"
    if p["misc_c"]:
        extra_c += "\n    $<$<COMPILE_LANGUAGE:C>:%s>" % p["misc_c"]

    extra_ld = ""
    if p["misc_ld"]:
        extra_ld = "\n    " + p["misc_ld"]

    project_name = re.sub(r"[^A-Za-z0-9_]", "_", p["output_name"]) or "app"
    preset_name = "ac6"
    build_dir = "ac6"

    cmake_txt = CMAKE_TMPL.format(
        target=p["target"], device=p["device"], uvprojx_name=os.path.basename(p["uvprojx"]),
        project_name=project_name, output_name=p["output_name"],
        preset_name=preset_name, build_dir=build_dir,
        c_std=c_std, c_ext=c_ext,
        sources=sources, defines=def_lines, includes=inc_lines,
        extra_c_flags=extra_c, extra_ld_flags=extra_ld,
        suffix=".axf", scatter_rel=sct_rel,
    )
    with open(os.path.join(outdir, "CMakeLists.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(cmake_txt)

    with open(os.path.join(outdir, "CMakePresets.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(PRESETS_TMPL.format(preset_name=preset_name, target=p["target"], build_dir=build_dir))

    # ---- 汇报 ----
    n_c = sum(1 for _, fs in p["groups"] for _, t in fs if t == FILE_C)
    n_a = sum(1 for _, fs in p["groups"] for _, t in fs if t == FILE_ASM)
    log("")
    log("Target      : %s (%s)" % (p["target"], p["device"]))
    log("CPU / FPU   : %s / %s" % (p["core"], fpu_desc))
    log("源文件      : %d 个 .c + %d 个 .s, 分 %d 组" % (n_c, n_a, len(p["groups"])))
    log("宏定义      : %s" % ", ".join(p["defines"]) if p["defines"] else "宏定义      : (无)")
    log("头文件目录  : %d 个" % len(p["includes"]))
    log("优化 / 标准 : %s / c%s%s" % (opt, c_std, " + GNU 扩展" if c_ext_use else ""))
    if p["others"]:
        log("跳过(非源码): %s" % ", ".join(os.path.basename(a) for a, _, _ in p["others"]))
    log("")
    log("已生成:")
    log("  %s" % os.path.join(outdir, "CMakeLists.txt"))
    log("  %s" % os.path.join(outdir, "CMakePresets.json"))
    log("  %s" % tc_path)
    log("  %s" % sct_dst)
    log("")
    log("编译:")
    log("  cd %s" % outdir)
    log("  cmake --preset %s" % preset_name)
    log("  cmake --build --preset %s" % preset_name)

    if do_build:
        log("")
        log("===== 开始验证编译 =====")
        bd = os.path.join(outdir, "build", build_dir)
        gen = ["-G", "Ninja"] if shutil.which("ninja") else []
        cmd1 = [cmake_exe(), "-S", outdir, "-B", bd] + gen + \
               ["-DCMAKE_TOOLCHAIN_FILE=" + tc_path, "-DCMAKE_BUILD_TYPE=Release"]
        if run_command(cmd1) != 0:
            raise SystemExit("cmake 配置失败")
        if run_command([cmake_exe(), "--build", bd]) != 0:
            raise SystemExit("编译失败")
        log("验证编译成功: %s" % bd)
    return p


def run_command(cmd, cwd=None):
    """执行外部命令, 逐行把输出喂给 log() (GUI 里可以实时滚动)"""
    log("> " + subprocess.list2cmdline(cmd))
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", bufsize=1)
    except OSError as e:
        log("启动命令失败: %s" % e)
        return -1
    if p.stdout is not None:
        for line in p.stdout:
            line = line.rstrip("\r\n")
            if line:
                log(line)
    return p.wait()


def cmake_exe():
    return shutil.which("cmake") or "cmake"


def target_names(uvprojx):
    """列出工程里所有 Target 名称"""
    try:
        root = ET.parse(uvprojx).getroot()
    except Exception:
        return []
    return [txt(t, "TargetName") for t in root.findall("./Targets/Target")]


def summarize(p):
    """把解析结果整理成 GUI 要显示的一段信息"""
    n_c = sum(1 for _, fs in p["groups"] for _, t in fs if t == FILE_C)
    n_a = sum(1 for _, fs in p["groups"] for _, t in fs if t == FILE_ASM)
    c_std, c_ext = V6LANG_MAP.get(p["v6lang"], ("99", False))
    return {
        "target": p["target"],
        "device": p["device"] or "(未知)",
        "core": p["core"] or "(未知)",
        "fpu": {0: "无 FPU", 2: "单精度", 3: "双精度"}.get(p["fpu"], "?"),
        "sources": "%d 个 .c + %d 个 .s" % (n_c, n_a),
        "groups": str(len(p["groups"])),
        "defines": ", ".join(p["defines"]) if p["defines"] else "(无)",
        "includes": "%d 个目录" % len(p["includes"]),
        "optim": OPTIM_MAP.get(p["optim"], "-O1"),
        "std": "c%s%s" % (c_std, " + GNU 扩展" if c_ext else ""),
        "output_name": p["output_name"],
    }


def main(argv=None):
    _init_console()
    ap = argparse.ArgumentParser(
        description="把 Keil MDK 工程 (.uvprojx) 转换成 CMake 工程 (Arm Compiler 6)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python keil2cmake.py Projects/MDK-ARM/atk_f429.uvprojx\n"
               "  python keil2cmake.py app.uvprojx -o D:/out --target Debug --build\n")
    ap.add_argument("uvprojx", help="Keil 工程文件 (.uvprojx)")
    ap.add_argument("-o", "--outdir", help="输出目录 (默认: .uvprojx 所在目录)")
    ap.add_argument("--target", help="Target 名称 (工程里有多个 Target 时使用)")
    ap.add_argument("--download-keil", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--build", action="store_true", help="生成后立即 cmake 配置并编译验证")
    ap.add_argument("--force", action="store_true", help="输出目录已有 CMakeLists.txt 时强制覆盖")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.uvprojx):
        raise SystemExit("错误: 找不到文件 %s" % args.uvprojx)

    _init_console()
    outdir = args.outdir or os.path.dirname(os.path.abspath(args.uvprojx))
    build_project(args.uvprojx, outdir, target=args.target, do_build=args.build, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
