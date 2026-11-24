ALTER TABLE job
    ADD COLUMN source_category text,
    ADD COLUMN work_location text,
    ADD COLUMN employment_type text;

ALTER TABLE job_source_mapping
    ADD COLUMN posting_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(posting_snapshot) = 'object');
