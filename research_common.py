"""Shared paths, immutable stage records, checksums, and command-line safety."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parent

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def write_json(path, value):
    with Path(path).open('x') as f: json.dump(value, f, indent=2); f.write('\n')

def prefix():
    selected = Path(os.environ.get('MILLEPEDE_PREFIX', str(REPO/'external/install'))).resolve()
    for resource in ('cmake/MilleConfig.cmake','lib/libMille2.so','bin/pede'):
        if not (selected/resource).is_file():
            raise FileNotFoundError(f'Incomplete dependency installation: {selected/resource}; no legacy fallback is used')
    return selected

def execute(command, directory, cwd=None, env=None):
    cwd = directory if cwd is None else cwd
    write_json(directory/'command.json', {'argv':list(map(str,command)), 'cwd':str(cwd)})
    with (directory/'stdout.log').open('x') as f:
        rc = subprocess.call(list(map(str,command)), cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT)
    write_json(directory/'exit.json', {'exit_code':rc})
    if rc: raise RuntimeError(f'Command exited {rc}; see {directory}/stdout.log')

def stage(run, name):
    p = run/name
    p.mkdir()  # Exclusive: never reuse partial or completed output directories.
    return p

def done(p, **info):
    info['output_hashes']={str(q.relative_to(p)):digest(q) for q in p.rglob('*') if q.is_file() and q.name != 'complete.json'}
    write_json(p/'complete.json', info)

def require(run, name):
    marker=run/name/'complete.json'
    if not marker.is_file(): raise RuntimeError(f'Complete {name} first')
    for filename,expected in json.loads(marker.read_text()).get('output_hashes',{}).items():
        if digest(run/name/filename)!=expected:raise RuntimeError(f'Modified stage output: {name}/{filename}')

def cli_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--result-dir', '--run-dir', dest='run_dir', metavar='RESULT_DIR',
                        type=Path, required=True, help='Result directory under result/; existing stages are never overwritten')
    return parser


def run(handler, parser):
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    if not args.run_dir.is_relative_to(REPO/'result') or args.run_dir == REPO/'result':
        parser.error('result directory must be a child of result/')
    if any(c.isspace() for c in str(args.run_dir)):
        parser.error('pede run directories must not contain whitespace')
    os.chdir(REPO)
    try:
        handler(args)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(1, f'ERROR: {error}\nPartial results retained; use a NEW result directory to retry.\n')
    print(f'Completed {handler.__name__}: {args.run_dir}')
