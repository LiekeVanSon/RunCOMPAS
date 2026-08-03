#!/usr/bin/env python3
"""
runSubmit.py -- turn a COMPAS yaml config into a COMPAS command line, and run it.

This is the standard COMPAS runSubmit, with one addition: any COMPAS option you
pass on the command line overrides the value in the yaml file. That is what lets
one slurm array task say "I am batch 7, evolve grid lines 7000-7999" without
anybody editing the yaml file.

    # use the yaml as-is
    python3 runSubmit.py compasConfig.yaml

    # same, but override three options for this invocation only
    python3 runSubmit.py compasConfig.yaml \
        --grid-start-line 7000 --grid-lines-to-process 1000 \
        --output-container batch_7

    # see the command that would run, without running it
    python3 runSubmit.py compasConfig.yaml --dry-run

Boolean COMPAS options are passed bare (e.g. --detailed-output).

Adapted from the COMPAS-provided runSubmit.py. See ../../docs/02_grid_runs.md.
"""
import os
import sys
import argparse
import warnings
from subprocess import call

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_FILE = os.path.join(HERE, 'compasConfig.yaml')


class pythonProgramOptions:
    """
    Stores COMPAS program options read from a yaml file, plus any overrides.

    overrides : dict of {'--option': 'value'}  applied after the yaml is read.
                A value of '' means a bare boolean flag.
    """

    def __init__(self, config_file=DEFAULT_CONFIG_FILE, grid_filename=None,
                 random_seed_filename='randomSeed.txt', output_directory=None,
                 overrides=None):

        with open(config_file) as f:
            config = yaml.load(f, Loader=yaml.FullLoader)

        self.booleanChoices = config.get('booleanChoices') or {}
        self.numericalChoices = config.get('numericalChoices') or {}
        self.stringChoices = config.get('stringChoices') or {}
        self.listChoices = config.get('listChoices') or {}

        # ---- locate the COMPAS executable -------------------------------
        compas_root_dir = os.environ.get('COMPAS_ROOT_DIR')
        if compas_root_dir is None:
            raise RuntimeError(
                "COMPAS_ROOT_DIR is not set.\n"
                "The job script normally exports it from your cluster profile "
                "(clusters/<name>.yaml : compas_root). To run by hand:\n"
                "    export COMPAS_ROOT_DIR=/path/to/COMPAS"
            )
        compas_exe = os.path.join(compas_root_dir, 'src/COMPAS')
        # COMPAS_EXECUTABLE_PATH is used for docker runs
        self.compas_executable = os.environ.get('COMPAS_EXECUTABLE_PATH', compas_exe)
        if not os.path.isfile(self.compas_executable):
            raise RuntimeError(
                f"No COMPAS executable at {self.compas_executable}\n"
                "Check compas_root in your cluster profile, and that COMPAS is built."
            )

        # ---- random seed from file, if present --------------------------
        if os.path.isfile(random_seed_filename):
            import numpy as np
            self.numericalChoices['--random-seed'] = int(np.loadtxt(random_seed_filename))

        # ---- grid file --------------------------------------------------
        if grid_filename:
            self.stringChoices['--grid'] = grid_filename
        self.grid_filename = self.stringChoices.get('--grid') or None

        # ---- output location --------------------------------------------
        compas_logs_output_override = os.environ.get('COMPAS_LOGS_OUTPUT_DIR_PATH')
        if compas_logs_output_override is None:
            self.stringChoices.setdefault('--output-path', os.getcwd())
        else:
            self.stringChoices['--output-path'] = compas_logs_output_override
        if output_directory is not None:
            self.stringChoices['--output-container'] = output_directory

        self.overrides = overrides or {}
        self.makeCommandString()

    def makeCommandString(self):
        """
        Build {option: value} for every option set in the yaml, apply the
        command-line overrides on top, then flatten to a shell string.
        """
        self.command = {}

        for k, v in self.booleanChoices.items():
            # True -> bare flag; False -> explicit 'False' (COMPAS accepts this)
            self.command[k] = '' if v is True else 'False'

        for k, v in self.numericalChoices.items():
            if v is not None:
                self.command[k] = str(v)

        for k, v in self.stringChoices.items():
            if v is not None:
                self.command[k] = str(v)

        for k, v in self.listChoices.items():
            if v:
                self.command[k] = ' '.join(map(str, v))

        # Command-line overrides win over everything in the yaml.
        for k, v in self.overrides.items():
            self.command[k] = v

        self.shellCommand = self.compas_executable
        for key, val in self.command.items():
            self.shellCommand += f' {key} {val}' if val != '' else f' {key}'

        return self.shellCommand


def parse_overrides(extra):
    """
    Turn a leftover argv list into an overrides dict.

        ['--grid-start-line', '7000', '--detailed-output', '--output-container', 'batch_7']
     -> {'--grid-start-line': '7000', '--detailed-output': '', '--output-container': 'batch_7'}

    A flag with no following value (or followed by another --flag) is treated as
    a bare boolean. Values that are themselves negative numbers are handled.
    """
    overrides = {}
    i = 0
    while i < len(extra):
        tok = extra[i]
        if not tok.startswith('--'):
            raise ValueError(
                f"Unexpected argument {tok!r}. COMPAS overrides must look like "
                f"'--option value' or '--boolean-option'."
            )
        if '=' in tok:                       # --option=value form
            key, val = tok.split('=', 1)
            overrides[key] = val
            i += 1
            continue
        nxt = extra[i + 1] if i + 1 < len(extra) else None
        is_value = nxt is not None and (
            not nxt.startswith('--') or _looks_numeric(nxt)
        )
        if is_value:
            overrides[tok] = nxt
            i += 2
        else:
            overrides[tok] = ''              # bare boolean flag
            i += 1
    return overrides


def _looks_numeric(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(
        description=("Run COMPAS from a yaml config. Any additional --option value "
                     "pairs override the yaml for this run only."),
        epilog="For the full option list run:  $COMPAS_ROOT_DIR/src/COMPAS --help",
    )
    parser.add_argument('config_file', nargs='?', default=DEFAULT_CONFIG_FILE,
                        help='COMPAS yaml config (default: ./compasConfig.yaml)')
    parser.add_argument('--dry-run', action='store_true',
                        help='print the COMPAS command instead of running it')
    args, extra = parser.parse_known_args()

    overrides = parse_overrides(extra)
    options = pythonProgramOptions(config_file=args.config_file, overrides=overrides)

    print(options.shellCommand, flush=True)
    if args.dry_run:
        return 0

    rc = call(options.shellCommand, shell=True)
    if rc != 0:
        print(f"\nCOMPAS exited with return code {rc}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
