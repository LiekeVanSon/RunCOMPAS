#!/usr/bin/env python3
"""
stroopwafel_interface.py -- run COMPAS with adaptive importance sampling (AIS).

Stroopwafel spends most of its samples where the systems you care about actually
are, instead of drawing uniformly from the whole ZAMS parameter space. For rare
outcomes (double neutron stars, massive double white dwarfs) this buys you orders
of magnitude in effective sample size. See van Son et al. / Broekgaarden et al.
(arXiv:1905.00910) and https://github.com/lokiysh/stroopwafel

It runs in four phases:
    explore  -- plain Monte Carlo, to find where the interesting systems are
    adapt    -- fit instrumental distributions around the hits
    refine   -- draw the remaining samples from those distributions
    postprocess -- compute the mixture weights that undo the sampling bias

Because samples are no longer drawn from the birth distribution, EVERY system
carries a `mixture_weight`. You must use it in any population statistic.
postProcessing/append_weights.py attaches those weights to the COMPAS output.

THE TWO THINGS YOU WILL WANT TO EDIT are marked "EDIT ME" below:
    create_dimensions()   -- which parameters to sample, and how
    interesting_systems() -- what counts as a "hit"

Everything else is driven by the driver script / command line, so you should not
need to hand-edit paths in this file.

See ../../docs/03_stroopwafel_runs.md
"""
import argparse
import os
import sys
import time

import h5py as h5
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Locate stroopwafel. STROOPWAFEL_PATH is exported by the job script from your
# cluster profile (clusters/<name>.yaml : stroopwafel_path). If it is empty we
# fall back to the pip-installed package.
# ---------------------------------------------------------------------------
_sw_path = os.environ.get('STROOPWAFEL_PATH', '').strip()
if _sw_path:
    sys.path.insert(0, _sw_path)
try:
    from stroopwafel import sw, classes, prior, sampler, distributions, constants, utils
except ImportError as err:
    raise SystemExit(
        f"Could not import stroopwafel ({err}).\n"
        "Install it with:      pip install --user stroopwafel\n"
        "or clone it:          git clone https://github.com/lokiysh/stroopwafel\n"
        "and set stroopwafel_path in your cluster profile to the clone.\n"
        "See docs/01_setup.md"
    )


# ===========================================================================
#  EDIT ME (1/2): which parameters does stroopwafel sample?
# ===========================================================================
def create_dimensions():
    """
    A "dimension" is one variable stroopwafel samples over. Each needs a range,
    a sampler (how to draw it) and a prior (the true birth distribution, used to
    compute the weights). See classes.py / sampler.py / prior.py in stroopwafel.

    should_print=False marks a variable that is not passed to COMPAS directly
    (here q, because COMPAS wants --initial-mass-2, which we derive in
    update_properties below).
    """
    # Dimension names must be REAL COMPAS OPTION NAMES: stroopwafel writes them
    # verbatim into the grid file it hands to COMPAS. Naming one 'Mass_1' gives
    # a grid COMPAS silently rejects, and a run that evolves nothing.
    m1 = classes.Dimension('--initial-mass-1', 5.0, 150, sampler.kroupa, prior.kroupa)
    q = classes.Dimension('q', 0.01, 1, sampler.uniform, prior.uniform, should_print=False)
    a = classes.Dimension('--semi-major-axis', .01, 1000, sampler.flat_in_log, prior.flat_in_log)
    return [m1, q, a]
    # For white-dwarf work, drop the lower mass limit to ~1.0 Msol -- WD
    # progenitors are far below the 5 Msol floor that suits compact objects.


def update_properties(locations, dimensions):
    """
    Fill in variables that are DERIVED from the sampled dimensions, or that are
    drawn independently of them (here: metallicity and eccentricity).

    Called once per sampled system, including every rejection sample, so the
    metallicity draw is done as one array rather than a million scalar calls.
    Statistically identical: the same number of independent uniforms from the
    same generator, just requested in a block.
    """
    if len(locations) == 0:
        return
    m1 = dimensions[0]
    q = dimensions[1]
    metallicity = 10 ** np.random.uniform(-4, np.log10(0.03), size=len(locations))
    for location, Z in zip(locations, metallicity):
        location.properties['--initial-mass-2'] = location.dimensions[m1] * location.dimensions[q]
        location.properties['--metallicity'] = Z
        location.properties['--eccentricity'] = 0


