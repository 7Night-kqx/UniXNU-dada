#!/usr/bin/env python3
"""Check that the kernel config, the archive builders and their products agree.

The kernel step of this port is expensive and needs macOS, so the parts that can be checked anywhere are
checked here instead:

  1. every CONFIG_XNU_* this defconfig turns on has a builder in board.toml [kernel] archives, and every
     switch it turns off has none (build-xnu-4k-baseline.sh links each enabled archive with -force_load
     and fails on a missing one);
  2. the builders put their object in the directory the kernel script reads, so the archive it tests for
     is the archive that gets made;
  3. build-arm-driver.py compiles NativeUfs without NATIVE_VERIFIED_BLOCK unless --verified-root is
     given. Verified block mode refuses to publish the device without an owner-signed policy bundle
     ("policy count invalid"), and this port has no bundle, so the default must be the plain read path.

Check 3 runs the real builder against a synthetic compile record, so it exercises the argument guards and
the emitted command line without Xcode. Exit status is 0 when every check passes.
"""
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

HERE = Path(__file__).resolve()
DEVICE = HERE.parents[2]

# CONFIG_XNU_<NAME> -> the archive build-xnu-4k-baseline.sh demands when it is 1, and the builder that makes it.
ARCHIVES = {
    'XNU_SOURCE_CORECRYPTO14': ('build/corecrypto-2023/libcorecrypto_kernel_noasm-namespaced.a',
                                ('build-corecrypto-14.py', None)),
    'XNU_SOURCE_PTHREAD': ('build/libpthread-kernel/libpthread_kernel.a',
                           ('build-libpthread-kernel.py', None)),
    'XNU_ARM_DT_PLATFORM': ('build/armdt-platform/libArmDTPlatform.a',
                            ('build-arm-driver.py', 'ArmDTPlatform')),
    'XNU_ARMCPU': ('build/armcpu/libArmCpu.a', ('build-arm-driver.py', 'ArmCpu')),
    'XNU_NATIVE_UFS': ('build/native-ufs/libNativeUfs.a', ('build-arm-driver.py', 'NativeUfs')),
    'XNU_NATIVE_AES': ('build/native-aes/libNativeAES.a', ('build-arm-driver.py', 'NativeAES')),
    'XNU_BOOT_PROGRESS': ('build/boot-progress/libBootProgress.a', ('build-boot-progress.py', None)),
    'XNU_NATIVE_LEGACY_DISPLAY': ('build/native-legacy-display/libNativeLegacyDisplay.late.a',
                                  ('build-native-legacy-display.py', None)),
}

failures = []
passes = []

# (switch, builders the switch requires together, archive the kernel script links, switch that makes
# them unnecessary)
#
# The storage transport derives from IOBlockStorageDevice, a class of Apple's separate IOStorageFamily
# project that the kernelcache already carries as com.apple.iokit.IOStorageFamily. The base class must
# NOT be compiled and linked: a second class of that name makes OSMetaClass panic ("class
# IOBlockStorageDevice is a duplicate; kext __kernel__ already has a class by that name").
#
# So the transport needs two things instead of a linked base class:
#   - build-storage-classes.py compiles the pinned reference object, which names the 190 dispatch slots;
#   - build-storage-kext-bindings.py rebinds the transport's imports to the prelinked kext's own
#     addresses, and rebuilds libNativeUfs.a around the bound object.
# Neither is needed in probe-only mode, where the transport has no block-device half at all.
REQUIRED_TOGETHER = (
    ('XNU_NATIVE_UFS', (('build-storage-classes.py', None),
                        ('build-storage-kext-bindings.py', None)),
     'build/native-ufs/libNativeUfs.a', 'XNU_NATIVE_UFS_PROBE_ONLY'),
)


def check(ok, message):
    (passes if ok else failures).append(message)
    return ok


def defconfig_switches(path):
    switches = {}
    for line in path.read_text().splitlines():
        match = re.match(r'\s*CONFIG_(\w+)\s*=\s*(\S+)', line)
        if match:
            switches[match.group(1)] = match.group(2).strip('"')
    return switches


