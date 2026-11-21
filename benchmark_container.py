"""Compare original verification policies under the published container limits."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import time

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sample',required=True)
    parser.add_argument('--scorer',required=True)
    parser.add_argument('--image',default='pulselogic')
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--output',default='linux-ablation.json')
    options=parser.parse_args()
    sample=Path(options.sample).resolve()
    spec=importlib.util.spec_from_file_location('official_score',options.scorer)
    scorer=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    tests=json.loads((sample/'tests.json').read_text())['tests']
    answers=json.loads((sample/'answers.json').read_text())['commits']
    rows=[]
    for mode in ('0','1'):
        for iteration in range(options.rounds):
            command=['docker','run','--rm','--network','none','--cpus','2','--memory','4g',
                     '--pids-limit','256','--read-only','--user','65534:65534',
                     '--tmpfs','/tmp:rw,nosuid,nodev,size=64m','--cap-drop','ALL',
                     '--security-opt','no-new-privileges','-e','HOME=/tmp',
                     '-e','VERIFY_ISOLATE='+mode,'-v',str(sample)+':/data:ro',options.image]
            started=time.monotonic()
            run=subprocess.run(command,capture_output=True,timeout=200)
            if run.returncode:
                raise RuntimeError(run.stderr.decode(errors='replace')[-3000:])
            result=scorer.evaluate_run(run.stdout,tests,answers)
            if not result.get('ok'):
                raise RuntimeError(result)
            metrics=next(json.loads(line[len('CPU_METRICS '):]) for line in
                         run.stderr.decode().splitlines() if line.startswith('CPU_METRICS '))
            cpu=metrics.get('cgroup')
            if cpu is None:
                cpu=metrics['parent']+metrics['children']
            row={'isolated':mode=='1','round':iteration+1,
                 'simulated_seconds':result['simulated_milliseconds']/1000,
                 'cpu_seconds':cpu,'total_seconds':result['simulated_milliseconds']/1000+cpu,
                 'missed_commits':result.get('missed_commits',[]),'wall_seconds':time.monotonic()-started}
            rows.append(row)
            print(json.dumps(row),flush=True)
            Path(options.output).write_text(json.dumps(rows,indent=2))
    for mode in (False,True):
        print(json.dumps({'isolated':mode,'median_total':statistics.median(
              row['total_seconds'] for row in rows if row['isolated']==mode)}),flush=True)

if __name__=='__main__':
    main()
