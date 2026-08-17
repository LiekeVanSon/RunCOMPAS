"""
Shared path setup for the tools in this directory.

They need to import two things that are not on the default path: stroopwafel
(a git clone, see docs/01_setup.md) and the repo's own
masterfolder/MainRun/stroopwafel_interface.py. Rather than making you export
environment variables, this works both out for you from the cluster profile.

Override either with STROOPWAFEL_PATH / INTERFACE_DIR if you want to test a
different clone or a modified interface.
"""
import os
import socket
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _stroopwafel_from_profile():
    """stroopwafel_path out of clusters/<matching host>.yaml, if there is one."""
    try:
        import yaml
    except ImportError:
        return None
    cdir = os.path.join(REPO, 'clusters')
    if not os.path.isdir(cdir):
        return None
    host = socket.gethostname()
    for candidate in sorted(os.listdir(cdir)):
        if not candidate.endswith('.yaml') or candidate == 'TEMPLATE.yaml':
            continue
        try:
            with open(os.path.join(cdir, candidate)) as f:
                profile = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        if any(m in host for m in (profile.get('hostname_match') or [])):
            path = (profile.get('stroopwafel_path') or '').strip()
            return os.path.expanduser(path) if path else None
    return None


def setup(need_interface=True):
    """
    Put stroopwafel and the repo interface on sys.path.
    Returns the stroopwafel directory used ('' if relying on an installed copy).
    """
    sw = os.environ.get('STROOPWAFEL_PATH', '').strip() or _stroopwafel_from_profile()
    if sw:
        sw = os.path.expanduser(sw)
        if not os.path.isdir(sw):
            raise SystemExit(
                f"stroopwafel not found at {sw}\n"
                f"Set STROOPWAFEL_PATH, or fix stroopwafel_path in your cluster "
                f"profile. See docs/01_setup.md")
        sys.path.insert(0, sw)

    if need_interface:
        interface = os.environ.get('INTERFACE_DIR', '').strip() or \
            os.path.join(REPO, 'masterfolder', 'MainRun')
        if not os.path.isfile(os.path.join(interface, 'stroopwafel_interface.py')):
            raise SystemExit(f"No stroopwafel_interface.py in {interface}")
        sys.path.insert(0, interface)

    try:
        import stroopwafel  # noqa: F401
    except ImportError as err:
        raise SystemExit(
            f"Could not import stroopwafel ({err}).\n"
            f"Clone it and set stroopwafel_path in your cluster profile -- see "
            f"docs/01_setup.md section 3.")

    return sw or '(installed package)'
