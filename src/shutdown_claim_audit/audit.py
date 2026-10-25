"""Offline documentation and source comparison using only static syntax."""
import ast
import fnmatch
import re
from pathlib import Path

GRACEFUL = ('graceful shutdown', 'shuts down gracefully', 'shutdown gracefully', 'clean shutdown', 'cleanly shuts down', 'handles sigint', 'handles sigterm', 'handles ctrl c', 'handles sighup', 'catches keyboardinterrupt', 'handles keyboardinterrupt', 'graceful exit', 'exits gracefully', 'stops gracefully', 'terminates gracefully', 'signal handler', 'signal handling', 'shutdown handler', 'handles signals', 'handles termination signals')
ABRUPT = ('no graceful shutdown', 'does not handle sigint', 'does not handle sigterm', 'does not handle ctrl c', 'does not handle sighup', 'does not catch keyboardinterrupt', 'no cleanup on shutdown', 'no cleanup on exit', 'no signal handling', 'exits immediately')
SIGNALS = {'SIGINT', 'SIGTERM', 'SIGHUP', 'SIGQUIT'}
WAITS = {'sigwait', 'sigwaitinfo', 'sigtimedwait', 'pause'}


def normalize(text):
    return ' '.join(text.lower().replace("n't", ' not').replace('-', ' ').split())


def claims(text, path='README.md'):
    result = []
    fence = None
    prose = []
    for line_no, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            prose.append((line_no, ''))
            continue
        if fence:
            prose.append((line_no, ''))
            continue
        prose.append((line_no, line))
    paragraphs = []
    for line_no, line in prose:
        if line.strip() and paragraphs and paragraphs[-1][1]:
            start, prior = paragraphs[-1]
            paragraphs[-1] = (start, prior + '\n' + line)
        else:
            paragraphs.append((line_no, line))
    for line_no, line in paragraphs:
        offset = 0
        for sentence in re.split(r'(?<=[.!?])\s+', line):
            position = line.find(sentence, offset)
            sentence_line = line_no + line[:position].count('\n')
            offset = position + len(sentence)
            normalized = normalize(sentence)
            masked = normalized
            abrupt = False
            for phrase in ABRUPT:
                pattern = r'(?<!\w)' + re.escape(phrase) + r'(?!\w)'
                if re.search(pattern, masked):
                    abrupt = True
                    masked = re.sub(pattern, lambda m: ' ' * len(m.group()), masked)
            if abrupt:
                result.append(dict(family='abrupt', path=path, line=sentence_line, sentence=sentence.strip()))
            if not re.search(r'\b(no|not|never|without)\b', normalized):
                if any(re.search(r'(?<!\w)' + re.escape(p) + r'(?!\w)', masked) for p in GRACEFUL):
                    result.append(dict(family='graceful', path=path, line=sentence_line, sentence=sentence.strip()))
    return result


def evidence(source, path):
    tree = ast.parse(source, filename=path)
    modules = {'signal'}
    imported = {}
    signal_imported = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'signal':
                    modules.add(alias.asname or alias.name)
                    signal_imported = True
        elif isinstance(node, ast.ImportFrom) and node.module == 'signal':
            signal_imported = True
            for alias in node.names:
                imported[alias.asname or alias.name] = alias.name

    def symbol(node):
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Name):
            return imported.get(node.id, node.id)
        return None

    def termination(node, method=False):
        return symbol(node) in SIGNALS and (isinstance(node, ast.Attribute) or method or signal_imported)

    def signal_call(node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules:
            return node.attr
        if isinstance(node, ast.Name):
            return imported.get(node.id)
        return None

    found = {}
    for node in ast.walk(tree):
        kind = None
        if isinstance(node, ast.Call):
            name = signal_call(node.func)
            if name == 'signal' and node.args and termination(node.args[0]):
                if len(node.args) < 2 or symbol(node.args[1]) != 'SIG_DFL':
                    kind = 'signal.signal'
            elif isinstance(node.func, ast.Attribute) and node.func.attr == 'add_signal_handler' and node.args and termination(node.args[0], True):
                kind = 'add_signal_handler'
            elif name in WAITS:
                kind = 'signal.' + name
        elif isinstance(node, ast.ExceptHandler):
            types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            if any(isinstance(t, ast.Name) and t.id == 'KeyboardInterrupt' for t in types):
                kind = 'KeyboardInterrupt'
        if kind:
            found[(node.lineno, kind)] = dict(path=path, line=node.lineno, kind=kind)
    return sorted(found.values(), key=lambda e: (e['line'], e['kind']))


def audit(root, ignore=()):
    root = Path(root)
    all_claims, production, test_only, errors = [], [], [], []
    source_count = 0
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        name = relative.as_posix()
        if any((root.joinpath(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
            continue
        if not path.is_file() or any(fnmatch.fnmatch(name, pattern) for pattern in ignore):
            continue
        if path.name == '.env' or path.name.startswith('.env.'):
            continue
        is_doc = name == 'README.md' or (relative.parts[0] == 'docs' and path.suffix == '.md')
        if is_doc:
            all_claims.extend(claims(path.read_text(encoding='utf-8', errors='replace'), name))
        if path.suffix != '.py':
            continue
        testing = any(p in ('tests', 'fixtures') for p in relative.parts[:-1]) or path.name.startswith('test_') or path.name.endswith('_test.py')
        if not testing and any(p in ('docs', 'examples') for p in relative.parts[:-1]):
            continue
        if not testing:
            source_count += 1
        try:
            items = evidence(path.read_text(encoding='utf-8-sig'), name)
            (test_only if testing else production).extend(items)
        except (SyntaxError, UnicodeError, OSError) as exc:
            errors.append(dict(path=name, message=str(exc)))
    findings, verified = [], []
    for family in ('graceful', 'abrupt'):
        selected = [c for c in all_claims if c['family'] == family]
        if not selected:
            continue
        severity, items, suffix = None, [], ''
        if not source_count:
            severity, items = 'HIGH', [dict(kind='no-source')]
        elif family == 'graceful' and not production:
            severity, items = ('MEDIUM', test_only) if test_only else ('HIGH', [])
            suffix = '-test-only' if test_only else ''
        elif family == 'abrupt' and production:
            severity, items = 'HIGH', production
        elif family == 'abrupt' and test_only:
            severity, items, suffix = 'MEDIUM', test_only, '-test-only'
        if severity:
            findings.append(dict(rule='documented-' + family + '-claim-false' + suffix, severity=severity, claims=selected, evidence=items))
        else:
            verified.append(dict(family=family, claims=selected, evidence=production if family == 'graceful' else []))
    return dict(findings=findings, verified_claims=verified, claims=all_claims, evidence=production, test_only_evidence=test_only, errors=errors)
