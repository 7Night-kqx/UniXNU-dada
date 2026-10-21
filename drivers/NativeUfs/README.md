# NativeUfs

Native UFS storage driver. Owns the host controller, SMMU, queues and the root read path.

Current state: the native UFS root works on sheng (SM8550). This repository overrides the base
submodule to accept the SM8750 name for the controller and the SM8750 GCC clock offsets; see
`docs/ufs-sm8750.md`. It has not run on dada hardware yet.

Linked into XNU by `unixnu build kernel`.
