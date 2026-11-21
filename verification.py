"""Bounded verification; retain all candidates that cannot be confirmed passing."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import shutil
import atexit

ROOT=Path(__file__).resolve().parent
OVERLAYS={}
PLANNERS={}

def prepared_tree(source,replacements):
    key=str(source)
    if key not in OVERLAYS:
        directory=tempfile.TemporaryDirectory(prefix='blast-tree-')
        atexit.register(directory.cleanup)
        tree=Path(directory.name)/'tree'
        from verify_worker import build_overlay
        build_overlay(source,tree,{})
        OVERLAYS[key]=(directory,tree,set())
    directory,tree,previous=OVERLAYS[key]
    for path in previous:
        target=tree/path
        target.unlink()
        if os.name=='nt':
            shutil.copyfile(source/path,target)
        else:
            target.symlink_to(source/path)
    for path,contents in replacements.items():
        target=tree/path
        target.unlink()
        target.write_text(contents,encoding='utf-8',newline='')
    OVERLAYS[key]=(directory,tree,set(replacements))
    return tree

def child_cpu(process,fallback):
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        values=[wintypes.FILETIME() for _ in range(4)]
        function=kernel.GetProcessTimes
        function.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        if function(int(process._handle),*[ctypes.byref(value) for value in values]):
            return sum((value.dwHighDateTime<<32)|value.dwLowDateTime for value in values[2:])/10_000_000
    return fallback

def refine(text,candidates,by_id,source,profile=None,wall_budget=3.0,force=False):
    from entry import parse_patch,reconstruct,schedule
    if not candidates:
        return set(),{'cpu':0.0,'passed':0}
    try:
        changes=parse_patch(text)
        replacements={}
        for change in changes:
            path=change['path']
            if not path.startswith(('sqlglot/','tests/fixtures/')) or '..' in Path(path).parts:
                return set(candidates),{'cpu':0.0,'passed':0}
            original=(source/path).read_text('utf-8')
            replacements[path]=reconstruct(original,change['hunks'])
    except (OSError,ValueError,UnicodeError):
        return set(candidates),{'cpu':0.0,'passed':0}
    # Expensive tests stay scheduled; only their measured cheap tail is run here.
    from verification_plan import Planner
    planner=PLANNERS.setdefault(str(source),Planner(source))
    cheap=planner.choose(candidates,by_id,schedule)
    if force:
        cheap=sorted(candidates)
    if not cheap:
        return set(candidates),{'cpu':0.0,'passed':0}
    before=None
    if os.name!='nt':
        import resource
        usage=resource.getrusage(resource.RUSAGE_CHILDREN)
        before=usage.ru_utime+usage.ru_stime
    with tempfile.TemporaryDirectory(prefix='blast-verify-') as directory:
        tree=prepared_tree(source,replacements)
        request={'source':str(source),'workspace':directory,'tree':str(tree),'replacements':replacements,'tests':cheap}
        start=time.perf_counter()
        errors=open(Path(directory)/'stderr.log','wb')
        process=subprocess.Popen([sys.executable,str(ROOT/'verify_worker.py')],stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL,stderr=errors,
                                 env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',
                                          OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1'),
                                 start_new_session=os.name!='nt')
        timeout=False
        try:
            process.communicate(json.dumps(request).encode(),timeout=wall_budget)
        except subprocess.TimeoutExpired:
            timeout=True
            if os.name!='nt':
                os.killpg(process.pid,signal.SIGKILL)
            else:
                process.kill()
            process.communicate()
        errors.close()
        elapsed=time.perf_counter()-start
        used=child_cpu(process,elapsed)
        if before is not None:
            usage=resource.getrusage(resource.RUSAGE_CHILDREN)
            used=usage.ru_utime+usage.ru_stime-before
        passed=set()
        progress=Path(directory)/'progress.jsonl'
        status={}
        records=[]
        reported_cpu=0.0
        if progress.is_file():
            for line in progress.read_text('utf-8').splitlines():
                try:
                    record=json.loads(line)
                    records.append(record)
                    if 'state' in record:
                        status[record['test']]=record['state']
                    reported_cpu=max(reported_cpu,record.get('cpu',0.0))
                except (ValueError,KeyError):
                    continue
            passed={node for node,state in status.items() if state=='passed' and node in candidates}
        if process.returncode not in (0,None) and not timeout:
            passed.clear()
        used=max(used,reported_cpu)
        if not force:
            planner.learn(records,by_id)
        diagnostic=(Path(directory)/'stderr.log').read_text('utf-8',errors='replace')[-1500:] if process.returncode else ''
        return set(candidates)-passed,{'cpu':used,'wall':elapsed,'passed':len(passed),'timeout':timeout,'error':diagnostic,
                                      'failed':sorted(node for node,state in status.items() if state=='failed'),
                                      'completed':sorted(node for node,state in status.items() if node in candidates and state in ('passed','failed','observed_pass','skipped'))}
