"""Pinned source proof. No installation, live API, credentials, or external network in verify."""
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import zipfile

APP = '77f4f9a975504817c3e79ce2f3dcc5756e33c325'
TREE = '6f8bcc33a01ee6883d113f9866c37b5dc6a02d6b'
OLD = '04b025f44814be1b05d7cab5a6314af93bc6520a'
MAIN = 'e8a94e2e28809e10168243cb188fc49371ff93f3'
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'proof' / 'out'
SOURCE = OUT / 'app'
FEE = 'src/lib/scan/feewallets.js'
ENV = {'PATH': os.environ['PATH'], 'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8',
       'TMPDIR': str(OUT), 'TZ': 'UTC'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def run(name, args, expected=0, cwd=SOURCE):
    completed = subprocess.run(args, cwd=cwd, env=ENV, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=120)
    (OUT / (name + '.log')).write_bytes(completed.stdout)
    assert completed.returncode == expected, (name, completed.returncode, expected)
    return completed.stdout.decode('utf-8')


def totals(log, tests, passes, failures):
    for key, value in [('tests', tests), ('pass', passes), ('fail', failures),
                       ('skipped', 0), ('cancelled', 0)]:
        assert re.search(r'^# ' + key + r' ' + str(value) + r'$', log, re.M), (key, value)
    assert 'ERR_MODULE_NOT_FOUND' not in log and 'SyntaxError' not in log


def verify_source(manifest):
    for row in manifest:
        assert digest((SOURCE / row['path']).read_bytes()) == row['sha256'], row['path']


def prepare():
    assert sys.platform == 'linux'
    assert not OUT.exists(), 'do not overwrite or automatically rerun a prior proof'
    OUT.mkdir(parents=True)
    assert git('rev-parse', APP + '^{tree}').decode().strip() == TREE
    assert git('show', '-s', '--format=%P', APP).decode().strip().split() == [OLD, MAIN]
    assert not git('status', '--porcelain').strip()
    assert shutil.which('node') and shutil.which('zip'), 'use existing runner tools only'
    # The proof commit must add only this harness, its workflow and the pinned source manifest.
    proof_paths = set(git('diff', '--name-only', APP, 'HEAD').decode().splitlines())
    assert proof_paths == {'.github/workflows/glass-pr17-integration-r1031.yml',
                           'proof/pr17-integration-r1031.py', 'proof/pr17-source-manifest.json'}
    SOURCE.mkdir()
    with tarfile.open(fileobj=io.BytesIO(git('archive', APP)), mode='r:') as archive:
        for member in archive.getmembers():
            p = PurePosixPath(member.name)
            assert not p.is_absolute() and '..' not in p.parts
            assert member.isfile() or member.isdir(), 'no links or special members'
        archive.extractall(SOURCE, filter='data')
    files = git('ls-tree', '-r', APP).decode().splitlines()
    manifest = []
    for line in files:
        meta, name = line.split('\t')
        mode, kind, blob = meta.split()
        assert kind == 'blob' and mode in ('100644', '100755')
        data = git('cat-file', 'blob', blob)
        manifest.append({'path': name, 'git_blob': blob, 'sha256': digest(data)})
    verify_source(manifest)
    pinned = json.loads((ROOT / 'proof/pr17-source-manifest.json').read_text())
    indexed = {row['path']: row for row in manifest}
    for row in pinned:
        assert indexed[row['path']]['git_blob'] == row['git_blob']
        assert indexed[row['path']]['sha256'] == row['sha256']
    assert set(git('diff', '--name-only', MAIN, APP).decode().splitlines()) == set(row['path'] for row in pinned)
    assert len(pinned) == 18
    for parent, name in [(OLD, 'submitted'), (MAIN, 'upstream')]:
        (OUT / (name + '-fee-original.bin')).write_bytes(git('show', parent + ':' + FEE))
    (OUT / 'source.patch').write_bytes(git('diff', OLD, APP))
    run('whitespace', ['git', 'diff', '--check', MAIN, APP], cwd=ROOT)
    save('source-manifest.json', manifest)
    save('prepared.json', {'application': APP, 'tree': TREE, 'parents': [OLD, MAIN],
                          'net_namespace': os.readlink('/proc/self/ns/net'),
                          'proof_head': git('rev-parse', 'HEAD').decode().strip(),
                          'changed_files': len(pinned)})


def verify():
    assert sys.platform == 'linux'
    prepared = json.loads((OUT / 'prepared.json').read_text())
    assert os.readlink('/proc/self/ns/net') != prepared['net_namespace'], 'must run in separate net namespace'
    with socket.socket() as probe:
        probe.settimeout(2)
        try:
            probe.connect(('192.0.2.1', 443))
            raise AssertionError('network route unexpectedly available')
        except OSError as error:
            assert error.errno == errno.ENETUNREACH, repr(error)
    manifest = json.loads((OUT / 'source-manifest.json').read_text())
    verify_source(manifest)
    environment = run('environment', ['node', '-e', "const assert=require('node:assert/strict'); assert.equal((1234).toLocaleString(),'1,234'); console.log(JSON.stringify({node:process.version,locale:Intl.NumberFormat().resolvedOptions().locale,platform:process.platform}));"])
    fee = SOURCE / FEE
    fixed = fee.read_bytes()
    controls = []
    try:
        for name, passes, failures in [('submitted', 4, 5), ('upstream', 8, 1)]:
            fee.write_bytes((OUT / (name + '-fee-original.bin')).read_bytes())
            log = run(name + '-negative-control', ['node', '--test', '--test-reporter=tap',
                      'test/fee-wallet-labels.test.mjs'], expected=1)
            totals(log, 9, passes, failures)
            assert log.count("code: 'ERR_ASSERTION'") == failures
            controls.append({'source': name, 'tests': 9, 'pass': passes, 'fail': failures})
    finally:
        fee.write_bytes(fixed)
    verify_source(manifest)
    test_files = [p.relative_to(SOURCE).as_posix() for p in sorted(SOURCE.glob('test/*.test.mjs'))]
    log = run('full-suite', ['node', '--test', '--test-reporter=tap', *test_files])
    totals(log, 69, 69, 0)
    syntax = []
    for row in manifest:
        if row['path'].endswith(('.js', '.mjs')):
            subprocess.run(['node', '--check', row['path']], cwd=SOURCE, env=ENV, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            syntax.append(row['path'])
    save('syntax.json', syntax)
    run('package-build', ['node', 'cli/pack.mjs'])
    built = SOURCE / 'dist/glass-extension-0.3.3.zip'
    package_manifest = []
    expected = {row['path']: row for row in manifest if row['path'].startswith(('src/', 'icons/'))
                or row['path'] in ('manifest.json', 'README.md', 'PRIVACY.md', 'LICENSE')}
    with zipfile.ZipFile(built) as archive:
        assert archive.testzip() is None
        names = [x.filename for x in archive.infolist() if not x.is_dir()]
        assert len(names) == len(set(names)) == len(expected)
        assert set(names) == set(expected)
        for name in names:
            data = archive.read(name)
            assert digest(data) == expected[name]['sha256'], name
            package_manifest.append({'path': name, 'sha256': digest(data)})
    shutil.copyfile(built, OUT / built.name)
    verify_source(manifest)
    save('package-manifest.json', package_manifest)
    save('result.json', {'status': 'PASS', 'application': APP, 'tree': TREE, 'parents': [OLD, MAIN],
                        'tests': 69, 'pass': 69, 'fail': 0, 'skip': 0, 'controls': controls,
                        'network': 'separate OS network namespace; ENETUNREACH confirmed before tests',
                        'environment': json.loads(environment), 'syntax_files': len(syntax),
                        'exact_package_files': len(package_manifest), 'package_sha256': digest(built.read_bytes()),
                        'source_before_after': 'all pinned git blobs match', 'dependency_installs': 0,
                        'limits': 'captured fixture and synthetic replay; actual renderer/CLI formatter in Node, not a new browser or live scan'})


if __name__ == '__main__':
    if sys.argv[1:] == ['prepare']:
        prepare()
    elif sys.argv[1:] == ['verify']:
        verify()
    else:
        raise SystemExit('usage: pr17-integration-r1031.py prepare|verify')
