CREATE TABLE user_profile (
    user_id bigint PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    job_title text NOT NULL DEFAULT '' CHECK (char_length(job_title) <= 80),
    phone text NOT NULL DEFAULT '' CHECK (char_length(phone) <= 32),
    office text NOT NULL DEFAULT '' CHECK (char_length(office) <= 80),
    bio text NOT NULL DEFAULT '' CHECK (char_length(bio) <= 300),
    avatar_webp bytea CHECK (octet_length(avatar_webp) <= 262144),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE user_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profile FORCE ROW LEVEL SECURITY;
CREATE POLICY own_user_profile ON user_profile FOR SELECT TO rs_app
    USING (user_id = nullif(current_setting('app.user_id',true),'')::bigint);
GRANT SELECT ON user_profile TO rs_app;
