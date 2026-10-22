#!/usr/bin/env python3
"""Generate the minimal dada (Xiaomi 15, SM8750) Apple-format DT for the first boot.

The tree mirrors the proven sheng entry fixture: the same required nodes and properties the source
XNU boot converged on, with pinned dada values taken from the stock device tree
(Resources/DTBs/dada.dtb in the Mu-Silicium checkout) and the Mu-Silicium platform package:

  - GICv3 distributor 0x16000000/0x10000, redistributor 0x16080000/0x200000, stride 0x40000,
    ITS 0x16040000 (interrupt-controller@16000000)
  - CPU timebase 19200000 Hz (timer@16800000, clock-frequency 0x124f800)
  - UFS controller 0x1d84000 and PHY 0x1d80000 (below the arm-io window at 0x16000000)
  - DRAM base 0x80000000; the size is a conservative first-boot value and is cross-checked at
    build time (docs/known-values.md)

This is first-boot bring-up data: the platform has no matching SM8750 platform expert yet, so the
kernel is expected to reach the video console and report the genuine missing-driver panic. No Apple
device identity is claimed; --ipad-identity only selects the identity of the target restore image
(libMobileGestalt prefix-matches hw.product against iPad/iPhone names).
"""
from pathlib import Path
import argparse
import os
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]

DRAM_BASE = 0x80000000
ARMIO_BASE = 0x16000000          # APSS peripheral block that contains the GIC and the UFS controller
ARMIO_SIZE = 0x08000000
GIC_DIST = 0x16000000
GIC_DIST_SIZE = 0x10000
GIC_REDIST = 0x16080000
GIC_REDIST_SIZE = 0x200000
TIMEBASE = 19200000
# Kept for the record: the ADT ufshc node carries no reg, so a future NativeUfs for SM8750 takes these
# windows from board constants (see the sheng note in its ADT tool).
UFS_HC = 0x1D84000
UFS_HC_SIZE = 0x3000
UFS_ICE = 0x1D88000
UFS_ICE_SIZE = 0x18000
UFS_PHY = 0x1D80000
UFS_PHY_SIZE = 0x2000
# The eight stock cores: six at 0x000..0x500, two at 0x10000 and 0x10100 (Aff2 = 1).
CPU_REGS_DEFAULT = '0,0x100,0x200,0x300,0x400,0x500,0x10000,0x10100'


def node(name, properties=(), children=()):
    properties = [('name', name.encode() + b'\0'), *properties]
    result = struct.pack('<II', len(properties), len(children))
    for key, data in properties:
        result += struct.pack('<32sI', key.encode(), len(data))
        result += data + bytes(-len(data) % 4)
    return result + b''.join(children)


