# NativeUfs on SM8750 (dada)

What `drivers/NativeUfs` needs beyond the SM8550 (sheng) port, and where each fact comes from.
The authoritative source for the clock side is the mainline Linux clock driver,
`dada_kernel/linux/drivers/clk/qcom/gcc-sm8750.c`; the device tree facts come from the stock
`Mu-Silicium/Resources/DTBs/dada.dtb`.

## Why this driver is overridden in this repository

`NativeUfs` is a submodule of the base repository (`UniXNU/NativeUfs.git`, pinned at `af785c1`).
The base build driver gives a device repository's module the same name priority over the base one
(`feeds.resolve` inserts the device pass before the base pass, and `add` uses `setdefault`), so
`drivers/NativeUfs/` here is the copy that `unixnu build kernel` compiles. `unixnu status` confirms it:

```
driver   NativeUfs                device
```

Nothing in the base repository's driver source was modified. The base repository's kernel build
script has one change for this board: the `IOBlockStorageFamily` base class is linked only when the
transport is not in probe-only mode (section 5).

## 1. Controller compatible

The dada device tree names the controller:

```
ufshc@1d84000 {
    compatible = "qcom,ufshc";
```

The sheng port matched only `"qcom,sm8550-ufshc"`, which is not a substring of `"qcom,ufshc"`, so
`NativeUfs::probe` returned `nullptr` and the driver never attached. Neither string contains the
other, so the match now accepts all of:

| Compatible | Board |
| --- | --- |
| `qcom,ufshc` | dada (stock SM8750 tree) |
| `qcom,sm8750-ufshc` | SM8750 trees that follow `sm8750.dtsi` ordering |
| `qcom,sm8550-ufshc` | sheng (SM8550) |

The match stays a property read on the provider (`IOPlatformDevice`), so a device tree that publishes
no `compatible` still does not attach.

## 2. GCC UFS clock offsets

Eight of the nine branch offsets differ between SM8550 and SM8750. `GccUfs.h` now carries the SM8750
values and records the SM8550 one each replaces. Addresses from `clk_branch::halt_reg` in
`gcc-sm8750.c` (all use `halt_bit` 0, enable bit 0):

| Macro | SM8750 | SM8550 |
| --- | --- | --- |
| `GCC_UFS_PHY_AXI_ENABLE` | `0x77018` | `0x77018` |
| `GCC_UFS_PHY_AHB_ENABLE` | `0x77028` | `0x77024` |
| `GCC_UFS_PHY_TX_SYMBOL0_ENABLE` | `0x7702c` | `0x77028` |
| `GCC_UFS_PHY_RX_SYMBOL0_ENABLE` | `0x77030` | `0x7702c` |
| `GCC_UFS_PHY_UNIPRO_CORE_ENABLE` | `0x7706c` | `0x77068` |
| `GCC_UFS_PHY_ICE_CORE_ENABLE` | `0x7707c` | `0x77074` |
| `GCC_UFS_PHY_PHY_AUX_ENABLE` | `0x770bc` | `0x770b0` |
| `GCC_UFS_PHY_RX_SYMBOL1_ENABLE` | `0x770d8` | `0x770cc` |
| `GCC_AGGRE_UFS_PHY_AXI_ENABLE` | `0x770f0` | `0x770e4` |

The two GDSC registers are unchanged: `gcc_ufs_phy_gdsc` at `0x77004` and `gcc_ufs_mem_phy_gdsc` at
`0x9e000`.

Unchanged from sheng, and verified to still hold on SM8750:

- the physical windows: `GCC_BASE = 0x00100000`, `UFS_HC_BASE = 0x01d84000`, `UFS_HC_SIZE = 0x3000`
  (the stock tree has `clock-controller@100000` and `ufshc@1d84000` with `reg` size `0x3000`);
- every offset above still falls inside the `0x77000 + 0x1000` window that `inspect()` maps, the
  highest being `0x770f0` (240 bytes short of the end);
- `hwcg_bit = 1` on `axi`, `ahb`, `unipro_core`, `ice_core`, `phy_aux` and `aggre`, so `CLK_OFF` on an
  idle branch is normal and the enable bits, not `CLK_OFF`, gate the read-only probe.

The four RCG2 command registers (`0x77034`, `0x7708c`, `0x770a4`, `0x770c0`) are recorded in
`GccUfs.h` but not programmed: `NativeUfs` still performs no rate change.

## 3. Write safety

The GCC window remains read-only. All 13 `write32` calls in `NativeUfs.cpp` target either the SMMU
(`0x304`-style offsets inside the mapped SMMU window) or the UFS host controller at `UFS_HC_BASE`
(`0x20`, `0x24`, `0x50`, `0x54`, `0x58`, `0x60`, and the doorbell helper). A wrong clock offset can
therefore corrupt a *read*, which makes the probe report a misleading power state, but cannot corrupt
a GCC register.

## 4. What the mainline Linux port says

`dada_kernel/linux` is a working Linux on this phone, and its UFS stack is the reference for what a
real SM8750 UFS host needs. Three facts from it agree with this port:

