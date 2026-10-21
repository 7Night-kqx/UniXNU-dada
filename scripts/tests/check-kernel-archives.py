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
    clang.write_text('#!/usr/bin/env python3\n'
                     'import struct, sys\n'
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