# ===========================================================================
#  EDIT ME (2/2): what is an "interesting" system?
# ===========================================================================
# Each mask takes the DCO arrays and returns a boolean array of hits. They are
# combined with "merges within a Hubble time" in interesting_systems() below.
#
# Stellar types: 10 HeWD, 11 COWD, 12 ONeWD, 13 NS, 14 BH
#
# --- the standard targets ---------------------------------------------------
def _mask_DNS(st1, st2, m1, m2):
    """Double neutron star."""
    return np.logical_and(st1 == 13, st2 == 13)


def _mask_BBH(st1, st2, m1, m2):
    """Binary black hole."""
    return np.logical_and(st1 == 14, st2 == 14)


def _mask_BHNS(st1, st2, m1, m2):
    """Black hole + neutron star, either order."""
    return np.logical_or(np.logical_and(st1 == 14, st2 == 13),
                         np.logical_and(st1 == 13, st2 == 14))


def _mask_AnyDCO(st1, st2, m1, m2):
    """Any double compact object, white dwarfs included."""
    return np.logical_and(np.isin(st1, [10, 11, 12, 13, 14]),
                          np.isin(st2, [10, 11, 12, 13, 14]))


SYSTEMS_OF_INTEREST = {
    'DNS': _mask_DNS,
    'BBH': _mask_BBH,
    'BHNS': _mask_BHNS,
    'AnyDCO': _mask_AnyDCO,
}


# --- EXAMPLE: adding your own ----------------------------------------------
# Not defaults -- worked examples of the two things you are likely to want:
# a mass cut, and combining two populations. Delete them if they are not yours.
#
# These come from a double-white-dwarf project, so they also show the one thing
# to remember for WD work: lower the m1 range in create_dimensions() to ~1 Msol,
# or you will never sample the progenitors at all.
def _mask_WDWD(st1, st2, m1, m2):
    """Any double white dwarf."""
    return np.logical_and(np.isin(st1, [10, 11, 12]), np.isin(st2, [10, 11, 12]))


def _mask_MassiveWDWD(st1, st2, m1, m2):
    """A CO white dwarf above 0.6 Msol paired with any WD or NS."""
    heavy1 = np.logical_and(np.logical_and(st1 == 11, m1 > 0.6), np.isin(st2, [10, 11, 12, 13]))
    heavy2 = np.logical_and(np.logical_and(st2 == 11, m2 > 0.6), np.isin(st1, [10, 11, 12, 13]))
    return np.logical_or(heavy1, heavy2)


def _mask_MassiveWDWD_NSNS(st1, st2, m1, m2):
    """
    Two populations at once. Workable here, but fragile: raising the WD cut to
    0.8 Msol makes the two so rare that the explore phase finds too few of
    either for the adaptation to latch onto.
    """
    return np.logical_or(_mask_MassiveWDWD(st1, st2, m1, m2), _mask_DNS(st1, st2, m1, m2))


SYSTEMS_OF_INTEREST.update({
    'WDWD': _mask_WDWD,
    'MassiveWDWD': _mask_MassiveWDWD,
    'MassiveWDWD_NSNS': _mask_MassiveWDWD_NSNS,
})


#############################################################################
#                                                                           #
#          You should not need to change anything below this line           #
#                                                                           #
#############################################################################

def configure_code_run(batch):
    """
    Tell stroopwafel what command to run for one batch. Stroopwafel writes the
    sampled systems to a grid file, then COMPAS evolves that grid.
    """
    batch_num = batch['number']
    grid_filename = os.path.join(OUTPUT_FOLDER, 'grid_' + str(batch_num) + '.csv')
    output_container = 'batch_' + str(batch_num)
    # offset the seed per batch so random numbers are never reused across batches
    random_seed = RANDOM_SEED_BASE + batch_num * NUM_SYSTEMS_PER_RUN

    compas_args = [COMPAS_EXECUTABLE,
                   '--grid', grid_filename,
                   '--output-container', output_container,
                   '--random-seed', random_seed]
    # NOTE: set --add-options-to-sysparms NEVER and --logfile-definitions in
    # compasConfig.yaml, otherwise the per-grid-line options bloat SysParms.
    for key, val in COMMAND_OPTIONS.items():
        compas_args.extend([key, val])
    for params in EXTRA_PARAMS:
        compas_args.extend(params.split("="))

    batch['grid_filename'] = grid_filename
    batch['output_container'] = output_container
    return compas_args


