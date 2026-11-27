"""招聘看板的时间口径与公司汇总。"""
from contextlib import aclosing
from datetime import date, datetime, timedelta
import logging
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import psycopg
from sqlalchemy import text

from app.core.config import get_settings
from app.core.talent_status import STATUS_OPTIONS

SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)


def board_period(kind: str, anchor: date | None, today: date):
    anchor = anchor or today
    if not date(1000,1,1) <= anchor <= today:
        raise HTTPException(422, "请选择今天或之前的有效统计日期")
    if kind == "week":
        start = anchor-timedelta(days=anchor.weekday())
        until, unit = start+timedelta(days=7), "day"
    elif kind == "month":
        start = anchor.replace(day=1)
        until = (start+timedelta(days=32)).replace(day=1)
        unit = "week"
    else:
        start = anchor.replace(month=1, day=1)
        until, unit = start.replace(year=start.year+1), "month"
    return start, min(until,today+timedelta(days=1)), unit


def recruitment_board(session, kind, anchor, department_id, job_id, status=None, job_status=None):
    today = datetime.now(SHANGHAI).date()
    start, until, unit = board_period(kind, anchor, today)
    options = session.execute(text("""
        SELECT
            (SELECT revision FROM recruitment_board_revision WHERE singleton) AS revision,
            (SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY d.name,d.id),'[]')
             FROM (SELECT id,name FROM department WHERE status='active') d) AS departments,
            (SELECT coalesce(jsonb_agg(to_jsonb(j) ORDER BY j.title,j.id),'[]')
             FROM (SELECT job.id,job.title,job.department_id FROM job JOIN department d ON d.id=job.department_id
                   WHERE NOT job.hidden AND d.status='active') j) AS jobs
    """)).mappings().one()
    department_names = {item["id"]:item["name"] for item in options["departments"]}
    job_departments = {item["id"]:item["department_id"] for item in options["jobs"]}
    adjustments = []
    if job_id is not None:
        if job_id not in job_departments:
            job_id = None
            adjustments.append("job_unavailable")
        elif department_id is not None and department_id != job_departments[job_id]:
            department_id = job_departments[job_id]
            adjustments.append("job_department_changed")
    if department_id is not None and department_id not in department_names:
        department_id = None
        adjustments.append("department_unavailable")
    counts = dict(session.execute(BOARD_QUERY, {
        "department":department_id,"job":job_id,"start":start,"until":until,"unit":unit,"monthly":kind!="week",
        "status":status,"job_status":job_status,
    }).mappings().one())
    return {
        "period":{"kind":kind,"start":start,"end":until-timedelta(days=1),"unit":unit,"timezone":"Asia/Shanghai"},
        "scope":{"department_id":department_id,"job_id":job_id,"status":status,"job_status":job_status,"adjustments":adjustments},
        "revision":str(options["revision"]),
        "options":{"departments":options["departments"],"jobs":options["jobs"],"talent_statuses":STATUS_OPTIONS},
        **counts,
    }


async def recruitment_board_updates(user_id: int):
    """Stream committed revisions. A reconnect starts with the current revision."""
    try:
        async with await psycopg.AsyncConnection.connect(
            get_settings().app_database_url,autocommit=True,connect_timeout=5
        ) as listener:
            await listener.execute("SELECT set_config('app.user_id',%s,false)", (str(user_id),))
            await listener.execute("LISTEN recruitment_board_updates")
            cursor = await listener.execute("SELECT revision FROM recruitment_board_revision WHERE singleton")
            current = await cursor.fetchone()
            if current is None:
                return
            revision = current[0]
            yield f"data: {revision}\n\n"
            while True:
                async with aclosing(listener.notifies(timeout=15,stop_after=1)) as notifications:
                    update = await anext(notifications,None)
                if update is None:
                    yield ": keepalive\n\n"
                    continue
                cursor = await listener.execute("SELECT revision FROM recruitment_board_revision WHERE singleton")
                current = await cursor.fetchone()
                if current is None:
                    return
                if current[0]>revision:
                    revision = current[0]
                    yield f"data: {revision}\n\n"
    except psycopg.Error:
        logger.warning("招聘看板更新连接中断")


