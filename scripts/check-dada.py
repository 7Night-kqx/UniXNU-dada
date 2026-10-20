#!/usr/bin/env python3
"""Firmware preflight for the Xiaomi 15 (dada) Mu-Silicium port.

The firmware step runs this before EDK II builds. It checks that the values this repository pins still
match the Mu-Silicium checkout, and it reports the device values that are still placeholders.

Hard errors (the build stops):
  - the UEFI FD descriptor in MemoryMapLib.c does not match Resources/Configs/dada.toml
  - the CPU stack descriptor does not match PcdCPUCoresStackBase in dada.dsc
  - a memory descriptor is not 4 KiB aligned, or two descriptors overlap
  - a Binaries/dada path named by Include/*.inc is missing
  - the board DT that the boot image needs is missing
  - this repository's [firmware] config/dsc points somewhere else

Warnings (printed, build continues) are measurements that belong to a later stage: the userdata
window, the root volume UUID, the trust cache pins and the UFS MMIO window.
"""
from pathlib import Path
import argparse
import json
import os
import re
import tomllib

DESCRIPTOR = re.compile(r'\{"([^"]+)"\s*,\s*(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),([^}]+)\}')
UFS_WINDOW = (0x01D80000, 0x00028000)   # ufsphy_mem@1d80000 .. 0x1da8000 (ufshc, ICE, MCQ)
PLATFORM = Path('Platforms/Xiaomi/dadaPkg')


def is_uefi(path):
    return (Path(path) / PLATFORM / 'dada.dsc').is_file()


def find_uefi(given):
    """The driver runs this from the build tree without exporting --uefi, so probe in name order and
    then look next to the device repository (the Mu-Silicium checkout is stored beside it)."""
    candidates = [given, os.environ.get('UNIXNU_UEFI'), 'uefi-dada', '../uefi-dada',
                  'Mu-Silicium', '../Mu-Silicium', '../../Mu-Silicium']
    for candidate in candidates:
        if candidate and is_uefi(candidate):
            return Path(candidate).resolve()
    device = find_device()
    if device:
        siblings = sorted(path for path in device.parent.iterdir() if path.is_dir() and is_uefi(path))
        preferred = [path for path in siblings if path.name in ('Mu-Silicium', 'uefi-dada', 'uefi-' + device.name)]
        for path in preferred or siblings:
            return path.resolve()
        if siblings:
            raise SystemExit(f'preflight: several UEFI checkouts next to {device}; pass --uefi DIR')
    raise SystemExit('preflight: cannot find the Mu-Silicium dada checkout; pass --uefi DIR or set UNIXNU_UEFI')


def find_device():
    """The device repository, so board.toml placeholders can be reported."""
    given = os.environ.get('UNIXNU_DEVICE')
    if given and (Path(given) / 'board.toml').is_file():
        return Path(given).resolve()
    here = Path.cwd().resolve()
    for base in (here, *here.parents):
        state = base / 'state.json'
        if state.is_file():
            try:
                device = json.loads(state.read_text()).get('device')
            except ValueError:
                device = None
            if device and (Path(device) / 'board.toml').is_file():
                return Path(device).resolve()
        if (base / 'board.toml').is_file():
            return base
    return None


