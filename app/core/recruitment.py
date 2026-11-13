"""One recruitment stage per application, plus ordered rounds owned by a job."""
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.core.extraction import norm

DEFAULT_ROUNDS = ("一面", "二面", "终面")
FIXED_LABELS = {"review": "待审核", "progress": "推进中", "rejected": "淘汰", "hired": "录取"}
StageCode = Literal["review", "progress", "interview", "rejected", "hired"]


class RoundInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=40)


def rounds_for(connection, job_id):
    return [dict(row) for row in connection.execute(text("""
        SELECT id,name,position FROM job_interview_round WHERE job_id=:job ORDER BY position
    """), {"job": job_id}).mappings()]


def stage(code, round_id=None, round_name=None):
    return {"code": code, "label": round_name if code == "interview" else FIXED_LABELS[code], "round_id": round_id}


def save_rounds(connection, job_id, submitted):
    old = {r["id"] for r in rounds_for(connection, job_id)}
    used = set()
    names = set(FIXED_LABELS.values())
    normalized = []
    for item in submitted:
        name = " ".join(item.name.split())
        key = norm(name)
        if not key or key in names:
            raise HTTPException(422, "面试轮次名称不能为空、重复或与固定状态同名")
        if key == "终面" and len(normalized) != len(submitted) - 1:
            raise HTTPException(422, "终面必须排在最后一轮")
        names.add(key)
        if item.id is not None:
            if item.id not in old or item.id in used:
                raise HTTPException(422, "面试轮次不属于当前岗位或重复")
            used.add(item.id)
        normalized.append((item.id, name))
    deleted = sorted(old - used)
    if deleted:
        args = {"ids": deleted}
        if connection.execute(text("""
            SELECT 1 FROM application WHERE interview_round_id=ANY(CAST(:ids AS bigint[])) LIMIT 1
        """), args).first():
            raise HTTPException(409, "有申请正在该轮次，请先调整这些申请的状态")
        connection.execute(text("DELETE FROM job_interview_round WHERE id=ANY(CAST(:ids AS bigint[]))"), args)
    for i, (identifier, name) in enumerate(normalized, 1):
        args = {"job": job_id, "id": identifier, "name": name, "pos": i}
        if identifier is None:
            connection.execute(text("""
                INSERT INTO job_interview_round(job_id,name,position) VALUES(:job,:name,:pos)
            """), args)
        else:
            connection.execute(text("""
                UPDATE job_interview_round SET name=:name,position=:pos WHERE id=:id AND job_id=:job
            """), args)


def expect_revision(row, expected):
    if row["workflow_version"] != expected:
        raise HTTPException(409, "招聘状态或岗位已变化，请刷新后重试")
