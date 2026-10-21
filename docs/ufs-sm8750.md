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

Nothing in the base repository or in the submodule was modified.

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

## 4. Still open
- **UFS PHY**: the stock tree has `ufsphy_mem@1d80000` with `compatible = "qcom,ufs-phy-qmp-v4-sun"`,
  a sun-specific QMP PHY. `NativeUfs` does not program the PHY; it relies on the firmware having left
  the link up, as on sheng.
- **SMMU**: `apps_smmu` stream mapping for the UFS master is not represented. The sheng path takes
  over the SMMU by hand (`SmmuTakeover.h`); the SM8750 context bank numbers are not in the stock tree
  in a form this driver reads, so the takeover is unverified on dada.
- **ICE**: the inline crypto engine sits at `0x1d90000` per the stock tree (`reg-names = "ice"`).
  `NativeUfs` does not configure it; reads are unencrypted through the same path sheng uses.
- **No hardware run**: neither change has been exercised on the phone. The kernel step still needs a
  macOS runner with Xcode, and the first boot is what will show whether the controller enumerates.
