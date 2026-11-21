"""Record raw operand relationships and test CPU on the pristine revision only.

Never reads event patches, sample answers, or results of mutant programs.
"""
import ast
import builtins
import gzip
import importlib.machinery
import json
import operator
import os
from pathlib import Path
import sys
import time
from execution_support import install_inline_pools

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'sqlglot'
OPS={'Is':operator.is_, 'IsNot':operator.is_not,'Eq':operator.eq,'NotEq':operator.ne}
CURRENT=['@import','import']
FACTS={}
REPORTS={}
CPU={}
SETUP_CPU={}

def relationship(left,right):
    """Encode identity and value equality only when equality cannot run user code."""
    lt,rt=type(left),type(right)
    pure=(type(None),bool,int,float,str,bytes)
    def builtin_comparison(kind):
        return any(issubclass(kind,base) and getattr(kind,'__eq__',None) is base.__eq__
                   and getattr(kind,'__ne__',None) is base.__ne__ for base in pure)
    if builtin_comparison(lt) and builtin_comparison(rt):
        equality=left == right
    elif (getattr(lt,'__eq__',None) is object.__eq__ and getattr(rt,'__eq__',None) is object.__eq__
          and getattr(lt,'__ne__',None) is object.__ne__ and getattr(rt,'__ne__',None) is object.__ne__):
        equality=left is right
    else:
        return 16
    return 1 << (int(left is right)*2+int(equality))

def observe(site,operation,left,right):
    result=OPS[operation](left,right)
    node=CURRENT[0]
    if CURRENT[1]=='setup' and node != '@import':
        node='@setup:'+node.rsplit('::',1)[0]
    values=FACTS.setdefault(site,{})
    previous=values.get(node,0)
    if not previous & 16:
        values[node]=previous|relationship(left,right)
    return result

class Expressions(ast.NodeTransformer):
    def __init__(self,path):
        self.path=path
    def visit_Compare(self,node):
        self.generic_visit(node)
        if len(node.ops)!=1 or type(node.ops[0]).__name__ not in OPS:
            return node
        site=f'{self.path}:{node.lineno}:{node.col_offset}'
        call=ast.Call(func=ast.Name(id='_br_operand_observer',ctx=ast.Load()),
                      args=[ast.Constant(site),ast.Constant(type(node.ops[0]).__name__),node.left,node.comparators[0]],keywords=[])
        return ast.copy_location(call,node)

class BaselineLoader(importlib.machinery.SourceFileLoader):
    def get_code(self,fullname):
        filename=self.get_filename(fullname)
        relative=Path(filename).resolve().relative_to(SOURCE).as_posix()
        tree=ast.parse(Path(filename).read_bytes(),filename)
        tree=Expressions(relative).visit(tree)
        return compile(ast.fix_missing_locations(tree),filename,'exec',dont_inherit=True)

class BaselineFinder:
    @staticmethod
    def find_spec(fullname,path=None,target=None):
        if fullname!='sqlglot' and not fullname.startswith('sqlglot.'):
            return None
        spec=importlib.machinery.PathFinder.find_spec(fullname,path)
        if spec and spec.origin and spec.origin.endswith('.py'):
            spec.loader=BaselineLoader(fullname,spec.origin)
        return spec

def main():
    # Also enforce this in Python; the offline build uses an OS network namespace.
    from network_guard import install
    install()
    sys.path.insert(0,str(SOURCE))
    os.chdir(SOURCE)
    os.environ['SKIP_INTEGRATION']='0'
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    sys.dont_write_bytecode=True
    builtins._br_operand_observer=observe
    sys.meta_path.insert(0,BaselineFinder())
    install_inline_pools()
    import pytest
    class Measurement:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_protocol(self,item,nextitem):
            CURRENT[:]=[item.nodeid,'setup']
            (ROOT/'current-profile-test.txt').write_text(item.nodeid)
            start=time.process_time()
            yield
            CPU[item.nodeid]=time.process_time()-start
            CURRENT[:]=['@import','import']
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_setup(self,item):
            start=time.process_time()
            yield
            scope=item.nodeid.rsplit('::',1)[0]
            SETUP_CPU[scope]=SETUP_CPU.get(scope,0)+time.process_time()-start
            CURRENT[1]='call'
        def pytest_runtest_logreport(self,report):
            if report.failed:
                REPORTS[report.nodeid]='failed'
            elif report.when=='call':
                REPORTS.setdefault(report.nodeid,report.outcome)
    status=pytest.main(['tests','-q','--tb=short','-p','no:cacheprovider'],plugins=[Measurement()])
    payload={'pin':'eb11aec4be8cb2122afe713a4816662c3f0fb2fa','python':sys.version,'relationship_version':2,
             'facts':FACTS,'cpu':CPU,'setup_cpu':SETUP_CPU,'reports':REPORTS,'pytest_exit':int(status)}
    from operand_proofs import unsafe_tests
    with gzip.open(ROOT/'dependencies.json.gz','rt',encoding='utf-8') as file:
        dependencies=json.load(file)
    identifiers=dependencies['test_ids']
    for lines in dependencies['lines'].values():
        for line,contexts in lines.items():
            lines[line]=[identifiers[number] for number in contexts]
    payload['unsafe_tests']=sorted(unsafe_tests(dependencies,SOURCE))
    with gzip.open(ROOT/'baseline-profile.json.gz','wt',encoding='utf-8') as file:
        json.dump(payload,file,separators=(',',':'))
    print('Profile written:',len(FACTS),'sites',flush=True)

if __name__=='__main__':
    main()
