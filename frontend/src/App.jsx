import { useCallback, useEffect, useState } from 'react'
import Trajectory from './Trajectory.jsx'

function JobsPage({ api, role }) {
  const [rows, setRows] = useState([])
  const [sheet, setSheet] = useState('插页-02')
  const [cyan, setCyan] = useState('0.08')
  const [magenta, setMagenta] = useState('0.02')
  const [error, setError] = useState('')

  useEffect(() => {
    let dead = false
    async function load() {
      try {
        const data = await api('/api/jobs')
        if (!dead) setRows(data)
      } catch {
        /* 轮询失败时保留现状 */
      }
    }
    load()
    const timer = setInterval(load, 1000)
    return () => {
      dead = true
      clearInterval(timer)
    }
  }, [api])

  async function send() {
    setError('')
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          sheet,
          cyan_mm: Number(cyan),
          magenta_mm: Number(magenta),
        }),
      })
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <h2>复核总表</h2>
      {role === 'writer' && (
        <p>
          <input value={sheet} onChange={(e) => setSheet(e.target.value)} />
          <input value={cyan} onChange={(e) => setCyan(e.target.value)} />
          <input value={magenta} onChange={(e) => setMagenta(e.target.value)} />
          <button onClick={send}>送复核</button>
        </p>
      )}
      {error && <p style={{ color: 'red' }}>{error}</p>}
      <table border="1" cellPadding="4">
        <thead>
          <tr><th>印张</th><th>青</th><th>品</th><th>状态</th><th>结论</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.sheet}</td>
              <td>{row.cyan_mm}</td>
              <td>{row.magenta_mm}</td>
              <td>{row.status}</td>
              <td>{row.verdict || '等待'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [page, setPage] = useState('jobs')
  const [error, setError] = useState('')

  const api = useCallback(
    async (path, options = {}) => {
      const res = await fetch(path, {
        ...options,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || '请求失败')
      return data
    },
    [token],
  )

  async function enter() {
    setError('')
    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      localStorage.setItem('print_token', data.access_token)
      localStorage.setItem('print_role', data.role)
      setToken(data.access_token)
      setRole(data.role)
    } catch (err) {
      setError(err.message)
    }
  }

  function leave() {
    localStorage.clear()
    setToken('')
    setRole('')
  }

  if (!token) {
    return (
      <main>
        <h1>印刷套准复核台</h1>
        <p>提交后接口只入队。另一进程领走偏差并写结论，页面轮询到结论出现。</p>
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button onClick={enter}>登录</button>
        {error && <p style={{ color: 'red' }}>{error}</p>}
        <p>printer / print123456 可送复核；checker / check123456 只看</p>
      </main>
    )
  }

  return (
    <main>
      <nav style={{ display: 'flex', gap: '8px', alignItems: 'center', borderBottom: '1px solid #999', paddingBottom: '8px' }}>
        <strong>印刷套准复核台</strong>
        <button onClick={() => setPage('jobs')} disabled={page === 'jobs'}>复核总表</button>
        <button onClick={() => setPage('trajectory')} disabled={page === 'trajectory'}>色差轨迹</button>
        <span style={{ marginLeft: 'auto' }}>{role === 'writer' ? '印刷员' : '查看员'}</span>
        <button onClick={leave}>退出</button>
      </nav>
      {page === 'jobs' ? <JobsPage api={api} role={role} /> : <Trajectory api={api} role={role} />}
    </main>
  )
}
