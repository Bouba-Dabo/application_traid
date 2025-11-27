import ast,glob,re

names = {}
files = glob.glob('**/*.py', recursive=True)
errors = []
for f in files:
    try:
        with open(f, 'r', encoding='utf8') as fh:
            src = fh.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                names.setdefault(('func', node.name), []).append(f)
            if isinstance(node, ast.ClassDef):
                names.setdefault(('class', node.name), []).append(f)
    except Exception as e:
        errors.append((f, str(e)))

dupes = {k:v for k,v in names.items() if len(set(v))>1}

print('\n=== AST PARSE ERRORS ===')
for e in errors:
    print(e[0], e[1])

print('\n=== DUPLICATE FUNCTION/CLASS DEFINITIONS ===')
for (kind,name), flist in sorted(dupes.items()):
    print(kind, name, sorted(set(flist)))

print('\n=== TODO / FIXME OCCURRENCES ===')
for f in files:
    try:
        s = open(f,'r',encoding='utf8').read()
    except Exception:
        continue
    for i,l in enumerate(s.splitlines(), start=1):
        if re.search(r"\bTODO\b|\bFIXME\b", l, re.I):
            print(f"{f}:{i}: {l.strip()}")

print('\n=== POTENTIAL DEBUG PRINTS (lines starting with print() ) ===')
for f in files:
    try:
        s = open(f,'r',encoding='utf8').read()
    except Exception:
        continue
    for i,l in enumerate(s.splitlines(), start=1):
        if re.match(r"\s*print\(", l):
            print(f"{f}:{i}: {l.strip()}")

print('\n=== FILE LIST SCANNED ===')
for f in sorted(files):
    print(f)
