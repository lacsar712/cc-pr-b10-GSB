import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54394/printreg")
SECRET = os.environ.get("JWT_SECRET", "print-register-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "printer": {"role": "writer", "password_hash": pwd.hash("print123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
"""

SCHEMA_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS trajectory_snapshots (
    id serial PRIMARY KEY,
    label text NOT NULL DEFAULT '',
    limit_count integer NOT NULL,
    baseline_id integer,
    payload jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
"""


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    cyan_mm: float
    magenta_mm: float


class SnapshotIn(BaseModel):
    limit: int = 10
    baseline_id: int | None = None
    label: str = ""


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可送复核")
    return user


def require_signer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可签发")
    return user


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(SCHEMA_JOBS)
        conn.execute(SCHEMA_SNAPSHOTS)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            conn.execute(
                """INSERT INTO jobs (sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at)
                   VALUES
                   ('封面-01', 0.05, -0.04, 'pending', '', '', 'printer', %s),
                   ('内页-09', 0.40, 0.02, 'pending', '', '', 'printer', %s)""",
                (now, now),
            )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "print-register-review"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/jobs")
def list_jobs(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            "SELECT id, sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by FROM jobs ORDER BY id DESC"
        ).fetchall()


@app.post("/api/jobs", status_code=202)
def enqueue(body: JobIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO jobs (sheet, cyan_mm, magenta_mm, status, created_by, created_at)
               VALUES (%s, %s, %s, 'pending', %s, %s)
               RETURNING id, sheet, status, verdict""",
            (body.sheet.strip(), body.cyan_mm, body.magenta_mm, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
    return row


def clamp_limit(limit: int) -> int:
    return max(1, min(limit, 100))


def trajectory_payload(conn, limit: int, baseline_id: int | None) -> dict:
    """最近 limit 条已出结论的偏差点，可选对照点并算青品差额。轨迹与快照共用此函数。"""
    rows = conn.execute(
        """SELECT id, sheet, cyan_mm, magenta_mm, verdict, created_at
           FROM jobs
           WHERE status = 'done'
           ORDER BY created_at DESC, id DESC
           LIMIT %s""",
        (clamp_limit(limit),),
    ).fetchall()
    points = [
        {
            "id": r["id"],
            "sheet": r["sheet"],
            "cyan_mm": r["cyan_mm"],
            "magenta_mm": r["magenta_mm"],
            "verdict": r["verdict"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    baseline = None
    diffs = []
    if baseline_id is not None:
        row = conn.execute(
            """SELECT id, sheet, cyan_mm, magenta_mm, verdict, created_at
               FROM jobs
               WHERE id = %s AND status = 'done'""",
            (baseline_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="对照点不存在或尚未出结论")
        baseline = {
            "id": row["id"],
            "sheet": row["sheet"],
            "cyan_mm": row["cyan_mm"],
            "magenta_mm": row["magenta_mm"],
            "verdict": row["verdict"],
            "created_at": row["created_at"].isoformat(),
        }
        diffs = [
            {
                "id": p["id"],
                "sheet": p["sheet"],
                "cyan_diff": round(p["cyan_mm"] - baseline["cyan_mm"], 6),
                "magenta_diff": round(p["magenta_mm"] - baseline["magenta_mm"], 6),
            }
            for p in points
        ]
    return {"limit": clamp_limit(limit), "points": points, "baseline": baseline, "diffs": diffs}


@app.get("/api/trajectory")
def get_trajectory(limit: int = 10, baseline_id: int | None = None, _user: dict = Depends(current_user)):
    with connect() as conn:
        return trajectory_payload(conn, limit, baseline_id)


@app.post("/api/trajectory/snapshots", status_code=201)
def sign_snapshot(body: SnapshotIn, user: dict = Depends(require_signer)):
    with connect() as conn:
        payload = trajectory_payload(conn, body.limit, body.baseline_id)
        row = conn.execute(
            """INSERT INTO trajectory_snapshots (label, limit_count, baseline_id, payload, created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, label, limit_count, baseline_id, created_by, created_at""",
            (
                body.label.strip(),
                payload["limit"],
                body.baseline_id,
                Jsonb(payload),
                user["username"],
                datetime.now(timezone.utc),
            ),
        ).fetchone()
        conn.commit()
    return {**row, "point_count": len(payload["points"])}


@app.get("/api/trajectory/snapshots")
def list_snapshots(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, label, limit_count, baseline_id, payload, created_by, created_at
               FROM trajectory_snapshots
               ORDER BY id DESC"""
        ).fetchall()
    return [
        {
            "id": r["id"],
            "label": r["label"],
            "limit_count": r["limit_count"],
            "baseline_id": r["baseline_id"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
            "point_count": len(r["payload"]["points"]),
            "has_baseline": r["payload"]["baseline"] is not None,
        }
        for r in rows
    ]


@app.get("/api/trajectory/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: int, _user: dict = Depends(current_user)):
    with connect() as conn:
        row = conn.execute(
            """SELECT id, label, limit_count, baseline_id, payload, created_by, created_at
               FROM trajectory_snapshots
               WHERE id = %s""",
            (snapshot_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    return row
