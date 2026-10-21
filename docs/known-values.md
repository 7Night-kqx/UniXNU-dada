# Known values and open measurements

Every number `board.toml` and `scripts/make-dada-adt.py` pin, where it comes from, and what still has
to be read on the phone. Values are from the **stock** device tree
(`Mu-Silicium/Resources/DTBs/dada.dtb`), the Mu-Silicium platform package, or the mainline bring-up
tree (`dada_kernel/device-tree/sm8750-xiaomi-dada-mainline.dts`); the two trees agree on every
address below.

## Memory map

| Region | Address | Size | Source |
| --- | --- | --- | --- |
| UEFI FD | `0xA7100000` | `0x300000` | `Resources/Configs/dada.toml`, `MemoryMapLib.c` `UEFI_FD` |
| FD reserved (before) | `0xA7000000` | `0x100000` | `MemoryMapLib.c` `FD_Reserved` |
| FD reserved (after) | `0xA7400000` | `0x200000` | `MemoryMapLib.c` `UEFI_FD_Reserved` |
| UEFI stack | `0xA760D000` | `0x40000` | `dada.dsc` `PcdCPUCoresStackBase`, `MemoryMapLib.c` |
| Kernel reservation | `0xA8000000` | `0x10000000` | `MemoryMapLib.c` `Kernel` |
| UFS PHY / controller / ICE / MCQ | `0x01D80000` | `0x28000` | stock DT nodes, `edk2-msm/ufs-info-dada.txt` |
| APSS block (GIC, timer, UFS) | `0x16000000` | `0x06000000` | `MemoryMapLib.c` `APSS_HM` |

`unixnu build firmware` checks the FD and stack numbers against the checkout before building; a
mismatch is an error, not a warning.

## Interrupts and timers

| Item | Value | Source |
| --- | --- | --- |
| GIC distributor | `0x16000000` + `0x10000` | `interrupt-controller@16000000` |
| GIC redistributor | `0x16080000` + `0x200000`, stride `0x40000` | same node (`#redistributor-regions = 1`) |
| GIC ITS | `0x16040000` + `0x20000` | `msi-controller@0x16040000` |
| Timer | `0x16800000`, `clock-frequency = 0x124f800` (19.2 MHz) | `timer@16800000` |
| Timer PPIs | 29/30/27/26 | ACPI stage 1 notes (`dada-uefi.md`) |

## CPUs

Stock tree `/cpus` reg values, in order:

| CPU node | `reg` | Cluster |
| --- | --- | --- |
| `cpu@0` … `cpu@500` | `0x000` … `0x500` | 6 efficiency/performance cores |
| `cpu@10000` | `0x10000` | prime core (Aff2 = 1) |
| `cpu@10100` | `0x10100` | prime core (Aff2 = 1) |

`[payload.adt] cpu_regs` reproduces them exactly; the ADT tool accepts values above `0xffff` because
of the two prime cores, and requires `Aff0 == 0`.

## ADT

| Key | Value | Note |
| --- | --- | --- |
| `dram-base` | `0x80000000` | DDR starts here on SM8750 |
| `dram-size` | `0x80000000` (2 GiB) | Measured 15.12 GiB total but fragmented across 16 regions (`0xa00000000` 8 GiB, `0x8c0000000` 5 GiB, rest scattered below `0xd8000000`); the ADT holds one contiguous pair, so 2 GiB is the conservative first-boot value. See *Memory map* below |
| `owned-memory-size` | `0x40000000` (1 GiB) | **TODO(device)**: enlarge for the Data ramdisk stage |
| `chip_id` | `0x208` | software identity seed for `IOPlatformUUID`, not an Apple chip id |
| `cpu_regs` | see above | |
| `psci_method` | `smc` | stock DT `enable-method = "psci"` |
| `crypto-hash-method` | `sha2-384` | target iPad DT |
| `root_match_volume_uuid` | all-zero | **TODO(device)**: UUID of the root volume once the userdata image exists |

## DRAM

Parsed from the stock tree's `memory` node (`#address-cells = 2`, `#size-cells = 2`), 16 non-empty
regions, **15.12 GiB** total:

| Address | Size | Note |
| --- | --- | --- |
| `0xa00000000` | 8 GiB | largest single region |
| `0x8c0000000` | 5 GiB | |
| `0x880000000` | 0.74 GiB | |
| below `0xd8000000` | ~1.3 GiB | 13 scattered fragments; gunyah owns `0x80000000..0x81200000` |

`dram-base = 0x80000000` with `dram-size = 0x80000000` describes only the low 2 GiB, which the ADT
cannot subdivide anyway. This is a **first-boot** value, not a statement that XNU owns all 15.12 GiB.

## Panel handover

| Item | Value | Source |
| --- | --- | --- |
| `splash_region` | present in `Resources/DTBs/dada.dtb` | `/reserved-memory/splash_region` |
| `label` | `cont_splash_region` | same node |
| `reg` | `0xfc800000` + `0x2b00000` | same node |
| GOP framebuffer | `1200x2670`, stride `0x12c0` (4800), `a8r8g8b8`, base `0xfc800000`, size `0xC38E80` | `dada_kernel/docs/uefi-fix.md` (measured through the UEFI GOP) |

