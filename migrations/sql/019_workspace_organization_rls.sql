ALTER TABLE recruiting_company ENABLE ROW LEVEL SECURITY;
ALTER TABLE recruiting_company FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_company_directory ON recruiting_company FOR SELECT TO rs_app USING (
    app_client_id() IS NULL AND (
        app_is_super_admin() OR EXISTS (
            SELECT 1 FROM department d WHERE d.company_id=recruiting_company.id AND d.id=ANY(app_departments())
        )
    )
);
