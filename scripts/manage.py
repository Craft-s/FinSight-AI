"""Run project commands with the root .env, without displaying credentials."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT / 'backend', check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description='FinSight local management')
    parser.add_argument('command', choices=['migrate', 'serve', 'worker', 'import-snapshots', 'refresh-tickers', 'import-document', 'cn-refresh-directory', 'cn-sync', 'cn-import-archive'])
    parser.add_argument('--manifest', help='Local trusted document manifest, required for import-document')
    parser.add_argument('--symbol', help='A-share symbol, for example 600519.SH')
    parser.add_argument('--host', default='127.0.0.1', help='API bind address; keep loopback unless explicitly needed for a VM preview')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env', override=False)
    if args.command in ('cn-refresh-directory', 'cn-sync', 'cn-import-archive'):
        command = ['-m', 'app.cli', args.command]
        if args.command == 'cn-sync':
            if not args.symbol:
                parser.error('--symbol is required for cn-sync')
            command += ['--symbol', args.symbol]
        if args.command == 'cn-import-archive':
            if not args.manifest:
                parser.error('--manifest is required for cn-import-archive')
            command += ['--manifest', str(Path(args.manifest).resolve())]
        run(*command)
    elif args.command == 'import-document':
        if not args.manifest:
            parser.error('--manifest is required for import-document')
        run('-m', 'app.cli', 'import-document', '--manifest', str(Path(args.manifest).resolve()))
    elif args.command == 'migrate':
        run('-m', 'alembic', 'upgrade', 'head')
    elif args.command == 'serve':
        run('-m', 'uvicorn', 'app.main:app', '--host', args.host, '--port', '8000')
    elif args.command == 'worker':
        run('-m', 'app.workers.run')
    elif args.command == 'refresh-tickers':
        run('-m', 'app.cli', 'refresh-tickers')
    else:
        if os.getenv('DATA_MODE') != 'sec_snapshot':
            parser.error('Import requires DATA_MODE=sec_snapshot and a dedicated snapshot database.')
        folders = sorted((ROOT / 'data' / 'snapshots').glob('*/manifest.json'))
        if not folders:
            parser.error('No archived snapshots found. Follow data/README.md first.')
        for manifest in folders:
            run('-m', 'app.cli', 'import-snapshot', '--directory', str(manifest.parent))


if __name__ == '__main__':
    main()
