"""Recognize private-local renames by comparing compiler output, without execution."""
import dis
import inspect
import re
import types

FORBIDDEN={'locals','globals','vars','eval','exec','__code__','f_locals','f_code',
           'currentframe','_getframe','getargvalues','getouterframes','settrace','setprofile'}
ORIGINAL={}

def constant_equal(left,right):
    if type(left) is not type(right):
        return False
    if isinstance(left,types.CodeType):
        return code_equal(left,right)
    if isinstance(left,tuple):
        return len(left)==len(right) and all(constant_equal(a,b) for a,b in zip(left,right))
    # repr preserves signed zero and the difference between bool and int.
    return repr(left)==repr(right)

def code_equal(left,right):
    fields=('co_code','co_names','co_argcount','co_posonlyargcount','co_kwonlyargcount',
            'co_nlocals','co_stacksize','co_flags','co_freevars','co_cellvars',
            'co_name','co_qualname','co_firstlineno','co_exceptiontable')
    if any(getattr(left,key)!=getattr(right,key) for key in fields):
        return False
    if tuple(left.co_lines())!=tuple(right.co_lines()):
        return False
    if left.co_varnames!=right.co_varnames:
        parameters=left.co_argcount+left.co_kwonlyargcount
        parameters+=bool(left.co_flags&inspect.CO_VARARGS)+bool(left.co_flags&inspect.CO_VARKEYWORDS)
        if left.co_varnames[:parameters]!=right.co_varnames[:parameters]:
            return False
        if left.co_freevars or left.co_cellvars or FORBIDDEN.intersection(left.co_names):
            return False
        if any(instruction.opname in ('LOAD_FAST_CHECK','DELETE_FAST') for instruction in dis.get_instructions(left)):
            return False
    return constant_equal(left.co_consts,right.co_consts)

def likely_binding_change(changes):
    pattern=re.compile(r'\b([A-Za-z_]\w*)\s*=(?!=)|\bfor\s+(\w+)\s+in\b|\bas\s+(\w+)\b')
    old=set()
    new=set()
    for change in changes:
        for _,hunk in change['hunks']:
            for line in hunk:
                if line[:1] not in ('-','+'):
                    continue
                target=old if line[0]=='-' else new
                for match in pattern.finditer(line[1:]):
                    target.update(value for value in match.groups() if value)
    return old!=new

def equivalent_patch(text,source):
    from entry import parse_patch,reconstruct
    try:
        changes=parse_patch(text)
        if not changes or not likely_binding_change(changes):
            return False
        for change in changes:
            path=change['path']
            if not path.startswith('sqlglot/') or not path.endswith('.py') or '..' in path.split('/'):
                return False
            file=source/path
            before=file.read_text('utf-8')
            after=reconstruct(before,change['hunks'])
            if len(before.splitlines())!=len(after.splitlines()):
                return False
            key=str(file)
            if key not in ORIGINAL:
                ORIGINAL[key]=compile(before,path,'exec',dont_inherit=True)
            modified=compile(after,path,'exec',dont_inherit=True)
            if not code_equal(ORIGINAL[key],modified):
                return False
        return True
    except (OSError,ValueError,SyntaxError,TypeError):
        return False