def check_archives(device, tree):
    board_path = device / 'board.toml'
    if not check(board_path.is_file(), f'{board_path} exists'):
        return
    board = tomllib.load(board_path.open('rb'))
    entries = board.get('kernel', {}).get('archives')
    if not check(isinstance(entries, list) and entries, 'board.toml: [kernel] archives is a non-empty list'):
        return
    listed = set()
    for entry in entries:
        parts = entry.split()
        listed.add((parts[0], parts[1] if len(parts) > 1 and not parts[1].startswith('-') else None))

    switches = defconfig_switches(device / 'configs' / board['kernel']['defconfig'])
    script = (tree / 'scripts/build-xnu-4k-baseline.sh').read_text() if tree else ''
    for name, (archive, builder) in ARCHIVES.items():
        value = switches.get(name, 'n')
        enabled = value == 'y'
        has_builder = builder in listed
        if enabled:
            check(has_builder, f'{name}=y has a builder ({builder[0]} {builder[1] or ""})'.replace('  ', ' '))
            if script:
                check(archive in script, f'{name} archive {archive} is the one the kernel script reads')
        else:
            check(not has_builder, f'{name}=n lists no builder')

    # Some archives exist only to supply symbols a selected one needs. The storage transports derive from
    # IOBlockStorageDevice, which is a class of Apple's separate IOStorageFamily project rather than of the
    # xnu tree, so a kernel that links a transport needs the base class compiled and linked before it --
    # otherwise the link stops with "vtable for IOBlockStorageDevice" undefined. A builder listed here must
    # be present exactly when its switch is on, and the kernel script must read that archive.
    for switch, builders, archive, *off in REQUIRED_TOGETHER:
        needed = switches.get(switch, 'n') == 'y'
        if off and switches.get(off[0], 'n') == 'y':
            needed = False
        for builder in builders:
            check((builder in listed) == needed,
                  f'{switch}={"y" if needed else "n"} lists {builder[0]} {"and" if needed else "only when it does not"} '
                  f'needs it')
        if needed and script:
            check(archive in script, f'{switch}=y links {archive}')


def check_products(device, builder):
    """The object directory of each component must be the one build-xnu-4k-baseline.sh reads."""
    text = builder.read_text()
    start = text.index("out = ROOT / 'build' / {")
    directory = dict(re.findall(r"'(\w+)':\s*'([\w-]+)'", text[start:text.index('}[component]', start)]))
    for component, archive in (('ArmDTPlatform', 'build/armdt-platform/libArmDTPlatform.a'),
                               ('ArmCpu', 'build/armcpu/libArmCpu.a'),
                               ('NativeAES', 'build/native-aes/libNativeAES.a'),
                               ('NativeUfs', 'build/native-ufs/libNativeUfs.a')):
        made = f"build/{directory.get(component)}/lib{component}.a"
        check(made == archive, f'{component} builds {archive} (mapping says {made})')


