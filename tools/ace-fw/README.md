# ACE Pro dryer cool-down patch (advanced, optional)

After drying stops, the ACE Pro (Gen 1) keeps its fan running for about **3 minutes** to cool the heater plate. These tools shorten that to **1 minute** in the firmware. kobra-screen recognises the patched firmware and shows a 1-minute countdown ("Cooling heater plate for safety").

> ⚠️ **Risky.** This rewrites the ACE Pro's firmware. If the upload is interrupted the ACE Pro may stop working, and no recovery method for its bootloader is known. Use it only if you know what you are doing and **have SSH access to the printer**. The USB install package does not do this.

## What you need

- ACE Pro Gen 1 with firmware **V1.3.863**. The patch refuses any other version.
- Anycubic's V1.3.863 firmware file. No firmware is included in this repository; obtain it yourself. Expected SHA-256:
  `3f3676ba357749b3d0367922cb7f6b0dedc9b3d104c1c29e719ac3e563f5eb63`
- SSH access to the printer.

## Steps

1. Patch on your computer:
   ```sh
   python3 patch_cooldown.py ACE_V1.3.863_20250518.bin ACE_V1.3.86K.bin
   ```
   Only the cool-down compare instruction (180 s → 60 s) and the version string (`V1.3.863` → `V1.3.86K`) change. The output's SHA-256 must be `0b50ec60e3a6e0c2eaaf1b0ba1fe31da2b9b9bae11f0c85491ec3a206bf7ef9c`.
2. Copy `ace_iap.py` and `ACE_V1.3.86K.bin` to the printer.
3. On the printer, stop Klipper (nothing else may use the ACE serial port). If the ACE is drying, stop it first.
4. Do a dry run, then upload:
   ```sh
   python3 ace_iap.py --dry-run ACE_V1.3.86K.bin
   python3 ace_iap.py --expect-version V1.3.86K ACE_V1.3.86K.bin
   ```
   The upload takes about a minute; the ACE restarts at the end. A "port not found after reboot" warning is harmless: while Klipper is stopped, the ACE's USB connection drops and comes back every few seconds.
5. Restart the printer.

To go back, run the same tool with the original V1.3.863 file.

## Known side effect

After an upload, the ACE bootloader writes unreadable bytes into the `boot_firmware` field of the `get_info` reply. The ACE driver and ace-standalone in this repository tolerate it (the bootloader version shows as "?"). How the stock Anycubic software behaves in this state has not been tested.

## Tests

```sh
python3 -m unittest test_ace_iap -v
```

The tests use a fake serial device, not real hardware.
