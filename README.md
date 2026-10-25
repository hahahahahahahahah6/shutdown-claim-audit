# shutdown-claim-audit

Offline audit of documented interruption promises against Python source.

A standard-library-only static CLI, version 0.1.0, for Python 3.9+.
It executes no audited code, uses no network, and never reads `.env` contents.
It reads root README.md and docs/**/*.md and compares their promises with AST evidence.

## Usage

```sh
python3 -m pip install .
shutdown-claim-audit /path/to/project
python3 -m shutdown_claim_audit /path/to/project --json
shutdown-claim-audit . --ignore 'vendor/*' --fail-on HIGH --verbose
shutdown-claim-audit --version
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

The positional path defaults to the current directory. `--ignore` is repeatable
and matches relative paths as globs. `--json` emits findings and verified_claims,
plus extracted claims, production/test evidence, parse errors, and status.
`--verbose` includes the complete report in text mode. Exit codes are 0 for
PASS, 2 for threshold failure, and 1 for usage errors. The default threshold
is MEDIUM; `--fail-on HIGH` still reports MEDIUM findings but does not fail them.

## Rules

Exact rule identifiers are shown below to keep the tool's own docs neutral.

```
documented-graceful-claim-false
documented-graceful-claim-false-test-only
documented-abrupt-claim-false
documented-abrupt-claim-false-test-only
```

| Family | Condition | Severity |
| --- | --- | --- |
| graceful | No production or test evidence | HIGH |
| graceful | Evidence only in tests/fixtures | MEDIUM |
| abrupt | Production evidence exists | HIGH |
| abrupt | Evidence only in tests/fixtures | MEDIUM |
| Either | No production source | HIGH, no-source |

Each family produces at most one finding with all relevant claim sentences
and evidence. A production-backed graceful family or an abrupt family with no
evidence anywhere is verified. No claims means PASS, regardless of evidence.
Contradictory families are evaluated independently.

## Claim vocabulary

Only these exact phrases count, with word boundaries after normalisation:

```
graceful shutdown
shuts down gracefully
shutdown gracefully
clean shutdown
cleanly shuts down
handles sigint
handles sigterm
handles ctrl c
handles sighup
catches keyboardinterrupt
handles keyboardinterrupt
graceful exit
exits gracefully
stops gracefully
terminates gracefully
signal handler
signal handling
shutdown handler
handles signals
handles termination signals
no graceful shutdown
does not handle sigint
does not handle sigterm
does not handle ctrl c
does not handle sighup
does not catch keyboardinterrupt
no cleanup on shutdown
no cleanup on exit
no signal handling
exits immediately
```

Normalisation lowercases, replaces `n't` with ` not`, replaces hyphens with
spaces, and collapses whitespace. Inline code counts as prose; fenced blocks
are excluded. ABRUPT spans are matched and masked before GRACEFUL matching.
A sentence with no/not/never/without suppresses GRACEFUL matches. ABRUPT
negations still count. This is deliberately asymmetric:

```
doesn't handle sigterm => abrupt
never shuts down gracefully => no claim
does not claim graceful shutdown => no claim
graceful-shutdown => graceful
handles Ctrl-C => graceful
```

Bare “shutdown” and bare “signal” are not claims.

## Named evidence

| AST evidence | Notes |
| --- | --- |
| `signal.signal` | First argument SIGINT/SIGTERM/SIGHUP/SIGQUIT; SIG_DFL excluded; SIG_IGN counts |
| `add_signal_handler` | Any receiver; same termination constants |
| `except KeyboardInterrupt` | Direct name or name inside a tuple |
| `signal.sigwait`, `signal.sigwaitinfo`, `signal.sigtimedwait`, `signal.pause` | Dotted calls and imported aliases |

Imports and aliases are tracked per file. Attribute signal constants count;
bare constants for the registration function require a signal import.
The method form accepts bare constants without import tracking. Deliberately
ignoring a signal via SIG_IGN counts as evidence. Items deduplicate by file,
line, and kind.

## Differentiation

- `error-claim-audit` checks failure behaviour produced by code, rather than interruption responses.
- `locking-claim-audit` checks cross-process exclusion, rather than teardown.
- `concurrency-claim-audit` checks parallelism within a run.
- bandit and ruff detect code issues rather than documentation promises.
- The standard library signal and asyncio documentation describes primitives rather than comparing docs with implementation.

## Honest limitations

Exact-phrase matching cannot understand arbitrary prose or resolve ambiguity.
Dead code counts. A handler for only one termination constant verifies the entire
positive family, even when the documentation names another constant. This checks
existence, not coverage, actual cleanup quality, or runtime reachability.
`atexit.register` does not count: it need not run on default SIGTERM termination.
A try/finally alone does not count. `signal.getsignal` and `signal.strsignal`
only inspect information and do not count. Strings and comments do not count.
Shell/subprocess strings containing trap or kill are not parsed.
Bare except and except Exception do not count. Dynamic imports, wrapper
functions, and indirect registrations can be missed. Syntax errors are recorded
in JSON; unparseable files contribute no evidence. This is a heuristic, not proof.

Production excludes tests/ directories, test_*.py, *_test.py, fixtures/,
docs/, and examples/. Test evidence is collected separately from tests/fixtures
and test-named modules. Symlinked files and symlinked directory contents are
skipped. Ignore patterns remove docs and evidence alike. The tool reads only
selected Markdown and source files; `.env` and `.env.*` contents are skipped.
No audited source is imported or executed.

## Development and packaging

Tests use unittest. The source distribution includes tests and all fixture files
via MANIFEST.in. The wheel installs the package and console entry point.
The package has no runtime dependencies and is licensed under MIT.
