#!/usr/bin/env python3
"""Build the maintained dashboard source and redirects for legacy entries."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def prepare_dashboard(output):
    node = os.environ.get('PPGW_NODE') or shutil.which('node')
    if not node:
        raise RuntimeError('Node.js 18+ is required to build frontend/ (or set PPGW_NODE).')
    result = subprocess.run([node, str(ROOT / 'frontend/build.mjs'), '--output', str(Path(output).resolve())],
                            cwd=ROOT, check=True, capture_output=True, text=True, encoding='utf-8')
    return result.stdout.strip()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(prepare_dashboard(args.output))
