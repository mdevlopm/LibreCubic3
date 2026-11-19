#!/bin/sh
set -e
for l in m pthread dl rt; do
  [ -e /usr/lib/lib$l.so ] || [ -e /usr/lib/lib$l.a ] || ar rcs /usr/lib/lib$l.a
done
cp /w/fenv.h /usr/include/fenv.h
# Python's sysconfig still names the buildroot cross compilers; point them at the native ones.
mkdir -p /buildroot/output/host/bin
for t in gcc g++ cpp ar ranlib; do
  ln -sf /usr/bin/$t /buildroot/output/host/bin/arm-buildroot-linux-uclibcgnueabihf-$t
done
# sanity: fenv works on this CPU (qemu emulates VFP)
printf '#include <fenv.h>\n#include <stdio.h>\nint main(){volatile double z=0;feclearexcept(FE_ALL_EXCEPT);volatile double r=1.0/z;printf("divbyzero=%%d\\n",fetestexcept(FE_DIVBYZERO)!=0);return 0;}\n' > /tmp/f.c
gcc -O0 /tmp/f.c -o /tmp/f && /tmp/f
cd /w
[ -d venv ] || python3 -m venv venv
. venv/bin/activate
python -m pip install -q "pip<24" "setuptools<60" wheel "Cython<3"
export NPY_BLAS_ORDER= NPY_LAPACK_ORDER= BLAS=None LAPACK=None ATLAS=None NPY_NUM_BUILD_JOBS=$(nproc)
cd /w/src/numpy-1.24.4
python -m pip wheel -v --no-deps --no-build-isolation . -w /w/out > /w/full2.log 2>&1 || true
ls -la /w/out