def make_fixture(root, builder):
    """A build tree just big enough for build-arm-driver.py to emit a command line.

    The compiler and xcrun are stubs, so this runs on any host: the check is about which macros and output
    paths the builder emits, not whether the driver compiles (that needs the iOS SDK). The stub still writes
    a real ARM64 object header with an iOS 17 LC_BUILD_VERSION, because the builder audits its own output.
    """
    (root / 'scripts').mkdir(parents=True)
    shutil.copy2(builder, root / 'scripts/build-arm-driver.py')
    bin_dir = root / 'stub-bin'
    bin_dir.mkdir()

    clang = bin_dir / 'clang'
    # A compiler that behaves like the one CI actually resolves: upstream clang, which has no
    # -Wunnecessary-virtual-specifier (an Apple-only option). Like the real one it reports an unknown
    # -Wno- option as an error when -Werror is in effect, and stays quiet when the command also says
    # -Wno-unknown-warning-option. Without that behaviour the fixture would accept any flag and this check
    # could never notice a flag the compiler does not know.
    clang.write_text('#!/usr/bin/env python3\n'
                     'import struct, sys\n'
                     'arguments = sys.argv[1:]\n'
                     'known = {"-Wno-unused-parameter", "-Wno-c99-designator", "-Wno-reorder-init-list",\n'
                     '         "-Wno-deprecated-enum-enum-conversion", "-Wno-unknown-warning-option"}\n'
                     'unknown = [a for a in arguments if a.startswith("-Wno-") and a not in known]\n'
                     'if unknown and "-Werror" in arguments and "-Wno-unknown-warning-option" not in arguments:\n'
                     '    print(f"error: unknown warning option \'{unknown[0]}\' [-Werror,-Wunknown-warning-option]",\n'
                     '          file=sys.stderr)\n'
                     '    raise SystemExit(1)\n'
                     'out = sys.argv[sys.argv.index("-o") + 1]\n'
                     'header = struct.pack("<8I", 0xfeedfacf, 0x100000c, 0, 1, 1, 24, 0, 0)\n'
                     'build = struct.pack("<6I", 0x32, 24, 2, 0x110000, 0, 0)\n'
                     'open(out, "wb").write(header + build)\n')
    clang.chmod(0o755)

    xcrun = bin_dir / 'xcrun'
    xcrun.write_text('#!/usr/bin/env python3\n'
                     'import sys\n'
                     'arguments = sys.argv[1:]\n'
                     'if arguments[:2] == ["ar", "rcs"]:\n'
                     '    open(arguments[2], "wb").close()\n')
    xcrun.chmod(0o755)

    record = root / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
    record.parent.mkdir(parents=True)
    source = root / 'iokit/IOPlatformExpert.cpp'
    source.parent.mkdir(parents=True)
    source.write_text('int reference;\n')
    json.dump({'directory': str(record.parent), 'file': str(source),
               'arguments': [str(clang), '-c', str(source), '-o', 'IOPlatformExpert.o', '-I', str(source.parent)]},
              record.open('w'))
    for component in ('NativeUfs', 'NativeAES', 'NativeVirtioBlock', 'ArmCpu'):
        (root / 'drivers' / component).mkdir(parents=True)
        (root / 'drivers' / component / f'{component}.cpp').write_text(f'int {component};\n')
    for directory in ('kits/VerifiedRoot/include', 'drivers/NativeVirtioBlock', 'build/storage-abi/include',
                      '.cache/xnu-work/osfmk'):
        (root / directory).mkdir(parents=True, exist_ok=True)


def run_builder(root, *arguments):
    environment = dict(os.environ, PATH=f'{root / "stub-bin"}{os.pathsep}{os.environ.get("PATH", "")}')
    return subprocess.run([sys.executable, str(root / 'scripts/build-arm-driver.py'), *arguments],
                          cwd=root, capture_output=True, text=True, env=environment)


def emitted_defines(root, directory):
    record = root / 'build' / directory / 'compile-command.json'
    if not record.is_file():
        return set()
    return set(re.findall(r'-DNATIVE_VERIFIED_BLOCK[\w]*(?:=\d+)?', record.read_text()))


def emitted_command(root, directory):
    record = root / 'build' / directory / 'compile-command.json'
    return record.read_text() if record.is_file() else ''


