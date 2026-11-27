-- 招聘看板只对超级管理员开放，但申请表的 RLS 策略没有超管分支，
-- 导致超管（无部门授权）在看板上永远看到 0 条申请。
-- 008 版本覆写 app_select 时遗漏了 app_is_super_admin()，这里补回。
ALTER POLICY app_select ON application USING (
    app_is_super_admin() OR (
        deleted_at IS NULL AND (
            handling_department_id = ANY (app_departments())
            OR id = ANY (app_application_ids())
            OR EXISTS (
                SELECT 1 FROM integration_job ij JOIN integration_client c ON c.id = ij.client_id
                WHERE ij.job_id = application.job_id AND ij.client_id = app_client_id()
                  AND c.status = 'active'
                  AND (ij.handling_department_id IS NULL OR ij.handling_department_id = application.handling_department_id)
            )
        )
    )
);
