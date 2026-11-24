CREATE TABLE job_posting_reference (
    job_id bigint PRIMARY KEY REFERENCES job(id) ON DELETE CASCADE,
    category text NOT NULL CHECK (btrim(category)<>''),
    sources jsonb NOT NULL CHECK (jsonb_typeof(sources)='array' AND jsonb_array_length(sources)>0),
    jd_snapshot text NOT NULL,
    requirements_snapshot text NOT NULL,
    evidence_scopes text[] NOT NULL CHECK (cardinality(evidence_scopes)>0),
    keyword_groups jsonb NOT NULL,
    researched_on date NOT NULL
);

ALTER TABLE job_posting_reference ENABLE ROW LEVEL SECURITY;
CREATE POLICY posting_scope ON job_posting_reference FOR SELECT TO rs_app USING (
    app_client_id() IS NULL AND (
        app_is_super_admin()
        OR EXISTS (SELECT 1 FROM job j
            WHERE j.id=job_posting_reference.job_id AND j.department_id=ANY(app_departments()))
        OR EXISTS (SELECT 1 FROM application a WHERE a.job_id=job_posting_reference.job_id
            AND a.deleted_at IS NULL AND a.handling_department_id=ANY(app_departments()))
    )
);
GRANT SELECT ON job_posting_reference TO rs_app;

CREATE OR REPLACE VIEW job_retrieval_input WITH (security_invoker=true) AS
SELECT j.id, j.department_id, jsonb_build_object('title',j.title,'jd',j.jd,'requirements',j.requirements,
    'required_skills',j.required_skills,'experience_tags',j.experience_tags) ||
    CASE WHEN ref.jd_snapshot=j.jd AND ref.requirements_snapshot=j.requirements
    THEN jsonb_build_object('reference',jsonb_build_object('scopes',ref.evidence_scopes,'keywords',ref.keyword_groups))
    ELSE '{}'::jsonb END AS snapshot
FROM job j LEFT JOIN job_posting_reference ref ON ref.job_id=j.id;

ALTER TABLE job_retrieval_index DROP CONSTRAINT job_retrieval_index_source_check;
ALTER TABLE job_retrieval_index ADD CONSTRAINT job_retrieval_index_source_check
    CHECK (source IN ('title','jd','reviewed','reference'));
ALTER TABLE job_requirement DROP CONSTRAINT job_requirement_kind_check;
ALTER TABLE job_requirement ADD CONSTRAINT job_requirement_kind_check
    CHECK (kind IN ('role','duty','qualification','qualification_check','skill','degree','months'));
ALTER TABLE job_requirement DROP CONSTRAINT job_requirement_check;
ALTER TABLE job_requirement ADD CONSTRAINT job_requirement_embedding_check
    CHECK ((kind IN ('role','duty','qualification')) = (embedding IS NOT NULL));