def check_verified_mode(builder):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        make_fixture(root, builder)

        result = run_builder(root, 'NativeUfs')
        check(result.returncode == 0, f"build-arm-driver.py NativeUfs runs (exit {result.returncode})")
        defines = emitted_defines(root, 'native-ufs')
        check(not defines, f'NativeUfs without --verified-root defines no verified block macro (got {sorted(defines)})')
        check(not (root / 'build/native-virtio-block-verified').exists(),
              'NativeUfs keeps its own output directory (build/native-ufs)')
        # NativeUfs includes <IOKit/storage/IOBlockStorageDevice.h>, which the xnu headers do not carry: the
        # build tree stages the public IOStorageFamily headers in build/storage-abi/include (the base
        # scripts/unixnu/kernel.py does it before the archive builders) and the command line must point there.
        storage = str(root / 'build/storage-abi/include')
        command = json.loads(emitted_command(root, 'native-ufs'))
        check(any(a == '-I' and b == storage for a, b in zip(command, command[1:])),
              'NativeUfs compiles with the staged IOStorageFamily include directory')

        # The probe-only transport has no block-device half, so it must not pull in Apple's
        # IOStorageFamily headers at all: the base class is not built into the kernel in that mode
        # (the kernelcache's own kext keeps the class name), and the driver must link without it.
        result = run_builder(root, 'NativeUfs', '--probe-only')
        command = json.loads(emitted_command(root, 'native-ufs'))
        check(result.returncode == 0 and '-DXNU_NATIVE_UFS_PROBE_ONLY=1' in command
              and not any(a == '-I' and b == storage for a, b in zip(command, command[1:])),
              'NativeUfs --probe-only compiles without the IOStorageFamily include directory')

        result = run_builder(root, 'NativeUfs', '--verified-root')
        defines = emitted_defines(root, 'native-ufs')
        check(result.returncode == 0 and '-DNATIVE_VERIFIED_BLOCK=1' in defines,
              f'NativeUfs --verified-root defines the verified block macro (got {sorted(defines)})')

        result = run_builder(root, 'NativeUfs', '--verified-root', '--verified-root-bundle')
        defines = emitted_defines(root, 'native-ufs')
        check(result.returncode == 0 and '-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1' in defines,
              f'NativeUfs --verified-root-bundle defines the bundle macro (got {sorted(defines)})')

        # The virtio transport is the original owner of the verified qualification directories.
        result = run_builder(root, 'NativeVirtioBlock', '--verified-root')
        check(result.returncode == 0 and (root / 'build/native-virtio-block-verified').is_dir(),
              'NativeVirtioBlock --verified-root still writes build/native-virtio-block-verified')

        # Guards that existed before this port and must not have been relaxed.
        result = run_builder(root, 'NativeAES', '--verified-root')
        check(result.returncode != 0 and 'read-only block transport' in result.stderr,
              'a non-storage component still refuses --verified-root')
        result = run_builder(root, 'NativeUfs', '--verified-root-bundle')
        check(result.returncode != 0 and 'requires verified block mode' in result.stderr,
              'a bundle still requires --verified-root')


def conditional_depth(path):
    """Return, for each line, how many #if blocks it is inside."""
    depths = []
    depth = 0
    for line in path.read_text().splitlines():
        depths.append(depth)
        stripped = line.strip()
        if stripped.startswith('#if'):
            depth += 1
        elif stripped.startswith('#endif'):
            depth -= 1
    return depths


def guarded_definitions(lines, depths):
    """Names declared at file scope inside a conditional guard, with the line that declares them.

    Only unindented declarations count. A call inside a guarded block is a use, not a definition, and
    leaving it in the set would hide the very defect this looks for: a call site at the guard's own
    depth would make the declaration look guarded all the way through.
    """
    declared = {}
    for index, line in enumerate(lines):
        if not depths[index] or line[:1].isspace() or line.lstrip().startswith(('//', '*', '#')):
            continue
        match = re.match(r'\s*(?:volatile\s+|static\s+|inline\s+|extern\s+|const\s+|unsigned\s+|struct\s+|class\s+|'
                         r'[A-Za-z_]\w*[\s*&]+)+([A-Za-z_]\w*)\s*(?:=|\()', line)
        if match:
            declared.setdefault(match.group(1), index)
            # "volatile uint64_t a = 0, b = 0;" declares b on the same line, and a symbol that is declared
            # only here is exactly what this check is about.
            for extra in re.findall(r',\s*([A-Za-z_]\w*)\s*(?==|,|;)', line):
                declared.setdefault(extra, index)
    return declared


