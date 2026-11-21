"""Choose verification groups by expected reduction in the complete scored cost."""
import ast
import itertools

class Planner:
    def __init__(self,source):
        self.source=source
        self.import_cost={}
        self.case_cost={}
        self.successes={}
        self.failures={}
        self.observed_cpu=0.0
        self.observed_recorded=0.0

    def seed(self,profile,by_id):
        if not profile or getattr(self,'seeded',False):
            return
        self.seeded=True
        setup=profile.get('setup_cpu',{})
        for node,seconds in profile.get('cpu',{}).items():
            if node in by_id and profile.get('reports',{}).get(node)=='passed':
                # Isolated verification repeats class fixtures for each test.
                scope=node.rsplit('::',1)[0]
                self.case_cost[node]=max(0.0001,seconds+setup.get(scope,0.0))

    def fixed_cost(self,path):
        if path in self.import_cost:
            return self.import_cost[path]
        try:
            tree=ast.parse((self.source/path).read_bytes())
            roots=set()
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    roots.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node,ast.ImportFrom) and node.module:
                    roots.add(node.module.split('.')[0])
            value=1.15 if roots & {'numpy','pandas','duckdb'} else 0.6
            self.import_cost[path]=value
            return value
        except (OSError,SyntaxError):
            self.import_cost[path]=1.0
            return 1.0

    def choose(self,candidates,by_id,schedule):
        if len(candidates)>220:
            return []
        groups={}
        for node in candidates:
            if by_id[node]['seconds']<=0.8:
                groups.setdefault(by_id[node]['file'],[]).append(node)
        if not groups:
            return []
        scale=max(0.25,min(2.0,self.observed_cpu/self.observed_recorded)) if self.observed_recorded else 1.0
        def case_estimate(nodes):
            return sum(self.case_cost.get(node,by_id[node]['seconds']*scale) for node in nodes)
        def estimate(path,nodes):
            return self.fixed_cost(path)+case_estimate(nodes)
        paths=sorted(groups,key=lambda path:estimate(path,groups[path]))[:6]
        duration={test['number']:round(test['seconds']*1000) for test in by_id.values()}
        def cost(nodes):
            workers=schedule([by_id[node] for node in nodes])
            return (max((sum(duration[n] for n in worker) for worker in workers),default=0)+2000*len(workers))/1000
        original=cost(candidates)
        best_margin=0.1
        best=[]
        for count in range(1,len(paths)+1):
            for subset in itertools.combinations(paths,count):
                selected=[node for path in subset for node in groups[path]]
                if len(selected)>100:
                    continue
                possible=original-cost(set(candidates)-set(selected))
                confidence=min((self.successes.get(path,1.5)/(self.successes.get(path,1.5)+self.failures.get(path,1.0)) for path in subset),default=0)
                # Shared imports are paid once in a fresh interpreter.
                expected=max(self.fixed_cost(path) for path in subset)+0.02*(len(subset)-1)+case_estimate(selected)
                margin=possible*confidence-expected*1.1
                if margin>best_margin:
                    best_margin=margin
                    best=selected
        return sorted(best,key=lambda node:(by_id[node]['file'],by_id[node]['seconds'],node))

    def learn(self,records,by_id):
        states={}
        case_times={}
        outcomes={}
        for record in records:
            if record.get('phase')=='load':
                path=record['file']
                measured=record.get('seconds',0)
                self.import_cost[path]=max(0.06,measured)
            elif record.get('phase')=='case':
                case_times[record['test']]=max(0.0001,record.get('seconds',0))
            elif record.get('test') in by_id:
                node=record['test']
                outcomes[node]=record['state']
                states.setdefault(by_id[node]['file'],[]).append(record['state'])
        for node,seconds in case_times.items():
            if node in by_id and outcomes.get(node) in ('passed','observed_pass'):
                self.case_cost[node]=seconds
                self.observed_cpu+=seconds
                self.observed_recorded+=by_id[node]['seconds']
        for path,values in states.items():
            if 'failed' in values:
                self.failures[path]=self.failures.get(path,1.0)+1
            elif values and all(value=='passed' for value in values):
                self.successes[path]=self.successes.get(path,1.5)+1