def empty_nvram_proxy():
    """A fresh volatile CHRP bank for upstream IODTNVRAM, with no controller."""
    def header(name, size):
        raw = bytearray(struct.pack('<BBH12s', 0x70, 0, size // 16, name))
        checksum = sum(raw)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        raw[1] = checksum
        return raw

    image = bytearray(8192)
    image[:16] = header(b'nvram', 32)
    image[32:48] = header(b'common', 16 + 2048)
    free_at = 32 + 16 + 2048
    image[free_at:free_at + 16] = header(b'free', len(image) - free_at)
    struct.pack_into('<I', image, 16, zlib.adler32(image[20:]))
    return [('nvram-bank-size', struct.pack('<I', len(image))),
            ('nvram-bank-count', struct.pack('<I', 1)),
            ('nvram-current-bank', struct.pack('<I', 0)),
            ('nvram-proxy-data', bytes(image))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'build/dada/boot/kernel.macho.adt')
    parser.add_argument('--dram-size', type=lambda value: int(value, 0), required=True,
                        help='DRAM size reported to XNU, e.g. 0x80000000')
    parser.add_argument('--owned-memory-size', type=lambda value: int(value, 0), default=None,
                        help='Explicit DarwinBoot contiguous window request, 256 MiB..8 GiB in 2 MiB steps')
    parser.add_argument('--root-match-volume-uuid', default=None,
                        help='/chosen/root-matching: select the root by the UUID of an IOMedia')
    parser.add_argument('--unique-chip-id', type=lambda value: int(value, 16), default=None,
                        help='/chosen/unique-chip-id (8 bytes); with --chip-id it seeds XNU IOPlatformUUID')
    parser.add_argument('--chip-id', type=lambda value: int(value, 16), default=None)
    parser.add_argument('--ipad-identity', action='store_true',
                        help='/product and /buttons describe the target restore image identity (iPad7,1) '
                             'without a Home button; the panel and button set are dada\'s')
    parser.add_argument('--cpu-regs', default=CPU_REGS_DEFAULT,
                        help='Comma-separated CPU MPIDR values (the /cpus reg of each core); the first is '
                             'the running boot CPU. The dada stock device tree numbers six cores 0x000..0x500 '
                             'and two cores 0x10000/0x10100 (Aff2 = 1)')
    parser.add_argument('--psci-method', choices=['smc', 'hvc'], default='smc',
                        help='/psci method used by the secondary-CPU start (the board DT declares smc)')
    parser.add_argument('--packed-data-ramdisk', action='store_true',
                        help='Hand off validated DBRDISK1 bytes to the native sparse block driver, without a dense memdev alias')
    parser.add_argument('--data-ramdisk', action='store_true',
                        help='/chosen/memory-map RAMDisk placeholder; the loader fills in the real range for a scratch Data seed')
    parser.add_argument('--trust-cache', action='store_true', help='Zeroed TrustCache sidecar handoff placeholder')
    args = parser.parse_args()
    if (args.unique_chip_id is None) != (args.chip_id is None):
        parser.error('--unique-chip-id and --chip-id must be supplied together')
    if args.unique_chip_id is not None and not (0 < args.unique_chip_id < 1 << 64 and 0 < args.chip_id < 1 << 32):
        parser.error('--unique-chip-id must fit 8 bytes and --chip-id 4 bytes, both nonzero')
    if args.owned_memory_size is not None and (
            not 0x10000000 <= args.owned_memory_size <= 0x200000000
            or args.owned_memory_size % 0x200000):
        parser.error('Owned memory must be 256 MiB to 8 GiB, aligned to 2 MiB')
    if args.dram_size <= 0 or args.dram_size % 0x1000:
        parser.error('--dram-size must be a positive multiple of 4 KiB')

    try:
        cpu_regs = [int(value, 0) for value in args.cpu_regs.split(',')]
    except ValueError:
        parser.error('--cpu-regs must be integers')
    # Up to eight distinct MPIDR values, Aff0 zero (the stock tree numbers whole cores), boot CPU 0 first.
    # The SM8750 numbers its two prime cores above 0xffff (Aff2 = 1), so the bound is the u32 MPIDR, not 16 bits.
    if (not cpu_regs or len(cpu_regs) > 8 or len(set(cpu_regs)) != len(cpu_regs) or cpu_regs[0] != 0
            or any(reg <= 0 or reg > 0xffffffff or reg & 0xff for reg in cpu_regs[1:])):
        parser.error('--cpu-regs: up to eight distinct MPIDR values, Aff0 zero, boot CPU 0 first')
    cpus = [node('cpu%d' % index, [('state', b'running\0' if index == 0 else b'waiting\0'),
                                   ('reg', struct.pack('<I', reg)),
                                   ('device_type', b'cpu\0'), ('compatible', b'arm,armv8\0'),
                                   ('timebase-frequency', struct.pack('<I', TIMEBASE))])
            for index, reg in enumerate(cpu_regs)]
    tree = node('device-tree', [
        # hw.product/hw.machine/hw.target come from the root 'model'/'target-type' (IODTPlatformExpert);
        # libMobileGestalt prefix-matches hw.product against iPhone/iPod/iPad.
        ('model', b'iPad7,1\0' if args.ipad_identity else b'dada-first-boot\0'),
        *([('target-type', b'J120\0')] if args.ipad_identity else []),
        # Root compatible strings of the stock board DT (xiaomi,dada / qcom,sm8750).
        ('compatible', b'xiaomi,dada\0qcom,sm8750\0'),
        ('#address-cells', struct.pack('<I', 2)),
        ('#size-cells', struct.pack('<I', 2)),
    ], [
        node('chosen', [('random-seed', os.urandom(64)),
                        # Without these XNU never publishes IOPlatformUUID and gethostuuid() blocks forever
                        # for callers (configd) that pass a zero timeout.
                        *([('unique-chip-id', struct.pack('<Q', args.unique_chip_id)), ('chip-id', struct.pack('<I', args.chip_id))]
                          if args.unique_chip_id is not None else []),
                        ('osenvironment', b'main\0'),
                        # Security status as on a dev-fused, undemoted board (tuple 0,0,1,1), as read by MobileGestalt.
                        *([('certificate-production-status', struct.pack('<I', 0)), ('effective-production-status-ap', struct.pack('<I', 0)),
                           ('certificate-security-mode', struct.pack('<I', 1)), ('effective-security-mode-sep', struct.pack('<I', 1))]
                          if args.ipad_identity else []),
                        *([('ephemeral-storage', struct.pack('<I', 1))] if (args.data_ramdisk or args.packed_data_ramdisk) else []),
                        *([('darwinboot-memory-size', struct.pack('<Q', args.owned_memory_size))]
                          if args.owned_memory_size else []),
                        ('dram-base', struct.pack('<Q', DRAM_BASE)),
                        ('dram-size', struct.pack('<Q', args.dram_size)),
                        # Image4 hash algorithm of the target iPad DT; selects the algorithm only.
                        ('crypto-hash-method', b'sha2-384\0'),
                        *([('root-matching', ('<dict><key>IOProviderClass</key><string>IOMedia</string>'
                                              '<key>IOPropertyMatch</key><dict><key>UUID</key><string>' +
                                              args.root_match_volume_uuid.upper() + '</string></dict></dict>\0').encode())]
                          if args.root_match_volume_uuid else []),
                        # No amfi-only-platform-code: that property is read by the original AMFI, and this
                        # board does not prelink AMFI (see board.toml [kernel.prelink] roots). Without
                        # AMFI there is no platform-code policy to restrict.
                        *empty_nvram_proxy()],
             [node('memory-map', ([('PackedRAMDisk' if args.packed_data_ramdisk else 'RAMDisk', bytes(16))]
                                  if (args.data_ramdisk or args.packed_data_ramdisk) else []) +
                                 ([('TrustCache', bytes(16))] if args.trust_cache else [])),
              # Read by the original AppleImage4 at start-up; present and empty here.
              node('manifest-properties')]),
        node('cpus', children=cpus),
        # More than one CPU adds the PSCI conduit the secondary start uses.
        *([node('psci', [('compatible', b'arm,psci-1.0\0'), ('method', args.psci_method.encode() + b'\0')])]
          if len(cpus) > 1 else []),
        # Suspend/resume is not implemented for this board.
        node('defaults', [('sleep-disabled', struct.pack('<I', 1))]),
        node('options', []),
        *([node('buttons', [('compatible', b'buttons\0'), ('device_type', b'buttons\0'),
                            # The phone has no Home button: voldown/volup/hold only. 2 = the
                            # non-mechanical answer BSPlatform passes through.
                            ('button-names', b'hold\0voldown\0volup\0'),
                            ('home-button-type', struct.pack('<I', 2))])] if args.ipad_identity else []),
        node('product', [('product-name', b'iPad Pro (12.9-inch) (2nd generation)\0' if args.ipad_identity else b'Xiaomi 15\0'),
                         ('model', b'xiaomi,dada\0'),
                         *([('product-description', b'iPad Pro (12.9-inch) (2nd generation)\0'),
                             ('artwork-device-idiom', b'pad\0'),
                             ('artwork-device-subtype', struct.pack('<I', 2732)),
                             ('artwork-scale-factor', struct.pack('<I', 2)),
                             ('artwork-display-gamut', b'P3\0'),
                             ('compatible-device-fallback', b'iPad6,7\0'),
                             ('thin-bezel', struct.pack('<I', 1))] if args.ipad_identity else [])]),
        # The panel is 1200x2670 (dada.dsc PcdFrameBuffer*); the artwork fields above describe the target
        # restore image identity, not the phone panel.
        node('arm-io', [('device_type', b'arm-io\0'),
                        ('#address-cells', struct.pack('<I', 2)),
                        ('#size-cells', struct.pack('<I', 2)),
                        ('ranges', struct.pack('<QQQ', 0, ARMIO_BASE, ARMIO_SIZE))],
             [node('intc', [('compatible', b'arm,gic-v3\0'),
                            ('reg', struct.pack('<4Q',
                                                GIC_DIST - ARMIO_BASE, GIC_DIST_SIZE,
                                                GIC_REDIST - ARMIO_BASE, GIC_REDIST_SIZE)),
                            ('redistributor-stride', struct.pack('<Q', 0x40000))]),
              # The UFS controller (0x1d84000), ICE (0x1d88000) and PHY (0x1d80000) lie inside the arm-io
              # window, but the node exists only for class matching: a future NativeUfs for SM8750 takes its
              # physical windows from board constants, as the sheng driver does.
              node('ufshc', [('compatible', b'qcom,sm8750-ufshc\0qcom,ufshc\0')])]),
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(tree)
    print(f'dada ADT: {len(tree)} bytes, {len(cpus)} CPUs, dram {args.dram_size:#x}, timebase {TIMEBASE}')
    if args.root_match_volume_uuid == '00000000-0000-0000-0000-000000000000':
        print('dada ADT: warning: root-matching UUID is the all-zero placeholder; the kernel will not mount a root')


if __name__ == '__main__':
    main()
