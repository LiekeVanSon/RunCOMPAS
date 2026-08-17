"""Check the vectorised stroopwafel utils against the ORIGINAL scalar formulas."""
import math, os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import setup  # noqa: E402
setup(need_interface=False)
from stroopwafel import utils
from stroopwafel.constants import R_COEFF, ZSOL, R_SOL_TO_AU

def orig_zams_radius(mass, metallicity):          # verbatim pre-change code
    metallicity_xi = math.log10(metallicity / ZSOL)
    rc = []
    for coeff in R_COEFF:
        value, total = 1, 0
        for series in coeff:
            total += series * value; value *= metallicity_xi
        rc.append(total)
    top = rc[0]*pow(mass,2.5) + rc[1]*pow(mass,6.5) + rc[2]*pow(mass,11) + rc[3]*pow(mass,19) + rc[4]*pow(mass,19.5)
    bottom = rc[5] + rc[6]*pow(mass,2) + rc[7]*pow(mass,8.5) + pow(mass,18.5) + rc[8]*pow(mass,19.5)
    return top / bottom * R_SOL_TO_AU

def orig_roche(m1, m2):
    q = m1 / m2
    return 0.49 / (0.6 + pow(q, -2.0/3.0) * math.log(1.0 + pow(q, 1.0/3.0)))

rng = np.random.default_rng(7)
N = 100000
m1 = 10 ** rng.uniform(np.log10(0.5), np.log10(150), N)
m2 = m1 * rng.uniform(0.01, 1.0, N)
Z  = 10 ** rng.uniform(-4, np.log10(0.03), N)
EPS = np.finfo(float).eps

old_r = np.array([orig_zams_radius(a, b) for a, b in zip(m1, Z)])
new_r = utils.get_zams_radius(m1, Z)                       # array call
new_s = np.array([utils.get_zams_radius(a, b) for a, b in zip(m1, Z)])  # scalar call
old_l = np.array([orig_roche(a, b) for a, b in zip(m1, m2)])
new_l = utils.calculate_roche_lobe_radius(m1, m2)

def rel(a, b): return float(np.nanmax(np.abs(a - b) / np.abs(b)))
print(f"  zams radius   array vs original scalar : max rel {rel(new_r, old_r):.3e}")
print(f"  zams radius   scalar-call vs original  : max rel {rel(new_s, old_r):.3e}")
print(f"  roche lobe    array vs original scalar : max rel {rel(new_l, old_l):.3e}")
print(f"  scalar call still returns a usable number: {utils.get_zams_radius(10.0, 0.0142):.6e}")

t=time.perf_counter(); [orig_zams_radius(a,b) for a,b in zip(m1,Z)]; t_old=time.perf_counter()-t
t=time.perf_counter(); utils.get_zams_radius(m1,Z); t_new=time.perf_counter()-t
print(f"\n  {N:,} radii: scalar loop {t_old:.2f} s -> array {t_new:.3f} s  ({t_old/t_new:.0f}x)")

ok = all(rel(a,b) < 10*EPS for a,b in [(new_r,old_r),(new_s,old_r),(new_l,old_l)])
print(f"\n  {'PASS -- identical to within 1 ULP' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