- **The host controller programming is the SM8550 one.** `drivers/ufs/host/ufs-qcom.c` maps
  `qcom,sm8750-ufshc` to `ufs_qcom_sm8550_drvdata` (`quirks = UFSHCD_QUIRK_BROKEN_LSDBS_CAP`,
  `no_phy_retention = true`), the same data as `qcom,sm8550-ufshc`. So a transport written for
  SM8550 is the right starting point for SM8750; only the clock offsets differ, and they are in
  section 2.
- **The SMMU stream is 0x60.** The mainline `sm8750.dtsi` node `ufs_mem_hc` has
  `iommus = <&apps_smmu 0x60 0>`, which is the stream id the driver's own SMMU takeover looks for.
- **The clock branches are the eight the driver programs**: `core_clk` (GCC_UFS_PHY_AXI_CLK),
  `bus_aggr_clk` (GCC_AGGRE_UFS_PHY_AXI_CLK), `iface_clk` (GCC_UFS_PHY_AHB_CLK),
  `core_clk_unipro` (GCC_UFS_PHY_UNIPRO_CORE_CLK), `ref_clk` (RPMH_LN_BB_CLK3), `tx_lane0_sync_clk`,
  `rx_lane0_sync_clk` and `rx_lane1_sync_clk`. The `GccUfs.h` table has nine entries: those eight
  plus the ICE core clock, which this driver does not program.

The mainline stack also *programs* the PHY (`drivers/phy/qualcomm/phy-qcom-qmp-ufs.c`, QMP v4 for
sun). This driver does not: it takes over the controller the firmware left running. That is a
reasonable assumption on this phone, because the phone's own UEFI drives the same UFS before
DarwinBoot runs (`Mu-Silicium/Mu_Basecore/MdeModulePkg/Bus/Ufs/UfsBlockIoPei`, `UfsPassThruDxe`).

## 5. Probe mode: the first hardware run

The transport derives from `IOBlockStorageDevice`, and that class is not in the xnu tree: the port
compiles it from Apple's IOStorageFamily source and links it into the kernel. The kernelcache's own
`com.apple.iokit.IOStorageFamily kext` defines the same class name, and `OSMetaClass` refuses the
second registration:

```
OSMetaClass: Kext com.apple.iokit.IOStorageFamily class IOBlockStorageDevice is a duplicate;
kext __kernel__ already has a class by that name. (libkern/c++/OSMetaClass.cpp:894)
```

The prelinked kext cannot simply be dropped: `com.apple.driver.AppleARMPlatform` and
`com.apple.filesystems.apfs` both list it in `OSBundleLibraries`, so the prelink closure pulls it
back in.

So the transport is built with its block-device half left out (`XNU_NATIVE_UFS_PROBE_ONLY`):

- `drivers/NativeUfs/NativeUfs.cpp` guards the `IOBlockStorageDevice` include, the `NativeUfsBlock`
  class and the root-window publication code with `#ifdef NATIVE_UFS_BLOCK_DEVICE`;
- `scripts/build-arm-driver.py NativeUfs --probe-only` defines `XNU_NATIVE_UFS_PROBE_ONLY=1` and
  leaves the IOStorageFamily include path out (the staged headers exist for a base class that this
  build does not have);
- the kernel script links `build/storage-classes/libStorageClasses.a` only when the probe-only
  switch is off, so the class is not registered in the kernel at all and the prelinked kext keeps
  the name to itself.

What is left of the driver still does the hardware work: the ladder `native-ufs-probe=1..13` (a
kernel argument, `board.toml [kernel] kernel_args`) maps the GCC and the controller, dumps their
registers, takes over the controller the firmware left up and issues read-only SCSI commands.
Nothing is written to the storage, the controller is not reset and the link is not restarted.

The first run is level 2 (the register dump alone). What it has to show:

- `gdsc` bit 31 set (the UFS GDSC is powered) and the `axi`/`ahb` branches enabled;
- `hc_read=1` with a plausible `cap` (`0x00010000`-ish, `HCS`/`HCE` capability bits) and `ver`;
- `utrlba` pointing at the firmware's request list.

## 6. Still open
- **UFS PHY**: the stock tree has `ufsphy_mem@1d80000` with `compatible = "qcom,ufs-phy-qmp-v4-sun"`,
  a sun-specific QMP PHY. `NativeUfs` does not program the PHY; it relies on the firmware having left
  the link up, as on sheng.
- **SMMU**: `apps_smmu` stream mapping for the UFS master is not represented. The sheng path takes
  over the SMMU by hand (`SmmuTakeover.h`); the SM8750 context bank numbers are not in the stock tree
  in a form this driver reads, so the takeover is unverified on dada.
- **ICE**: the inline crypto engine sits at `0x1d90000` per the stock tree (`reg-names = "ice"`).
  `NativeUfs` does not configure it; reads are unencrypted through the same path sheng uses.
- **No hardware run yet**: the probe mode has never been exercised on the phone. The kernel step
  needs a macOS runner with Xcode, and the first boot is what will show whether the controller
  enumerates.
