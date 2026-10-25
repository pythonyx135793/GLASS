"""Exact-source regression, suite and package checks; no installs or uploads."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

APP = "113b44300b4b951efe5c60c912731ee2f28727e2"
TREE = "e6ad461dd1da1e6d24f8473841778910ea8846b4"
BASE = "c5de94674aa94239140d500f07f59243d86bbf00"
BASE_TREE = "d89c7071183e9d3481dd8e7dd41b909ff12182be"
CHANGED = ["src/lib/market.js", "test/market-history.test.mjs"]
FAILURES = [
    "warm history separates supplied token amounts and reuses pool lookup",
    "warm history separates explicit launch times and their checkpoints",
    "a newly discovered launch time does not reuse the pool-time summary",
    "prior-launch report reflects changed supply and discovered time after warming history",
]


def command(args, cwd, expected=0):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)
    print("COMMAND", json.dumps(args), "EXIT", result.returncode, flush=True)
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", flush=True)
    assert result.returncode == expected, (args, result.returncode, expected)
    return result.stdout


def git(cwd, *args):
    return subprocess.check_output(["git", "-C", str(cwd), *args], timeout=15)


def check_source(cwd, commit, tree):
    assert git(cwd, "rev-parse", "HEAD").decode().strip() == commit
    assert git(cwd, "rev-parse", "HEAD^{tree}").decode().strip() == tree
    assert git(cwd, "status", "--porcelain") == b""
    print("SOURCE", commit, tree, flush=True)


app, baseline = (Path(p).resolve(strict=True) for p in sys.argv[1:])
assert app != baseline
guard = Path(__file__).with_name("offline-guard.mjs").resolve(strict=True)
check_source(app, APP, TREE)
check_source(baseline, BASE, BASE_TREE)
major = int(subprocess.check_output(["node", "-p", "process.versions.node.split('.')[0]"], text=True))
assert major >= 22, "Use a preinstalled supported Node; this proof does not download runtimes"
command(["node", "-p", "JSON.stringify({node:process.version,icu:process.versions.icu,platform:process.platform,locale:Intl.NumberFormat().resolvedOptions().locale})"], app)
package = json.loads((app / "package.json").read_text())
assert not package.get("dependencies") and not package.get("devDependencies")
assert package["scripts"]["test"] == "node --test test/*.test.mjs"
assert package["scripts"]["pack"] == "node cli/pack.mjs"
assert not any(name in package["scripts"] for name in ("pretest", "posttest", "prepack", "postpack"))
for path in CHANGED:
    raw = (app / path).read_bytes()
    assert raw == git(app, "show", f"HEAD:{path}")
    print("SOURCE_SHA256", path, hashlib.sha256(raw).hexdigest(), flush=True)

# Copy only our independent new tests into the disposable original checkout.
# Restore that one added file even when a regression/harness assertion fails.
target = baseline / "test/market-history.test.mjs"
assert not target.exists()
try:
    target.write_bytes((app / "test/market-history.test.mjs").read_bytes())
    output = command(["node", "--import", guard.as_uri(), "--test", "--test-reporter=tap", "test/market-history.test.mjs"], baseline, expected=1)
    assert re.findall(r"^not ok \d+ - (.+)$", output, re.M) == FAILURES
    assert "# pass 4\n" in output and "# fail 4\n" in output
    assert output.count("code: 'ERR_ASSERTION'") == 4
finally:
    if target.exists():
        target.unlink()
check_source(baseline, BASE, BASE_TREE)

tests = sorted(str(p.relative_to(app)) for p in (app / "test").glob("*.test.mjs"))
output = command(["node", "--import", guard.as_uri(), "--test", "--test-reporter=tap", *tests], app)
assert "# tests 60\n" in output and "# pass 60\n" in output and "# fail 0\n" in output
assert "# skipped 0\n" in output and "# cancelled 0\n" in output
for path in CHANGED:
    command(["node", "--check", path], app)
command(["git", "diff", "--check"], app)
check_source(app, APP, TREE)

# Run the unmodified real packer and compare every packaged file with its Git blob.
command(["node", "cli/pack.mjs"], app)
version = json.loads((app / "manifest.json").read_text())["version"]
archive = app / f"dist/glass-extension-{version}.zip"
expected = ["manifest.json", "LICENSE", "README.md", "PRIVACY.md"]
for folder in ("icons", "src"):
    expected.extend(p.relative_to(app).as_posix() for p in (app / folder).rglob("*") if p.is_file())
with zipfile.ZipFile(archive) as bundle:
    entries = [i.filename for i in bundle.infolist() if not i.is_dir()]
    assert len(entries) == len(set(entries))
    assert sorted(entries) == sorted(expected)
    for path in sorted(entries):
        raw = bundle.read(path)
        assert raw == git(app, "show", f"HEAD:{path}"), path
        print("PACKAGE_SHA256", path, hashlib.sha256(raw).hexdigest(), flush=True)
print("PACKAGE", len(expected), hashlib.sha256(archive.read_bytes()).hexdigest(), flush=True)
assert git(app, "diff", "HEAD") == b""
assert git(app, "ls-files", "--others", "--exclude-standard").decode().splitlines() == [archive.relative_to(app).as_posix()]
assert git(app, "rev-parse", "HEAD").decode().strip() == APP
check_source(baseline, BASE, BASE_TREE)
print("PASS: original 4 controls/4 assertion failures; fixed 60/60; syntax; exact packaged Git bytes; original checkout restored.", flush=True)