def check_verified_symbols(device):
    """A symbol declared only under NATIVE_VERIFIED_BLOCK must not be used outside that guard.

    The plain NativeUfs build -- the one board.toml [kernel] archives lists, without --verified-root --
    does not define NATIVE_VERIFIED_BLOCK, so an unguarded use of timerTicks() or native_ufs_read_ticks
    fails with "use of undeclared identifier". The CI compile reported exactly that, three errors in
    total, while the declaration sat twenty lines above inside the guard.

    This is a preprocessing question, not a compiler question, so it needs no Xcode.
    """
    for source in sorted((device / 'drivers').glob('*/*.cpp')):
        lines = source.read_text().splitlines()
        depths = conditional_depth(source)
        relative = source.relative_to(device)
        for name, declared_at in sorted(guarded_definitions(lines, depths).items()):
            if len(name) < 4:
                continue
            uses = [index + 1 for index, line in enumerate(lines)
                    if index != declared_at and depths[index] == 0
                    and not line.lstrip().startswith(('//', '*'))
                    and re.search(rf'\b{re.escape(name)}\b', line)]
            check(not uses,
                  f'{relative}: {name} is declared only under NATIVE_VERIFIED_BLOCK (line {declared_at + 1}) '
                  f'but used outside it: lines {uses}')


def check_binding_slide(device):
    """The storage bindings must take the prelink slide, and must only move the kext's own addresses.

    build-storage-kext-bindings.py reads the original kernelcache, but the transport is linked into the
    kernel that emit_prelink.py has already rebased: it moves every component segment to "original va +
    slide". A `.set` of a kext-owned slot is an absolute address, so it has to move by that slide too, or
    every call lands 0x1000000 below the real function. Kernel exports must not move: prelink resolves
    those against the newly built kernel, so this image only supplies the name.

    These are source properties (no kernelcache and no Xcode needed), which is why they can be checked here.
    """
    script = device.parent / 'UniXNU/scripts/build-storage-kext-bindings.py'
    if not script.is_file():
        # A checkout that only has the device repository: the base tree is not next to it.
        return
    text = script.read_text()
    check("'--slide'" in text or '--slide' in text,
          'build-storage-kext-bindings.py accepts the prelink slide')
    check("UNIXNU_PRELINK_SLIDE" in text,
          'build-storage-kext-bindings.py falls back to UNIXNU_PRELINK_SLIDE from the kernel step')
    check('inside_kext' in text,
          'build-storage-kext-bindings.py decides by "inside the kext", not by export-table membership')
    # The assembly is written from `bound` only; if the kernel-export slots leaked into it they would be
    # pinned to the original image's layout.
    check(".set {name}, {address:#x}" in text and 'for name, address in sorted(bound.items())' in text,
          'build-storage-kext-bindings.py writes .set only for kext-owned slots')
    kernel = device.parent / 'UniXNU/scripts/unixnu/kernel.py'
    if kernel.is_file():
        check('UNIXNU_PRELINK_SLIDE' in kernel.read_text(),
              'the kernel step hands the board prelink slide to the bindings builder')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--device', type=Path, help='device repository (default: the one this script is in)')
    parser.add_argument('--tree', type=Path, help='assembled build tree, to check the kernel script too')
    arguments = parser.parse_args()

    device = arguments.device or DEVICE
    # Inside the assembled tree this script lives in scripts/tests, but board.toml only exists in the device
    # repository: reach it through the tree's recorded device, or through the directory it was copied from.
    if not (device / 'board.toml').is_file():
        for base in device.parents[:3]:
            state = base / 'state.json'
            recorded = json.loads(state.read_text()).get('device') if state.is_file() else None
            if recorded and (Path(recorded) / 'board.toml').is_file():
                device = Path(recorded)
                break
    builder = device / 'scripts/build-arm-driver.py'
    if not check(builder.is_file(), f'{builder.name} is in the build tree'):
        report()
        return 1

    # The firmware preflight runs from the assembled tree, so the kernel script is right there. Without it
    # the archive-path checks are skipped and only the config and builder checks run.
    tree = arguments.tree
    if tree is None:
        candidate = Path.cwd()
        if (candidate / 'scripts/build-xnu-4k-baseline.sh').is_file():
            tree = candidate

    check_archives(device, tree)
    check_products(device, builder)
    check_verified_mode(builder)
    check_verified_symbols(device)
    check_binding_slide(device)
    return report()


def report():
    for message in passes:
        print(f'PASS {message}')
    for message in failures:
        print(f'FAIL {message}')
    print(f'{len(passes)} passed, {len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
