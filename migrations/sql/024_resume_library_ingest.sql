-- File-server resumes share the parsing and permission chain with existing applications.
-- A NULL job represents a library entry and never creates a recruiting position.
ALTER TABLE application ALTER COLUMN job_id DROP NOT NULL;
ALTER TABLE application ALTER COLUMN submitted_job_id DROP NOT NULL;

ALTER TABLE resume_dropfile DROP CONSTRAINT resume_dropfile_object_key_key;
DROP INDEX dropfile_sha;
ALTER TABLE resume_dropfile
    ADD COLUMN department_id bigint REFERENCES department(id),
    ADD COLUMN application_id bigint REFERENCES application(id) ON DELETE SET NULL,
    DROP CONSTRAINT resume_dropfile_candidate_id_fkey,
    ADD FOREIGN KEY (candidate_id) REFERENCES candidate(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX dropfile_department_sha ON resume_dropfile(department_id, sha256);
CREATE INDEX dropfile_application ON resume_dropfile(application_id);

-- The source application determines who can open the exact resume version and its followups.
ALTER POLICY talent_select ON talent USING (
    app_client_id() IS NULL AND (app_is_super_admin() OR EXISTS (
        SELECT 1 FROM application a WHERE a.id=talent.source_application_id
          AND a.deleted_at IS NULL AND a.handling_department_id=ANY(app_departments())
    ))
);
ALTER TABLE talent
    DROP CONSTRAINT talent_source_application_id_fkey,
    ADD FOREIGN KEY (source_application_id) REFERENCES application(id) ON DELETE CASCADE,
    DROP CONSTRAINT talent_status_check,
    ADD CHECK (status IN ('uncontacted','interviewing','hired','rejected','blacklist','custom')),
    ALTER COLUMN status SET DEFAULT 'uncontacted';
ALTER TABLE talent_followup ADD COLUMN status_label text;
