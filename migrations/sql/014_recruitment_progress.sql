ALTER TABLE application DROP CONSTRAINT valid_recruitment_stage;
ALTER TABLE application ADD CONSTRAINT valid_recruitment_stage CHECK (
    recruitment_stage IN ('review','progress','interview','rejected','hired')
    AND (recruitment_stage='interview')=(interview_round_id IS NOT NULL)
);

-- Legacy active applications without a selected round are still in progress.
UPDATE application SET recruitment_stage='progress' WHERE status='active' AND recruitment_stage='review';

-- Keep existing round IDs, including those attached to applications.
WITH final_order AS (
    SELECT id,row_number() OVER (PARTITION BY job_id ORDER BY (name='终面'),position) AS position
    FROM job_interview_round
)
UPDATE job_interview_round r SET position=f.position
FROM final_order f WHERE r.id=f.id AND r.position<>f.position;
