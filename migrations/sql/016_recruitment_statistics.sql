ALTER TABLE job ADD COLUMN planned_headcount integer CHECK (planned_headcount BETWEEN 1 AND 100000),
                ADD COLUMN target_date date;

CREATE INDEX IF NOT EXISTS idx_audit_recruitment_events ON audit_log(target_id,created_at)
    WHERE action IN ('recruitment_stage_change','application_transfer') AND result='ok';

-- Only aggregates leave this function. Check the role and department assignments in the database.
CREATE FUNCTION recruitment_statistics(requested_department bigint, requested_job bigint, from_date date, until_date date, bucket_unit text)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
WITH actor AS (
    SELECT u.id,EXISTS (SELECT 1 FROM public.system_admin s WHERE s.user_id=u.id) AS admin
    FROM public.app_user u WHERE u.id=nullif(current_setting('app.user_id',true),'')::bigint AND u.status='active'
), departments AS (
    SELECT d.id,d.name,d.status FROM public.department d CROSS JOIN actor u
    WHERE u.admin OR (d.status='active' AND EXISTS (
        SELECT 1 FROM public.department_admin m WHERE m.user_id=u.id AND m.department_id=d.id
    ))
), job_scope AS (
    SELECT j.id,j.title,j.department_id,d.name AS department_name,j.status,j.planned_headcount,j.target_date
    FROM public.job j JOIN departments d ON d.id=j.department_id
    WHERE (requested_department IS NULL OR j.department_id=requested_department)
      AND (requested_job IS NULL OR j.id=requested_job)
), job_options AS (
    SELECT j.id,j.title,j.department_id FROM public.job j JOIN departments d ON d.id=j.department_id
    WHERE requested_department IS NULL OR j.department_id=requested_department
), apps AS MATERIALIZED (
    SELECT a.*,v.document_id,coalesce(pending.status,active.status,v.status) AS parse_status,
           coalesce(c.merged_into,a.candidate_id) AS person,
           CASE WHEN a.recruitment_stage IN ('review','progress','interview') THEN coalesce(
              (SELECT max(e.created_at) FROM public.audit_log e WHERE e.target_id=a.id::text AND e.target_type='application'
                AND e.action IN ('recruitment_stage_change','application_transfer') AND e.result='ok'),a.submitted_at
           ) <= now()-interval '7 days' ELSE false END AS stalled,
           EXISTS (SELECT 1 FROM public.resume_field_issue i
                   JOIN public.field_usage_policy p ON p.field_key=i.field_key
                   JOIN public.search_state s ON s.policy_version=p.policy_version
                   WHERE i.version_id=a.active_version_id AND p.purpose='preview' AND p.allowed)
           OR EXISTS (SELECT 1 FROM public.resume_extra_value x WHERE x.version_id=a.active_version_id
                      AND NOT x.reviewed AND x.field_key IS NULL) AS needs_review
    FROM public.application a JOIN job_scope j ON j.id=a.job_id AND j.department_id=a.handling_department_id
    JOIN public.resume_version v ON v.id=a.submitted_version_id
    LEFT JOIN public.resume_version pending ON pending.id=a.pending_version_id
    LEFT JOIN public.resume_version active ON active.id=a.active_version_id
    JOIN public.candidate c ON c.id=a.candidate_id
    WHERE a.deleted_at IS NULL
), job_counts AS (
    SELECT job_id,count(*) AS applications,
           count(*) FILTER (WHERE recruitment_stage='review') AS review,
           count(*) FILTER (WHERE recruitment_stage IN ('progress','interview')) AS progress,
           count(DISTINCT person) FILTER (WHERE recruitment_stage='hired') AS hired,
           count(*) FILTER (WHERE recruitment_stage='rejected') AS rejected,
           count(*) FILTER (WHERE needs_review) AS verify,
           count(*) FILTER (WHERE parse_status='failed') AS failed,count(*) FILTER (WHERE stalled) AS stalled
    FROM apps GROUP BY job_id
), job_rows AS (
    SELECT j.*,coalesce(c.applications,0) AS applications,coalesce(c.review,0) AS review,
           coalesce(c.progress,0) AS progress,coalesce(c.hired,0) AS hired,coalesce(c.rejected,0) AS rejected,
           coalesce(c.verify,0) AS verify,coalesce(c.failed,0) AS failed,coalesce(c.stalled,0) AS stalled,
           CASE WHEN j.status='open' AND j.planned_headcount IS NOT NULL
                THEN greatest(j.planned_headcount-coalesce(c.hired,0),0) END AS gap,
           CASE WHEN j.status='open' AND j.planned_headcount IS NOT NULL
                THEN least(j.planned_headcount,coalesce(c.hired,0)) END AS filled,
           CASE WHEN j.status='open' AND j.target_date IS NOT NULL
                  AND j.target_date < (now() AT TIME ZONE 'Asia/Shanghai')::date AND j.planned_headcount>coalesce(c.hired,0)
                THEN true ELSE false END AS overdue
    FROM job_scope j LEFT JOIN job_counts c ON c.job_id=j.id
), admin_assignments AS (
    SELECT m.department_id,u.id AS user_id FROM public.department_admin m
    JOIN public.app_user u ON u.id=m.user_id AND u.status='active'
    JOIN departments d ON d.id=m.department_id AND d.status='active'
    WHERE (SELECT admin FROM actor) AND (requested_department IS NULL OR m.department_id=requested_department)
      AND NOT EXISTS (SELECT 1 FROM public.system_admin s WHERE s.user_id=u.id)
), department_rows AS (
    SELECT d.id,d.name,d.status,
        (SELECT count(*) FROM admin_assignments m WHERE m.department_id=d.id) AS admins,
        count(j.id) FILTER (WHERE j.status='open') AS open_jobs,
        coalesce(sum(j.planned_headcount) FILTER (WHERE j.status='open'),0) AS planned,
        coalesce(sum(j.filled),0) AS filled,coalesce(sum(j.gap),0) AS gap,
        count(j.id) FILTER (WHERE j.status='open' AND j.planned_headcount IS NULL) AS unplanned,
        coalesce(sum(j.hired),0) AS hired,coalesce(sum(j.applications),0) AS applications
    FROM departments d LEFT JOIN job_rows j ON j.department_id=d.id
    WHERE (requested_department IS NULL OR d.id=requested_department)
      AND (requested_job IS NULL OR j.id=requested_job)
    GROUP BY d.id,d.name,d.status
), intake AS (
    SELECT (a.submitted_at AT TIME ZONE 'Asia/Shanghai')::date AS day,j.id AS job_id,j.department_id
    FROM public.application a JOIN job_scope j ON j.id=a.submitted_job_id AND j.department_id=a.submitted_department_id
    WHERE a.deleted_at IS NULL
      AND a.submitted_at >= from_date::timestamp AT TIME ZONE 'Asia/Shanghai'
      AND a.submitted_at < until_date::timestamp AT TIME ZONE 'Asia/Shanghai'
), hires AS (
    -- Keep the first recorded acceptance per identified candidate in this scope.
    SELECT DISTINCT ON (coalesce(c.merged_into,c.id)) e.created_at,j.id AS job_id,j.department_id
    FROM public.audit_log e JOIN public.application a ON e.target_id=a.id::text AND a.deleted_at IS NULL
    JOIN public.candidate c ON c.id=a.candidate_id
    JOIN job_scope j ON j.id=CASE WHEN (e.detail->>'job_id') ~ '^[1-9][0-9]{0,17}$' THEN (e.detail->>'job_id')::bigint END
    WHERE e.action='recruitment_stage_change' AND e.target_type='application' AND e.result='ok'
      AND e.detail->'to'->>'code'='hired'
      AND e.created_at < until_date::timestamp AT TIME ZONE 'Asia/Shanghai'
    ORDER BY coalesce(c.merged_into,c.id),e.created_at,e.id
), hires_in_period AS (
    SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS day,job_id,department_id FROM hires
    WHERE created_at >= from_date::timestamp AT TIME ZONE 'Asia/Shanghai'
), days AS (
    SELECT stamp::date AS bucket, greatest(stamp::date,from_date) AS start_date,
           least((stamp+CASE bucket_unit WHEN 'month' THEN interval '1 month' WHEN 'week' THEN interval '7 days' ELSE interval '1 day' END)::date,until_date)-1 AS end_date
    FROM generate_series(date_trunc(bucket_unit,from_date::timestamp),date_trunc(bucket_unit,(until_date-1)::timestamp),
              CASE bucket_unit WHEN 'month' THEN interval '1 month' WHEN 'week' THEN interval '7 days' ELSE interval '1 day' END) stamp
), job_trend AS (
    SELECT d.*, (SELECT count(*) FROM intake a WHERE a.day BETWEEN d.start_date AND d.end_date) AS applications,
           (SELECT count(*) FROM hires_in_period h WHERE h.day BETWEEN d.start_date AND d.end_date) AS hired FROM days d
), rounds AS (
    SELECT j.id AS job_id,j.title,r.id,r.name,r.position,count(a.id) AS total
    FROM job_scope j JOIN public.job_interview_round r ON r.job_id=j.id
    LEFT JOIN apps a ON a.job_id=j.id AND a.interview_round_id=r.id
    WHERE requested_job IS NOT NULL
    GROUP BY j.id,j.title,r.id,r.name,r.position
), totals AS (
    SELECT count(*) AS jobs,count(*) FILTER(WHERE status='open') AS open_jobs,
        count(*) FILTER(WHERE status='paused') AS paused_jobs,count(*) FILTER(WHERE status='closed') AS closed_jobs,
        coalesce(sum(planned_headcount) FILTER(WHERE status='open'),0) AS planned,
        coalesce(sum(filled),0) AS filled,coalesce(sum(gap),0) AS gap,
        count(*) FILTER(WHERE gap>0) AS unfilled_jobs,count(*) FILTER(WHERE overdue) AS overdue,
        count(*) FILTER(WHERE status='open' AND planned_headcount IS NULL) AS unplanned,
        (SELECT count(*) FROM apps) AS applications,(SELECT count(DISTINCT person) FROM apps) AS people,
        (SELECT count(DISTINCT document_id) FROM apps) AS resumes,
        (SELECT count(DISTINCT person) FROM apps WHERE recruitment_stage='hired') AS hired,
        (SELECT count(*) FROM apps WHERE recruitment_stage='hired') AS hired_applications,
        coalesce(sum(review),0) AS review,coalesce(sum(progress),0) AS progress,coalesce(sum(rejected),0) AS rejected,
        coalesce(sum(verify),0) AS verify,coalesce(sum(failed),0) AS failed,coalesce(sum(stalled),0) AS stalled,
        (SELECT count(*) FROM intake) AS new_applications,(SELECT count(*) FROM hires_in_period) AS period_hired
    FROM job_rows
)
SELECT jsonb_build_object(
    'scope',CASE WHEN actor.admin THEN 'super_admin' ELSE 'dept_hr' END,
    'departments',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',id,'name',name) ORDER BY name,id),'[]') FROM departments),
    'job_options',(SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY x.title,x.id),'[]') FROM job_options x),
    'totals',(SELECT to_jsonb(x) FROM totals x),
    'accounts',CASE WHEN actor.admin THEN jsonb_build_object(
        'admins',(SELECT count(DISTINCT user_id) FROM admin_assignments),
        'super_admins',(SELECT count(*) FROM public.system_admin s JOIN public.app_user u ON u.id=s.user_id AND u.status='active'),
        'disabled',(SELECT count(*) FROM public.app_user WHERE status='disabled'),
        'uncovered',(SELECT count(*) FROM department_rows WHERE status='active' AND admins=0)
    ) END,
    'jobs',(SELECT coalesce(jsonb_agg(to_jsonb(x) || jsonb_build_object(
                    'period_hired',(SELECT count(*) FROM hires_in_period h WHERE h.job_id=x.id),
                    'new_applications',(SELECT count(*) FROM intake a WHERE a.job_id=x.id)
                ) ORDER BY x.gap DESC NULLS LAST,x.department_name,x.title,x.id),'[]') FROM job_rows x),
    'department_rows',(SELECT coalesce(jsonb_agg((to_jsonb(x)-'admins')
                    || CASE WHEN actor.admin THEN jsonb_build_object('admins',x.admins) ELSE '{}'::jsonb END
                    || jsonb_build_object('period_hired',(SELECT count(*) FROM hires_in_period h WHERE h.department_id=x.id),
                                          'new_applications',(SELECT count(*) FROM intake a WHERE a.department_id=x.id))
                    ORDER BY x.name,x.id),'[]') FROM department_rows x),
    'trend',(SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY x.bucket),'[]') FROM job_trend x),
    'rounds',(SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY x.position),'[]') FROM rounds x)
)
FROM actor
WHERE (actor.admin OR EXISTS (SELECT 1 FROM departments))
  AND (requested_department IS NULL OR EXISTS (SELECT 1 FROM departments WHERE id=requested_department))
  AND (requested_job IS NULL OR EXISTS (SELECT 1 FROM job_scope WHERE id=requested_job))
  AND bucket_unit IN ('day','week','month') AND until_date-from_date BETWEEN 1 AND 366;
$$;
REVOKE ALL ON FUNCTION recruitment_statistics(bigint,bigint,date,date,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recruitment_statistics(bigint,bigint,date,date,text) TO rs_app;

-- Used by the HR list to open exactly the pending items counted by the board.
CREATE FUNCTION recruitment_stalled(requested_application bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.application a JOIN public.department_admin m ON m.department_id=a.handling_department_id
        JOIN public.app_user u ON u.id=m.user_id AND u.status='active'
        JOIN public.department d ON d.id=m.department_id AND d.status='active'
        WHERE a.id=requested_application AND a.deleted_at IS NULL
          AND u.id=nullif(current_setting('app.user_id',true),'')::bigint
          AND a.recruitment_stage IN ('review','progress','interview')
          AND coalesce((SELECT max(e.created_at) FROM public.audit_log e
              WHERE e.target_id=a.id::text AND e.target_type='application'
                AND e.action IN ('recruitment_stage_change','application_transfer') AND e.result='ok'),
              a.submitted_at) <= now()-interval '7 days'
    );
$$;
REVOKE ALL ON FUNCTION recruitment_stalled(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recruitment_stalled(bigint) TO rs_app;
