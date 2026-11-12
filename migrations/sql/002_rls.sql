-- 002_rls.sql · RLS 兜底层（对应 02-权限管理设计.md §2.2）
-- 会话变量约定（一律事务级 set_config(..., true)）：
--   app.departments     '3,7'        部门 HR 的授权部门
--   app.client_id       '5'          集成账号 id（机器访问）
--   app.application_ids '12,18'      限时授权的申请清单（超管调查授权）
--   app.user_id         '42'         操作人（审计用，不参与可见性）

-- 业务角色：LOGIN、非 owner、无 BYPASSRLS；应用连接池统一用此角色。
-- 超管（POSTGRES_USER 初始用户）永远绕过 RLS，绝不用于业务查询（02 §5 坑 2）。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rs_app') THEN
        CREATE ROLE rs_app LOGIN PASSWORD '${RS_APP_PASSWORD}';
    ELSE
        ALTER ROLE rs_app LOGIN PASSWORD '${RS_APP_PASSWORD}' NOBYPASSRLS;
    END IF;
END
$$;

-- ========= 会话上下文解析（STABLE，已实测 current_setting 为 STABLE）=========

CREATE OR REPLACE FUNCTION app_departments() RETURNS bigint[]
LANGUAGE sql STABLE AS
$$ SELECT COALESCE(string_to_array(NULLIF(current_setting('app.departments', true), ''), ',')::bigint[], '{}') $$;

CREATE OR REPLACE FUNCTION app_client_id() RETURNS bigint
LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.client_id', true), '')::bigint $$;

CREATE OR REPLACE FUNCTION app_application_ids() RETURNS bigint[]
LANGUAGE sql STABLE AS
$$ SELECT COALESCE(string_to_array(NULLIF(current_setting('app.application_ids', true), ''), ',')::bigint[], '{}') $$;

CREATE OR REPLACE FUNCTION app_is_super_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER AS
$$
-- 注意 NULLIF：事务级 GUC 结束后 current_setting(missing_ok) 返回空串而非 NULL（连接池复用场景）
SELECT COALESCE(NULLIF(current_setting('app.user_id', true), '')::bigint, -1) IN (SELECT user_id FROM system_admin)
$$;

-- ========= 申请可见性（核心条件，供派生表引用） =========
-- 部门 HR：handling_department ∈ app.departments
-- 集成账号：client_id 授权岗位的申请
-- 限时授权：id ∈ app.application_ids

-- application
ALTER TABLE application ENABLE ROW LEVEL SECURITY;
ALTER TABLE application FORCE ROW LEVEL SECURITY;
CREATE POLICY app_select ON application FOR SELECT TO rs_app
    USING (
        handling_department_id = ANY (app_departments())
        OR id = ANY (app_application_ids())
        OR EXISTS (
            SELECT 1 FROM integration_job ij
            WHERE ij.job_id = application.job_id
              AND ij.client_id = app_client_id()
        )
    );

-- 候选人链：candidate → document → version → 派生表，均继承申请可见性
ALTER TABLE candidate ENABLE ROW LEVEL SECURITY;
ALTER TABLE candidate FORCE ROW LEVEL SECURITY;
-- 注意：策略内不可引用 candidate 自身（merged_into 合并可见性放应用层），否则策略递归报错
CREATE POLICY cand_select ON candidate FOR SELECT TO rs_app
    USING (
        EXISTS (
            SELECT 1 FROM application a
            WHERE a.candidate_id = candidate.id
              AND (
                    a.handling_department_id = ANY (app_departments())
                 OR a.id = ANY (app_application_ids())
                 OR EXISTS (SELECT 1 FROM integration_job ij
                            WHERE ij.job_id = a.job_id AND ij.client_id = app_client_id())
              )
        )
    );

ALTER TABLE resume_document ENABLE ROW LEVEL SECURITY;
ALTER TABLE resume_document FORCE ROW LEVEL SECURITY;
CREATE POLICY doc_select ON resume_document FOR SELECT TO rs_app
    USING (EXISTS (SELECT 1 FROM candidate c WHERE c.id = resume_document.candidate_id));

ALTER TABLE resume_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE resume_version FORCE ROW LEVEL SECURITY;
CREATE POLICY ver_select ON resume_version FOR SELECT TO rs_app
    USING (EXISTS (SELECT 1 FROM resume_document d WHERE d.id = resume_version.document_id));

-- 版本派生表（统一模板：EXISTS resume_version）
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'resume_record','resume_profile','resume_contact','resume_education',
        'resume_work','resume_project','resume_skill','resume_source_span',
        'resume_evidence_link','resume_extra_value','resume_field_issue',
        'resume_record_source','resume_search_record'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY ver_select ON %I FOR SELECT TO rs_app
             USING (EXISTS (
                 SELECT 1 FROM resume_version v
                 WHERE v.id = %I.version_id
             ))', t, t);
    END LOOP;
END
$$;

-- 评审：本部门申请可见
ALTER TABLE application_review ENABLE ROW LEVEL SECURITY;
ALTER TABLE application_review FORCE ROW LEVEL SECURITY;
CREATE POLICY review_select ON application_review FOR SELECT TO rs_app
    USING (EXISTS (
        SELECT 1 FROM application a
        WHERE a.id = application_review.application_id
          AND a.handling_department_id = ANY (app_departments())
    ));

-- 审计：读仅超管；写（INSERT）由应用执行
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_select ON audit_log FOR SELECT TO rs_app
    USING (app_is_super_admin());
CREATE POLICY audit_insert ON audit_log FOR INSERT TO rs_app
    WITH CHECK (true);

-- 主数据与配置表：业务角色可读，写仅超管应用路径
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'department','app_user','job','skill','skill_alias','school','school_alias',
        'school_tag','field_definition','field_alias','field_usage_policy',
        'search_term_group','search_term','search_state',
        'integration_client','integration_job','system_admin','department_admin'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY read_all ON %I FOR SELECT TO rs_app USING (true)', t);
        EXECUTE format(
            'CREATE POLICY admin_write ON %I FOR INSERT TO rs_app WITH CHECK (app_is_super_admin())', t);
        EXECUTE format(
            'CREATE POLICY admin_update ON %I FOR UPDATE TO rs_app USING (app_is_super_admin()) WITH CHECK (app_is_super_admin())', t);
    END LOOP;
END
$$;

-- ========= 授权 =========
GRANT USAGE ON SCHEMA public TO rs_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO rs_app;
GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rs_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rs_app;
-- 迁移与种子由 owner（postgres）执行，不经 rs_app
