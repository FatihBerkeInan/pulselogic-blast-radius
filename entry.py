"""Original dependency-based selector. No patch identities or answer tables."""
import ast
import gzip
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get('SOURCE_DIR', ROOT / 'sqlglot'))

def schedule(tests):
    if not tests:
        return []
    ordered = sorted(tests, key=lambda t: (-round(t['seconds'] * 1000), t['number']))
    best = None
    for count in range(1, 9):
        workers, loads = [[] for _ in range(count)], [0] * count
        for test in ordered:
            index = min(range(count), key=lambda i: loads[i])
            workers[index].append(test['number'])
            loads[index] += round(test['seconds'] * 1000)
        workers = [w for w in workers if w]
        cost = max(loads) + 2000 * len(workers)
        if best is None or cost < best[0]:
            best = cost, workers
    return best[1]

def parse_patch(text):
    changes, current, old_line = [], None, None
    removed = []
    for line in text.splitlines():
        if line.startswith('diff --git '):
            current, old_line = None, None
        elif line.startswith('+++ '):
            path = line[4:]
            if path.startswith('b/'):
                path = path[2:]
            current = {'path':path, 'lines':set(), 'hunks':[]}
            changes.append(current)
        elif line.startswith('@@ ') and current is not None:
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            if not match:
                raise ValueError('Unsupported patch hunk')
            old_line = int(match[1])
            removed = []
            current['hunks'].append([old_line, []])
        elif current is not None and old_line is not None and current['hunks']:
            if not line or line[0] not in ' +-':
                continue
            current['hunks'][-1][1].append(line)
            if line[0] == '-':
                current['lines'].add(old_line)
                removed.append(old_line)
                old_line += 1
            elif line[0] == '+':
                current['lines'].update(removed or [max(1, old_line - 1), old_line])
            else:
                removed = []
                old_line += 1
    return changes

def reconstruct(original, hunks):
    lines = original.splitlines(keepends=True)
    out, cursor = [], 0
    for start, entries in hunks:
        start = max(0, start - 1)
        out.extend(lines[cursor:start])
        cursor = start
        for entry in entries:
            if entry[0] in ' -':
                if cursor >= len(lines) or lines[cursor].rstrip('\r\n') != entry[1:]:
                    raise ValueError('Patch context does not match pinned source')
                if entry[0] == ' ':
                    out.append(lines[cursor])
                cursor += 1
            else:
                out.append(entry[1:] + '\n')
    out.extend(lines[cursor:])
    return ''.join(out)

def load_map():
    if (ROOT / 'dependencies.json.gz').exists():
        with gzip.open(ROOT / 'dependencies.json.gz', 'rt', encoding='utf-8') as f:
            return json.load(f)
    return json.loads((ROOT / 'dependencies.json').read_text('utf-8'))

class Selector:
    def __init__(self, tests, dependencies):
        self.tests = tests
        self.by_id = {t['id']:t for t in tests}
        self.all_ids = set(self.by_id)
        self.dependencies = dependencies
        self.dialect_references = {}
        self.class_references = {}
        self.attribute_readers={}
        if 'test_ids' in dependencies:
            nodes = dependencies['test_ids']
            self.dialect_references = {d:{nodes[n] for n in contexts} for d,contexts in dependencies.get('dialect_references',{}).items()}
            self.class_references = {d:{nodes[n] for n in contexts} for d,contexts in dependencies.get('class_references',{}).items()}
            self.attribute_readers={name:{nodes[n] for n in contexts} for name,contexts in dependencies.get('attribute_readers',{}).items()}
            for lines in dependencies['lines'].values():
                for line, contexts in lines.items():
                    lines[line] = [nodes[n] for n in contexts]
        self.file_union = {p:set().union(*(set(v) for v in lines.values())) if lines else set()
                           for p, lines in dependencies['lines'].items()}
        self.trees = {}
        self.setup_contexts = {}
        self.uncertain = {node for node, outcome in dependencies.get('reports', {}).items()
                          if outcome in ('failed', 'skipped')} & self.all_ids
        self.uncertain.update(self.all_ids - set(dependencies.get('reports', self.by_id)))

    def select(self, text):
        selected = set()
        changes = parse_patch(text)
        if not changes:
            return self.all_ids
        for change in changes:
            path = change['path']
            if path.startswith('/') or '..' in Path(path).parts:
                return self.all_ids
            source = SOURCE / path
            if not source.is_file():
                return self.all_ids
            if not path.endswith('.py'):
                readers = self.dependencies['files'].get(path)
                if readers is None:
                    return self.all_ids
                selected.update(readers)
                for reader in readers:
                    test_file = reader.split('::')[0]
                    if test_file not in self.setup_contexts:
                        contexts = set()
                        try:
                            tree = ast.parse((SOURCE / test_file).read_bytes())
                            test_lines = self.dependencies['lines'].get(test_file, {})
                            for node in ast.walk(tree):
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ('setUpClass', 'setup_class', 'setUpModule', 'setup_module'):
                                    for line in range(node.lineno, node.end_lineno + 1):
                                        contexts.update(test_lines.get(str(line), []))
                        except (OSError, SyntaxError):
                            pass
                        self.setup_contexts[test_file] = contexts
                    if reader in self.setup_contexts[test_file]:
                        prefix = reader.rsplit('::', 1)[0] + '::'
                        selected.update(node for node in self.all_ids if node.startswith(prefix))
                continue
            original = source.read_text('utf-8')
            try:
                if path not in self.trees:
                    self.trees[path] = ast.parse(original.encode('utf-8'))
                tree = self.trees[path]
                modified = ast.parse(reconstruct(original, change['hunks']).encode('utf-8'))
                compile(modified,path,'exec',dont_inherit=True)
                if ast.dump(tree, include_attributes=False) == ast.dump(modified, include_attributes=False):
                    continue
            except (SyntaxError, ValueError):
                return self.all_ids
            lines = self.dependencies['lines'].get(path, {})
            specific_dialect = re.match(r'sqlglot/(?:dialects|parsers|generators|typing)/([^/]+)\.py$',path)
            dialect = specific_dialect[1] if specific_dialect else None
            initialization_lines = set(self.dependencies.get('import_lines', {}).get(path, []))
            from statement_scope import initialization_changed
            initialization_delta=initialization_changed(tree,modified)
            for number in change['lines']:
                containers = [n for n in ast.walk(tree)
                              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                              and n.lineno <= number <= n.end_lineno]
                if containers:
                    if number in initialization_lines or initialization_delta:
                        selected.update(self.dialect_references.get(dialect,self.all_ids))
                    func = min(containers, key=lambda n:n.end_lineno-n.lineno)
                    function_tests = set()
                    for line in range(func.lineno, func.end_lineno + 1):
                        function_tests.update(lines.get(str(line), []))
                    precise=set()
                    if os.environ.get('STATEMENT_SCOPE','1')=='1':
                        from statement_scope import execution_span
                        scope=execution_span(tree,modified,func,number)
                        for line in range(scope.lineno,scope.end_lineno+1):
                            precise.update(lines.get(str(line),[]))
                    selected.update(precise or function_tests or self.file_union.get(path) or self.all_ids)
                else:
                    # Initialization can affect tests without executing this module again.
                    if dialect in self.dialect_references:
                        from attribute_dependencies import isolated_scalar_setting
                        setting=isolated_scalar_setting(tree,modified,number)
                        consumers=self.dialect_references[dialect] & self.attribute_readers.get(setting,set())
                        if consumers and setting not in self.dependencies.get('initialization_attributes',[]):
                            selected.update(consumers)
                        else:
                            selected.update(self.dialect_references[dialect])
                            selected.update(self.file_union.get(path, set()))
                    else:
                        classes = [n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.lineno <= number <= n.end_lineno]
                        klass = min(classes,key=lambda n:n.end_lineno-n.lineno) if classes else None
                        references = self.class_references.get(klass.name,set()) if klass else set()
                        selected.update(references or self.all_ids)
            if path.startswith('tests/'):
                selected.update(t['id'] for t in self.tests if t['file'] == path)
        if selected:
            selected.update(self.uncertain)
        return selected & self.all_ids

