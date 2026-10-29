#!/usr/bin/env python3
"""Reproduce local module diffs using the exact pinned upstream Git objects.

Reads the supplied EICrecon checkout without changing it or using the network.
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[2]
LOCAL = REPO / 'localDetEICrecon/experimental/telescope'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Read-only EICrecon Git checkout')
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    args = parser.parse_args()
    manifest = json.loads((LOCAL / 'provenance.json').read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    for entry in manifest['copied_files']:
        original = subprocess.check_output(['git','-C',str(args.source),'show',
            manifest['upstream_commit']+':'+entry['upstream']])
        if hashlib.sha256(original).hexdigest() != entry['sha256']:
            raise ValueError('Upstream content hash mismatch: '+entry['upstream'])
        current = (LOCAL / entry['local']).read_text()
        patch = ''.join(difflib.unified_diff(original.decode().splitlines(keepends=True),
            current.splitlines(keepends=True), fromfile=entry['upstream'],
            tofile='localDetEICrecon/experimental/telescope/'+entry['local']))
        name = entry['local'].replace('/','__')+'.patch'
        (args.output / name).write_text(patch)
        print(entry['upstream'], 'modified' if patch else 'identical')

if __name__ == '__main__':
    main()
