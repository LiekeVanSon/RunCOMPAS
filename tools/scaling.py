import os, sys, time
import numpy as np
sys.path.insert(0, os.environ['STROOPWAFEL_PATH']); sys.path.insert(0, os.environ['INTERFACE_DIR'])
from stroopwafel import distributions, constants
import stroopwafel_interface as SI

dims = SI.create_dimensions()
init = distributions.InitialDistribution(dims)
def hits(n):
    l, m = init.run_sampler(int(n*3)); l = list(np.asarray(l)[m][:n])
    [x.revert_variables_to_original_scales() for x in l]; [x.transform_variables_to_new_scales() for x in l]
    return l

print("\n--- Gaussian.calculate_rejection_rate: scales with NUMBER OF HITS ---")
for n in (25, 50, 100, 200):
    g = distributions.Gaussian.draw_distributions(hits(n), 1.0/np.power(n,1/3))
    t=time.perf_counter(); distributions.Gaussian.calculate_rejection_rate(g, SI.update_properties, SI.rejected_systems, dims)
    dt=time.perf_counter()-t
    print(f"  {n:5d} hits -> {dt:7.2f} s   ({dt/n*1000:6.1f} ms per Gaussian)")

print("\n--- mixture weights: scales with N_samples x N_gaussians ---")
g200 = distributions.Gaussian.draw_distributions(hits(200), 1.0/np.power(200,1/3))
for ns in (5000, 20000, 80000):
    s = hits(ns)
    t=time.perf_counter(); distributions.Gaussian.calculate_probability_of_locations_from_distribution(s, g200)
    dt=time.perf_counter()-t
    print(f"  {ns:6d} samples x 200 gaussians -> {dt:6.2f} s   ({dt/(ns*200)*1e9:5.1f} ns per pair)")
