-- 023_talent_pool.sql · 人才库（Talent Pool）与文件服务器投递登记
-- 定位：简历库 = 原料池（自动流入、无流程）；人才库 = 精加工池（有跟进/状态/面试）。
-- 「放入人才库」是复制，候选人可同时存在于简历库与人才库。
-- 文件服务器本机只保留接口方法（resume_dropfile 表 + 预留接口），定时扫描在另一台机器实现。

-- ========= 人才库条目 =========

CREATE TABLE talent (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id          bigint NOT NULL REFERENCES candidate(id),
    active_version_id     bigint NOT NULL REFERENCES resume_version(id),
    status                text NOT NULL DEFAULT 'interviewing'
                          CHECK (status IN ('interviewing','hired','rejected','blacklist','custom')),
    status_label          text,                          -- 手写自定义状态时的文案（status='custom' 时必填）
    source_application_id bigint REFERENCES application(id),  -- 从哪条简历库申请复制而来（可空）
    entered_by            bigint REFERENCES app_user(id),
    entered_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'custom') = (status_label IS NOT NULL AND length(btrim(status_label)) BETWEEN 1 AND 40))
);
CREATE UNIQUE INDEX talent_candidate_unique ON talent(candidate_id);
CREATE INDEX talent_status ON talent(status);

-- ========= 人才库跟进（@同事 / 附件 / 软删除） =========

CREATE TABLE talent_followup (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    talent_id       bigint NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    author_id       bigint REFERENCES app_user(id) ON DELETE SET NULL,
    author_name     text NOT NULL,
    body            text NOT NULL CHECK (length(btrim(body)) BETWEEN 1 AND 1000),
    status          text,                              -- 记录本次跟进后的状态快照
    attachment_name text,
    attachment_data bytea,
    mentions        bigint[],                         -- @同事的用户 id 数组
    deleted_at      timestamptz,                      -- 软删除
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((attachment_name IS NULL) = (attachment_data IS NULL)),
    CHECK (octet_length(attachment_data) <= 5242880)
);
CREATE INDEX talent_followup_latest ON talent_followup(talent_id, id DESC);

-- ========= 人才库面试安排 =========

CREATE TABLE talent_interview (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    talent_id       bigint NOT NULL REFERENCES talent(id) ON DELETE CASCADE,
    scheduled_at    timestamptz NOT NULL,
    interviewer_ids bigint[],
    note            text,
    created_by      bigint REFERENCES app_user(id),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX talent_interview_talent ON talent_interview(talent_id, scheduled_at);

-- ========= 文件服务器投递登记（本机仅保留接口方法） =========

CREATE TABLE resume_dropfile (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    object_key   text NOT NULL UNIQUE,                -- 文件服务器上的相对路径
    sha256       text NOT NULL,
    mime_type    text NOT NULL,
    file_size    integer,
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','processing','imported','failed','duplicate')),
    fail_reason  text,
    candidate_id bigint REFERENCES candidate(id),     -- 导入成功后回填
    created_at   timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE UNIQUE INDEX dropfile_sha ON resume_dropfile(sha256);

-- ========= RLS（读走 rs_app，写走 owner_engine） =========

ALTER TABLE talent ENABLE ROW LEVEL SECURITY;
ALTER TABLE talent FORCE ROW LEVEL SECURITY;
CREATE POLICY talent_select ON talent FOR SELECT TO rs_app USING (
    app_client_id() IS NULL AND (
        app_is_super_admin() OR EXISTS (
            SELECT 1 FROM application a WHERE a.candidate_id = talent.candidate_id
              AND a.deleted_at IS NULL AND a.handling_department_id = ANY (app_departments())
        )
    )
);

ALTER TABLE talent_followup ENABLE ROW LEVEL SECURITY;
ALTER TABLE talent_followup FORCE ROW LEVEL SECURITY;
CREATE POLICY talent_followup_select ON talent_followup FOR SELECT TO rs_app USING (
    app_client_id() IS NULL AND EXISTS (
        SELECT 1 FROM talent t WHERE t.id = talent_followup.talent_id
    )
);

ALTER TABLE talent_interview ENABLE ROW LEVEL SECURITY;
ALTER TABLE talent_interview FORCE ROW LEVEL SECURITY;
CREATE POLICY talent_interview_select ON talent_interview FOR SELECT TO rs_app USING (
    app_client_id() IS NULL AND EXISTS (
        SELECT 1 FROM talent t WHERE t.id = talent_interview.talent_id
    )
);

-- 文件服务器登记表：仅供超管读（普通 HR 不直接看投递登记，由导入流程转成简历库条目后可见）
ALTER TABLE resume_dropfile ENABLE ROW LEVEL SECURITY;
ALTER TABLE resume_dropfile FORCE ROW LEVEL SECURITY;
CREATE POLICY dropfile_select ON resume_dropfile FOR SELECT TO rs_app USING (
    app_client_id() IS NULL AND app_is_super_admin()
);

GRANT SELECT ON talent, talent_followup, talent_interview, resume_dropfile TO rs_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rs_app;