def interesting_systems(batch):
    """
    Mark which systems in a finished batch are hits. Stroopwafel uses the count
    to decide where to concentrate the refine phase.
    """
    try:
        folder = os.path.join(OUTPUT_FOLDER, batch['output_container'])

        if not HDF5:
            system_parameters = pd.read_csv(folder + '/BSE_System_Parameters.csv', skiprows=2)
            system_parameters.rename(columns=lambda x: x.strip(), inplace=True)
            seeds = system_parameters['SEED']
            double_compact_objects = pd.read_csv(folder + '/BSE_Double_Compact_Objects.csv', skiprows=2)
            double_compact_objects.rename(columns=lambda x: x.strip(), inplace=True)
            sfile = None
        else:
            # COMPAS >= v02.49.01 names the file COMPAS_Output.h5 inside the
            # container; rename it so batches stay distinguishable once merged.
            batch_h5 = os.path.join(folder, 'batch_' + str(batch['number']) + '.h5')
            if os.path.isfile(os.path.join(folder, 'COMPAS_Output.h5')):
                os.rename(os.path.join(folder, 'COMPAS_Output.h5'), batch_h5)
            sfile = h5.File(batch_h5, 'r')
            seeds = sfile['BSE_System_Parameters']['SEED'][:]

            # A batch in which nothing formed a DCO has NO
            # BSE_Double_Compact_Objects group at all -- COMPAS only writes a
            # group once it has a row for it. That is normal for small batches
            # and rare targets, so it means "no hits", not an error.
            if 'BSE_Double_Compact_Objects' not in sfile:
                for sample in batch['samples']:
                    sample.properties['SEED'] = 0
                    sample.properties['is_hit'] = 0
                    sample.properties['batch'] = batch['number']
                for index, sample in enumerate(batch['samples']):
                    sample.properties['SEED'] = seeds[index]
                sfile.close()
                return 0

            double_compact_objects = sfile['BSE_Double_Compact_Objects']

        for index, sample in enumerate(batch['samples']):
            sample.properties['SEED'] = seeds[index]
            sample.properties['is_hit'] = 0
            sample.properties['batch'] = batch['number']

        st1 = double_compact_objects['Stellar_Type(1)'][:]
        st2 = double_compact_objects['Stellar_Type(2)'][:]
        m1 = double_compact_objects['Mass(1)'][:]
        m2 = double_compact_objects['Mass(2)'][:]
        dco_seeds = double_compact_objects['SEED'][:]
        merge_mask = double_compact_objects['Merges_Hubble_Time'][:] == 1

        interesting_mask = np.logical_and(merge_mask,
                                          SYSTEMS_OF_INTEREST[SYS_INT](st1, st2, m1, m2))

        interesting_seeds = set(dco_seeds[interesting_mask])
        for sample in batch['samples']:
            if sample.properties['SEED'] in interesting_seeds:
                sample.properties['is_hit'] = 1

        if sfile is not None:
            sfile.close()

        return int(np.sum(interesting_mask))

    # KeyError: an expected group/column is missing from the h5.
    # OSError/IOError: the file is missing or unreadable, i.e. COMPAS failed.
    except (IOError, OSError, KeyError) as error:
        print(f'Error in interesting_systems(batch {batch.get("number")}): '
              f'{type(error).__name__}: {error}\n'
              '  Either the COMPAS run for this batch failed (check the batch logs\n'
              '  under the MainRun directory), or its output is missing a group this\n'
              '  function expects. Treating the batch as zero hits and continuing.')
        return 0


def _zams_radius(mass, metallicity):
    """
    Vectorised ZAMS radius (Tout et al. 1996), in AU.

    Same arithmetic as stroopwafel.utils.get_zams_radius, done on whole arrays.
    The scalar version is called twice per sampled system and re-derives the
    9x5 metallicity polynomial every single time; at a million rejection
    samples that alone was over half the runtime.

    The coefficient accumulation below deliberately mirrors the scalar loop
    (`total += series * value; value *= xi`) rather than using a polynomial
    helper, so the floating-point operations happen in the same order and the
    result is bit-for-bit identical.
    """
    mass = np.asarray(mass, dtype=float)
    metallicity_xi = np.log10(np.asarray(metallicity, dtype=float) / constants.ZSOL)

    coeffs = []
    for coeff in constants.R_COEFF:
        value = np.ones_like(metallicity_xi)
        total = np.zeros_like(metallicity_xi)
        for series in coeff:
            total = total + series * value
            value = value * metallicity_xi
        coeffs.append(total)

    top = (coeffs[0] * mass ** 2.5 + coeffs[1] * mass ** 6.5
           + coeffs[2] * mass ** 11 + coeffs[3] * mass ** 19
           + coeffs[4] * mass ** 19.5)
    bottom = (coeffs[5] + coeffs[6] * mass ** 2 + coeffs[7] * mass ** 8.5
              + mass ** 18.5 + coeffs[8] * mass ** 19.5)
    return top / bottom * constants.R_SOL_TO_AU


