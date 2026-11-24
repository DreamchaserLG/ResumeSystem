ALTER TABLE job ADD COLUMN recruiter_user_id bigint REFERENCES app_user(id) ON DELETE SET NULL;

-- Imported reference jobs with no configured rounds use the standard workflow.
INSERT INTO job_interview_round(job_id,name,position)
SELECT j.id,r.name,r.position
FROM job j JOIN job_posting_reference ref ON ref.job_id=j.id
CROSS JOIN (VALUES ('一面',1),('二面',2),('终面',3)) AS r(name,position)
WHERE NOT EXISTS (SELECT 1 FROM job_interview_round existing WHERE existing.job_id=j.id);
