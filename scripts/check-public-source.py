#!/usr/bin/env python3
"""Reject deployment records, runtime credentials, and their reachable history."""
import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PREFIXES = ('dist/', 'docs/validation/', 'docs/superpowers/')
RUNTIME_NAMES = {'read-token', 'core-token', 'revision-key', 'instance-id',
                 'proxy-auth', 'service.env', 'api.json', 'maintenance.json', '.env'}


def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + ROOT.as_posix(),
                                   '-C', str(ROOT), *args], stderr=subprocess.PIPE)


def private_path(name):
    path = PurePosixPath(name)
    return (name.startswith(PRIVATE_PREFIXES) or path.name in RUNTIME_NAMES
            or path.suffix in {'.bundle', '.bak', '.key', '.pem'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pre-push', action='store_true', help='check every ref supplied by Git on stdin')
    args = parser.parse_args()
    revisions = ['HEAD']
    if args.pre_push:
        revisions = []
        for row in sys.stdin:
            parts = row.split()
            if len(parts) != 4 or not re.fullmatch(r'[0-9a-f]{40,64}', parts[1]):
                parser.error('invalid pre-push input')
            if set(parts[1]) != {'0'}:
                revisions.append(parts[1])
    try:
        paths = set()
        for revision in set(revisions):
            for row in git('rev-list', '--objects', revision).decode('utf-8').splitlines():
                _, sep, name = row.partition(' ')
                if sep and private_path(name): paths.add(name)
        if not args.pre_push:
            paths.update(name for name in git('ls-files', '-z').decode('utf-8').split('\0') if name and private_path(name))
        if paths:
            print(f'Public source check rejected {len(paths)} private paths, including reachable history. '
                  'Keep deployment records outside Git and base changes on the sanitized main.', file=sys.stderr)
            return 1
    except (subprocess.CalledProcessError, UnicodeError):
        print('Public source check failed to inspect Git history; refusing to continue.', file=sys.stderr)
        return 1
    print(f'Public source check passed ({len(set(revisions))} refs).')
    return 0


if __name__ == '__main__': raise SystemExit(main())
