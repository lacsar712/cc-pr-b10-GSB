import { useEffect, useState } from 'react'

const LIMITS = [10, 20, 50]

function fmtTime(iso) {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

function VerdictTag({ verdict }) {
  if (verdict === '套准') return <span style={{ color: '#0a7a23', fontWeight: 700 }}>套准</span>
  if (verdict === '套不准') return <span style={{ color: '#c62828', fontWeight: 700 }}>套不准</span>
  return <span>—</span>
}

export default function TrajectoryPage({ token, role }) {
  const [limit, setLimit] = useState(10)
  const [points, setPoints] = useState([])
  const [referenceId, setReferenceId] = useState('')
  const [delta, setDelta] = useState(null)
  const [snapshots, setSnapshots] = useState([])
  const [openSnapshot, setOpenSnapshot] = useState(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  async function api(path, options = {}) {
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
  }

  async function loadTrack() {
    const data = await api(`/api/trajectory?limit=${limit}`)
    setPoints(data.points)
    if (referenceId) {
      const d = await api(`/api/trajectory/delta?reference_id=${referenceId}&limit=${limit}`)
      setDelta(d)
    }
  }

  async function loadSnapshots() {
    setSnapshots(await api('/api/trajectory/snapshots'))
  }

  // 在线轨迹轮询：打开快照详情时不刷详情，冻结视图不受新结论影响
  useEffect(() => {
    if (openSnapshot) return
    loadTrack().catch((e) => setError(e.message))
    const timer = setInterval(() => loadTrack().catch(() => {}), 2000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, limit, referenceId, openSnapshot])

  useEffect(() => {
    loadSnapshots().catch((e) => setError(e.message))
    const timer = setInterval(() => {
      if (!openSnapshot) loadSnapshots().catch(() => {})
    }, 2000)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, openSnapshot])

  async function chooseReference(value) {
    setReferenceId(value)
    setMessage('')
    setError('')
    if (!value) {
      setDelta(null)
      return
    }
    try {
      setDelta(await api(`/api/trajectory/delta?reference_id=${value}&limit=${limit}`))
    } catch (e) {
      setError(e.message)
    }
  }

  async function issue() {
    setError('')
    setMessage('')
    try {
      const payload = { limit }
      if (referenceId) payload.reference_id = Number(referenceId)
      const snap = await api('/api/trajectory/snapshots', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setMessage(`已签发快照 #${snap.id}，点集冻结为签发时的 ${snap.point_count} 个点`)
      await loadSnapshots()
    } catch (e) {
      setError(e.message)
    }
  }

  async function openSnap(id) {
    setError('')
    try {
      setOpenSnapshot(await api(`/api/trajectory/snapshots/${id}`))
    } catch (e) {
      setError(e.message)
    }
  }

  const frozen = openSnapshot
  const shownPoints = frozen ? openSnapshot.points : points
  const shownDelta = frozen ? openSnapshot.deltas : delta?.deltas
  const shownReference = frozen ? openSnapshot.reference_point : delta?.reference_point

  return (
    <section>
      <h2>色差轨迹</h2>
      <p>最近已出结论的青品偏差点，轨迹与差额均由服务端返回；签发的快照永久冻结签发当时点集。</p>

      <p>
        条数：
        <select value={limit} onChange={(e) => { setLimit(Number(e.target.value)); setDelta(null); setReferenceId('') }} disabled={frozen}>
          {LIMITS.map((n) => <option key={n} value={n}>最近 {n} 条</option>)}
        </select>{' '}
        对照点：
        <select value={referenceId} onChange={(e) => chooseReference(e.target.value)} disabled={frozen}>
          <option value="">不选对照</option>
          {points.map((p) => (
            <option key={p.job_id} value={p.job_id}>
              #{p.job_id} {p.sheet}（{p.verdict}）
            </option>
          ))}
        </select>{' '}
        {role === 'writer' && !frozen && <button onClick={issue}>签发轨迹快照</button>}
      </p>
      {message && <p style={{ color: '#0a7a23' }}>{message}</p>}
      {error && <p style={{ color: '#c62828' }}>{error}</p>}

      <h3>{frozen ? `快照 #${openSnapshot.id}（已冻结点集）` : '在线轨迹'}</h3>
      {frozen && (
        <p>
          由 {openSnapshot.issued_by} 于 {fmtTime(openSnapshot.issued_at)} 签发，共 {openSnapshot.point_count} 点
          {' '}<button onClick={() => setOpenSnapshot(null)}>返回在线轨迹</button>
        </p>
      )}
      <table border={1} cellPadding={4}>
        <thead>
          <tr><th>时间</th><th>#</th><th>印张</th><th>青偏差(mm)</th><th>品偏差(mm)</th><th>结论</th></tr>
        </thead>
        <tbody>
          {shownPoints.map((p) => (
            <tr key={p.job_id}>
              <td>{fmtTime(p.created_at)}</td>
              <td>{p.job_id}</td>
              <td>{p.sheet}</td>
              <td>{p.cyan_mm}</td>
              <td>{p.magenta_mm}</td>
              <td><VerdictTag verdict={p.verdict} /></td>
            </tr>
          ))}
          {shownPoints.length === 0 && (
            <tr><td colSpan={6}>暂无已出结论的点</td></tr>
          )}
        </tbody>
      </table>

      <h3>对照差额</h3>
      {!shownReference ? (
        <p>{frozen ? '该快照签发时未选对照点。' : '选择一条已出结论作为对照点，这里显示服务端算好的青品差额。'}</p>
      ) : (
        <>
          <p>
            对照点：#{shownReference.job_id} {shownReference.sheet}，
            青 {shownReference.cyan_mm} mm，品 {shownReference.magenta_mm} mm，
            结论 <VerdictTag verdict={shownReference.verdict} />
          </p>
          <table border={1} cellPadding={4}>
            <thead>
              <tr><th>#</th><th>印张</th><th>青差额(mm)</th><th>品差额(mm)</th></tr>
            </thead>
            <tbody>
              {shownDelta.map((d) => (
                <tr key={d.job_id}>
                  <td>{d.job_id}</td>
                  <td>{d.sheet}</td>
                  <td>{d.cyan_delta_mm > 0 ? `+${d.cyan_delta_mm}` : d.cyan_delta_mm}</td>
                  <td>{d.magenta_delta_mm > 0 ? `+${d.magenta_delta_mm}` : d.magenta_delta_mm}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>已签发快照</h3>
      <table border={1} cellPadding={4}>
        <thead>
          <tr><th>#</th><th>签发时间</th><th>签发人</th><th>冻结点数</th><th>对照点</th><th></th></tr>
        </thead>
        <tbody>
          {snapshots.map((s) => (
            <tr key={s.id}>
              <td>{s.id}</td>
              <td>{fmtTime(s.issued_at)}</td>
              <td>{s.issued_by}</td>
              <td>{s.point_count}</td>
              <td>{s.reference_id ? `#${s.reference_id} ${s.reference_sheet}` : '无'}</td>
              <td><button onClick={() => openSnap(s.id)}>查看冻结版</button></td>
            </tr>
          ))}
          {snapshots.length === 0 && (
            <tr><td colSpan={6}>尚未签发任何快照</td></tr>
          )}
        </tbody>
      </table>
    </section>
  )
}
