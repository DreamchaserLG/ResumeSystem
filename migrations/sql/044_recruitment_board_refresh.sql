-- 用业务记录的事务提交刷新看板，沿用存量环境的发布机制。
DO $board$
BEGIN
IF to_regclass('public.recruitment_board_revision') IS NOT NULL THEN
    RETURN;
END IF;
CREATE TABLE recruitment_board_revision (
    singleton boolean PRIMARY KEY CHECK(singleton),
    revision bigint NOT NULL DEFAULT 0,
    last_transaction_id xid8
);
INSERT INTO recruitment_board_revision(singleton) VALUES (true);
ALTER TABLE recruitment_board_revision ENABLE ROW LEVEL SECURITY;
CREATE POLICY recruitment_board_revision_admin ON recruitment_board_revision FOR SELECT TO rs_app
    USING ((SELECT app_client_id()) IS NULL AND (SELECT app_is_super_admin()));
GRANT SELECT ON recruitment_board_revision TO rs_app;

CREATE TABLE recruitment_board_changes (transaction_id xid8 PRIMARY KEY);
ALTER TABLE recruitment_board_changes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON recruitment_board_changes FROM PUBLIC,rs_app;

CREATE FUNCTION mark_recruitment_board_changed() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
    INSERT INTO recruitment_board_changes VALUES (pg_current_xact_id()) ON CONFLICT DO NOTHING;
    RETURN NULL;
END
$$;
REVOKE ALL ON FUNCTION mark_recruitment_board_changed() FROM PUBLIC,rs_app;

CREATE FUNCTION publish_recruitment_board_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE next_revision bigint;
BEGIN
    UPDATE recruitment_board_revision SET revision=revision+1,last_transaction_id=NEW.transaction_id
    WHERE singleton AND last_transaction_id IS DISTINCT FROM NEW.transaction_id
    RETURNING revision INTO next_revision;
    IF FOUND THEN
        PERFORM pg_notify('recruitment_board_updates','');
    END IF;
    DELETE FROM recruitment_board_changes WHERE transaction_id=NEW.transaction_id;
    RETURN NULL;
END
$$;
REVOKE ALL ON FUNCTION publish_recruitment_board_revision() FROM PUBLIC,rs_app;
CREATE CONSTRAINT TRIGGER recruitment_board_change_commit AFTER INSERT ON recruitment_board_changes
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION publish_recruitment_board_revision();

DO $$
DECLARE source record;
BEGIN
    FOR source IN SELECT * FROM (VALUES
        ('department','name,status'),
        ('job','title,department_id,status,planned_headcount,hidden'),
        ('application','candidate_id,job_id,handling_department_id,submitted_at,deleted_at'),
        ('talent','candidate_id,source_application_id,status,entered_at'),
        ('talent_interview','talent_id,created_at')
    ) fields(relation,columns) LOOP
        EXECUTE format('CREATE TRIGGER recruitment_board_insert_delete AFTER INSERT OR DELETE ON %I
            FOR EACH ROW EXECUTE FUNCTION mark_recruitment_board_changed()',source.relation);
        EXECUTE format('CREATE TRIGGER recruitment_board_update AFTER UPDATE OF %s ON %I
            FOR EACH ROW WHEN ((OLD.%s) IS DISTINCT FROM (NEW.%s))
            EXECUTE FUNCTION mark_recruitment_board_changed()',
            source.columns,source.relation,replace(source.columns,',',',OLD.'),replace(source.columns,',',',NEW.'));
    END LOOP;
END
$$;
CREATE TRIGGER recruitment_board_outcome_recorded AFTER INSERT ON audit_log
    FOR EACH ROW WHEN (NEW.result='ok' AND (
        (NEW.action='recruitment_stage_change' AND NEW.target_type='application' AND NEW.detail->'to'->>'code'='hired')
        OR (NEW.action='talent_status' AND NEW.target_type='talent' AND NEW.detail->>'status' IN ('pending_onboard','hired'))
    )) EXECUTE FUNCTION mark_recruitment_board_changed();
END
$board$;
