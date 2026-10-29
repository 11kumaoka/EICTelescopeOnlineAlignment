#!/usr/bin/env python3
"""Build pinned official Mille and pede in a NEW external workspace (EIC Linux container)."""
import argparse,json,subprocess,os
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--workspace',type=Path,default=REPO/'external',help='Must not exist')
p.add_argument('--local-sources',type=Path,help='Optional directory of existing git clones; revisions still verified')
p.add_argument('--jobs',type=int,default=2)
a=p.parse_args();a.workspace=a.workspace.resolve();a.workspace.mkdir(parents=True,exist_ok=False)
lock=json.loads((REPO/'environment/dependencies.json').read_text());prefix=a.workspace/'install'
def run(cmd,name):
 with (a.workspace/(name+'.log')).open('x') as log:
  rc=subprocess.call(list(map(str,cmd)),cwd=REPO,stdout=log,stderr=subprocess.STDOUT)
 if rc:raise SystemExit(f'{name} failed ({rc}); inspect {log.name}. Workspace retained; retry with a NEW --workspace.')
for name,dep in lock.items():
 src=a.workspace/'src'/name;src.parent.mkdir(exist_ok=True)
 origin=a.local_sources.resolve()/name if a.local_sources else dep['url']
 run(['git','clone','--no-hardlinks',origin,src],name+'-clone')
 run(['git','-C',src,'checkout','--detach',dep['commit']],name+'-checkout')
 head=subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()
 if head!=dep['commit']:raise SystemExit('Dependency revision mismatch')
 if a.local_sources and subprocess.check_output(['git','-C',str(origin),'status','--porcelain'],text=True):
  raise SystemExit('Local source contains modifications; clean pinned clones required')
 build=a.workspace/'build'/name
 cmd=['cmake','-S',src,'-B',build,f'-DCMAKE_INSTALL_PREFIX={prefix}',
      '-DSUPPORT_ROOT=ON','-DBUILD_TESTS=OFF', '-DCMAKE_BUILD_TYPE=Release']
 if name=='millepede-ii':cmd += [f'-DMille_DIR={prefix}/cmake','-DSUPPORT_LAPACK=ON','-DSUPPORT_OPENMP=ON']
 run(cmd,name+'-configure');run(['cmake','--build',build,'-j',str(a.jobs)],name+'-build')
 run(['cmake','--install',build],name+'-install')
(a.workspace/'dependencies.json').write_text(json.dumps(lock,indent=2)+'\n')
print(f'Installed pinned dependencies: {prefix}\nUse MILLEPEDE_PREFIX={prefix}')