The node name must stay exactly `splash_region`, with no unit address: Qualcomm's ABL looks it up by
that literal path and tears the display down if it is missing. dada's stock tree already has it, so
**no `[[bootimg.dtb_node]]` override is needed** -- and adding one would break the build, because
`fdtput -c` on an existing node fails with `FDT_ERR_EXISTS` and `unixnu bootimg` treats that as an
error.

## Userdata partition

| Item | Value | Source |
| --- | --- | --- |
| `first_lba` | `3124736` (`0x2FAE00`) | GPT entry 34, LUN 0 |
| `last_lba` | `100781311` (`0x601CCFF`) | same |
| `lba_bytes` | `4096` | logical block size |
| capacity | `400001335296` B (372.53 GiB) | derived |

## Firmware inputs

| Item | Value |
| --- | --- |
| Mu-Silicium config | `Resources/Configs/dada.toml` |
| DSC / FDF | `Platforms/Xiaomi/dadaPkg/dada.dsc`, `dada.fdf` |
| Firmware volume | `Build/dadaPkg/{DEBUG,RELEASE}_CLANGPDB/FV/SILICIUM_UEFI.fd` |
| Defines | `ENABLE_SECUREBOOT=0`, `MEMORY_PROTECTION=TRUE`, `SHIP_MODE=FALSE`, `BUILDID_STRING=dada-research` |
| Mu_Basecore patches | `AsmMacroLib`, `Auth-Service`, `Boot-Manager`, `Timer`, `Usb-Bus` (same series as `build_uefi.py`) |
| Submodules | `Binaries` (= `Device-Binaries`, branch `dada`), `Common/Mu`, `Common/Mu_OEM_Sample`, OpenSSL |
| Boot image | Android header v4, `requires_kernel_header = true`, gzip, DTB appended |

`Silicium-ACPI` is not in the submodule list: dada ships its own ACPI tables in
`Platforms/Xiaomi/dadaPkg/AcpiTables`, and the DSC does not include the Silicium-ACPI tree.

## Boot image

| Item | Value |
| --- | --- |
| Android header | v4 (`Resources/Configs/dada.toml`), no load addresses, 4 KiB page |
| BootShim | `requires_kernel_header = true`, so the ARM64 header is present |
| Firmware volume | exactly `0x300000` bytes at `0xA7100000` |
| Device tree | appended after the gzip stream (`Resources/DTBs/dada.dtb`) |
| Shim header `image_size` | the shim plus the firmware volume, not `FD_SIZE` alone |

Two stock trees need a change before `unixnu bootimg` accepts the image; the patches are in
`docs/patches/`.

| Patch | Tree | Why |
| --- | --- | --- |
| `unixnu-bootimg-header-v4.patch` | UniXNU | `verify()` only understood header v0-v2; v4 has no addresses, a fixed 4 KiB page and a 4 KiB signature block |
| `mu-silicium-bootshim-header-size.patch` | Mu-Silicium | `BootShim.S` writes `FD_SIZE` into the ARM64 `image_size`; UniXNU verifies shim + firmware volume |
| `mu-silicium-unixnu-components.patch` | Mu-Silicium | puts `UniXNU.inf`, `UniXNUDxe.inf` and `DarwinBoot.inf` into `dada.dsc`/`dada.fdf`; without it the volume holds only Mu-Silicium and the menu you see is Mu-Silicium's |

Checked locally against the RELEASE firmware volume, with an empty ramdisk:

```
ANDROID! v4, kernel 2,185,293 B, ramdisk 0 B, image 2,195,456 B
gzip inflates to 3,145,840 B = 112 B shim + 0x300000 firmware volume, then 966,281 B of DTB
```

## Chip identity

The IPSW identity (`iPad7,1` / iPadOS 17.7.11 / `21H461` / `j120ap`) is the image the UniXNU kernel
build and prelink are pinned to; it says nothing about the phone. `ipad_identity = true` makes the ADT
present the same identity so `libMobileGestalt` matches, which is what the later userland stages need.
Switch it off for a neutral first-boot tree, or replace it with a phone identity when one is chosen.

## Kernel build: what is selected, and why

`configs/dada_defconfig` starts from the sheng reference and keeps only what the first boot needs. The
archive builders in `board.toml [kernel] archives` are exactly the ones those switches require;
`scripts/build-xnu-4k-baseline.sh` links each with `-Wl,-force_load` and fails on a missing archive.

