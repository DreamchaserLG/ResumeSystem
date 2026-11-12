"""岗位接口：M2 权限链路的业务验证点。

部门 HR 只见本部门岗位（应用层显式条件 + RLS 兜底）；超管只见元数据，无简历正文（02 §1）。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.auth import AuthContext, require_auth
from app.core.db import rls_session

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(auth: AuthContext = Depends(require_auth)):
    if auth.role == "super_admin":
        # 超管：全公司岗位元数据（管理视图），不含候选人资料
        with rls_session(user_id=auth.user_id) as s:
            rows = s.execute(
                text(
                    "SELECT j.id, j.title, j.jd, j.status, j.department_id, d.name AS department_name"
                    " FROM job j JOIN department d ON d.id = j.department_id ORDER BY j.id"
                )
            ).mappings().all()
    else:
        # 部门 HR：应用层显式注入授权部门集合（02 §2.1），RLS 二次兜底
        with rls_session(departments=auth.departments, user_id=auth.user_id) as s:
            rows = s.execute(
                text(
                    "SELECT j.id, j.title, j.jd, j.status, j.department_id, d.name AS department_name"
                    " FROM job j JOIN department d ON d.id = j.department_id"
                    " WHERE j.department_id = ANY(:deps) ORDER BY j.id"
                ),
                {"deps": auth.departments},
            ).mappings().all()
    return {"items": [dict(r) for r in rows], "scope": auth.role}