def select_with_proofs(text,selector,profile):
    from operand_proofs import narrow
    ids=selector.select(text)
    unsafe=set((profile or {}).get('unsafe_tests',[]))
    if os.environ.get('OPERAND_PROOFS','1')=='1' and profile and 'unsafe_tests' in profile:
        ids=narrow(text,ids,SOURCE,profile,unsafe)
    if ids and profile and os.environ.get('SEMANTIC_PROOFS','1')=='1':
        from semantic_proofs import equivalent_patch
        if equivalent_patch(text,SOURCE):
            retained=(unsafe|{node for node in selector.all_ids if profile.get('reports',{}).get(node)!='passed'})&selector.all_ids
            duration={test['number']:round(test['seconds']*1000) for test in selector.by_id.values()}
            def cost(nodes):
                workers=schedule([selector.by_id[node] for node in nodes])
                return max((sum(duration[n] for n in worker) for worker in workers),default=0)+2000*len(workers)
            if cost(retained)+100<cost(ids):
                ids=retained
    return ids

def main():
    started=time.process_time()
    def cgroup_cpu():
        try:
            fields=dict(line.split() for line in Path('/sys/fs/cgroup/cpu.stat').read_text().splitlines())
            return int(fields['usage_usec'])/1_000_000
        except (OSError,ValueError,KeyError):
            return None
    cgroup_started=cgroup_cpu()
    data = Path(os.environ.get('DATA_DIR', '/data'))
    tests = json.loads((data / 'tests.json').read_text('utf-8'))['tests']
    selector = Selector(tests, load_map())
    from operand_proofs import read_profile
    from verification import refine
    profile=read_profile(ROOT)
    child_seconds=0.0
    verification_deadline=time.monotonic()+120.0
    for patch in sorted((data / 'commits').glob('*.patch')):
        text=patch.read_text('utf-8')
        try:
            ids=select_with_proofs(text,selector,profile)
            info={'cpu':0,'passed':0}
            remaining=verification_deadline-time.monotonic()
            if os.environ.get('VERIFY','1')=='1' and remaining>0.1:
                ids,info=refine(text,ids,selector.by_id,SOURCE,profile,
                                wall_budget=min(remaining,float(os.environ.get('VERIFY_WALL','6.0'))))
                child_seconds+=info['cpu']
        except Exception as error:
            print(f'{patch.stem}: conservative fallback ({type(error).__name__})',file=sys.stderr)
            ids=selector.all_ids
            info={'cpu':0,'passed':0}
        workers = schedule([selector.by_id[node] for node in ids])
        print(json.dumps({'commit':patch.stem, 'workers':workers}, separators=(',',':')))
        print(f'{patch.stem}: {len(ids)} tests, {len(workers)} workers; verified {info["passed"]} passed; cpu {info["cpu"]:.4f}', file=sys.stderr)
        if info.get('error'):
            print(f'{patch.stem}: verifier detail {info["error"][-220:]}',file=sys.stderr)
    cgroup_ended=cgroup_cpu()
    print('CPU_METRICS '+json.dumps({'parent':time.process_time()-started,'children':child_seconds,
          'cgroup':cgroup_ended-cgroup_started if cgroup_started is not None and cgroup_ended is not None else None}),file=sys.stderr)

if __name__ == '__main__':
    main()
