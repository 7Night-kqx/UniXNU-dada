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
| `dram-size` | `0x80000000` (2 GiB) | **TODO(device)**: read the SMEM RAM table; the stock DT also lists regions above `0x880000000` |
| `owned-memory-size` | `0x40000000` (1 GiB) | **TODO(device)**: enlarge for the Data ramdisk stage |
| `chip_id` | `0x208` | software identity seed for `IOPlatformUUID`, not an Apple chip id |
| `cpu_regs` | see above | |
| `psci_method` | `smc` | stock DT `enable-method = "psci"` |
| `crypto-hash-method` | `sha2-384` | target iPad DT |
| `root_match_volume_uuid` | all-zero | **TODO(device)**: UUID of the root volume once the userdata image exists |

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

## Open measurements (in order of need)

1. **SMEM RAM table** → real `dram_size` and a usable `owned_memory_size`.
2. **GPT `userdata` entry** on LUN 0 → `[userdata] first_lba` / `last_lba`.
3. **Platform seed** → `--unique-chip-id` (not stored in any repository).
4. **Trust cache bundles** the board accepts → `[payload] trust_cache_sha256`.
5. **Root volume UUID** → `[payload.adt] root_match_volume_uuid`.
6. **Splash region** for the stock ABL, if the panel powers off without one (`[[bootimg.dtb_node]]`).
7. **SM8750 platform expert and UFS binding** in XNU: `drivers/` modules matching `xiaomi,dada` and
   `qcom,sm8750-ufshc`, built against the XNU compile records.

## Other paths seen on the phone

From the mainline Linux work in `dada-uefi.md`: the ESP is `/dev/sda35`, the Linux rootfs
`/dev/sda36`, and the UEFI boot path expects `EFI/BOOT/BOOTAA64.EFI`. Those are **not** the UniXNU
userdata window and are listed only because they are easy to confuse with it.
