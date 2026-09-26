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


SCHEMA = """
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
CREATE TABLE IF NOT EXISTS trajectory_snapshots (
    id serial PRIMARY KEY,
    reference_id integer,
    reference_sheet text NOT NULL DEFAULT '',
    point_count integer NOT NULL,
    points jsonb NOT NULL,
    reference_point jsonb,
    deltas jsonb,
    issued_by text NOT NULL,
    issued_at timestamptz NOT NULL
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
    limit: int = 20
    reference_id: int | None = None


TRAJECTORY_LIMITS = (10, 20, 50)


def trajectory_rows(conn, limit: int):
    """服务端取最近若干条已出结论的青品偏差点，按时间正序。"""
    return conn.execute(
        """SELECT id, sheet, cyan_mm, magenta_mm, verdict, reason, created_by, created_at
           FROM jobs
           WHERE status = 'done'
           ORDER BY id DESC
           LIMIT %s""",
        (limit,),
    ).fetchall()[::-1]


def serialize_point(row: dict) -> dict:
    return {
        "job_id": row["id"],
        "sheet": row["sheet"],
        "cyan_mm": row["cyan_mm"],
        "magenta_mm": row["magenta_mm"],
        "verdict": row["verdict"],
        "reason": row["reason"],
        "created_by": row["created_by"],
        "created_at": row["created_at"].isoformat(),
    }


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


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(SCHEMA)
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


@app.get("/api/trajectory")
def trajectory(limit: int = 20, _user: dict = Depends(current_user)):
    """最近若干已出结论的青品偏差点，服务端按时间返回，前端不得用总表自拼。"""
    if limit not in TRAJECTORY_LIMITS:
        raise HTTPException(status_code=400, detail="条数只能是 10、20、50")
    with connect() as conn:
        points = [serialize_point(r) for r in trajectory_rows(conn, limit)]
    return {"points": points, "count": len(points)}


@app.get("/api/trajectory/delta")
def trajectory_delta(
    reference_id: int,
    limit: int = 20,
    _user: dict = Depends(current_user),
):
    """对照点与轨迹点的青品差额一律由服务端算好返回。"""
    if limit not in TRAJECTORY_LIMITS:
        raise HTTPException(status_code=400, detail="条数只能是 10、20、50")
    with connect() as conn:
        rows = trajectory_rows(conn, limit)
        reference = next((r for r in rows if r["id"] == reference_id), None)
        if reference is None:
            ref_row = conn.execute(
                """SELECT id, sheet, cyan_mm, magenta_mm, verdict, reason, created_by, created_at
                   FROM jobs WHERE id = %s AND status = 'done'""",
                (reference_id,),
            ).fetchone()
            if ref_row is None:
                raise HTTPException(status_code=404, detail="对照点不存在或尚未出结论")
        else:
            ref_row = reference
        deltas = [
            {
                "job_id": r["id"],
                "sheet": r["sheet"],
                "cyan_delta_mm": round(r["cyan_mm"] - ref_row["cyan_mm"], 4),
                "magenta_delta_mm": round(r["magenta_mm"] - ref_row["magenta_mm"], 4),
            }
            for r in rows
        ]
    return {"reference_point": serialize_point(ref_row), "deltas": deltas}


@app.post("/api/trajectory/snapshots", status_code=201)
def issue_snapshot(body: SnapshotIn, user: dict = Depends(current_user)):
    """签发瞬间冻结当时点集与对照差额；之后新结论只影响在线轨迹，不回写快照。"""
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可签发轨迹快照")
    if body.limit not in TRAJECTORY_LIMITS:
        raise HTTPException(status_code=400, detail="条数只能是 10、20、50")
    with connect() as conn:
        rows = trajectory_rows(conn, body.limit)
        points = [serialize_point(r) for r in rows]
        reference_point = None
        deltas = None
        reference_sheet = ""
        if body.reference_id is not None:
            ref_row = next((r for r in rows if r["id"] == body.reference_id), None)
            if ref_row is None:
                raise HTTPException(status_code=404, detail="对照点不在本次冻结点集中")
            reference_point = serialize_point(ref_row)
            reference_sheet = ref_row["sheet"]
            deltas = [
                {
                    "job_id": r["id"],
                    "sheet": r["sheet"],
                    "cyan_delta_mm": round(r["cyan_mm"] - ref_row["cyan_mm"], 4),
                    "magenta_delta_mm": round(r["magenta_mm"] - ref_row["magenta_mm"], 4),
                }
                for r in rows
            ]
        row = conn.execute(
            """INSERT INTO trajectory_snapshots
                 (reference_id, reference_sheet, point_count, points, reference_point, deltas, issued_by, issued_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, issued_at""",
            (
                body.reference_id,
                reference_sheet,
                len(points),
                Jsonb(points),
                Jsonb(reference_point),
                Jsonb(deltas),
                user["username"],
                datetime.now(timezone.utc),
            ),
        ).fetchone()
        conn.commit()
    return {
        "id": row["id"],
        "reference_id": body.reference_id,
        "reference_sheet": reference_sheet,
        "point_count": len(points),
        "points": points,
        "reference_point": reference_point,
        "deltas": deltas,
        "issued_by": user["username"],
        "issued_at": row["issued_at"].isoformat(),
    }


@app.get("/api/trajectory/snapshots")
def list_snapshots(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, reference_id, reference_sheet, point_count, issued_by, issued_at
               FROM trajectory_snapshots ORDER BY id DESC"""
        ).fetchall()
    return [
        {
            "id": r["id"],
            "reference_id": r["reference_id"],
            "reference_sheet": r["reference_sheet"],
            "point_count": r["point_count"],
            "issued_by": r["issued_by"],
            "issued_at": r["issued_at"].isoformat(),
        }
        for r in rows
    ]


@app.get("/api/trajectory/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: int, _user: dict = Depends(current_user)):
    with connect() as conn:
        r = conn.execute(
            "SELECT * FROM trajectory_snapshots WHERE id = %s",
            (snapshot_id,),
        ).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    return {
        "id": r["id"],
        "reference_id": r["reference_id"],
        "reference_sheet": r["reference_sheet"],
        "point_count": r["point_count"],
        "points": r["points"],
        "reference_point": r["reference_point"],
        "deltas": r["deltas"],
        "issued_by": r["issued_by"],
        "issued_at": r["issued_at"].isoformat(),
    }
