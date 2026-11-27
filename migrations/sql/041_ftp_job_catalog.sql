CREATE TABLE ftp_catalog_folder (
    path text PRIMARY KEY,
    department_id bigint REFERENCES department(id),
    job_id bigint UNIQUE REFERENCES job(id) ON DELETE SET NULL,
    managed_job boolean NOT NULL DEFAULT false,
    present boolean NOT NULL DEFAULT true,
    CHECK (job_id IS NULL OR (department_id IS NOT NULL AND strpos(path, '/') > 0))
);

CREATE TABLE ftp_catalog_file (
    path text PRIMARY KEY,
    folder_path text NOT NULL REFERENCES ftp_catalog_folder(path),
    file_size bigint NOT NULL CHECK (file_size >= 0),
    modified text NOT NULL,
    sha256 text,
    application_id bigint REFERENCES application(id) ON DELETE SET NULL,
    version_id bigint REFERENCES resume_version(id) ON DELETE SET NULL,
    managed_application boolean NOT NULL DEFAULT false,
    present boolean NOT NULL DEFAULT true,
    parse_queued_at timestamptz
);
CREATE INDEX ftp_catalog_file_application_idx ON ftp_catalog_file(application_id) WHERE present;

CREATE TABLE ftp_catalog_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    synced_at timestamptz NOT NULL
);

-- 目录路径和文件指纹仅由服务端投影读写。
ALTER TABLE ftp_catalog_folder ENABLE ROW LEVEL SECURITY;
ALTER TABLE ftp_catalog_file ENABLE ROW LEVEL SECURITY;
ALTER TABLE ftp_catalog_state ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ftp_catalog_folder, ftp_catalog_file, ftp_catalog_state FROM rs_app;
