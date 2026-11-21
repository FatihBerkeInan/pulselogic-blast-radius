"""Reason about isolated equality/identity edits using pristine operand facts."""
import ast
import copy
import gzip
import json
from pathlib import Path

ALLOWED={'Is','IsNot','Eq','NotEq'}

class IgnoreEqualityOperator(ast.NodeTransformer):
    def visit_Compare(self,node):
        self.generic_visit(node)
        if len(node.ops)==1 and type(node.ops[0]).__name__ in ALLOWED:
            node.ops=[ast.Is()]
        return node

def comparison_edits(before,after,path):
    left=IgnoreEqualityOperator().visit(copy.deepcopy(before))
    right=IgnoreEqualityOperator().visit(copy.deepcopy(after))
    if ast.dump(left)!=ast.dump(right):
        return None
    old=[n for n in ast.walk(before) if isinstance(n,ast.Compare)]
    new=[n for n in ast.walk(after) if isinstance(n,ast.Compare)]
    edits=[]
    for a,b in zip(old,new):
        if ast.dump(a.ops[0])==ast.dump(b.ops[0]):
            continue
        if len(a.ops)!=1 or len(b.ops)!=1 or type(a.ops[0]).__name__ not in ALLOWED or type(b.ops[0]).__name__ not in ALLOWED:
            return None
        edits.append((f'{path}:{a.lineno}:{a.col_offset}',type(a.ops[0]).__name__,type(b.ops[0]).__name__))
    return edits or None

def same_result(mask,old,new):
    if mask & 16:
        return False
    for value in range(4):
        if mask & (1<<value):
            identity=bool(value&2)
            equality=bool(value&1)
            truth={'Is':identity,'IsNot':not identity,'Eq':equality,'NotEq':not equality}
            if truth[old]!=truth[new]:
                return False
    return True

def read_profile(root):
    path=root/'baseline-profile.json.gz'
    if not path.is_file():
        return None
    with gzip.open(path,'rt',encoding='utf-8') as file:
        return json.load(file)

def unsafe_tests(dependencies,source):
    """Do not infer nonexecution when a test inspects source or launches a child."""
    unsafe=set()
    for path,lines in dependencies['lines'].items():
        file=source/path
        if not file.is_file() or not path.endswith('.py'):
            continue
        try:
            tree=ast.parse(file.read_bytes())
        except (OSError,SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node,ast.Attribute) and (node.attr=='__code__' or node.attr.startswith('co_')):
                if path.startswith('tests/'):
                    unsafe.update(test for test in dependencies.get('reports',{}) if test.split('::')[0]==path)
            if not isinstance(node,ast.Call):
                continue
            function=node.func
            name=function.id if isinstance(function,ast.Name) else function.attr if isinstance(function,ast.Attribute) else ''
            owner=function.value.id if isinstance(function,ast.Attribute) and isinstance(function.value,ast.Name) else ''
            risky=(name in ('getsource','getsourcelines','run_path','Popen','check_output','check_call','settrace','setprofile','dis','get_instructions')
                   or (name=='parse' and owner=='ast') or owner=='subprocess')
            if risky:
                for line in range(node.lineno,node.end_lineno+1):
                    unsafe.update(lines.get(str(line),[]))
                if path.startswith('tests/'):
                    unsafe.update(node for node in dependencies.get('reports',{}) if node.split('::')[0]==path)
    return unsafe

def narrow(text,candidates,source,profile,unsafe):
    if not profile:
        return set(candidates)
    from entry import parse_patch,reconstruct
    edits=[]
    try:
        for change in parse_patch(text):
            path=change['path']
            if not path.startswith('sqlglot/') or not path.endswith('.py'):
                return set(candidates)
            before=ast.parse((source/path).read_bytes())
            after=ast.parse(reconstruct((source/path).read_text('utf-8'),change['hunks']).encode('utf-8'))
            compile(after,path,'exec',dont_inherit=True)
            if ast.dump(before)==ast.dump(after):
                continue
            local=comparison_edits(before,after,path)
            if local is None:
                return set(candidates)
            edits.extend(local)
    except (OSError,ValueError,SyntaxError):
        return set(candidates)
    if not edits:
        return set(candidates)
    retained=set()
    for node in candidates:
        if node in unsafe or profile.get('reports',{}).get(node)!='passed':
            retained.add(node)
            continue
        for site,old,new in edits:
            facts=profile['facts'].get(site)
            if facts is None:
                retained.add(node)
                break
            mask=facts.get(node,0)|facts.get('@import',0)
            scope=node.rsplit('::',1)[0]
            mask|=facts.get('@setup:'+scope,0)
            if not same_result(mask,old,new):
                retained.add(node)
                break
    return retained