| Switch | Value | Builder in `[kernel] archives` |
| --- | --- | --- |
| `XNU_SOURCE_CORECRYPTO14` | `y` | `build-corecrypto-14.py` + `--namespace` |
| `XNU_SOURCE_PTHREAD` | `y` | `build-libpthread-kernel.py` |
| `XNU_ARM_DT_PLATFORM` | `y` | `build-arm-driver.py ArmDTPlatform` |
| `XNU_ARMCPU` | `y` | `build-arm-driver.py ArmCpu` |
| `XNU_NATIVE_UFS` | `y` | `build-arm-driver.py NativeUfs` |
| `XNU_NATIVE_AES` | `y` | `build-arm-driver.py NativeAES` + `build-native-aes-bindings.py` |
| `XNU_BOOT_PROGRESS` | `n` | none |
| `XNU_NATIVE_LEGACY_DISPLAY` | `n` | none |

### Why boot progress is off

`build-xnu-4k-baseline.sh` refuses `XNU_BOOT_PROGRESS=1` unless the audited UFS/owner-root, Virtio block
and source-corecrypto build are selected together, because the boot page reports stages of the owner-root
path. dada has no owner-root path yet. With `XNU_BOOT_PROGRESS=0` every `boot_progress_*` call compiles to
a no-op macro (`patches/xnu/zzz-boot-progress.patch`), so the patch stays applied and costs nothing. The
`boot-progress=2` entry in `kernel_args` is a runtime argument of the XNU side and is unrelated to this
build switch.

### Why the legacy display instrument is off

`build-native-legacy-display.py` compiles `kits/sheng` sources — touch, keystore, GPU power, Novatek,
cpufreq — to drive the original `IOMobileFramebufferLegacy` lifecycle. Those are SM8550 board programs.

The text console does not need it. DarwinBoot fills `boot_args.Video[6]` from the UEFI GOP
(`drivers/DarwinBoot/DarwinBootPkg/Driver/BootVideo.h`: base, display=1, rowBytes, width, height,
depth=32) and XNU's own `pexpert` video console draws through that. The instrument becomes necessary
when a framebuffer *service* is wanted rather than a console, and it needs a trimmed, board-neutral
builder first.

### NativeUfs verified block mode

`drivers/NativeUfs` serves reads through `configureVerification()` only when `NATIVE_VERIFIED_BLOCK` is
defined. That path requires an owner-signed policy bundle at run time: with no policy it refuses with
`policy count invalid` and never publishes the device, so no root is reachable. This repository's
`scripts/build-arm-driver.py` therefore does not force the macro; it follows `--verified-root`, matching
`sheng`'s `CONFIG_XNU_VERIFIED_ROOT_PROBE=y` + bundle. dada has no bundle yet, so the driver compiles
without it and `start()` returns `true` directly.

## Open measurements (in order of need)

1. **SMEM RAM table** → a usable `owned_memory_size` and a `dram_size` that reflects the fragmented map.
2. **Platform seed** → `--unique-chip-id` (not stored in any repository).
3. **Trust cache bundles** the board accepts → `[payload] trust_cache_sha256`.
4. **Root volume UUID** → `[payload.adt] root_match_volume_uuid`.
5. **UFS bring-up on hardware**: the compatible match and the SM8750 GCC offsets are corrected in this
   repository's `drivers/NativeUfs` override (`docs/ufs-sm8750.md`), checked by
   `tests/check_sm8750_facts.py`, and run by the firmware preflight. What is still unverified is the
   run itself: no one has booted the phone with this driver, so the SMMU takeover, the QMP v4 sun PHY
   state and the ICE configuration are all still assumed rather than observed.
6. **`xiaomi,dada` platform expert** in XNU: a `drivers/` module for this board, built against the XNU
   compile records. `NativeUfs` matches the UFS controller through `ArmDTPlatform`, which is generic.

Closed: the GPT `userdata` window, the splash region, and the UFS controller compatible / SM8750 clock
offsets (see above and `docs/ufs-sm8750.md`).

## UFS

| Item | Value | Source |
| --- | --- | --- |
| Controller | `ufshc@1d84000`, `reg` `0x3000` | stock DT |
| `compatible` | `qcom,ufshc` (generic only) | stock DT |
| PHY | `ufsphy_mem@1d80000`, `qcom,ufs-phy-qmp-v4-sun` | stock DT |
| GCC UFS branches | SM8750 offsets in `drivers/NativeUfs/GccUfs.h` | `gcc-sm8750.c` |

The stock tree names the controller `qcom,ufshc` alone. The sheng port matched only
`qcom,sm8550-ufshc`, so the driver never attached to dada. See `docs/ufs-sm8750.md` for the accepted
names, the eight differing clock offsets, and what is still assumed.

Reads are served without owner verification for this first boot: `NATIVE_VERIFIED_BLOCK` is not defined,
so `NativeUfsBlock::start()` returns `true` instead of `configureVerification()`. With the macro defined
and no policy bundle the driver refuses with `policy count invalid` and never publishes the device. See
*Kernel build* above for the builder that controls this.

## Other paths seen on the phone

From the mainline Linux work in `dada-uefi.md`: the ESP is `/dev/sda35`, the Linux rootfs
`/dev/sda36`, and the UEFI boot path expects `EFI/BOOT/BOOTAA64.EFI`. Those are **not** the UniXNU
userdata window and are listed only because they are easy to confuse with it.
