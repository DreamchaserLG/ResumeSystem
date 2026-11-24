ALTER TABLE job_posting_reference
    ADD COLUMN reference_recruitment_start date,
    ADD COLUMN reference_recruitment_end date,
    ADD COLUMN skill_terms jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(skill_terms)='object'),
    ADD CHECK (reference_recruitment_start <= reference_recruitment_end);

CREATE OR REPLACE VIEW job_retrieval_input WITH (security_invoker=true) AS
SELECT j.id, j.department_id, jsonb_build_object('title',j.title,'jd',j.jd,'requirements',j.requirements,
    'required_skills',j.required_skills,'experience_tags',j.experience_tags) ||
    CASE WHEN ref.jd_snapshot=j.jd AND ref.requirements_snapshot=j.requirements
    THEN jsonb_build_object('reference',jsonb_build_object('scopes',ref.evidence_scopes,
        'keywords',ref.keyword_groups,'skill_terms',ref.skill_terms))
    ELSE '{}'::jsonb END AS snapshot
FROM job j LEFT JOIN job_posting_reference ref ON ref.job_id=j.id;

-- Preserve previously approved rules when the source snapshot gains an empty skill mapping.
UPDATE job_retrieval_approval
SET snapshot=jsonb_set(snapshot,'{reference,skill_terms}','{}'::jsonb)
WHERE snapshot ? 'reference' AND NOT (snapshot->'reference' ? 'skill_terms');
