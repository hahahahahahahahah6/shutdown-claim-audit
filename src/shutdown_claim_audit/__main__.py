import argparse
import json
from pathlib import Path
from . import __version__
from .audit import audit


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage()
        self.exit(1, '%s: error: %s\n' % (self.prog, message))


def main(argv=None):
    parser = Parser(description='Audit documented interruption promises against Python source.')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[], metavar='GLOB')
    parser.add_argument('--fail-on', choices=('MEDIUM', 'HIGH'), default='MEDIUM')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    if not Path(args.path).is_dir():
        parser.error('path must be an existing directory')
    try:
        result = audit(args.path, args.ignore)
    except OSError as exc:
        parser.error(str(exc))
    failed = any(f['severity'] == 'HIGH' or args.fail_on == 'MEDIUM' for f in result['findings'])
    result['status'] = 'FAIL' if failed else 'PASS'
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result['status'])
        for finding in result['findings']:
            print(finding['severity'] + ' ' + finding['rule'])
            for claim in finding['claims']:
                print('  {path}:{line}: {sentence}'.format(**claim))
            for item in finding['evidence']:
                print('  ' + json.dumps(item, sort_keys=True))
        if args.verbose:
            print(json.dumps(result, indent=2))
    return 2 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
