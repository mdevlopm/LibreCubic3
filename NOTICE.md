# Third-party components and credits

Our own code in this repository is licensed under GPLv3 ([LICENSE](LICENSE)). The items below are distributed under their own licenses or derived from other projects.

| Component | Location | License / note |
|---|---|---|
| ACE Pro driver | `ace-driver/` | Derived from the ACEPRO Klipper driver, [Kobra-S1/ACEPRO](https://github.com/Kobra-S1/ACEPRO) (GPLv3), itself a fork of [szkrisz/ACEPROSV08](https://github.com/szkrisz/ACEPROSV08) (GPLv3). Our starting point was the copy shipped with the vanilla-klipper app; we changed it substantially (serial handling, drying, reconnects, error reporting, tangle protection, refactoring, tests), guided by our own analysis of the ACE protocol and firmware behaviour. Distributed under GPLv3. Copyright of the original code remains with its authors. |
| numpy 1.24.4 | `open-kobra/vendor/numpy-1.24.4-cp311-cp311-linux_armv7l.whl` | BSD-3-Clause, © NumPy Developers. License text inside the wheel (`numpy/LICENSE.txt`). Build recipe: `tools/numpy-uclibc/`. |
| Material Icons font | `kobra-screen/fonts/MaterialIcons-Regular.ttf` | Apache License 2.0, © Google. Text: `kobra-screen/fonts/LICENSE-MaterialIcons.txt`. On the printer Rinkhals' own copy is used; this file is only for previews and tests on a computer. |
| Kobra 3 stock `start.sh` (firmware 2.4.6.7) | `open-kobra/rescue/manifests/K3_2.4.6.7.start.sh` | Verbatim copy of Anycubic's boot script. The rescue layer uses it as a reference to recognise and repair a damaged `start.sh`. © Anycubic. |
| Rinkhals | <https://github.com/rinkhals-community/Rinkhals> | MIT, © Julien Batonnet and Rinkhals contributors. This repository contains no Rinkhals code. The **all-in-one release asset** contains Rinkhals 20261101_01 (`update-k2p-k3.swu`) unchanged, except that the last line of its installer, `reboot`, is replaced by `exit 0` so the other parts install before the single restart. |
| vanilla-klipper app | <https://github.com/Kobra-S1/vanilla-klipper-swu> | Not in this repository. The **all-in-one release asset** contains its 2026-06-01 build (`app-vanilla-klipper-k2p-k3.swu`) unchanged. Its Klipper code is GPLv3 ([Klipper3d/klipper](https://github.com/Klipper3d/klipper)); the project itself has no license file. Source: the project's repository. |
| Klipper | <https://github.com/Klipper3d/klipper> | GPLv3. `leviq3-klipper/` and `ace-driver/` are Klipper extensions. |
| ACE protocol research | <https://github.com/printers-for-people/ACEResearch> | CC0. Protocol reference for `ace-standalone`. |

`leviq3-klipper/leviq3.py` was written from scratch based on reverse-engineered behaviour of the LEVIQ commands in the stock firmware; it contains no copied stock code.
