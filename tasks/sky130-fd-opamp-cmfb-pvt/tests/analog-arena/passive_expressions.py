"""Resolve constant RLC expressions for eligibility only; never rewrite the DUT.

No eval, simulator, file access, behavioral variables, or topology recognition.
The opt-in caller supplies the declared root interface and numeric-literal parser.
"""
import ast
import math
import re


FUNCTIONS = {name: getattr(math, name) for name in
             ('sqrt', 'exp', 'log', 'log10', 'sin', 'cos', 'tan', 'asin', 'acos',
              'atan', 'sinh', 'cosh', 'tanh', 'floor', 'ceil')}
FUNCTIONS.update(abs=abs, min=min, max=max, pow=pow, ln=math.log)
TOKEN = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[a-zA-Z]*|[a-zA-Z_]\w*|\*\*|[-+*/%^(),]")
ASSIGN = re.compile(r"(?<!\w)([a-zA-Z_]\w*)\s*=")


def assignments(text):
    text = re.sub(r'^\s*params:\s*', '', text, flags=re.I).strip()
    if not text:
        return {}
    matches = list(ASSIGN.finditer(text))
    if not matches or text[:matches[0].start()].strip():
        raise ValueError('expected constant parameter assignments')
    result = {}
    for index, match in enumerate(matches):
        end = matches[index+1].start() if index+1 < len(matches) else len(text)
        value = text[match.end():end].strip()
        key = match[1].lower()
        if not value or key in result:
            raise ValueError('empty or duplicate parameter '+key)
        result[key] = value
    return result


def expression(text, resolve, number):
    text = text.strip().lower()
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1]
    # Curly braces are expression delimiters, including nested constant terms.
    text = text.replace('{', '(').replace('}', ')')
    tokens = []
    position = 0
    for match in TOKEN.finditer(text):
        if text[position:match.start()].strip():
            raise ValueError('unsupported/nonconstant expression '+text)
        token = match[0]
        if token[0].isdigit() or token[0]=='.':
            value = number(token)
            if value is None:
                raise ValueError('nonfinite numeric value '+token)
            token = repr(value)
        tokens.append('**' if token=='^' else token)
        position = match.end()
    if text[position:].strip():
        raise ValueError('unsupported/nonconstant expression '+text)
    tree = ast.parse(' '.join(tokens), mode='eval')

    def visit(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int,float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            return resolve(node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            return visit(node.operand) * (-1 if isinstance(node.op,ast.USub) else 1)
        if isinstance(node, ast.BinOp):
            a,b = visit(node.left),visit(node.right)
            if isinstance(node.op,ast.Add): return a+b
            if isinstance(node.op,ast.Sub): return a-b
            if isinstance(node.op,ast.Mult): return a*b
            if isinstance(node.op,ast.Div): return a/b
            if isinstance(node.op,ast.Pow): return a**b
            if isinstance(node.op,ast.Mod): return math.fmod(a,b)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
            return FUNCTIONS[node.func.id](*(visit(arg) for arg in node.args))
        raise ValueError('unsupported/nonconstant expression '+text)

    value = visit(tree.body)
    if not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('nonfinite expression '+text)
    return value


def resolver(definitions, inherited, number):
    cache = {}
    active = set()
    def get(name):
        name = name.lower()
        if name in cache: return cache[name]
        if name in active: raise ValueError('cyclic parameter '+name)
        if name not in definitions:
            if name in inherited: return inherited[name]
            if name in ('pi','e'): return getattr(math,name)
            raise ValueError('unknown/nonconstant parameter '+name)
        active.add(name)
        value = expression(definitions[name],get,number)
        active.remove(name)
        cache[name] = value
        return value
    return get


def header(text):
    match = ASSIGN.search(text)
    prefix = text[:match.start()] if match else text
    prefix = re.sub(r'\s*params:\s*$', '', prefix, flags=re.I)
    return prefix.split(), assignments(text[match.start():] if match else '')


def validate_passive(lines, required, number):
    """Validate every statement and evaluate each reachable RLC at its call scope."""
    if not required:
        raise ValueError('passive-expression mode needs --require-subcircuit')
    circuits, globals_ = {}, {}
    current = None
    for lineno,text in lines:
        if not text or text[0] in '*;': continue
        words = text.split()
        kind = words[0].lower()
        if kind=='.subckt':
            positional, defaults = header(' '.join(words[1:]))
            if current or len(positional)<2 or positional[0].lower() in circuits:
                raise ValueError('nested, duplicate or malformed subcircuit')
            current = positional[0].lower()
            circuits[current] = {'pins':[pin.lower() for pin in positional[1:]],
                                 'defaults':defaults,'params':{},'elements':[]}
        elif kind=='.ends':
            if current is None or len(words)>2 or (len(words)==2 and words[1].lower()!=current):
                raise ValueError('mismatched .ends')
            current = None
        elif kind=='.param':
            destination = circuits[current]['params'] if current else globals_
            for key,value in assignments(text[len(words[0]):]).items():
                if key in destination: raise ValueError('duplicate parameter '+key)
                destination[key] = value
        elif kind.startswith('.') or current is None or kind[0] not in 'rlcx':
            raise ValueError('disallowed statement at line %d: %s'%(lineno,text))
        else:
            circuits[current]['elements'].append((lineno,text))
    if current: raise ValueError('missing .ends')
    name,*pins = [word.lower() for word in required]
    if name not in circuits or circuits[name]['pins']!=pins:
        raise ValueError('required subcircuit interface mismatch')
    global_get = resolver(globals_,{},number)
    global_values = {name:global_get(name) for name in globals_}
    leaf_count = 0

    def walk(name,inherited,overrides,stack):
        nonlocal leaf_count
        if name in stack or name not in circuits:
            raise ValueError('recursive or unknown subcircuit '+name)
        circuit = circuits[name]
        get = resolver({**circuit['defaults'],**circuit['params'],
                        **{key:repr(value) for key,value in overrides.items()}}, inherited,number)
        scope = dict(inherited)
        for key in set(circuit['defaults']) | set(circuit['params']) | set(overrides):
            scope[key] = get(key)
        names=set()
        for lineno,text in circuit['elements']:
            parts=text.split(maxsplit=3)
            instance=parts[0].lower()
            if instance in names: raise ValueError('duplicate instance '+instance)
            names.add(instance)
            if instance.startswith('x'):
                positional, params = header(text)
                target=positional[-1].lower()
                if target not in circuits or len(positional)-2!=len(circuits[target]['pins']):
                    raise ValueError('unknown subcircuit or pin count at line %d'%lineno)
                values={key:expression(value,get,number) for key,value in params.items()}
                walk(target,scope,values,stack+(name,))
            else:
                if len(parts)!=4: raise ValueError('malformed RLC at line %d'%lineno)
                value=expression(parts[3],get,number)
                if value<=0: raise ValueError('RLC value must be positive at line %d'%lineno)
                leaf_count+=1
    walk(name,global_values,{},())
    if not leaf_count: raise ValueError('required subcircuit has no RLC leaf')