BOARD_QUERY = text("""
    WITH active_departments AS MATERIALIZED (
        SELECT id,name FROM department WHERE status='active'
    ), departments AS (
        SELECT * FROM active_departments
        WHERE CAST(:department AS bigint) IS NULL OR id=:department
    ), jobs AS MATERIALIZED (
        SELECT j.id,j.title,j.department_id,j.status,j.planned_headcount,d.name AS department
        FROM job j JOIN departments d ON d.id=j.department_id
        WHERE NOT j.hidden AND (CAST(:job AS bigint) IS NULL OR j.id=:job)
    ), applications AS MATERIALIZED (
        SELECT a.id,a.candidate_id,current_scope.job_id,current_scope.department_id,a.submitted_at
        FROM application a
        LEFT JOIN job assignment ON assignment.id=a.job_id
        LEFT JOIN active_departments owning ON owning.id=assignment.department_id
        LEFT JOIN active_departments handling ON handling.id=a.handling_department_id
        CROSS JOIN LATERAL (
            SELECT CASE WHEN owning.id IS NOT NULL THEN assignment.id END AS job_id,
                   coalesce(owning.id,handling.id) AS department_id
        ) current_scope
        WHERE a.deleted_at IS NULL AND assignment.hidden IS NOT TRUE
          AND (CAST(:job AS bigint) IS NULL OR current_scope.job_id=:job)
          AND (CAST(:department AS bigint) IS NULL OR current_scope.department_id=:department)
    ), people AS MATERIALIZED (
        SELECT t.id,t.status,t.entered_at,a.candidate_id,a.job_id,a.department_id,
               coalesce((to_jsonb(t)->>'onboarded_month')::date, confirmed.month) AS onboarded_month
        FROM talent t JOIN applications a ON a.id=t.source_application_id
        LEFT JOIN LATERAL (
            SELECT date_trunc('month',e.created_at AT TIME ZONE 'Asia/Shanghai')::date AS month
            FROM audit_log e
            WHERE t.status='hired' AND e.target_type='talent' AND e.target_id=t.id::text
              AND e.action='talent_status' AND e.result='ok' AND e.detail->>'status'='hired'
            ORDER BY e.id DESC LIMIT 1
        ) confirmed ON true
    ), hired_people AS MATERIALIZED (
        SELECT * FROM people WHERE status='hired'
    ), offer_events AS MATERIALIZED (
        SELECT e.id,e.created_at,a.candidate_id,a.job_id,a.department_id
        FROM audit_log e JOIN applications a ON e.target_id=a.id::text
        WHERE e.action='recruitment_stage_change' AND e.target_type='application' AND e.result='ok'
          AND e.detail->'to'->>'code'='hired'
    ), offers AS (
        SELECT DISTINCT ON (candidate_id) created_at,job_id,department_id
        FROM offer_events ORDER BY candidate_id,created_at,id
    ), applications_in_period AS MATERIALIZED (
        SELECT * FROM applications
        WHERE submitted_at >= (CAST(:start AS date)::timestamp AT TIME ZONE 'Asia/Shanghai')
          AND submitted_at < (CAST(:until AS date)::timestamp AT TIME ZONE 'Asia/Shanghai')
    ), cohort AS (
        SELECT candidate_id,min(submitted_at) AS applied_at FROM applications_in_period
        GROUP BY candidate_id
    ), selected_candidates AS MATERIALIZED (
        SELECT a.candidate_id,a.job_id,coalesce(t.status,'unassigned') AS status
        FROM applications_in_period a LEFT JOIN talent t USING (candidate_id)
        WHERE (CAST(:status AS text) IS NULL OR coalesce(t.status,'unassigned')=:status)
          AND (CAST(:job_status AS text) IS NULL OR EXISTS (
              SELECT 1 FROM jobs j WHERE j.id=a.job_id AND j.status=:job_status
          ))
    ), selected_statuses AS (
        SELECT status,count(DISTINCT candidate_id) AS people
        FROM selected_candidates GROUP BY status
    ), selected_jobs AS (
        SELECT j.id,j.department,coalesce(j.title,'未关联岗位') AS title,
               count(DISTINCT c.candidate_id) AS people
        FROM selected_candidates c LEFT JOIN jobs j ON j.id=c.job_id
        GROUP BY j.id,j.department,j.title
    ), cohort_talent AS (
        SELECT p.id,p.candidate_id,greatest(c.applied_at,p.entered_at) AS started_at
        FROM cohort c JOIN people p USING (candidate_id)
    ), cohort_interviews AS (
        SELECT p.candidate_id,min(i.created_at) AS booked_at
        FROM cohort_talent p JOIN talent_interview i ON i.talent_id=p.id AND i.created_at>=p.started_at
        GROUP BY p.candidate_id
    ), cohort_offers AS (
        SELECT i.candidate_id,min(e.created_at) AS offered_at
        FROM cohort_interviews i
        JOIN (
            SELECT candidate_id,created_at FROM offer_events
            UNION ALL
            SELECT p.candidate_id,e.created_at FROM people p
            JOIN audit_log e ON e.target_id=p.id::text
            WHERE e.action='talent_status' AND e.target_type='talent' AND e.result='ok'
              AND e.detail->>'status'='pending_onboard'
        ) e USING (candidate_id)
        WHERE e.created_at>=i.booked_at GROUP BY i.candidate_id
    ), cohort_onboarded AS (
        SELECT 1 FROM cohort_offers o JOIN hired_people p USING (candidate_id)
        WHERE p.onboarded_month>=date_trunc('month',o.offered_at AT TIME ZONE 'Asia/Shanghai')::date
          AND EXISTS (
              SELECT 1 FROM audit_log e
              WHERE e.target_type='talent' AND e.target_id=p.id::text AND e.action='talent_status' AND e.result='ok'
                AND e.detail->>'status'='hired' AND e.created_at>=o.offered_at
          )
    ), events AS (
        SELECT submitted_at AS stamp,job_id,department_id,'applications' AS kind FROM applications
        UNION ALL
        SELECT i.created_at,p.job_id,p.department_id,'interviews'
        FROM talent_interview i JOIN people p ON p.id=i.talent_id
        UNION ALL
        SELECT created_at,job_id,department_id,'offers' FROM offers
    ), activity AS MATERIALIZED (
        SELECT (stamp AT TIME ZONE 'Asia/Shanghai')::date AS day, job_id,department_id,kind
        FROM events
        WHERE stamp >= (CAST(:start AS date)::timestamp AT TIME ZONE 'Asia/Shanghai')
          AND stamp < (CAST(:until AS date)::timestamp AT TIME ZONE 'Asia/Shanghai')
    ), activity_totals AS (
        SELECT count(*) FILTER (WHERE kind='applications') AS applications,
               count(*) FILTER (WHERE kind='interviews') AS interviews,
               count(*) FILTER (WHERE kind='offers') AS offers
        FROM activity
    ), by_job AS (
        SELECT job_id,count(*) FILTER (WHERE kind='applications') AS applications,
               count(*) FILTER (WHERE kind='interviews') AS interviews,
               count(*) FILTER (WHERE kind='offers') AS offers
        FROM activity GROUP BY job_id
    ), by_department AS (
        SELECT department_id,count(*) FILTER (WHERE kind='applications') AS applications,
               count(*) FILTER (WHERE kind='interviews') AS interviews,
               count(*) FILTER (WHERE kind='offers') AS offers
        FROM activity GROUP BY department_id
    ), by_time AS (
        SELECT date_trunc(:unit,day::timestamp)::date AS bucket,
               count(*) FILTER (WHERE kind='applications') AS applications,
               count(*) FILTER (WHERE kind='interviews') AS interviews,
               count(*) FILTER (WHERE kind='offers') AS offers
        FROM activity GROUP BY 1
    ), onboarded AS (
        SELECT job_id,count(*) AS count FROM hired_people GROUP BY job_id
    ), job_rows AS (
        SELECT j.*,coalesce(a.applications,0) AS applications,coalesce(a.interviews,0) AS interviews,
               coalesce(a.offers,0) AS offers,coalesce(h.count,0) AS onboarded,
               CASE WHEN j.status='open' AND j.planned_headcount IS NOT NULL
                  THEN greatest(0,j.planned_headcount-coalesce(h.count,0)) END AS gap
        FROM jobs j LEFT JOIN by_job a ON a.job_id=j.id LEFT JOIN onboarded h ON h.job_id=j.id
    ), department_rows AS (
        SELECT d.id,d.name,coalesce(a.applications,0) AS applications,coalesce(a.interviews,0) AS interviews,
               coalesce(a.offers,0) AS offers
        FROM departments d LEFT JOIN by_department a ON a.department_id=d.id
        WHERE CAST(:job AS bigint) IS NULL OR d.id IN (SELECT department_id FROM jobs)
        UNION ALL
        SELECT NULL::bigint,'未归属部门',a.applications,a.interviews,a.offers
        FROM by_department a WHERE a.department_id IS NULL
    ), series AS (
        SELECT greatest(g.bucket::date,CAST(:start AS date)) AS start,
               least((g.bucket+step)::date,CAST(:until AS date))-1 AS end,
               coalesce(a.applications,0) AS applications,coalesce(a.interviews,0) AS interviews,
               coalesce(a.offers,0) AS offers
        FROM (SELECT CASE :unit WHEN 'month' THEN interval '1 month' WHEN 'week' THEN interval '1 week'
                    ELSE interval '1 day' END AS step) bucket_size
        CROSS JOIN LATERAL generate_series(
            date_trunc(:unit,CAST(:start AS date)::timestamp),CAST(:until AS date)::timestamp-interval '1 day',step
        ) AS g(bucket)
        LEFT JOIN by_time a ON a.bucket=g.bucket::date
    )
    SELECT (SELECT to_jsonb(a) FROM activity_totals a) || jsonb_build_object(
                'open_jobs',(SELECT count(*) FROM jobs WHERE status='open'),
                'planned',(SELECT sum(planned_headcount) FROM jobs WHERE status='open'),
                'gap',(SELECT sum(gap) FROM job_rows),
                'talent_pool',(SELECT count(*) FROM people),
                'onboarded_people',(SELECT count(*) FROM hired_people),
                'period_onboarded',CASE WHEN :monthly THEN (SELECT count(*) FROM hired_people
                               WHERE onboarded_month>=:start AND onboarded_month<:until) END
           ) AS totals,
           (SELECT coalesce(jsonb_object_agg(status,amount),'{}') FROM
               (SELECT status,count(*) AS amount FROM people GROUP BY status) s) AS stages,
           (SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.start),'[]') FROM series s) AS trend,
           jsonb_build_object('applicants',(SELECT count(*) FROM cohort),
                              'talent_pool',(SELECT count(*) FROM cohort_talent),
                              'interviewed',(SELECT count(*) FROM cohort_interviews),
                              'offered',(SELECT count(*) FROM cohort_offers),
                              'onboarded',(SELECT count(*) FROM cohort_onboarded)) AS funnel,
           jsonb_build_object(
               'people',(SELECT count(DISTINCT candidate_id) FROM selected_candidates),
               'statuses',(SELECT coalesce(jsonb_object_agg(status,people),'{}') FROM selected_statuses),
               'jobs',(SELECT coalesce(jsonb_agg(to_jsonb(j) ORDER BY j.people DESC,j.title,j.id),'[]') FROM selected_jobs j)
           ) AS selection,
           (SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY d.applications DESC,d.name,d.id),'[]') FROM department_rows d) AS departments,
           (SELECT coalesce(jsonb_agg(to_jsonb(j) ORDER BY j.gap DESC NULLS LAST,j.applications DESC,j.title,j.id),'[]') FROM job_rows j) AS jobs
""")
