import { useEffect, useState } from 'react'

const LIMITS = [5, 10, 20, 50]

function fmtTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

function fmtDiff(n) {
  const v = Number(n)
  return `${v >= 0 ? '+' : ''}${v.toFixed(3)}`
}

function PointTable({ points, diffs }) {
  const diffById = new Map((diffs || []).map((d) => [d.id, d]))
  const hasDiff = (diffs || []).length > 0
  return (
    <table border="1" cellPadding="4">
      <thead>
        <tr>
          <th>时间</th><th>印张</th><th>青</th><th>品</th><th>结论</th>
          {hasDiff && <><th>青差额</th><th>品差额</th></>}
        </tr>
      </thead>
      <tbody>
        {points.map((p) => {
          const d = diffById.get(p.id)
          return (
            <tr key={p.id}>
              <td>{fmtTime(p.created_at)}</td>
              <td>{p.sheet}</td>
              <td>{p.cyan_mm}</td>
              <td>{p.magenta_mm}</td>
              <td>{p.verdict}</td>
              {hasDiff && (
                <>
                  <td>{d ? fmtDiff(d.cyan_diff) : ''}</td>
                  <td>{d ? fmtDiff(d.magenta_diff) : ''}</td>
                </>
              )}
            </tr>
          )
        })}
        {points.length === 0 && (
          <tr><td colSpan={hasDiff ? 7 : 5}>暂无已出结论的印张</td></tr>
        )}
      </tbody>
    </table>
  )
}

export default function Trajectory({ api, role }) {
  const [limit, setLimit] = useState(10)
  const [baselineId, setBaselineId] = useState('')
  const [traj, setTraj] = useState({ points: [], baseline: null, diffs: [] })
  const [snapshots, setSnapshots] = useState([])
  const [openSnap, setOpenSnap] = useState(null)
  const [label, setLabel] = useState('')
  const [error, setError] = useState('')

  // 在线轨迹：数据与差额都由服务端返回，页面不拿总表自己拼
  useEffect(() => {
    let dead = false
    async function load() {
      try {
        const q = baselineId ? `&baseline_id=${baselineId}` : ''
        const data = await api(`/api/trajectory?limit=${limit}${q}`)
        if (!dead) {
          setTraj(data)
          setError('')
        }
      } catch (err) {
        if (!dead) setError(err.message)
      }
    }
    load()
    const timer = setInterval(load, 1000)
    return () => {
      dead = true
      clearInterval(timer)
    }
  }, [api, limit, baselineId])

  useEffect(() => {
    let dead = false
    async function load() {
      try {
        const data = await api('/api/trajectory/snapshots')
        if (!dead) setSnapshots(data)
      } catch {
        /* 快照列表刷新失败时保留现状 */
      }
    }
    load()
    const timer = setInterval(load, 1000)
    return () => {
      dead = true
      clearInterval(timer)
    }
  }, [api])

  async function sign() {
    setError('')
    try {
      await api('/api/trajectory/snapshots', {
        method: 'POST',
        body: JSON.stringify({
          limit,
          baseline_id: baselineId ? Number(baselineId) : null,
          label,
        }),
      })
      setLabel('')
      setSnapshots(await api('/api/trajectory/snapshots'))
    } catch (err) {
      setError(err.message)
    }
  }

  async function openSnapshot(id) {
    setOpenSnap(await api(`/api/trajectory/snapshots/${id}`))
  }

  const baselineOptions = [...traj.points]
  if (traj.baseline && !baselineOptions.some((p) => p.id === traj.baseline.id)) {
    baselineOptions.push(traj.baseline)
  }

  return (
    <section>
      <h2>色差轨迹</h2>
      <p>
        条数选择：
        <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          {LIMITS.map((n) => (
            <option key={n} value={n}>最近 {n} 条</option>
          ))}
        </select>
        {' '}对照选择：
        <select value={baselineId} onChange={(e) => setBaselineId(e.target.value)}>
          <option value="">无对照点</option>
          {baselineOptions.map((p) => (
            <option key={p.id} value={p.id}>
              #{p.id} {p.sheet}（青 {p.cyan_mm} / 品 {p.magenta_mm}）
            </option>
          ))}
        </select>
        {role === 'writer' && (
          <>
            {' '}快照备注：
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="可留空" />
            <button onClick={sign} disabled={traj.points.length === 0}>签发轨迹快照</button>
          </>
        )}
      </p>
      {error && <p style={{ color: 'red' }}>{error}</p>}

      <h3>轨迹表（最近 {traj.points.length} 条已出结论）</h3>
      <PointTable points={traj.points} diffs={[]} />

      <h3>差额区</h3>
      {traj.baseline ? (
        <div>
          <p>
            对照点：#{traj.baseline.id} {traj.baseline.sheet}
            （青 {traj.baseline.cyan_mm} / 品 {traj.baseline.magenta_mm}，{traj.baseline.verdict}），
            差额 = 轨迹点 − 对照点
          </p>
          <PointTable points={traj.points} diffs={traj.diffs} />
        </div>
      ) : (
        <p>选择对照点后，由服务端返回各轨迹点与对照点的青品差额。</p>
      )}

      <h3>已签发快照（{snapshots.length}）</h3>
      <table border="1" cellPadding="4">
        <thead>
          <tr><th>编号</th><th>备注</th><th>签发人</th><th>签发时间</th><th>点数</th><th>含对照</th><th></th></tr>
        </thead>
        <tbody>
          {snapshots.map((s) => (
            <tr key={s.id}>
              <td>#{s.id}</td>
              <td>{s.label || '—'}</td>
              <td>{s.created_by}</td>
              <td>{fmtTime(s.created_at)}</td>
              <td>{s.point_count}</td>
              <td>{s.has_baseline ? '是' : '否'}</td>
              <td><button onClick={() => openSnapshot(s.id)}>查看</button></td>
            </tr>
          ))}
          {snapshots.length === 0 && (
            <tr><td colSpan="7">尚无已签发快照</td></tr>
          )}
        </tbody>
      </table>

      {openSnap && (
        <div style={{ border: '2px solid #333', padding: '8px', marginTop: '8px' }}>
          <h3>
            快照 #{openSnap.id}{openSnap.label ? `：${openSnap.label}` : ''}
            {' '}（{openSnap.created_by} 签发于 {fmtTime(openSnap.created_at)}）
            <button onClick={() => setOpenSnap(null)} style={{ marginLeft: '12px' }}>关闭</button>
          </h3>
          <p>签发时冻结的点集，之后新入库的判定不影响此快照。</p>
          {openSnap.payload.baseline && (
            <p>
              对照点：#{openSnap.payload.baseline.id} {openSnap.payload.baseline.sheet}
              （青 {openSnap.payload.baseline.cyan_mm} / 品 {openSnap.payload.baseline.magenta_mm}），
              差额 = 轨迹点 − 对照点
            </p>
          )}
          <PointTable points={openSnap.payload.points} diffs={openSnap.payload.diffs} />
        </div>
      )}
    </section>
  )
}
