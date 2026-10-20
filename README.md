# UniXNU-dada

Device repository for the **Xiaomi 15 (`dada`, Qualcomm SM8750 "sun")** on
[UniXNU](https://github.com/UniXNU/UniXNU), the project that boots iOS and macOS on non-Apple ARM64
hardware through UEFI.

| Repository | Contents |
| --- | --- |
| [UniXNU](https://github.com/UniXNU/UniXNU) | Build driver, UEFI boot manager, DarwinBoot, generic drivers, kits and the 4 KiB XNU patches |
| this repository | `board.toml`, defconfig, ADT generator, firmware preflight and the device package |
| Mu-Silicium (local: `~/Src/Repo/Mu-Silicium`) | UEFI firmware port, `Platforms/Xiaomi/dadaPkg`, `Binaries/dada` |

The UEFI side already exists: Mu-Silicium boots dada to a framebuffer, a UEFI menu, UFS and USB
device mode. This repository adds the UniXNU values and the pieces XNU needs on top of it.

## State

| Stage | State |
| --- | --- |
| 1. UEFI firmware, framebuffer, menu, UFS, side buttons, UART7 | works in Mu-Silicium (`Mu-dada.img`) |
| 2. UniXNU menu and configuration | not run yet |
| 3. Payload volume (kernel, ADT, trust cache) | board values written; device inputs missing |
| 4. DarwinBoot handoff | not run yet |
| 5. XNU kernel entry / platform expert | **the first goal**; SM8750 platform expert does not exist yet |
| 6. UFS root volume | needs the userdata window and a `qcom,sm8750-ufshc` binding |
| 7. Userland | far away |

Nothing here has been booted on hardware yet. `board.toml` marks every value that still has to be
measured on the phone with `TODO(device)`; `scripts/check-dada.py` prints them as warnings before
each firmware build.

## Build

Prerequisites: `clang`, `python3`, `make`, `mtools`, `dtc`, a Mu-Silicium checkout with its
submodules, and a `mu_basecore` tree for `--edk2` (or let `unixnu fetch` clone it).

```sh
cd ~/Src/Repo/UniXNU

./unixnu defconfig dada_defconfig -D ../UniXNU-dada
./unixnu fetch

# 1. Firmware only: proves the dada DSC, the memory map and the vendor binaries.
./unixnu build firmware --uefi ~/Src/Repo/Mu-Silicium --edk2 <mu_basecore>

# 2. Kernel: the first run writes the compile records the device archives are built against.
./unixnu build kernel

# 3. Package and payload (device inputs: platform seed, trust cache, Data seed).
./unixnu build package
./unixnu build payload --unique-chip-id <hex>

# 4. Boot image: BootShim + gzip(firmware volume) + device tree, Android header v4.
./unixnu bootimg --uefi ~/Src/Repo/Mu-Silicium --dtb ~/Src/Repo/Mu-Silicium/Resources/DTBs/dada.dtb

fastboot boot out/dada/boot/boot.img
```

`unixnu status` shows where every module comes from. `--local` / `--local-module` point a module at a
working copy while developing.

### Two changes the stock trees need

`unixnu bootimg` is stricter than the device bootloader about two things, and dada trips both.
The patches are in `docs/patches/`; apply them once to the checkouts:

```sh
# 1. UniXNU verifies Android boot header v0-v2 only; dada uses v4 (no load addresses, 4 KiB page,
#    4 KiB boot signature after the data). Adds the v3/v4 verification path.
git -C ~/Src/Repo/UniXNU apply ~/Src/Repo/UniXNU-dada/docs/patches/unixnu-bootimg-header-v4.patch

# 2. The Mu-Silicium BootShim writes FD_SIZE into the ARM64 header image_size field, while UniXNU
#    verifies that the field describes the shim plus the firmware volume. One-line shim change.
git -C ~/Src/Repo/Mu-Silicium apply ~/Src/Repo/UniXNU-dada/docs/patches/mu-silicium-bootshim-header-size.patch
```

With both applied, the boot image for dada packs and verifies:

```
ANDROID! header v4, kernel 2,185,293 bytes (gzip of 112-byte shim + 0x300000 firmware volume),
appended dada device tree (966,281 bytes), 2,195,456 bytes total
```

The v4 support is a candidate for upstream UniXNU (`scripts/unixnu/bootimg.py`); the shim change
belongs to the UEFI port, as uefi-sheng carries it too.


### Just the firmware, without UniXNU

The Mu-Silicium checkout still builds on its own:

```sh
cd ~/Src/Repo/Mu-Silicium
python3 build_uefi.py -d dada -r RELEASE -c
# -> Build/dadaPkg/RELEASE_CLANGPDB/FV/SILICIUM_UEFI.fd (0x300000 bytes)
```

## Files

| Path | Purpose |
| --- | --- |
| `board.toml` | Firmware, payload, ADT and prelink values for dada |
| `configs/dada_defconfig` | Kernel modules linked into the XNU build |
| `scripts/make-dada-adt.py` | Apple-format device tree the kernel is handed (`Payloads/kernel.macho.adt`) |
| `scripts/check-dada.py` | Firmware preflight: memory map, stack, vendor inputs, placeholders |
| `package/unixnu.toml` | Fallback UniXNU configuration for a hand-made payload volume |
| `docs/known-values.md` | Every pinned address with its source, and what is still unknown |
| `docs/patches/` | The two changes the stock UniXNU and Mu-Silicium checkouts need to pack a dada boot image |

## Device facts pinned here

| Item | Value | Source |
| --- | --- | --- |
| Firmware base / size | `0xA7100000` / `0x300000` | `Resources/Configs/dada.toml` + `MemoryMapLib.c` |
| Boot image header | v4 | `Resources/Configs/dada.toml` |
| GIC distributor / redistributor | `0x16000000` / `0x16080000`, stride `0x40000` | stock DT `interrupt-controller@16000000` |
| Timer | `0x16800000`, 19.2 MHz | stock DT `timer@16800000` |
| UFS controller / PHY | `0x1D84000` / `0x1D80000` | stock DT, `edk2-msm/ufs-info-dada.txt` |
| CPUs | 8 cores: `0,0x100..0x500`, `0x10000`, `0x10100` | stock DT `/cpus` |
| Panel | 1200x2670, 32 bpp | `dada.dsc` `PcdFrameBuffer*` |
| Console UART | UART7 `qup_uart@a9c000`, 115200n8 | Mu-Silicium device notes |

## Known gaps

| Subsystem | State |
| --- | --- |
| Touch | no UEFI and no XNU driver (Synaptics TCM over SPI, `synaptics,tcm-spi`) |
| GPU | Adreno 830: Mu-Silicium has no driver, UniXNU's Adreno path is A740/SM8550 only |
| WiFi / BT / audio / camera / sensors | no drivers anywhere in the stack |
| USB host | wiring exists, Type-C role switch does not work on SM8750 |
| Suspend / resume | not implemented; the ADT sets `sleep-disabled` |

## References

- [UniXNU porting guide](https://unixnu.com/porting/)
- [Mu-Silicium dada notes](../Mu-Silicium/dada-uefi.md) and [driver inventory](../Mu-Silicium/dada-uefi-drivers.md)
- Stock device tree: `Mu-Silicium/Resources/DTBs/dada.dtb`
- Mainline bring-up tree: `~/Src/Repo/dada_kernel/device-tree/sm8750-xiaomi-dada-mainline.dts`
- UFS observations: `~/Src/Repo/edk2-msm/ufs-info-dada.txt`

## License

BSD-2-Clause-Patent. See [LICENSE](LICENSE).
