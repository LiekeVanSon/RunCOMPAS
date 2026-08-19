#!/usr/bin/env python3
"""
=============================================================================
 archive_run.py  --  move finished runs off the working filesystem
=============================================================================

 Some clusters cannot write their big data volume from compute nodes, so runs
 have to land somewhere smaller first (see `output_root` in your cluster
 profile). This moves a completed run from there to `archive_root`, which is
 the big volume.

 RUN THIS ON THE LOGIN NODE. That is usually the only place with write access
 to the archive volume -- which is the whole reason jobs cannot write there
 themselves.

 USAGE
     python3 archive_run.py --list             # what is taking up space
     python3 archive_run.py my_run --dry-run   # show what would move
     python3 archive_run.py my_run             # move it
     python3 archive_run.py my_run --copy      # copy, keep the original
     python3 archive_run.py --all              # archive every finished run

 DIRECTORY STRUCTURE
 Whatever nesting you use under output_root is mirrored into archive_root, so
 the two layouts always match:

     $HOME/CompasOutput/v03.29.05/N1e6_fid          (working, jobs write here)
       ->  /vol/astro8/.../CompasOutput/v03.29.05/N1e6_fid    (archive)

 That extra level comes from RUN_SUBDIR in the driver scripts. Name a run
 either by its full relative path or by its bare name when unambiguous:

     python3 archive_run.py v03.29.05/N1e6_fid
     python3 archive_run.py N1e6_fid

 To archive somewhere other than the profile's roots, be explicit:

     python3 archive_run.py my_run --from /some/where --to /some/place

 A run is considered finished when it has a merged COMPAS_Output.h5 (or
 COMPAS_Output_wWeights.h5). Unfinished runs are skipped unless you pass
 --force, so you cannot archive a run out from under a queued job.

 SAFETY
 Transfers use rsync, so an interrupted one (dropped connection, Ctrl-C, full
 filesystem) resumes on the next --force run instead of starting over, and you
 get progress on a multi-GB move.

 A move NEVER deletes the source until the destination has been verified to
 have the same number of files and the same total bytes. If anything goes
 wrong, the original is still there and you can just run it again.

 See docs/06_troubleshooting.md
=============================================================================
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys

import yaml

REPO = os.path.dirname(os.path.abspath(__file__))


def load_cluster(name=None):
    """Read clusters/<name>.yaml, or auto-detect one from the hostname."""
    cdir = os.path.join(REPO, 'clusters')
    if name:
        path = os.path.join(cdir, f'{name}.yaml')
        if not os.path.isfile(path):
            raise SystemExit(f"No cluster profile {path}")
    else:
        host = socket.gethostname()
        path = None
        for candidate in sorted(os.listdir(cdir)):
            if not candidate.endswith('.yaml') or candidate == 'TEMPLATE.yaml':
                continue
            with open(os.path.join(cdir, candidate)) as f:
                prof = yaml.safe_load(f) or {}
            if any(m in host for m in (prof.get('hostname_match') or [])):
                path = os.path.join(cdir, candidate)
                break
        if path is None:
            raise SystemExit(f"Could not match hostname {host!r} to a cluster profile.")

    with open(path) as f:
        profile = yaml.safe_load(f)
    profile['_path'] = path
    return profile


def dir_size(path, apparent=False):
    """
    Bytes under path. Uses du, which is far faster than walking in python.

    By default this is DISK USAGE, not apparent size, because that is what fills
    a quota. The two differ a lot here: COMPAS h5 files are sparse, so a run
    reporting 47 GB of apparent size may occupy only 16 GB on disk.
    """
    flag = '-sb' if apparent else '-s'
    try:
        cmd = ['du', flag, path] if apparent else ['du', '-s', '--block-size=1', path]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return int(out.stdout.split()[0])
    except Exception:
        return 0


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def find_runs(root):
    """
    Every run directory under root, as paths relative to it.

    A run is any directory containing a MainRun/ subdirectory, so this works
    whatever nesting you use -- "myrun", "v03.29.05/myrun", or deeper. The
    relative path is what gets mirrored into the archive, which is how the
    working and archived layouts stay identical.
    """
    runs = []
    for dirpath, dirnames, _ in os.walk(root):
        if 'MainRun' in dirnames:
            runs.append(os.path.relpath(dirpath, root))
            dirnames[:] = []          # don't descend into a run
    return sorted(runs)


def resolve_run(name, runs):
    """
    Accept either a full relative path ("v03.29.05/myrun") or a bare run name
    ("myrun") when it is unambiguous.
    """
    name = name.strip('/')
    if name in runs:
        return name
    matches = [r for r in runs if os.path.basename(r) == name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(
            f"{name!r} is ambiguous -- it exists in several places:\n"
            + '\n'.join(f"    {m}" for m in matches)
            + "\nGive the full relative path.")
    return None


def is_finished(run_dir):
    """A run is finished once postProcessing has produced a merged file."""
    main = os.path.join(run_dir, 'MainRun')
    return any(os.path.isfile(os.path.join(main, f))
               for f in ('COMPAS_Output.h5', 'COMPAS_Output_wWeights.h5'))


def queued_jobs():
    """Job names currently in the queue, so we never move a live run."""
    try:
        out = subprocess.run(['squeue', '-u', os.environ.get('USER', ''), '-h', '-o', '%j'],
                             capture_output=True, text=True, timeout=60)
        return set(out.stdout.split())
    except Exception:
        return set()


def looks_busy(run_name, names):
    """Job names are prefixed grid_/sw_/pp_/ci_ by the drivers."""
    return any(n.endswith(run_name) for n in names)


def have_rsync():
    return shutil.which('rsync') is not None


def tree_stats(path):
    """(file count, total bytes) under path -- used to verify a transfer."""
    n = total = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
                n += 1
            except OSError:
                pass
    return n, total


def transfer(src, dst, dry_run=False, checksum=False):
    """
    Copy src -> dst, preferring rsync.

    rsync matters here because these are multi-GB trees over NFS:

      * it resumes. An interrupted transfer (dropped connection, Ctrl-C, a full
        filesystem) leaves what it managed to send, and running the same command
        again continues from there. shutil starts from zero every time.
      * it shows progress, so a 20 GB move is not a silent ten-minute stare.
      * --partial keeps partly-written files instead of discarding them.

    Falls back to shutil.copytree if rsync is missing, which still works, just
    without resume or progress.
    """
    if dry_run:
        return True

    os.makedirs(os.path.dirname(dst.rstrip('/')) or '/', exist_ok=True)

    if not have_rsync():
        print("        (rsync not found -- falling back to a plain copy, "
              "no resume or progress)")
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return True

    # --sparse matters a lot here. COMPAS h5 files are full of holes: unwritten
    # parts of the preallocated HDF5 chunks are never materialised on disk, so a
    # 170 MB file can occupy 45 MB. A plain copy fills those holes in for real.
    # Measured on one batch file, source 45 MB on disk -> 268 MB without
    # --sparse, 108 MB with it. Content is byte-identical either way.
    cmd = ['rsync', '-a', '--sparse', '--partial']
    if sys.stdout.isatty():
        cmd.append('--info=progress2')
    if checksum:
        cmd.append('--checksum')
    # trailing slashes: copy the CONTENTS of src into dst
    cmd += [src.rstrip('/') + '/', dst.rstrip('/') + '/']

    result = subprocess.run(cmd)
    return result.returncode == 0


def archive_one(rel_path, output_root, archive_root, copy=False,
                dry_run=False, force=False, busy=frozenset(),
                checksum=False):
    # the relative path is mirrored, so <root>/v03.29.05/myrun keeps its
    # v03.29.05/ level in the archive
    src = os.path.join(output_root, rel_path)
    dst = os.path.join(archive_root, rel_path)
    run_name = os.path.basename(rel_path)

    if not os.path.isdir(src):
        print(f"  SKIP  {rel_path}: not found in {output_root}")
        return False
    if os.path.exists(dst) and not force:
        print(f"  SKIP  {rel_path}: already exists at {dst}\n"
              f"        If a previous transfer was interrupted, --force resumes it "
              f"(rsync only sends what is missing).")
        return False
    if looks_busy(run_name, busy) and not force:
        print(f"  SKIP  {rel_path}: it still has jobs in the queue")
        return False
    if not is_finished(src) and not force:
        print(f"  SKIP  {rel_path}: no merged COMPAS_Output.h5 yet -- not finished "
              f"(use --force to archive anyway)")
        return False

    size = dir_size(src)
    verb = "copy" if copy else "move"
    print(f"  {verb} {rel_path}  ({human(size)})")
    print(f"        {src}")
    print(f"     -> {dst}")

    if dry_run:
        return True

    try:
        ok = transfer(src, dst, checksum=checksum)
    except OSError as err:
        raise SystemExit(f"\nFailed to {verb} {rel_path}: {err}\n"
                         f"Are you on the login node? Compute nodes usually cannot "
                         f"write to {archive_root}.")
    if not ok:
        print(f"  FAILED {rel_path}: transfer did not complete.\n"
              f"        Nothing was deleted. Re-run with --force to resume.")
        return False

    # Verify BEFORE deleting anything. A move that loses data because the copy
    # was silently short is the one failure mode that cannot be undone, so the
    # source stays put until the destination is known to match.
    src_files, src_bytes = tree_stats(src)
    dst_files, dst_bytes = tree_stats(dst)
    if (src_files, src_bytes) != (dst_files, dst_bytes):
        print(f"  FAILED {rel_path}: destination does not match the source\n"
              f"        source: {src_files} files, {human(src_bytes)}\n"
              f"        dest  : {dst_files} files, {human(dst_bytes)}\n"
              f"        Nothing was deleted. Re-run with --force to resume.")
        return False
    print(f"        verified {dst_files} files, {human(dst_bytes)}")

    if not copy:
        shutil.rmtree(src)
        print(f"        removed the original")
    print(f"        done")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run_name', nargs='*', help='run directory name(s) to archive')
    ap.add_argument('--list', action='store_true', help='list runs and their sizes, then exit')
    ap.add_argument('--all', action='store_true', help='archive every finished run')
    ap.add_argument('--copy', action='store_true', help='copy instead of move')
    ap.add_argument('--dry-run', action='store_true', help='show what would happen')
    ap.add_argument('--force', action='store_true',
                    help='archive even if unfinished, busy, or already present; also resumes an interrupted transfer')
    ap.add_argument('--checksum', action='store_true',
                    help='rsync --checksum: compare file contents, not size+mtime (slow, paranoid)')
    ap.add_argument('--cluster', default=None, help='override the cluster profile')
    ap.add_argument('--from', dest='src_root', default=None,
                    help='override output_root (where runs are now)')
    ap.add_argument('--to', dest='dst_root', default=None,
                    help='override archive_root (where they should go)')
    args = ap.parse_args()

    profile = load_cluster(args.cluster)
    output_root = args.src_root or profile['output_root']
    archive_root = args.dst_root or (profile.get('archive_root') or '').strip()

    if not archive_root:
        raise SystemExit(
            f"No archive_root set in {profile['_path']}, and no --to given.\n"
            f"Add one, e.g.:\n    archive_root: \"/vol/astro8/<you>/CompasOutput\"")

    print(f"cluster profile : {profile['_path']}")
    print(f"working storage : {output_root}")
    print(f"archive         : {archive_root}\n")

    if not os.path.isdir(output_root):
        raise SystemExit(f"{output_root} does not exist -- nothing to archive.")

    # relative paths, so any nesting (e.g. v03.29.05/myrun) is mirrored as-is
    runs = find_runs(output_root)

    if args.list:
        if not runs:
            print("  (no runs)")
            return
        busy = queued_jobs()
        total = 0
        total_apparent = 0
        for r in runs:
            path = os.path.join(output_root, r)
            size = dir_size(path)
            apparent = dir_size(path, apparent=True)
            total += size
            total_apparent += apparent
            state = ("in queue" if looks_busy(os.path.basename(r), busy)
                     else "finished" if is_finished(path) else "unfinished")
            sparse = f"  ({human(apparent)} apparent)" if apparent > 1.5 * size else ""
            print(f"  {human(size):>10}  {state:<11} {r}{sparse}")
        print(f"  {human(total):>10}  TOTAL on disk")
        if total_apparent > 1.5 * total:
            print(f"  {human(total_apparent):>10}  total apparent size -- the difference is "
                  f"holes in\n              the HDF5 files. Archiving preserves them "
                  f"(rsync --sparse).")
        return

    if args.all:
        targets = runs
    elif args.run_name:
        targets = []
        for name in args.run_name:
            match = resolve_run(name, runs)
            if match is None:
                print(f"  SKIP  {name}: no such run under {output_root}")
                continue
            targets.append(match)
    else:
        raise SystemExit("Give a run name, or --all, or --list. See --help.")

    busy = queued_jobs()
    n = sum(archive_one(r, output_root, archive_root, args.copy,
                        args.dry_run, args.force, busy, args.checksum)
            for r in targets)

    print(f"\n{n} run(s) {'would be ' if args.dry_run else ''}archived.")
    if args.dry_run:
        print("(dry run -- nothing was moved)")


if __name__ == '__main__':
    main()
