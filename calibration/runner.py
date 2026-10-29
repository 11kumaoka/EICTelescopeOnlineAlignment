"""Run existing independent stage CLIs, retaining the controller lock in child stages."""
import os
import signal
import subprocess
import sys
from research_common import REPO,write_json


def stage(module,arguments,directory,lock_fd,stop,progress):
    if stop.is_set():raise InterruptedError('Graceful stop requested before next stage')
    directory.mkdir(parents=True,exist_ok=False)
    command=[sys.executable,'-m',module,*map(str,arguments)]
    write_json(directory/'command.json',{'argv':command,'cwd':str(REPO)})
    progress(module)
    with (directory/'stdout.log').open('x') as log:
        process=subprocess.Popen(command,cwd=REPO,stdout=log,stderr=subprocess.STDOUT,
                                 start_new_session=True,pass_fds=(lock_fd,))
        write_json(directory/'process.json',{'pid':process.pid,'process_group':process.pid})
        try:code=process.wait()
        except BaseException:
            os.killpg(process.pid,signal.SIGTERM);process.wait();raise
    write_json(directory/'exit.json',{'exit_code':code})
    if code:raise RuntimeError(f'{module} exited {code}; see {directory}/stdout.log')
    if stop.is_set():raise InterruptedError('Graceful stop requested; current stage finished')
