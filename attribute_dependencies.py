"""Index named configuration reads from pristine source and measured coverage."""
import ast

def augment(mapping,source):
    readers={}
    initialization=set()
    for path,lines in mapping['lines'].items():
        file=source/path
        if not file.is_file() or not path.endswith('.py'):
            continue
        tree=ast.parse(file.read_bytes())
        import_lines=set(mapping.get('import_lines',{}).get(path,[]))
        for node in ast.walk(tree):
            name=None
            if isinstance(node,ast.Attribute) and isinstance(node.ctx,ast.Load):
                name=node.attr
            elif isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('getattr','hasattr') and len(node.args)>=2:
                value=node.args[1]
                if isinstance(value,ast.Constant) and isinstance(value.value,str):
                    name=value.value
            if not name or not name.isupper():
                continue
            contexts=readers.setdefault(name,set())
            for line in range(node.lineno,node.end_lineno+1):
                contexts.update(lines.get(str(line),[]))
                if line in import_lines:
                    initialization.add(name)
    mapping['attribute_readers']={name:sorted(contexts) for name,contexts in readers.items() if contexts}
    mapping['initialization_attributes']=sorted(initialization)

def isolated_scalar_setting(before,after,number):
    """Accept a single scalar assignment in an otherwise identical ordinary class."""
    classes=[node for node in ast.walk(before) if isinstance(node,ast.ClassDef) and node.lineno<=number<=node.end_lineno]
    if not classes:
        return None
    old=min(classes,key=lambda node:node.end_lineno-node.lineno)
    new_classes=[node for node in ast.walk(after) if isinstance(node,ast.ClassDef) and node.name==old.name]
    if len(new_classes)!=1:
        return None
    new=new_classes[0]
    risky=('Enum','IntEnum','StrEnum','NamedTuple','namedtuple')
    if old.decorator_list or new.decorator_list or any(any(label in ast.unparse(base) for label in risky) for base in old.bases):
        return None
    if [ast.dump(node) for node in old.keywords]!=[ast.dump(node) for node in new.keywords]:
        return None
    if ast.dump(ast.Module(body=old.bases,type_ignores=[]))!=ast.dump(ast.Module(body=new.bases,type_ignores=[])):
        return None
    if len(old.body)!=len(new.body):
        return None
    changed=[]
    for a,b in zip(old.body,new.body):
        if ast.dump(a)==ast.dump(b):
            continue
        if not isinstance(a,ast.Assign) or not isinstance(b,ast.Assign) or len(a.targets)!=1 or len(b.targets)!=1:
            return None
        if not isinstance(a.targets[0],ast.Name) or ast.dump(a.targets[0])!=ast.dump(b.targets[0]):
            return None
        name=a.targets[0].id
        if not name.isupper():
            return None
        try:
            values=[ast.literal_eval(a.value),ast.literal_eval(b.value)]
        except (ValueError,TypeError):
            return None
        if any(type(value) not in (type(None),bool,int,float,str,bytes) for value in values):
            return None
        changed.append(name)
    return changed[0] if len(changed)==1 else None
