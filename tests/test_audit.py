import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from shutdown_claim_audit.audit import audit, claims, evidence, GRACEFUL, ABRUPT
from shutdown_claim_audit.__main__ import main

FIXTURES = Path(__file__).parent / 'fixtures'


class AuditTests(unittest.TestCase):
    def project(self, doc, code='x = 1', extra=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'README.md').write_text(doc)
        if code is not None:
            (root / 'main.py').write_text(code)
        for name, value in (extra or {}).items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value)
        return root

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = main(list(args))
        return status, out.getvalue()

    def test_fixtures(self):
        for name, status, rule in [
            ('good/graceful', 0, None), ('good/abrupt', 0, None),
            ('bad/graceful', 2, 'documented-graceful-claim-false'),
            ('bad/abrupt', 2, 'documented-abrupt-claim-false'),
            ('testonly', 2, 'documented-graceful-claim-false-test-only')]:
            with self.subTest(name=name):
                code, output = self.run_cli(str(FIXTURES/name), '--json')
                data = json.loads(output)
                self.assertEqual(code, status)
                self.assertEqual([f['rule'] for f in data['findings']], [rule] if rule else [])
                self.assertEqual(len(data['verified_claims']), 0 if rule else 1)

    def test_threshold(self):
        code, output = self.run_cli(str(FIXTURES/'testonly'), '--fail-on', 'HIGH', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['findings'][0]['severity'], 'MEDIUM')

    def test_version(self):
        with self.assertRaises(SystemExit) as exc, contextlib.redirect_stdout(io.StringIO()) as out:
            main(['--version'])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), '0.1.0')

    def test_usage(self):
        with self.assertRaises(SystemExit) as exc, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            main(['--unknown'])
        self.assertEqual(exc.exception.code, 1)

    def test_ignore(self):
        root = self.project('graceful shutdown', 'import signal\nsignal.pause()')
        self.assertFalse(audit(root)['findings'])
        self.assertEqual(audit(root, ['main.py'])['findings'][0]['evidence'], [{'kind': 'no-source'}])

    def test_ignore_evidence_only(self):
        root = self.project('graceful shutdown', extra={'worker.py':'import signal\nsignal.pause()'})
        self.assertEqual(audit(root, ['worker.py'])['findings'][0]['severity'], 'HIGH')

    def test_both_no_source(self):
        root = self.project('graceful shutdown. No graceful shutdown.', None)
        self.assertEqual(len(audit(root)['findings']), 2)

    def test_both_test_only(self):
        root = self.project('graceful shutdown. No graceful shutdown.', extra={'tests/task.py':'import signal\nsignal.pause()'})
        self.assertEqual([f['severity'] for f in audit(root)['findings']], ['MEDIUM', 'MEDIUM'])

    def test_no_claim(self):
        self.assertFalse(audit(self.project('Notes', 'import signal\nsignal.pause()'))['findings'])

    def test_docs(self):
        root = self.project('Notes', extra={'docs/nested/a.md':'graceful shutdown', 'other.md':'no graceful shutdown'})
        self.assertEqual(len(audit(root)['claims']), 1)

    def test_env_never_opened(self):
        root = self.project('Notes', extra={'.env':'SHUTDOWN_AUDIT_MARKER_DO_NOT_READ'})
        original = Path.open
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path.name, '.env')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            result = audit(root)
        self.assertNotIn('SHUTDOWN_AUDIT_MARKER_DO_NOT_READ', json.dumps(result))

    def test_symlinks(self):
        root = self.project('graceful shutdown', extra={'examples/handler.py':'import signal\nsignal.pause()'})
        (root/'linked.py').symlink_to(root/'examples/handler.py')
        self.assertEqual(audit(root)['evidence'], [])

    def test_self_audit(self):
        result = audit(Path(__file__).resolve().parents[1])
        self.assertEqual(result['claims'], [])
        self.assertEqual(result['findings'], [])

    def test_parse_errors(self):
        self.assertEqual(len(audit(self.project('Notes', 'def broken('))['errors']), 1)

    def test_multiline_negation(self):
        self.assertEqual(claims('We do not promise\ngraceful shutdown.'), [])

    def test_dedupe(self):
        items = evidence('import signal\nsignal.pause(); signal.pause()', 'a.py')
        self.assertEqual(len(items), 1)

    def test_all_evidence_reported(self):
        result = audit(self.project('no graceful shutdown', 'import signal\nsignal.pause()\nsignal.pause()'))
        self.assertEqual(len(result['findings'][0]['evidence']), 2)


