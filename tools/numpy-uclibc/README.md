# numpy for the Kobra 3 (Rinkhals, armv7 uClibc, CPython 3.11)

Klipper's resonance tools (SHAPER_CALIBRATE, TEST_RESONANCES OUTPUT=resonances,
MEASURE_AXES_NOISE) need numpy; PyPI wheels are glibc-only and do not load on the
printer's uClibc Python.

Build (Fedora host, needs `qemu-user-static-arm` and podman):

1. Unpack the numpy 1.24.4 sdist to `<work>/src/numpy-1.24.4` and in
   `numpy/core/setup_common.py` add `C99_COMPLEX_FUNCS = []` right after the list
   (uClibc exports some complex functions without prototypes; npymath's own are used).
2. Copy `fenv.h` and `build2.sh` into `<work>` and run:
   `podman run --rm --platform linux/arm/v7 -v <work>:/w:Z ghcr.io/jbatonnet/armv7-uclibc:rinkhals sh /w/build2.sh`
3. Wheel lands in `<work>/out/`. `check.py` is a quick sanity test (FFT, libm, FP flags).

The LibreCubic3 USB package ships this wheel (`open-kobra/vendor/`) and installs it into
the vanilla-klipper app's `site-packages` automatically. A vanilla-klipper app reinstall
or Rinkhals update replaces that folder; run the LibreCubic3 package again afterwards.