def normalize(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uefi', metavar='DIR', help='Mu-Silicium checkout (default: probe the usual places)')
    args = parser.parse_args()

    uefi = find_uefi(args.uefi)
    package = uefi / PLATFORM
    config = tomllib.loads((uefi / 'Resources/Configs/dada.toml').read_text())
    source = (package / 'Library/MemoryMapLib/MemoryMapLib.c').read_text()
    rows = [(match[1], int(match[2], 16), int(match[3], 16), match[4]) for match in DESCRIPTOR.finditer(source)]
    by_name = {normalize(name): (name, base, size, attrs) for name, base, size, attrs in rows}

    errors, warnings = [], []
    if not rows:
        errors.append('MemoryMapLib.c: no memory descriptors found')
    if len(rows) > 128:
        errors.append(f'MemoryMapLib.c: {len(rows)} descriptors exceed the 128-entry capacity')
    for name, base, size, _ in rows:
        if base % 4096 or size % 4096 or not size:
            errors.append(f'MemoryMapLib.c: {name} is not 4 KiB aligned ({base:#x}+{size:#x})')
    for index, (name, base, size, _) in enumerate(rows):
        for other, other_base, other_size, _ in rows[index + 1:]:
            if max(base, other_base) < min(base + size, other_base + other_size):
                errors.append(f'MemoryMapLib.c: {name} overlaps {other}')

    fd = config['uefi_fd']
    fd_row = by_name.get('uefifd')
    if not fd_row:
        errors.append('MemoryMapLib.c: no UEFI FD descriptor')
    elif (fd_row[1], fd_row[2]) != (fd['base'], fd['size']):
        errors.append(f'MemoryMapLib.c: UEFI FD is {fd_row[1]:#x}+{fd_row[2]:#x}, '
                      f'Resources/Configs/dada.toml says {fd["base"]:#x}+{fd["size"]:#x}')
    if fd['size'] // 0x1000 <= 0:
        errors.append('Resources/Configs/dada.toml: FD size is smaller than one block')

    dsc = (package / 'dada.dsc').read_text()
    stack = re.search(r'PcdCPUCoresStackBase\|(0x[0-9a-fA-F]+)', dsc)
    stack_row = by_name.get('uefistack')
    if not stack:
        errors.append('dada.dsc: no PcdCPUCoresStackBase')
    elif not stack_row:
        errors.append('MemoryMapLib.c: no UEFI stack descriptor')
    elif stack_row[1] != int(stack[1], 16):
        errors.append(f'MemoryMapLib.c: UEFI stack is {stack_row[1]:#x}, dada.dsc says {int(stack[1], 16):#x}')

    dependencies = set()
    for inc in (package / 'Include').glob('*.inc'):
        dependencies.update(re.findall(r'Binaries/dada/[^\s}]+', inc.read_text()))
    missing = sorted(path for path in dependencies if not (uefi / path).is_file())
    for path in missing:
        errors.append(f'vendor input missing: {path}')

    dtb = uefi / 'Resources/DTBs/dada.dtb'
    if not dtb.is_file():
        blob = sorted((package / 'FdtBlob').glob('*.dtb'))
        if blob:
            warnings.append(f'Resources/DTBs/dada.dtb is missing; bootimg needs it (FdtBlob has {blob[0].name})')
        else:
            errors.append('no dada device tree: Resources/DTBs/dada.dtb and Platforms/Xiaomi/dadaPkg/FdtBlob/*.dtb are both missing')

    ufs_covered = False
    start, length = UFS_WINDOW
    for name, base, size, _ in rows:
        if base <= start and start + length <= base + size:
            ufs_covered = True
            break
    if not ufs_covered:
        warnings.append(f'MemoryMapLib.c: no MMIO window covers UFS {start:#x}+{length:#x}; '
                        'UFS registers would fault once the UEFI driver touches them')

    board = None
    device = find_device()
    if device:
        board = tomllib.loads((device / 'board.toml').read_text())
        firmware = board.get('firmware', {})
        if firmware.get('config') != 'dada':
            errors.append(f"board.toml: [firmware] config is {firmware.get('config')!r}, expected 'dada'")
        if firmware.get('dsc') != 'Platforms/Xiaomi/dadaPkg/dada.dsc':
            errors.append(f"board.toml: [firmware] dsc is {firmware.get('dsc')!r}, expected Platforms/Xiaomi/dadaPkg/dada.dsc")
        if not (uefi / str(firmware.get('dsc', ''))).is_file():
            errors.append(f"board.toml: [firmware] dsc does not exist in {uefi}")
        userdata = board.get('userdata', {})
        if not userdata.get('first_lba') or not userdata.get('last_lba'):
            warnings.append('board.toml: [userdata] first_lba/last_lba are not measured yet '
                            '(needed by "unixnu build userdata" and the root stage)')
        adt = board.get('payload', {}).get('adt', {})
        if str(adt.get('root_match_volume_uuid', '')).strip('0-') == '':
            warnings.append('board.toml: [payload.adt] root_match_volume_uuid is still the all-zero placeholder '
                            '(the kernel will not mount a root)')
        if not board.get('payload', {}).get('trust_cache_sha256'):
            warnings.append('board.toml: [payload] trust_cache_sha256 is empty; --trust-cache will accept any bundle')
        if not board.get('ipsw', {}).get('product'):
            warnings.append('board.toml: [ipsw] is incomplete')

    report = {
        'uefi': str(uefi),
        'device_repository': str(device) if device else None,
        'descriptors': len(rows),
        'vendor_inputs_checked': len(dependencies),
        'fd': {'base': hex(fd['base']), 'size': hex(fd['size'])},
        'ufs_window_covered': ufs_covered,
        'errors': errors,
        'warnings': warnings,
        'hardware_boot_demonstrated': False,
    }
    out = Path('build/dada')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'check.json').write_text(json.dumps(report, indent=2) + '\n')

    print(f'preflight: {len(rows)} memory descriptors, {len(dependencies)} vendor inputs, UEFI FD {fd["base"]:#x}+{fd["size"]:#x}')
    for warning in warnings:
        print(f'preflight: warning: {warning}')
    for error in errors:
        print(f'preflight: error: {error}')
    print(f'preflight: {len(errors)} error(s), {len(warnings)} warning(s)')
    raise SystemExit(bool(errors))


if __name__ == '__main__':
    main()
