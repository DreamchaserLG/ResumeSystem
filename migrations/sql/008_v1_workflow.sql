ALTER TABLE resume_version ADD COLUMN extractor_version text;
ALTER TABLE application ADD COLUMN pending_version_id bigint REFERENCES resume_version(id);
ALTER TABLE application ADD COLUMN deleted_at timestamptz;

ALTER POLICY app_select ON application USING (
    deleted_at IS NULL AND (
        handling_department_id = ANY (app_departments())
        OR id = ANY (app_application_ids())
        OR EXISTS (
            SELECT 1 FROM integration_job ij JOIN integration_client c ON c.id = ij.client_id
            WHERE ij.job_id = application.job_id AND ij.client_id = app_client_id()
              AND c.status = 'active'
              AND (ij.handling_department_id IS NULL OR ij.handling_department_id = application.handling_department_id)
        )
    )
);
CREATE POLICY dept_admin_revoke ON department_admin FOR DELETE TO rs_app USING (app_is_super_admin());

INSERT INTO field_definition (field_key, display_name, value_type) VALUES
    ('project.description','项目描述','text'), ('project.period','项目时间','period'),
    ('education.period','教育时间','period'), ('education.school_tags','院校标签','text'),
    ('source.unassigned','待确认章节','text')
ON CONFLICT DO NOTHING;
INSERT INTO field_usage_policy (field_key, purpose, allowed, reason, policy_version)
SELECT f, p, true, 'v1 业务白名单', 1 FROM (VALUES
    ('work.description','preview'), ('work.employment_type','preview'),
    ('education.major','preview'), ('education.period','preview'), ('education.school_tags','preview'),
    ('project.project_name','preview'), ('project.role','preview'), ('project.description','preview'),
    ('project.period','preview'), ('skill.name','preview'), ('source.unassigned','preview'),
    ('project.description','search'), ('work.company','filter'), ('education.school_raw','filter'),
    ('profile.city','filter'), ('skill.name','filter'), ('education.school_tags','filter')
) AS approved(f,p) ON CONFLICT DO NOTHING;

CREATE TABLE job_school_filter (
    job_id bigint NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    tag_code text NOT NULL,
    approved_by bigint NOT NULL REFERENCES app_user(id),
    reason text NOT NULL CHECK (length(trim(reason)) > 0),
    PRIMARY KEY (job_id, tag_code)
);
ALTER TABLE job_school_filter ENABLE ROW LEVEL SECURITY;
CREATE POLICY approved_tag_read ON job_school_filter FOR SELECT TO rs_app
    USING (app_is_super_admin() OR EXISTS (SELECT 1 FROM job WHERE id=job_id AND department_id=ANY(app_departments())));
GRANT SELECT ON job_school_filter TO rs_app;

CREATE TABLE resume_original_grant (
    application_id bigint NOT NULL REFERENCES application(id) ON DELETE CASCADE,
    user_id bigint NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    granted_by bigint NOT NULL REFERENCES app_user(id),
    expires_at timestamptz NOT NULL,
    reason text NOT NULL CHECK (length(trim(reason)) > 0),
    PRIMARY KEY (application_id, user_id),
    CHECK (granted_by <> user_id)
);
ALTER TABLE resume_original_grant ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON resume_original_grant FROM PUBLIC, rs_app;

ALTER TABLE resume_version ADD COLUMN preview_key text;
CREATE INDEX idx_application_pending_version ON application(pending_version_id) WHERE pending_version_id IS NOT NULL;
CREATE INDEX idx_application_department_list ON application(handling_department_id, id DESC) WHERE deleted_at IS NULL;

-- Use application/version scope instead of inheriting every document owned by a visible candidate.
-- A non-inlined, invoker-rights function keeps RLS in effect on the application and history lookups.
CREATE FUNCTION app_can_read_resume_version(requested bigint) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, public AS $$
    SELECT EXISTS (SELECT 1 FROM public.application_version_history WHERE version_id=requested)
        OR (public.app_client_id() IS NULL AND EXISTS (
            SELECT 1 FROM public.application WHERE pending_version_id=requested
        ))
$$;
REVOKE ALL ON FUNCTION app_can_read_resume_version(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_can_read_resume_version(bigint) TO rs_app;
ALTER POLICY ver_select ON resume_version USING (app_can_read_resume_version(id));
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'resume_record','resume_profile','resume_contact','resume_education',
        'resume_work','resume_project','resume_skill','resume_source_span',
        'resume_evidence_link','resume_extra_value','resume_field_issue',
        'resume_record_source','resume_search_record'
    ] LOOP
        EXECUTE format('ALTER POLICY ver_select ON %I USING (app_can_read_resume_version(version_id))', t);
    END LOOP;
END $$;

ALTER TABLE resume_extra_value ADD COLUMN reviewed boolean NOT NULL DEFAULT false;

CREATE TABLE resume_file_cleanup (
    object_key text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE resume_file_cleanup ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON resume_file_cleanup FROM PUBLIC, rs_app;

-- All version-local dependents are deleted with their source version.
ALTER TABLE resume_skill DROP CONSTRAINT resume_skill_version_id_parent_record_no_fkey,
    ADD CONSTRAINT resume_skill_version_id_parent_record_no_fkey FOREIGN KEY(version_id,parent_record_no)
        REFERENCES resume_record(version_id,record_no) ON DELETE CASCADE;
ALTER TABLE resume_extra_value DROP CONSTRAINT resume_extra_value_version_id_span_no_fkey,
    ADD CONSTRAINT resume_extra_value_version_id_span_no_fkey FOREIGN KEY(version_id,span_no)
        REFERENCES resume_source_span(version_id,span_no) ON DELETE CASCADE,
    ADD CONSTRAINT resume_extra_value_record_fkey FOREIGN KEY(version_id,record_no)
        REFERENCES resume_record(version_id,record_no) ON DELETE CASCADE;
ALTER TABLE resume_field_issue DROP CONSTRAINT resume_field_issue_version_id_span_no_fkey,
    ADD CONSTRAINT resume_field_issue_version_id_span_no_fkey FOREIGN KEY(version_id,span_no)
        REFERENCES resume_source_span(version_id,span_no) ON DELETE CASCADE,
    ADD CONSTRAINT resume_field_issue_record_fkey FOREIGN KEY(version_id,record_no)
        REFERENCES resume_record(version_id,record_no) ON DELETE CASCADE;
