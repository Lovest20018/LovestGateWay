#!/usr/bin/env python3
"""Validate a release's exact source tree, package identity and checksums before upload."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integration'))
from manage_deploy import verify_package

VERSION = re.compile(r'^\d+\.\d+\.\d+(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?$')
SHA = re.compile(r'^[0-9a-f]{40}$')


def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + ROOT.as_posix(), '-C', str(ROOT), *args], stderr=subprocess.PIPE)


def file_sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_source(path, commit, version, normalize_lf=False):
    tracked = {}
    for row in git('ls-tree', '-rz', commit).split(b'\0'):
        if not row: continue
        metadata, name = row.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        if kind != 'blob' or mode not in {'100644', '100755'}: raise ValueError('Unsupported source entry')
        tracked[name.decode()] = oid
    actual, manifest = {}, {}
    total = 0
    prefix = f'lovestgateway-{version}-source/'
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive:
            name = item.name
            if name.rstrip('/') == prefix.rstrip('/') and item.isdir(): continue
            if name.startswith(prefix): name = name[len(prefix):]
            if (not name or '\\' in name or ':' in name or PurePosixPath(name).is_absolute()
                    or any(part in {'', '.', '..'} for part in name.rstrip('/').split('/'))):
                raise ValueError('Unsafe source archive path')
            if item.isdir(): continue
            total += item.size
            if (not item.isfile() or name in actual or item.size > 128*1024*1024
                    or total > 512*1024*1024 or len(actual) >= 4096):
                raise ValueError('Unexpected source archive entry')
            raw = archive.extractfile(item).read()
            actual[name] = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if normalize_lf and b'\0' not in raw and actual[name] != tracked.get(name):
                canonical = raw.replace(b'\r\n', b'\n')
                actual[name] = hashlib.sha1(b'blob ' + str(len(canonical)).encode() + b'\0' + canonical).hexdigest()
            relative = PurePosixPath(name)
            if (any(p in {'.git', 'dist', 'target', '__pycache__'} for p in relative.parts)
                    or relative.parts[:2] == ('docs', 'validation')):
                continue
            if relative.suffix in {'.rs', '.py', '.sh', '.service', '.timer', '.yml', '.yaml', '.json', '.toml', '.md'}:
                raw = raw.replace(b'\r\n', b'\n')
            manifest[name] = hashlib.sha256(raw).hexdigest()
    if actual != tracked: raise ValueError('Source archive does not match the release commit')
    return hashlib.sha256((json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()).hexdigest()


def validate(directory, version, commit, ledger):
    if not VERSION.fullmatch(version) or not SHA.fullmatch(commit): raise ValueError('Invalid release identity')
    if git('rev-parse', commit + '^{commit}').decode().strip() != commit: raise ValueError('Not a full commit')
    spec = importlib.util.spec_from_file_location('upstream_checker', Path(__file__).with_name('check-upstream.py'))
    checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)
    normalize_lf = False
    try:
        upstream = checker.validate_ledger(json.loads(git('show', commit + ':.github/upstream.json')))
    except subprocess.CalledProcessError:
        # The first validated release predates this ledger. No other old commit may borrow it.
        upstream = checker.ledger_value(ledger)
        if commit != upstream.get('initial_release_commit'):
            raise ValueError('Release commit has no recorded upstream baseline') from None
        normalize_lf = True
    package = directory / f'lovestgateway-{version}-debian-amd64.tar.gz'
    source = directory / f'lovestgateway-{version}-source.tar.gz'
    assets = {}
    for path in (package, source):
        checksum = path.with_suffix(path.suffix + '.sha256')
        if any(item.is_symlink() or not item.is_file() for item in (path, checksum)):
            raise ValueError('Release assets must be regular files')
        expected = f'{file_sha(path)}  {path.name}'
        if checksum.read_text(encoding='ascii').strip() != expected: raise ValueError('Invalid external checksum')
        for item in (path, checksum): assets[item.name] = {'sha256': file_sha(item), 'bytes': item.stat().st_size}
    with verify_package(package, file_sha(package)) as release:
        if release.build.get('version') != version or release.build.get('build_commit') != commit:
            raise ValueError('Package version/commit mismatch')
        build = release.build
    if verify_source(source, commit, version, normalize_lf=normalize_lf) != build['source_sha256']:
        raise ValueError('Package source manifest does not match the source archive')
    try: installer = git('show', commit + ':install.sh')
    except subprocess.CalledProcessError: installer = None  # historical releases predate the online entry point
    if installer is not None:
        path = directory / 'install.sh'
        if path.is_symlink() or not path.is_file() or path.read_bytes() != installer:
            raise ValueError('Online installer does not match the release commit')
        assets[path.name] = {'sha256': file_sha(path), 'bytes': path.stat().st_size}
    return {'schema': 1, 'version': version, 'tag': 'v' + version, 'commit': commit,
            'architecture': 'amd64', 'source_manifest_sha256': build['source_sha256'],
            'source_export': 'git-tree-checkout-crlf' if normalize_lf else 'git-archive',
            'upstream': {key: upstream[key] for key in ('repository', 'merged_base', 'reviewed_head', 'reviewed_release')},
            'assets': assets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--ledger', type=Path, default=ROOT / '.github/upstream.json')
    args = parser.parse_args()
    value = validate(args.directory, args.version, args.commit, args.ledger)
    (args.directory / 'release-manifest.json').write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'validated': True, 'tag': value['tag'], 'commit': value['commit'], 'assets': len(value['assets'])}))


if __name__ == '__main__': main()
