"""Publication guard acceptance checks; generated values are test-only."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.check_public_files import candidates, forbidden_file, scan, scan_index


class PublicFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return name

    def test_rejects_private_files_even_when_tracked(self):
        for name in ['.env', '.env.production', 'work/settings.json', 'outputs/release.zip',
                     'db/backup.dump', 'frontend/tsconfig.tsbuildinfo', '.ssh/id_ed25519',
                     'keys/signing.pfx', 'data/private/report.json', 'session.har',
                     'backup/data.tar.gz', 'backup/data.sql.gz', 'queue/dump.rdb']:
            with self.subTest(name=name):
                self.assertTrue(forbidden_file(name))
        self.assertFalse(forbidden_file('.env.example'))
        self.assertFalse(forbidden_file('backend/requirements.lock'))

    def test_detects_windows_and_unix_profile_paths(self):
        for prefix in ['C:' + '/Users/', '/mnt/c/' + 'Users/', '/' + 'home/']:
            name = self.write('notes.md', prefix + 'private-account/project')
            self.assertEqual(scan(self.root, [name])[0].category, 'personal-profile-path')

    def test_allows_portable_documentation_placeholders(self):
        name = self.write('notes.md', 'C:/Users/<username>/project\n/' + 'home/user/project\n/var/tmp/app')
        self.assertEqual(scan(self.root, [name]), [])

    def test_detects_private_key_and_provider_key_without_disclosing_value(self):
        name = self.write('source.py', '-----BEGIN ' + 'PRIVATE KEY-----\n' + 'ghp_' + 'A1' * 20)
        findings = scan(self.root, [name])
        self.assertEqual({row.category for row in findings}, {'private-key-block', 'provider-key-shape'})
        self.assertNotIn('A1' * 20, repr(findings))

    def test_known_secret_match_includes_binary_and_never_returns_secret(self):
        secret = 'TEST ONLY dynamic credential'
        (self.root / 'asset.bin').write_bytes(b'\0' + secret.encode())
        findings = scan(self.root, ['asset.bin'], (secret,))
        self.assertEqual(findings[0].category, 'known-local-secret')
        self.assertNotIn(secret, repr(findings))

    def test_rejects_traversal_without_reading_outside_root(self):
        self.assertEqual(scan(self.root, ['../outside.txt'])[0].category, 'private-or-generated-file')

    def test_safe_sources_and_deleted_paths_pass(self):
        name = self.write('app.py', 'ADMIN_TOKEN = ""\nAPI_URL = "http://127.0.0.1:8000"')
        self.assertEqual(scan(self.root, [name, 'deleted.py']), [])

    def test_git_candidates_include_tracked_ignored_file(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        self.write('.gitignore', '.env\nwork/\n')
        self.write('.env', 'TEST_ONLY=yes')
        self.write('work/private.json', '{}')
        self.write('app.py', '# public')
        subprocess.run(['git', 'add', '--force', '.env'], cwd=self.root, check=True, capture_output=True)
        paths = candidates(self.root)
        self.assertIn('.env', paths)
        self.assertIn('app.py', paths)
        self.assertNotIn('work/private.json', paths)
        self.assertEqual(scan(self.root, paths)[0].category, 'private-or-generated-file')

    def test_staged_secret_is_detected_after_working_tree_is_cleaned(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        name = self.write('app.py', 'ghp_' + 'B7' * 20)
        subprocess.run(['git', 'add', name], cwd=self.root, check=True, capture_output=True)
        self.write(name, '# clean working tree')
        self.assertEqual(scan(self.root, [name]), [])
        self.assertEqual(scan_index(self.root)[0].category, 'staged-provider-key-shape')
        (self.root / name).unlink()
        self.assertEqual(scan_index(self.root)[0].category, 'staged-provider-key-shape')

    def test_staged_binary_and_ignored_file_are_checked(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        self.write('.gitignore', '.env\n')
        self.write('.env', 'TEST_ONLY=yes')
        secret = 'TEST ONLY staged credential'
        (self.root / 'asset.bin').write_bytes(b'\0' + secret.encode())
        subprocess.run(['git', 'add', '--force', '.env', 'asset.bin'], cwd=self.root, check=True, capture_output=True)
        findings = scan_index(self.root, (secret,))
        self.assertEqual({row.category for row in findings},
                         {'staged-private-or-generated-file', 'staged-known-local-secret'})
        self.assertNotIn(secret, repr(findings))


if __name__ == '__main__':
    unittest.main()
