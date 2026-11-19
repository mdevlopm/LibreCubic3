/* Minimal <fenv.h> for ARM VFP on uClibc builds without fenv support.
 * Only the exception-flag API numpy uses; flags are the sticky bits of FPSCR. */
#ifndef _FENV_H
#define _FENV_H 1

#define FE_INVALID   0x01
#define FE_DIVBYZERO 0x02
#define FE_OVERFLOW  0x04
#define FE_UNDERFLOW 0x08
#define FE_INEXACT   0x10
#define FE_ALL_EXCEPT 0x1f

#define FE_TONEAREST  0x000000
#define FE_UPWARD     0x400000
#define FE_DOWNWARD   0x800000
#define FE_TOWARDZERO 0xc00000

typedef unsigned int fexcept_t;
typedef struct { unsigned int __cw; } fenv_t;

static inline unsigned int __fpscr_get(void)
{
    unsigned int r;
    __asm__ __volatile__("vmrs %0, fpscr" : "=r"(r));
    return r;
}

static inline void __fpscr_set(unsigned int r)
{
    __asm__ __volatile__("vmsr fpscr, %0" : : "r"(r));
}

static inline int fetestexcept(int e) { return (int)(__fpscr_get() & (unsigned)e & FE_ALL_EXCEPT); }
static inline int feclearexcept(int e) { __fpscr_set(__fpscr_get() & ~((unsigned)e & FE_ALL_EXCEPT)); return 0; }
static inline int feraiseexcept(int e) { __fpscr_set(__fpscr_get() | ((unsigned)e & FE_ALL_EXCEPT)); return 0; }
static inline int fegetround(void) { return (int)(__fpscr_get() & 0xc00000); }
static inline int fesetround(int r)
{
    if (r & ~0xc00000) return -1;
    __fpscr_set((__fpscr_get() & ~0xc00000u) | (unsigned)r);
    return 0;
}

#endif
