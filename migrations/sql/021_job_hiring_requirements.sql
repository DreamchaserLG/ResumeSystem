ALTER TABLE job
    ADD COLUMN requirements text NOT NULL DEFAULT '' CHECK (length(requirements) <= 4000),
    ADD COLUMN required_skills text[] NOT NULL DEFAULT '{}' CHECK (cardinality(required_skills) <= 20),
    ADD COLUMN experience_tags text[] NOT NULL DEFAULT '{}' CHECK (cardinality(experience_tags) <= 12),
    ADD COLUMN recruitment_start date,
    ADD COLUMN recruitment_end date,
    ADD CONSTRAINT job_recruitment_dates_ordered CHECK (recruitment_start <= recruitment_end);
