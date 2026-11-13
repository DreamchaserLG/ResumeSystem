"""Recruitment overview: SQL aggregates scoped to the authenticated user's role."""
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.core.auth import AuthContext, require_auth
from app.core.db import rls_session

router = APIRouter(prefix="/statistics", tags=["statistics"])


@router.get("")
def statistics(
    department_id: int | None = Query(default=None, gt=0),
    job_id: int | None = Query(default=None, gt=0),
    days: Literal["7", "30", "90"] = "30",
    start: date | None = None,
    end: date | None = None,
    granularity: Literal["day", "week", "month"] | None = None,
    auth: AuthContext = Depends(require_auth),
):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if (start is None) != (end is None):
        raise HTTPException(422, "请同时填写起止日期")
    end = end or today
    start = start or (end - timedelta(days=int(days)-1))
    span = (end-start).days + 1
    if not 1 <= span <= 366 or end > today:
        raise HTTPException(422, "日期范围需在一年内，且结束日期不能晚于今天")
    unit = granularity or ("day" if span <= 14 else "week" if span <= 100 else "month")
    if unit == "day" and span > 100:
        raise HTTPException(422, "按日查看最多支持 100 天，请切换按周或按月")
    with rls_session(user_id=auth.user_id) as session:
        payload = session.execute(text("""
            SELECT recruitment_statistics(:department,:job,:start,:until,:unit)
        """), {"department":department_id, "job":job_id, "start":start,
                "until":end+timedelta(days=1), "unit":unit}).scalar_one()
    if payload is None:
        raise HTTPException(404, "统计范围不存在或无权访问")
    return {**payload, "period":{"start":start,"end":end,"unit":unit,"timezone":"Asia/Shanghai"}}


@router.get("/atlas")
def atlas(
    department_id: int | None = Query(default=None, gt=0),
    job_id: int | None = Query(default=None, gt=0),
    period: Literal["month", "30", "90", "year"] = "month",
    start: date | None = None,
    end: date | None = None,
    region: str | None = Query(default=None, pattern=r"^(?:[0-9]{6}|overseas|unknown)$"),
    school_id: int | None = Query(default=None, gt=0),
    major: str | None = Query(default=None, max_length=100),
    tier: Literal["is_985", "is_211", "is_double_first_class", "qs"] | None = None,
    degree: Literal["bachelor", "master", "phd"] | None = None,
    auth: AuthContext = Depends(require_auth),
):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if (start is None) != (end is None):
        raise HTTPException(422, "请同时填写起止日期")
    if start is not None and end is not None:
        if not 0 <= (end-start).days < 366 or end > today:
            raise HTTPException(422, "日期范围需在一年内，且结束日期不能晚于今天")
        unit = "day" if (end-start).days < 31 else "week"
    elif period == "month":
        start, unit = today.replace(day=1), "day"
    elif period == "year":
        start, unit = today.replace(month=1, day=1), "month"
    else:
        start, unit = today-timedelta(days=int(period)-1), "week"
    end = end or today
    with rls_session(user_id=auth.user_id) as session:
        payload = session.execute(text("""
            SELECT recruitment_atlas(:department,:job,:start,:until,:unit,:region,:school,:major,:tier,:degree)
        """), {"department":department_id, "job":job_id, "start":start,
                "until":end+timedelta(days=1), "unit":unit, "region":region,
                "school":school_id, "major":major, "tier":tier, "degree":degree}).scalar_one()
    if payload is None:
        raise HTTPException(404, "统计范围不存在或无权访问")
    return {**payload, "period":{"start":start,"end":end,"unit":unit}}
