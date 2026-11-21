"""Execute a small candidate set against an isolated, patched source overlay."""
import json
import os
from pathlib import Path
import sys
import time
import shutil

START_CPU=time.process_time()
from network_guard import install
install()

def main():
    request=json.load(sys.stdin)
    source=Path(request['source'])
    workspace=Path(request['workspace'])
    overlay=Path(request.get('tree',workspace/'tree'))
    replacements=request['replacements']
    if 'tree' not in request:
        build_overlay(source,overlay,replacements)
    os.environ['SKIP_INTEGRATION']='0'
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(overlay))
    os.chdir(overlay)
    run_tests(request,workspace)

def build_overlay(source,overlay,replacements):
    overlay.mkdir()
    # Linux uses symlinks: unchanged files consume almost no space in /tmp.
    for directory,folders,files in os.walk(source):
        folders[:]=[name for name in folders if name not in ('.git','__pycache__','.pytest_cache')]
        relative=Path(directory).relative_to(source)
        target=overlay/relative
        target.mkdir(exist_ok=True)
        for name in files:
            original=Path(directory)/name
            destination=target/name
            key=(relative/name).as_posix()
            if key in replacements:
                destination.write_text(replacements[key],encoding='utf-8',newline='')
            elif os.name=='nt':
                shutil.copyfile(original,destination)
            else:
                destination.symlink_to(original)
def run_tests(request,workspace):
    import unittest
    statuses={}
    current=[None]
    case_start=[0.0]
    scope_failed=[False]
    log=open(workspace/'progress.jsonl','w',encoding='utf-8',buffering=1)
    def report(node,state):
        statuses[node]=state
        log.write(json.dumps({'test':node,'state':state,'cpu':time.process_time()-START_CPU})+'\n')
    class Result(unittest.TestResult):
        def __init__(self):
            super().__init__()
            self.failfast=True
        def stop(self):
            # unittest stops the failing method's subtest loop, not later tests.
            pass
        def startTest(self,test):
            super().startTest(test)
            current[0]=lookup.get(test.id())
            case_start[0]=time.process_time()
        def stopTest(self,test):
            if current[0]:
                log.write(json.dumps({'phase':'case','test':current[0],'seconds':time.process_time()-case_start[0],
                                      'cpu':time.process_time()-START_CPU})+'\n')
            super().stopTest(test)
        def addSuccess(self,test):
            super().addSuccess(test)
            node=lookup.get(test.id())
            if node and statuses.get(node)!='failed':
                statuses[node]='passed'
        def addFailure(self,test,error):
            super().addFailure(test,error)
            report(lookup.get(test.id(),test.id()),'failed')
        def addError(self,test,error):
            super().addError(test,error)
            report(lookup.get(test.id(),test.id()),'failed')
            if test.id() not in lookup:
                scope_failed[0]=True
        def addSubTest(self,test,subtest,error):
            super().addSubTest(test,subtest,error)
            if error is not None:
                report(lookup.get(test.id(),test.id()),'failed')
        def addSkip(self,test,reason):
            super().addSkip(test,reason)
            report(lookup.get(test.id(),test.id()),'skipped')
        def addUnexpectedSuccess(self,test):
            super().addUnexpectedSuccess(test)
            report(lookup.get(test.id(),test.id()),'failed')
    lookup={}
    loader=unittest.TestLoader()
    groups={}
    for node in request['tests']:
        parts=node.split('::')
        dotted=parts[0].removesuffix('.py').replace('/','.')+'.'+'.'.join(parts[1:])
        lookup[dotted]=node
        groups.setdefault(parts[0],[]).append((node,dotted))
    from execution_support import install_inline_pools
    def task_failed(error):
        if current[0]:
            report(current[0],'failed')
        else:
            scope_failed[0]=True
    install_inline_pools(fast_fail=True,on_error=task_failed)
    def execute(group,suite):
        statuses.clear()
        scope_failed[0]=False
        suite.run(Result())
        # A success is committed only after the module/class cleanup finishes.
        module_failed=any(state=='failed' for state in statuses.values())
        for node,state in list(statuses.items()):
            if node in lookup.values() and state=='passed' and not scope_failed[0]:
                report(node,'observed_pass' if module_failed else 'passed')
    for group in groups.values():
        load_start=time.process_time()
        loaded=[(member,loader.loadTestsFromName(member[1])) for member in group]
        log.write(json.dumps({'phase':'load','file':group[0][0].split('::')[0],
                              'seconds':time.process_time()-load_start,'cpu':time.process_time()-START_CPU})+'\n')
        if request.get('isolate_tests',True) and hasattr(os,'fork'):
            # Only imports are shared. Every test gets pristine class/module state
            # and its own setup, body, cleanup, and timer in a forked process.
            import signal
            for member,suite in loaded:
                node=member[0]
                pid=os.fork()
                if pid==0:
                    globals()['START_CPU']=time.process_time()
                    expired=[False]
                    def alarm(signum,frame):
                        expired[0]=True
                        raise TimeoutError('Per-test verification budget exhausted')
                    signal.signal(signal.SIGALRM,alarm)
                    signal.setitimer(signal.ITIMER_REAL,request.get('budgets',{}).get(node,2.0))
                    code=0
                    try:
                        execute([member],unittest.TestSuite([suite]))
                        signal.setitimer(signal.ITIMER_REAL,0)
                        if expired[0]:
                            report(node,'failed')
                    except BaseException:
                        signal.setitimer(signal.ITIMER_REAL,0)
                        report(node,'failed')
                        code=2
                    log.close()
                    os._exit(code)
                _,status=os.waitpid(pid,0)
                if status:
                    report(node,'failed')
        else:
            execute(group,unittest.TestSuite(suite for member,suite in loaded))
    log.close()

if __name__=='__main__':
    try:
        main()
    except BaseException as error:
        print('Verifier stopped:',type(error).__name__,str(error),file=sys.stderr)
        raise SystemExit(2)
