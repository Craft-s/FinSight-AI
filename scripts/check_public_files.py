"""Check Git publication candidates without printing matched private values.

This small project guard checks files/paths and common credential shapes. It is
not a general secret scanner, OCR tool, or Git history scanner. It checks the
working tree and staged blobs, including tracked files matching .gitignore.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import NamedTuple

PRIVATE_PARTS = {
    '.git', '.venv', 'node_modules', 'work', 'outputs', 'dist', '__pycache__',
    '.pytest_cache', '.ruff_cache', 'playwright-report', 'test-results',
    '.ssh', '.aws', '.azure', '.vscode', '.idea',
}
PRIVATE_SUFFIXES = {
    '.db', '.sqlite', '.sqlite3', '.dump', '.backup', '.bak', '.log', '.har',
    '.pem', '.key', '.p12', '.pfx', '.keystore', '.tsbuildinfo', '.local',
    '.zip', '.7z', '.tar', '.tgz', '.dmp', '.pgdump', '.rdb', '.aof',
}
PRIVATE_NAMES = {'.netrc', '_netrc', '.pypirc', '.npmrc', 'credentials.json'}
PROFILE_PATH = re.compile(
    r'(?:[a-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+'
    r'|/mnt/[a-z]/' + r'Users/|/(?:home|Users)/)'
    r'(?P<account>[^\\/\s\"\'<>`]+)', re.I,
)
PLACEHOLDER_ACCOUNTS = {'user', 'username', 'test', 'testuser', 'example', 'runner'}
PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z]+ )?' + r'PRIVATE KEY-----')
PROVIDER_KEY = re.compile(
    r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}'
    r'|sk-(?:proj-)?[A-Za-z0-9_-]{32,}|AIza[0-9A-Za-z_-]{30,}'
    r'|AKIA[A-Z0-9]{16})\b'
)


class Finding(NamedTuple):
    path: str
    line: int
    category: str


def candidates(root: Path) -> list[str]:
    result = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=root, check=True, capture_output=True,
    )
    return sorted(set(result.stdout.decode('utf-8').split('\0')) - {''})


def forbidden_file(relative: str) -> bool:
    path = PurePosixPath(relative)
    name = path.name.lower()
    return (
        path.is_absolute() or '..' in path.parts
        or any(part.lower() in PRIVATE_PARTS for part in path.parts)
        or path.suffix.lower() in PRIVATE_SUFFIXES
        or name.endswith(('.tar.gz', '.sql.gz'))
        or name in PRIVATE_NAMES
        or (name == '.env' or name.startswith('.env.')) and name != '.env.example'
        or name.startswith(('id_rsa', 'id_ed25519', 'service-account'))
        or relative.startswith(('data/live/', 'data/private/'))
    )


def scan_bytes(relative: str, raw: bytes, known_secrets: tuple[str, ...] = ()) -> list[Finding]:
    findings: list[Finding] = []
    if any(secret and secret.encode('utf-8') in raw for secret in known_secrets):
        findings.append(Finding(relative, 0, 'known-local-secret'))
    if b'\0' in raw[:8192]:
        return findings  # Binary content/metadata needs separate inspection.
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return findings
    for number, line in enumerate(text.splitlines(), 1):
        if any(match['account'].lower() not in PLACEHOLDER_ACCOUNTS
               for match in PROFILE_PATH.finditer(line)):
            findings.append(Finding(relative, number, 'personal-profile-path'))
        if PRIVATE_KEY.search(line):
            findings.append(Finding(relative, number, 'private-key-block'))
        if PROVIDER_KEY.search(line):
            findings.append(Finding(relative, number, 'provider-key-shape'))
    return findings


def scan(root: Path, paths: list[str], known_secrets: tuple[str, ...] = ()) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for relative in paths:
        if forbidden_file(relative):
            findings.append(Finding(relative, 0, 'private-or-generated-file'))
            continue
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            findings.append(Finding(relative, 0, 'symlink-or-path-escape'))
            continue
        if not path.exists():
            continue  # A tracked deletion will not be published.
        if not path.is_file():
            findings.append(Finding(relative, 0, 'non-regular-file'))
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            findings.append(Finding(relative, 0, 'unreadable-file'))
            continue
        findings.extend(scan_bytes(relative, raw, known_secrets))
    return findings


def scan_index(root: Path, known_secrets: tuple[str, ...] = ()) -> list[Finding]:
    """Read actual staged blobs, including staged content changed/deleted on disk."""
    listing = subprocess.run(
        ['git', 'ls-files', '--stage', '-z'], cwd=root, check=True, capture_output=True,
    ).stdout
    records: list[tuple[str, str]] = []
    findings: list[Finding] = []
    for record in listing.split(b'\0'):
        if not record:
            continue
        header, name = record.split(b'\t', 1)
        mode, object_id, stage = header.decode('ascii').split()
        relative = name.decode('utf-8')
        if stage != '0' or mode not in {'100644', '100755'}:
            findings.append(Finding(relative, 0, 'staged-unmerged-or-non-regular-file'))
        elif forbidden_file(relative):
            findings.append(Finding(relative, 0, 'staged-private-or-generated-file'))
        else:
            records.append((relative, object_id))
    if not records:
        return findings
    object_ids = sorted({object_id for _, object_id in records})
    batch = subprocess.run(
        ['git', 'cat-file', '--batch'], cwd=root, check=True, capture_output=True,
        input=('\n'.join(object_ids) + '\n').encode('ascii'),
    ).stdout
    blobs: dict[str, bytes] = {}
    offset = 0
    for expected_id in object_ids:
        end = batch.index(b'\n', offset)
        object_id, kind, size_text = batch[offset:end].decode('ascii').split()
        if object_id != expected_id or kind != 'blob':
            raise ValueError('Unexpected Git object')
        size = int(size_text)
        start = end + 1
        if len(batch) < start + size + 1 or batch[start + size:start + size + 1] != b'\n':
            raise ValueError('Incomplete Git object')
        blobs[object_id] = batch[start:start + size]
        offset = start + size + 1
    for relative, object_id in records:
        findings.extend(Finding(row.path, row.line, 'staged-' + row.category)
                        for row in scan_bytes(relative, blobs[object_id], known_secrets))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        paths = candidates(args.root)
        findings = scan(args.root, paths) + scan_index(args.root)
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Public-file check could not complete; verify Git and file permissions.')
        return 2
    print(f'Public-file check: {len(paths)} Git candidates, {len(findings)} findings.')
    for finding in findings:
        print(f'  {finding.path!r}:{finding.line}: {finding.category}')
    print('Scope: candidate files only; images, history and unknown secrets need separate review.')
    return int(bool(findings))


if __name__ == '__main__':
    raise SystemExit(main())
