-- Wake the local indexer only after the publishing transaction commits.
CREATE FUNCTION notify_retrieval_refresh() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('resume_retrieval_refresh', '');
    RETURN NULL;
END;
$$;

CREATE TRIGGER application_retrieval_refresh
AFTER INSERT OR UPDATE OF active_version_id, status, deleted_at ON application
FOR EACH STATEMENT EXECUTE FUNCTION notify_retrieval_refresh();

CREATE TRIGGER job_retrieval_refresh
AFTER INSERT OR UPDATE OF title, jd, requirements, required_skills, experience_tags ON job
FOR EACH STATEMENT EXECUTE FUNCTION notify_retrieval_refresh();

CREATE TRIGGER posting_retrieval_refresh
AFTER INSERT OR UPDATE OR DELETE ON job_posting_reference
FOR EACH STATEMENT EXECUTE FUNCTION notify_retrieval_refresh();
