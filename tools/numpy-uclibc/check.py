import numpy as np, warnings
print("numpy", np.__version__)
x = np.linspace(0, 1, 4096, endpoint=False)
s = np.sin(2*np.pi*50*x) + 0.5*np.sin(2*np.pi*120*x)
f = np.abs(np.fft.rfft(s))
print("fft peaks", sorted(np.argsort(f)[-2:]))
print("math", float(np.exp(1.0)), float(np.log1p(1.0)), complex(np.exp(1j*np.pi)).real)
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    np.array([1.0]) / np.array([0.0])
    print("divbyzero warning:", any("divide by zero" in str(i.message) for i in w))
a = np.random.default_rng(0).random((200, 3))
print("mean/std/polyfit ok", round(float(a.mean()), 3), np.polyfit(x[:50], s[:50], 3).shape)
