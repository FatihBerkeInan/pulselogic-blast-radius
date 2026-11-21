"""Narrow runtime edits only when their enclosing control-flow shape is stable."""
import ast
import copy

def initialization_changed(before,after):
    def strip(node):
        if not isinstance(node,ast.AST):
            return node
        result=copy.copy(node)
        for name,value in ast.iter_fields(node):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and name=='body':
                first=node.body[0] if node.body else None
                doc=(isinstance(first,ast.Expr) and isinstance(first.value,ast.Constant) and isinstance(first.value.value,str))
                setattr(result,name,[first,ast.Pass()] if doc else [ast.Pass()])
            elif isinstance(node,ast.Lambda) and name=='body':
                setattr(result,name,ast.Constant(None))
            elif isinstance(value,ast.AST):
                setattr(result,name,strip(value))
            elif isinstance(value,list):
                setattr(result,name,[strip(child) for child in value])
        return result
    return ast.dump(strip(before))!=ast.dump(strip(after))

def control_shape(node):
    pieces=[type(node).__name__]
    for name,value in ast.iter_fields(node):
        if name not in ('body','orelse','finalbody','handlers','cases'):
            continue
        if isinstance(value,list):
            pieces.append((name,tuple(control_shape(item) for item in value)))
        elif isinstance(value,ast.AST):
            pieces.append((name,control_shape(value)))
    return tuple(pieces)

def corresponding(old,new,target):
    if old is target:
        return new if type(old) is type(new) else None
    if type(old) is not type(new):
        return None
    for field,value in ast.iter_fields(old):
        other=getattr(new,field,None)
        if isinstance(value,ast.AST) and isinstance(other,ast.AST):
            result=corresponding(value,other,target)
            if result is not None:
                return result
        elif isinstance(value,list) and isinstance(other,list):
            for left,right in zip(value,other):
                if isinstance(left,ast.AST) and isinstance(right,ast.AST):
                    result=corresponding(left,right,target)
                    if result is not None:
                        return result
    return None

def execution_span(tree,modified,function,line):
    if isinstance(function,ast.Lambda):
        return function
    replacement=corresponding(tree,modified,function)
    if replacement is None or control_shape(function)!=control_shape(replacement):
        return function
    statements=[node for node in ast.walk(function)
                if isinstance(node,ast.stmt) and node.lineno<=line<=node.end_lineno]
    return min(statements,key=lambda node:node.end_lineno-node.lineno) if statements else function