def _roche_lobe_radius(mass1, mass2):
    """Vectorised Eggleton (1983) Roche lobe radius; mirrors utils.calculate_roche_lobe_radius."""
    q = np.asarray(mass1, dtype=float) / np.asarray(mass2, dtype=float)
    return 0.49 / (0.6 + q ** (-2.0 / 3.0) * np.log(1.0 + q ** (1.0 / 3.0)))


def rejected_systems(locations, dimensions):
    """
    Mark systems the birth distribution would never produce (touching at birth,
    Roche-lobe overflow at ZAMS, secondary below the minimum mass), so they do
    not eat into the sample budget.

    Stroopwafel calls this with up to TOTAL_REJECTION_SAMPLES (1e6) locations at
    the start, and again with REJECTION_SAMPLES_PER_BATCH for EVERY adapted
    Gaussian -- so its cost scales with the number of hits. Doing it per system
    in python is what makes a large run crawl between batches, hence the
    array-at-a-time version here.
    """
    if len(locations) == 0:
        return 0

    m1, q, a = dimensions[0], dimensions[1], dimensions[2]
    mass_1 = np.fromiter((loc.dimensions[m1] for loc in locations), float, len(locations))
    mass_2 = np.fromiter((loc.properties['--initial-mass-2'] for loc in locations),
                         float, len(locations))
    try:
        metallicity = np.fromiter((loc.properties['--metallicity'] for loc in locations),
                                  float, len(locations))
    except KeyError:
        Z = dimensions[3]
        metallicity = np.fromiter((loc.dimensions[Z] for loc in locations),
                                  float, len(locations))
    eccentricity = np.fromiter((loc.properties['--eccentricity'] for loc in locations),
                               float, len(locations))
    separation = np.fromiter((loc.dimensions[a] for loc in locations), float, len(locations))

    radius_1 = _zams_radius(mass_1, metallicity)
    radius_2 = _zams_radius(mass_2, metallicity)
    sep = separation * (1 - eccentricity)
    rl_1 = radius_1 / (sep * _roche_lobe_radius(mass_1, mass_2))
    rl_2 = radius_2 / (sep * _roche_lobe_radius(mass_2, mass_1))

    rejected = ((mass_2 < constants.MINIMUM_SECONDARY_MASS)
                | (separation <= (radius_1 + radius_2))
                | (rl_1 > 1) | (rl_2 > 1))

    for location, is_rejected in zip(locations, rejected):
        location.properties['is_rejected'] = int(is_rejected)
    return int(rejected.sum())


