ALTER TABLE resume_profile ADD COLUMN target_role text;
-- Explicit resume disclosures are kept apart from evidence and search projections.
CREATE TABLE resume_card_private (
    version_id bigint PRIMARY KEY REFERENCES resume_version(id) ON DELETE CASCADE,
    gender text CHECK (gender IN ('male','female','other')),
    address text CHECK (char_length(address) <= 250)
);
CREATE TABLE resume_preview_page (
    version_id bigint NOT NULL REFERENCES resume_version(id) ON DELETE CASCADE,
    page_no integer NOT NULL CHECK (page_no BETWEEN 1 AND 40),
    object_key text NOT NULL,
    PRIMARY KEY (version_id,page_no)
);
ALTER TABLE resume_version ADD COLUMN avatar_key text;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['resume_card_private','resume_preview_page'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY ver_select ON %I FOR SELECT TO rs_app USING (app_client_id() IS NULL AND app_can_read_resume_version(version_id))',t);
        EXECUTE format('GRANT SELECT ON %I TO rs_app',t);
    END LOOP;
END $$;

INSERT INTO field_definition(field_key,display_name,value_type,description) VALUES
    ('profile.target_role','求职岗位','text','简历中明确填写的目标岗位'),
    ('profile.gender','性别','enum','仅允许经授权的 HR 预览，不推断'),
    ('profile.address','详细地址','text','仅允许经授权的 HR 预览'),
    ('profile.avatar','头像','text','仅允许经授权的 HR 预览'),
    ('experience.domain','经历领域','text','依据获准经历文本的关键词分类')
ON CONFLICT DO NOTHING;

INSERT INTO field_usage_policy(field_key,purpose,allowed,reason,policy_version)
SELECT f,p,true,'简历卡片业务白名单',s.policy_version
FROM search_state s CROSS JOIN (VALUES
    ('profile.name','search'),('profile.target_role','preview'),('profile.target_role','search'),
    ('experience.domain','preview'),('experience.domain','filter')
) x(f,p) WHERE s.singleton ON CONFLICT DO NOTHING;
