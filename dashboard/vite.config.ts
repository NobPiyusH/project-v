import react from '@vitejs/plugin-react'
import { execFile, spawn } from 'node:child_process'
import path from 'node:path'
import { defineConfig, type Plugin } from 'vite'

const ROOT = path.resolve(import.meta.dirname, '..')
const SCRIPTS = path.join(ROOT, 'scripts')

/**
 * Start / restart button for the studio's backend (control plane + media worker + Docker services), served by this
 * dev server because it is the one piece that stays up when the backend is down. Localhost only: requests must come
 * from this dashboard (Origin check + a custom header that other sites cannot send without a CORS preflight).
 */
function studioControl(): Plugin {
  let busy: string | null = null
  let lastOutput = ''
  let worker = { at: 0, up: false }

  const run = (label: string, scripts: string[][]) => {
    busy = label
    lastOutput = ''
    const next = (i: number) => {
      if (i >= scripts.length) { busy = null; return }
      const p = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ...scripts[i]],
        { cwd: ROOT, windowsHide: true })
      p.stdout.on('data', (d) => { lastOutput = (lastOutput + d).slice(-4000) })
      p.stderr.on('data', (d) => { lastOutput = (lastOutput + d).slice(-4000) })
      // 'exit', not 'close': the servers start.ps1 launches inherit the pipes and keep them open.
      p.on('exit', () => next(i + 1))
      p.on('error', (e) => { lastOutput += String(e); busy = null })
    }
    next(0)
  }

  const controlPlaneUp = async () => {
    try {
      const r = await fetch('http://127.0.0.1:8080/api/system', { signal: AbortSignal.timeout(2000) })
      return r.ok
    } catch {
      return false
    }
  }

  // Checking the worker process costs ~1 s of PowerShell, so it is cached for 15 s.
  const workerUp = () => new Promise<boolean>((resolve) => {
    if (Date.now() - worker.at < 15000) { resolve(worker.up); return }
    execFile('powershell.exe', ['-NoProfile', '-Command',
      "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*workers.main*' }).Count"],
    { windowsHide: true, timeout: 10000 }, (err, out) => {
      worker = { at: Date.now(), up: !err && Number(String(out).trim()) > 0 }
      resolve(worker.up)
    })
  })

  return {
    name: 'studio-control',
    configureServer(server) {
      server.middlewares.use('/studio', async (req, res) => {
        const send = (code: number, body: unknown) => {
          res.statusCode = code
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(body))
        }
        if (req.method === 'GET' && req.url === '/status') {
          const [controlPlane, mediaWorker] = await Promise.all([controlPlaneUp(), workerUp()])
          send(200, { controlPlane, worker: mediaWorker, busy, lastOutput })
          return
        }
        const origin = req.headers.origin ?? ''
        if (req.method !== 'POST' || req.headers['x-studio'] !== '1'
          || !/^http:\/\/(127\.0\.0\.1|localhost):5173$/.test(origin)) {
          send(403, { error: 'Only the studio dashboard can do this.' })
          return
        }
        if (busy) { send(409, { error: `Already ${busy}.` }); return }
        worker.at = 0
        if (req.url === '/start') {
          run('starting', [[path.join(SCRIPTS, 'start.ps1')]])
        } else if (req.url === '/restart') {
          run('restarting', [[path.join(SCRIPTS, 'stop.ps1'), '-Backend'], [path.join(SCRIPTS, 'start.ps1')]])
        } else {
          send(404, { error: 'Unknown action' })
          return
        }
        send(202, { busy })
      })
    },
  }
}

// The dashboard switch between the Java control plane (default) and the Python one (docs/plans/java-to-python-migration.md, S7).
// Nothing changes unless PROJECTV_PY_AREAS is set: every /api path goes to Java on :8080, exactly as before. With e.g.
//   PROJECTV_PY_AREAS=/api/dream,/api/autopilot   (comma-separated path prefixes)
//   PROJECTV_PY_API_URL=http://127.0.0.1:8081      (default)
// only those areas go to the Python app (started with STUDIO_DREAM_ROUTES=true on its own port); everything else still goes to Java.
// Unset the variable and restart the dev server to switch back.
function apiProxy() {
  const java = process.env.PROJECTV_API_URL || 'http://127.0.0.1:8080'
  const py = process.env.PROJECTV_PY_API_URL || 'http://127.0.0.1:8081'
  const areas = (process.env.PROJECTV_PY_AREAS || '').split(',').map((a) => a.trim()).filter(Boolean)
  const proxy: Record<string, string> = { '/api': java }
  // Vite matches proxy keys in order: the longer, more specific prefixes first.
  const out: Record<string, string> = {}
  for (const a of areas.sort((x, y) => y.length - x.length)) out[a] = py
  return { ...out, ...proxy }
}

// Dev server on localhost only; /api is proxied to the Spring Boot control plane.
export default defineConfig({
  plugins: [react(), studioControl()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // 127.0.0.1, not localhost: the control plane runs with preferIPv4Stack and Node would try ::1 first.
    proxy: apiProxy(),
  },
})
