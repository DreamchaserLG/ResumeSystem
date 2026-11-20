-- External identifiers live in their own namespace and never reuse local IDs.
-- Use internal_hr for the native application and moka for Moka identifiers.
CREATE TABLE department_source_mapping (
    source_system text NOT NULL CHECK (source_system ~ '^[a-z][a-z0-9_]*$'),
    source_department_id text NOT NULL CHECK (btrim(source_department_id) <> ''),
    department_id bigint NOT NULL REFERENCES department(id) ON DELETE CASCADE,
    PRIMARY KEY (source_system, source_department_id)
);

CREATE TABLE job_source_mapping (
    source_system text NOT NULL CHECK (source_system ~ '^[a-z][a-z0-9_]*$'),
    source_job_id text NOT NULL CHECK (btrim(source_job_id) <> ''),
    job_id bigint NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    PRIMARY KEY (source_system, source_job_id)
);

-- One staging record per source row. Department and job IDs may remain unmapped.
-- An application link is recorded only after the original is in private storage.
CREATE TABLE resume_source_import (
    source_system text NOT NULL CHECK (source_system ~ '^[a-z][a-z0-9_]*$'),
    source_resume_id text NOT NULL CHECK (btrim(source_resume_id) <> ''),
    source_department_id text,
    source_job_id text,
    moka_resume_id text,
    moka_application_id text,
    moka_department_id text,
    moka_job_id text,
    source_upload_id text,
    application_id bigint,
    imported_version_id bigint,
    source_updated_at timestamptz,
    staged_at timestamptz NOT NULL DEFAULT now(),
    source_file_name text,
    source_file_path text,
    source_file_url text CHECK (source_file_url !~ '[?#]' AND source_file_url !~* '^(https?:)?//[^/]*@'),
    file_check_status text NOT NULL DEFAULT 'unchecked' CHECK (file_check_status IN (
        'unchecked', 'missing_base_url', 'expired', 'access_denied',
        'not_found', 'unreachable', 'invalid_file', 'reachable'
    )),
    file_checked_at timestamptz,
    declared_work_years numeric(5,2) CHECK (declared_work_years >= 0),
    expected_work_location text,
    expected_salary_min integer CHECK (expected_salary_min >= 0),
    expected_salary_max integer CHECK (expected_salary_max >= 0),
    expected_salary_currency text CHECK (expected_salary_currency ~ '^[A-Z]{3}$'),
    expected_salary_period text CHECK (expected_salary_period IN ('hour', 'day', 'month', 'year')),
    source_labels jsonb,
    PRIMARY KEY (source_system, source_resume_id),
    FOREIGN KEY (application_id, imported_version_id)
        REFERENCES application_version_history(application_id, version_id) MATCH FULL ON DELETE CASCADE,
    CHECK ((file_check_status = 'unchecked') = (file_checked_at IS NULL)),
    CHECK (expected_salary_min <= expected_salary_max)
);
CREATE INDEX idx_resume_source_import_application ON resume_source_import(application_id);
CREATE INDEX idx_resume_source_import_moka ON resume_source_import(moka_resume_id) WHERE moka_resume_id IS NOT NULL;

COMMENT ON COLUMN resume_source_import.source_file_url IS
    'Stable file reference without credentials or signed query parameters. Resolve Moka signed URLs on demand.';
COMMENT ON COLUMN resume_source_import.source_labels IS
    'Labels from the source system. Canonical capability and school tags are maintained separately.';

-- Staging may contain private storage references. Use the trusted import connection.
ALTER TABLE department_source_mapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE department_source_mapping FORCE ROW LEVEL SECURITY;
ALTER TABLE job_source_mapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_source_mapping FORCE ROW LEVEL SECURITY;
ALTER TABLE resume_source_import ENABLE ROW LEVEL SECURITY;
ALTER TABLE resume_source_import FORCE ROW LEVEL SECURITY;
REVOKE ALL ON department_source_mapping, job_source_mapping, resume_source_import FROM PUBLIC, rs_app;

-- Versioned values may be published when supported by evidence. Declared years
-- stay separate from calculated work duration, and desired location from city.
ALTER TABLE resume_profile
    ADD COLUMN declared_work_years numeric(5,2) CHECK (declared_work_years >= 0),
    ADD COLUMN expected_work_location text;

INSERT INTO field_definition (field_key, display_name, value_type, description) VALUES
    ('profile.declared_work_years', '申报工作年限', 'number', '来源记录声明的工作年限，单位为年'),
    ('profile.expected_work_location', '意向工作地点', 'location', '候选人期望工作的地点');