def claim_test(text, expected):
    def test(self):
        self.assertEqual([c['family'] for c in claims(text)], expected)
    return test


for index, phrase in enumerate(GRACEFUL):
    setattr(AuditTests, 'test_graceful_phrase_%02d' % index, claim_test(phrase, ['graceful']))
for index, phrase in enumerate(ABRUPT):
    setattr(AuditTests, 'test_abrupt_phrase_%02d' % index, claim_test(phrase, ['abrupt']))
for index, (text, expected) in enumerate([
    ('```\ngraceful shutdown\n```', []), ('~~~md\ngraceful shutdown\n~~~', []),
    ('`graceful shutdown`', ['graceful']), ("doesn't handle sigterm", ['abrupt']),
    ('graceful-shutdown', ['graceful']), ('handles Ctrl-C', ['graceful']),
    ('never shuts down gracefully', []), ('shutdown', []), ('signal', []),
    ('does not claim graceful shutdown', []), ('handles all sigint', []),
    ('ungraceful shutdown', []), ('graceful   shutdown', ['graceful'])]):
    setattr(AuditTests, 'test_claim_edge_%02d' % index, claim_test(text, expected))


def evidence_test(code, count):
    def test(self):
        self.assertEqual(len(evidence(code, 'main.py')), count)
    return test


for index, (code, count) in enumerate([
    ('import signal\nsignal.signal(SIGINT)', 1),
    ('import signal\nsignal.signal(SIGTERM, SIG_DFL)', 0),
    ('import signal\nsignal.signal(SIGINT, SIG_IGN)', 1),
    ('loop.add_signal_handler(SIGINT)', 1),
    ('try: pass\nexcept KeyboardInterrupt: pass', 1),
    ('try: pass\nexcept (ValueError, KeyboardInterrupt): pass', 1),
    ('try: pass\nexcept: pass', 0),
    ('try: pass\nexcept Exception: pass', 0),
    ('signal.pause()', 1), ('atexit.register(cleanup)', 0),
    ('try: pass\nfinally: pass', 0), ('signal.getsignal(signal.SIGINT)', 0),
    ('signal.strsignal(signal.SIGINT)', 0),
    ('"signal.pause()"\n# signal.pause()', 0),
    ('import signal as s\ns.signal(s.SIGHUP, handler)', 1),
    ('from signal import signal as register, SIGQUIT as Q\nregister(Q, handler)', 1),
    ('from signal import pause as wait\nwait()', 1),
    ('signal.signal(SIGINT, handler)', 0),
    ('signal.signal(signal.SIGINT, handler)', 1),
    ('other.signal(other.SIGINT, handler)', 0),
    ('signal.signal(signal.SIGUSR1, handler)', 0),
    ('loop.add_signal_handler(signal.SIGTERM, handler)', 1),
    ('signal.sigwait({signal.SIGTERM})', 1),
    ('signal.sigwaitinfo({signal.SIGTERM})', 1),
    ('signal.sigtimedwait({signal.SIGTERM}, 1)', 1),
    ('def never_called():\n signal.pause()', 1),
    ('subprocess.run("trap cleanup TERM", shell=True)', 0),
    ('import signal as s\ns.signal(s.SIGINT, s.SIG_DFL)', 0),
]):
    setattr(AuditTests, 'test_evidence_%02d' % index, evidence_test(code, count))


if __name__ == '__main__':
    unittest.main()