def build_command_options(use_run_submit, config_file, output_folder):
    """
    Base COMPAS options for every batch. If use_run_submit is set we take them
    from compasConfig.yaml via runSubmit, so the AIS run and a plain grid run
    share one source of truth for the physics.
    """
    command_options = {'--output-path': output_folder, '--logfile-type': 'HDF5'}

    if not use_run_submit:
        return command_options

    try:
        from runSubmit import pythonProgramOptions
        options = pythonProgramOptions(config_file=config_file)
        yaml_options = dict(options.command)
        # stroopwafel sets these itself, per batch
        for key in ('--grid', '--output-container', '--number-of-systems',
                    '--number-of-binaries', '--output-path', '--random-seed'):
            yaml_options.pop(key, None)
        command_options.update(yaml_options)
        print(f"Loaded COMPAS options from {config_file}")
    except Exception as err:
        print(f"WARNING: could not read {config_file} ({err}).\n"
              "         Falling back to stroopwafel defaults -- your physics "
              "settings will NOT be applied.")
    return command_options


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--num_systems', type=int, default=int(1e6),
                        help='total number of systems to evolve')
    parser.add_argument('--num_cores', type=int, default=10,
                        help='how many COMPAS processes to run in parallel')
    parser.add_argument('--num_per_core', type=int, default=int(1e4),
                        help='systems per batch (one batch = one core at a time)')
    parser.add_argument('--output_folder', required=True,
                        help='MainRun directory to write batches into')
    parser.add_argument('--output_filename', default='samples.csv',
                        help='stroopwafel sample/weight table (default samples.csv)')
    parser.add_argument('--config_file', default='compasConfig.yaml',
                        help='COMPAS yaml holding the physics settings')
    parser.add_argument('--sys_int', default='DNS', choices=sorted(SYSTEMS_OF_INTEREST),
                        help='which systems count as hits')
    parser.add_argument('--random_seed_base', type=int, default=0,
                        help='COMPAS random seed of the first system')
    parser.add_argument('--mc_only', action='store_true',
                        help='plain Monte Carlo: skip adapt/refine (no AIS)')
    parser.add_argument('--no_hpc', action='store_true',
                        help='run locally instead of submitting batches through slurm')
    parser.add_argument('--quiet', action='store_true',
                        help='hide COMPAS stdout/stderr')
    namespace, extra_params = parser.parse_known_args()

    # -- module-level state the stroopwafel callbacks read -------------------
    global OUTPUT_FOLDER, COMPAS_EXECUTABLE, COMMAND_OPTIONS, EXTRA_PARAMS
    global NUM_SYSTEMS_PER_RUN, RANDOM_SEED_BASE, SYS_INT, HDF5

    compas_root = os.environ.get('COMPAS_ROOT_DIR')
    if not compas_root:
        raise SystemExit("COMPAS_ROOT_DIR is not set -- see docs/01_setup.md")
    COMPAS_EXECUTABLE = os.path.join(compas_root, 'src/COMPAS')
    if not os.path.isfile(COMPAS_EXECUTABLE):
        raise SystemExit(f"No COMPAS executable at {COMPAS_EXECUTABLE}")

    OUTPUT_FOLDER = os.path.abspath(namespace.output_folder)
    NUM_SYSTEMS_PER_RUN = namespace.num_per_core
    RANDOM_SEED_BASE = namespace.random_seed_base
    SYS_INT = namespace.sys_int
    EXTRA_PARAMS = extra_params
    HDF5 = True

    # Refuse to silently overwrite a finished run.
    if os.path.exists(os.path.join(OUTPUT_FOLDER, 'COMPAS_Output.h5')):
        raise SystemExit(
            f"{OUTPUT_FOLDER} already contains COMPAS_Output.h5 -- this run looks "
            "finished. Move it aside or pick a new run name before rerunning.")
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    COMMAND_OPTIONS = build_command_options(True, namespace.config_file, OUTPUT_FOLDER)

    print(f"Output folder     : {OUTPUT_FOLDER}")
    print(f"Systems of interest: {SYS_INT}")
    print(f"Total systems      : {namespace.num_systems:.3g} "
          f"on {namespace.num_cores} cores, {NUM_SYSTEMS_PER_RUN} per batch")

    start_time = time.time()
    sw_object = sw.Stroopwafel(
        namespace.num_systems, namespace.num_cores, NUM_SYSTEMS_PER_RUN,
        OUTPUT_FOLDER, namespace.output_filename,
        debug=not namespace.quiet,
        run_on_helios=not namespace.no_hpc,
        mc_only=namespace.mc_only,
    )

    dimensions = create_dimensions()
    sw_object.initialize(dimensions, interesting_systems, configure_code_run,
                         rejected_systems, update_properties_method=update_properties)

    initial_pdf = distributions.InitialDistribution(dimensions)

    # -- phase 1: explore ---------------------------------------------------
    t = time.time()
    sw_object.explore(initial_pdf)
    print("explore time = %d seconds" % (time.time() - t))

    if not namespace.mc_only:
        # -- phase 2: adapt -------------------------------------------------
        t = time.time()
        sw_object.adapt(n_dimensional_distribution_type=distributions.Gaussian)
        print("adapt time = %d seconds" % (time.time() - t))

        # -- phase 3: refine ------------------------------------------------
        t = time.time()
        sw_object.refine()
        print("refine time = %d seconds" % (time.time() - t))

    # -- phase 4: weights ---------------------------------------------------
    t = time.time()
    sw_object.postprocess(distributions.Gaussian, only_hits=False)
    print("postprocess time = %d seconds" % (time.time() - t))

    print("Total running time = %d seconds" % (time.time() - start_time))


if __name__ == '__main__':
    main()
