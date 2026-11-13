CREATE TABLE job_interview_round (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id bigint NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (name = btrim(name) AND char_length(name) BETWEEN 1 AND 40),
    position smallint NOT NULL CHECK (position BETWEEN 1 AND 12),
    UNIQUE (job_id,id),
    UNIQUE (job_id,position) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (job_id,name) DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE job_interview_round ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_interview_round FORCE ROW LEVEL SECURITY;
CREATE POLICY round_read ON job_interview_round FOR SELECT TO rs_app USING (
    app_is_super_admin() OR EXISTS (
        SELECT 1 FROM job j WHERE j.id=job_interview_round.job_id AND j.department_id=ANY(app_departments())
    ) OR EXISTS (SELECT 1 FROM application a WHERE a.job_id=job_interview_round.job_id AND app_client_id() IS NULL)
);
GRANT SELECT ON job_interview_round TO rs_app;
INSERT INTO job_interview_round(job_id,name,position)
SELECT j.id,r.name,r.position FROM job j CROSS JOIN (VALUES ('一面',1),('二面',2),('终面',3)) r(name,position);

ALTER TABLE application ADD COLUMN submitted_job_id bigint REFERENCES job(id);
UPDATE application SET submitted_job_id=job_id;
ALTER TABLE application ALTER COLUMN submitted_job_id SET NOT NULL;
ALTER TABLE application ADD COLUMN recruitment_stage text NOT NULL DEFAULT 'review',
    ADD COLUMN interview_round_id bigint,
    ADD COLUMN workflow_version integer NOT NULL DEFAULT 1 CHECK (workflow_version>0),
    ADD CONSTRAINT valid_recruitment_stage CHECK (
        recruitment_stage IN ('review','interview','rejected','hired')
        AND (recruitment_stage='interview')=(interview_round_id IS NOT NULL)
    ),
    ADD CONSTRAINT application_current_job_round FOREIGN KEY(job_id,interview_round_id)
        REFERENCES job_interview_round(job_id,id);
UPDATE application SET recruitment_stage='rejected' WHERE status='closed';
CREATE INDEX idx_application_job_stage ON application(job_id,recruitment_stage,id) WHERE deleted_at IS NULL;
CREATE INDEX idx_application_round ON application(interview_round_id) WHERE interview_round_id IS NOT NULL;
CREATE INDEX idx_application_candidate_job ON application(candidate_id,job_id) WHERE deleted_at IS NULL;

CREATE FUNCTION sync_application_workflow() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        NEW.submitted_job_id := coalesce(NEW.submitted_job_id, NEW.job_id);
    ELSE
        IF NEW.submitted_job_id IS DISTINCT FROM OLD.submitted_job_id THEN
            RAISE EXCEPTION 'submitted job is immutable';
        END IF;
        IF ROW(NEW.recruitment_stage,NEW.interview_round_id,NEW.job_id) IS DISTINCT FROM
           ROW(OLD.recruitment_stage,OLD.interview_round_id,OLD.job_id) THEN
            NEW.workflow_version := OLD.workflow_version+1;
            -- Keep the v1 integration contract when a recruitment stage changes.
            NEW.status := CASE NEW.recruitment_stage WHEN 'review' THEN 'pending'
                         WHEN 'rejected' THEN 'closed' ELSE 'active' END;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER sync_application_workflow
BEFORE INSERT OR UPDATE ON application FOR EACH ROW EXECUTE FUNCTION sync_application_workflow();

-- Admins may see counts across departments, without access to individual applications.
CREATE FUNCTION admin_job_application_counts() RETURNS TABLE(job_id bigint, application_count bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT a.job_id,count(*) FROM public.application a
    WHERE a.deleted_at IS NULL AND EXISTS (
        SELECT 1 FROM public.system_admin sa
        WHERE sa.user_id=nullif(current_setting('app.user_id', true),'')::bigint
    ) GROUP BY a.job_id
$$;
REVOKE ALL ON FUNCTION admin_job_application_counts() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION admin_job_application_counts() TO rs_app;
