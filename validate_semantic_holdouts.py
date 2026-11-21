"""Validate fresh operator and local-binding edits against related dialect modules."""
import argparse
import ast
import difflib
import json
from pathlib import Path
import random
import re
import entry
import operand_proofs
import verification

def candidates(tree,method):
    found=[]
    for function in (node for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))):
        nodes=list(ast.walk(function))
        if method=='operator':
            for node in nodes:
                if isinstance(node,ast.Compare) and len(node.ops)==1 and type(node.ops[0]).__name__ in ('Is','IsNot','Eq','NotEq'):
                    if node.left.end_lineno==node.comparators[0].lineno:
                        found.append((node,[]))
        else:
            if any(node is not function and isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda,ast.comprehension)) for node in nodes):
                continue
            parameters={arg.arg for arg in function.args.posonlyargs+function.args.args+function.args.kwonlyargs}
            parameters.update(arg.arg for arg in (function.args.vararg,function.args.kwarg) if arg)
            names=[node for node in nodes if isinstance(node,ast.Name)]
            for node in names:
                if isinstance(node.ctx,ast.Store) and node.id not in parameters:
                    renamed=node.id+'__br_private'
                    if not any(name.id==renamed for name in names):
                        found.append((node,[name for name in names if name.id==node.id]))
    return found

def mutate(text,choice,method):
    node,names=choice
    lines=text.splitlines(keepends=True)
    if method=='operator':
        number=node.left.end_lineno-1
        line=lines[number].encode()
        start=node.left.end_col_offset
        end=node.comparators[0].col_offset
        match=re.search(rb'\bis\s+not\b|\bis\b|==|!=',line[start:end])
        if match is None:
            raise ValueError('No isolated comparison operator')
        operation=type(node.ops[0]).__name__
        replacement={'Is':b'==','IsNot':b'!=','Eq':b'is','NotEq':b'is not'}[operation]
        lines[number]=(line[:start+match.start()]+replacement+line[start+match.end():]).decode()
    else:
        for name in sorted(names,key=lambda item:(item.lineno,item.col_offset),reverse=True):
            line=lines[name.lineno-1].encode()
            lines[name.lineno-1]=(line[:name.col_offset]+(node.id+'__br_private').encode()+line[name.end_col_offset:]).decode()
    result=''.join(lines)
    compile(result,'mutation.py','exec',dont_inherit=True)
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--method',choices=['operator','rename'],required=True)
    parser.add_argument('--seed',type=int,default=881923)
    parser.add_argument('--count',type=int,default=8)
    parser.add_argument('--output',required=True)
    options=parser.parse_args()
    rng=random.Random(options.seed)
    tests=json.loads((entry.ROOT/'sample/tests.json').read_text())['tests']
    selector=entry.Selector(tests,entry.load_map())
    profile=operand_proofs.read_profile(entry.ROOT)
    assert profile and profile.get('relationship_version')==2
    possible=[]
    for file in sorted((entry.SOURCE/'sqlglot/generators').glob('*.py')):
        targets={test['id'] for test in tests if test['file']=='tests/dialects/test_'+file.stem+'.py'}
        if not targets:
            continue
        text=file.read_text('utf-8')
        choices=candidates(ast.parse(text),options.method)
        if choices:
            possible.append((file,targets,text,rng.choice(choices)))
    rng.shuffle(possible)
    rows=[]
    for file,targets,text,choice in possible[:options.count]:
        replacement=mutate(text,choice,options.method)
        path=file.relative_to(entry.SOURCE).as_posix()
        patch='diff --git a/'+path+' b/'+path+'\n'+''.join(difflib.unified_diff(text.splitlines(True),replacement.splitlines(True),'a/'+path,'b/'+path))
        selected=entry.select_with_proofs(patch,selector,profile)
        selected,_=verification.refine(patch,selected,selector.by_id,entry.SOURCE,profile,wall_budget=3)
        _,truth=verification.refine(patch,targets,selector.by_id,entry.SOURCE,profile,wall_budget=12,force=True)
        failed=set(truth.get('failed',[]))&targets
        row={'method':options.method,'path':path,'line':choice[0].lineno,'module_tests':len(targets),
             'completed':len(set(truth.get('completed',[]))&targets),'failed':len(failed),
             'missed':sorted(failed-selected),'timeout':truth.get('timeout',False)}
        rows.append(row)
        print(json.dumps(row),flush=True)
        Path(options.output).write_text(json.dumps(rows,indent=2))
    assert rows and all(row['completed']==row['module_tests'] and not row['missed'] and not row['timeout'] for row in rows),rows
    print(json.dumps({'method':options.method,'mutations':len(rows),'tests':sum(row['completed'] for row in rows),'missed':0}),flush=True)

if __name__=='__main__':
    main()
