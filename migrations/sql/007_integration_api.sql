-- M7: integration credentials and application-specific version history.
ALTER TABLE integration_client
    ADD COLUMN requests_per_second integer NOT NULL DEFAULT 10 CHECK (requests_per_second > 0),
    ADD COLUMN requests_per_day integer NOT NULL DEFAULT 1000 CHECK (requests_per_day > 0);

ALTER TABLE integration_job ADD COLUMN handling_department_id bigint REFERENCES department(id);
UPDATE integration_job ij SET handling_department_id = j.department_id FROM job j WHERE j.id = ij.job_id;

CREATE TABLE integration_api_key (
    client_id bigint PRIMARY KEY REFERENCES integration_client(id) ON DELETE CASCADE,
    key_digest text NOT NULL UNIQUE CHECK (key_digest ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE integration_api_key ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON integration_api_key FROM PUBLIC, rs_app;

CREATE FUNCTION authenticate_integration_key(digest text) RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT k.client_id FROM public.integration_api_key k
    JOIN public.integration_client c ON c.id = k.client_id
    WHERE k.key_digest = digest AND c.status = 'active'
$$;
REVOKE ALL ON FUNCTION authenticate_integration_key(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION authenticate_integration_key(text) TO rs_app;

CREATE TABLE application_version_history (
    application_id bigint REFERENCES application(id) ON DELETE CASCADE,
    version_id bigint REFERENCES resume_version(id) ON DELETE CASCADE,
    PRIMARY KEY (application_id, version_id)
);
INSERT INTO application_version_history
SELECT id, submitted_version_id FROM application
UNION
SELECT id, active_version_id FROM application WHERE active_version_id IS NOT NULL;
CREATE INDEX idx_application_version_history_version ON application_version_history (version_id, application_id);

CREATE FUNCTION remember_application_version() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO public.application_version_history VALUES (NEW.id, NEW.submitted_version_id)
        ON CONFLICT DO NOTHING;
    END IF;
    IF NEW.active_version_id IS NOT NULL THEN
        INSERT INTO public.application_version_history VALUES (NEW.id, NEW.active_version_id)
        ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_remember_application_version
AFTER INSERT OR UPDATE OF active_version_id ON application
FOR EACH ROW EXECUTE FUNCTION remember_application_version();

ALTER TABLE application_version_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE application_version_history FORCE ROW LEVEL SECURITY;
CREATE POLICY application_history_select ON application_version_history FOR SELECT TO rs_app
    USING (EXISTS (SELECT 1 FROM application a WHERE a.id = application_id));
GRANT SELECT ON application_version_history TO rs_app;

ALTER POLICY app_select ON application USING (
    handling_department_id = ANY (app_departments())
    OR id = ANY (app_application_ids())
    OR EXISTS (
        SELECT 1 FROM integration_job ij JOIN integration_client c ON c.id = ij.client_id
        WHERE ij.job_id = application.job_id AND ij.client_id = app_client_id()
          AND c.status = 'active'
          AND (ij.handling_department_id IS NULL OR ij.handling_department_id = application.handling_department_id)
    )
);
ALTER POLICY ver_select ON resume_version USING (
    CASE WHEN app_client_id() IS NULL THEN
        EXISTS (SELECT 1 FROM resume_document d WHERE d.id = resume_version.document_id)
    ELSE
        EXISTS (SELECT 1 FROM application_version_history h WHERE h.version_id = resume_version.id)
    END
);
