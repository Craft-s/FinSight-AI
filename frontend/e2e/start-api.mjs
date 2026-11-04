/* global console, process */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const localPython = resolve('../.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const python = process.env.FINSIGHT_E2E_PYTHON || (existsSync(localPython) ? localPython : 'python')
const api = spawn(python, ['-u', 'e2e/serve_api.py'], { stdio: 'inherit', shell: false })
api.on('error', () => { console.error('E2E Python could not start. Set FINSIGHT_E2E_PYTHON to an installed backend Python environment.'); process.exit(1) })
api.on('exit', code => process.exit(code ?? 1))
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => api.kill(signal))
