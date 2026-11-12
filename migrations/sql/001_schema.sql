-- 001_schema.sql · 全量建表（对应 01-数据库设计.md v2.1，37 张表）
-- 顺序：3.1 主数据授权 → 3.2 候选人/文件/版本 → 3.3 申请 → resume_record
--       → 3.5 技能/学校主数据 → 3.4 明细表 → 3.6 证据 → 3.7 字段元数据
--       → 3.8 长尾/异常 → 3.9 检索投影与词典 → 3.10 评审审计 → 3.11 集成账号

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ========= 3.1 主数据与授权 =========

CREATE TABLE department (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    display_name  text NOT NULL,
    email         text NOT NULL UNIQUE,
    password_hash text NOT NULL DEFAULT '',          -- v1: 本地会话登录
    perm_version  bigint NOT NULL DEFAULT 1,
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE system_admin (
    user_id     bigint PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
    granted_by  bigint REFERENCES app_user(id),
    granted_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE department_admin (
    department_id  bigint NOT NULL REFERENCES department(id) ON DELETE CASCADE,
    user_id        bigint NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    granted_by     bigint REFERENCES app_user(id),
    granted_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (department_id, user_id)
);

CREATE TABLE job (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    department_id bigint NOT NULL REFERENCES department(id),
    title         text NOT NULL,
    jd            text,
    status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','paused','closed')),
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_job_department ON job(department_id) WHERE status = 'open';

-- ========= 3.2 候选人与简历文件 =========

CREATE TABLE candidate (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    merged_into bigint REFERENCES candidate(id)
);

CREATE TABLE resume_document (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id bigint NOT NULL REFERENCES candidate(id),
    object_key   text NOT NULL,
    preview_key  text,
    mime_type    text NOT NULL,
    sha256       text NOT NULL,
    file_size    integer,
    has_photo    boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, sha256)
);

CREATE TABLE resume_version (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id     bigint NOT NULL REFERENCES resume_document(id),
    version_no      integer NOT NULL,
    parser_version  text NOT NULL,
    status          text NOT NULL DEFAULT 'parsing'
                    CHECK (status IN ('parsing','parsed','failed','published','superseded')),
    fail_reason     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    published_at    timestamptz,
    UNIQUE (document_id, version_no)
);
CREATE INDEX idx_version_status ON resume_version(status);

-- ========= 3.3 申请 =========

CREATE TABLE application (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id                   bigint NOT NULL REFERENCES job(id),
    candidate_id             bigint NOT NULL REFERENCES candidate(id),
    submitted_department_id  bigint NOT NULL REFERENCES department(id),
    handling_department_id   bigint NOT NULL REFERENCES department(id),
    submitted_version_id     bigint NOT NULL REFERENCES resume_version(id),
    active_version_id        bigint REFERENCES resume_version(id),
    status                   text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending','active','on_hold','closed')),
    submitted_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_application_handling ON application(handling_department_id, status);
CREATE INDEX idx_application_active_version ON application(active_version_id);

-- active_version 必须属于该申请候选人同一简历链且状态为 published
CREATE FUNCTION check_active_version() RETURNS trigger AS $$
BEGIN
    IF NEW.active_version_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM resume_version rv
        JOIN resume_document rd ON rd.id = rv.document_id
        WHERE rv.id = NEW.active_version_id
          AND rv.status = 'published'
          AND rd.candidate_id = NEW.candidate_id
    ) THEN
        RAISE EXCEPTION 'active_version_id % is not a published version of candidate %',
            NEW.active_version_id, NEW.candidate_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_active_version
    BEFORE INSERT OR UPDATE OF active_version_id ON application
    FOR EACH ROW EXECUTE FUNCTION check_active_version();

-- ========= 统一条目登记表（先建，明细表引用它） =========

CREATE TABLE resume_record (
    version_id  bigint NOT NULL REFERENCES resume_version(id) ON DELETE CASCADE,
    record_no   integer NOT NULL,
    kind        text NOT NULL CHECK (kind IN
                ('profile','contact','education','work','project','skill','summary','other','extension')),
    PRIMARY KEY (version_id, record_no)
);

-- ========= 3.5 技能与学校主数据 =========

CREATE TABLE skill (
    id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name  text NOT NULL UNIQUE
);
CREATE TABLE skill_alias (
    alias_normalized text NOT NULL PRIMARY KEY,
    skill_id         bigint NOT NULL REFERENCES skill(id) ON DELETE CASCADE
);

CREATE TABLE school (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name text NOT NULL UNIQUE
);
CREATE TABLE school_alias (
    alias_normalized text NOT NULL,
    school_id        bigint NOT NULL REFERENCES school(id) ON DELETE CASCADE,
    PRIMARY KEY (alias_normalized, school_id)
);
CREATE TABLE school_tag (
    school_id    bigint NOT NULL REFERENCES school(id) ON DELETE CASCADE,
    tag_code     text NOT NULL,
    scope        text NOT NULL DEFAULT 'school' CHECK (scope IN ('school','subject')),
    subject      text,
    list_version text NOT NULL,
    source       text NOT NULL,
    CHECK ((scope = 'school' AND subject IS NULL) OR (scope = 'subject' AND subject IS NOT NULL)),
    UNIQUE NULLS NOT DISTINCT (school_id, tag_code, scope, subject, list_version)
);

-- ========= 3.4 结构化明细表 =========

CREATE TABLE resume_profile (
    version_id    bigint NOT NULL,
    record_no     integer NOT NULL,
    name          text,
    city          text,
    photo_present boolean NOT NULL DEFAULT false,
    PRIMARY KEY (version_id, record_no),
    FOREIGN KEY (version_id, record_no) REFERENCES resume_record(version_id, record_no) ON DELETE CASCADE
);

CREATE TABLE resume_contact (
    version_id  bigint NOT NULL,
    record_no   integer NOT NULL,
    type        text NOT NULL CHECK (type IN ('phone','email','url','other')),
    value       text NOT NULL,
    PRIMARY KEY (version_id, record_no),
    FOREIGN KEY (version_id, record_no) REFERENCES resume_record(version_id, record_no) ON DELETE CASCADE
);

CREATE TABLE resume_education (
    version_id       bigint NOT NULL,
    record_no        integer NOT NULL,
    school_raw       text NOT NULL,
    school_id        bigint REFERENCES school(id),
    degree_level     text CHECK (degree_level IN
                     ('phd','master','bachelor','associate','high_school','other')),
    major            text,
    start_date       date,
    end_date         date,
    start_period_raw text,
    end_period_raw   text,
    PRIMARY KEY (version_id, record_no),
    FOREIGN KEY (version_id, record_no) REFERENCES resume_record(version_id, record_no) ON DELETE CASCADE,
    CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date)
);
CREATE INDEX idx_resume_education_filter ON resume_education(degree_level, school_id, version_id);

CREATE TABLE resume_work (
    version_id       bigint NOT NULL,
    record_no        integer NOT NULL,
    company          text NOT NULL,
    title            text,
    employment_type  text CHECK (employment_type IN
                     ('full_time','part_time','internship','contract','other')),
    start_date       date,
    end_date         date,
    start_period_raw text,
    end_period_raw   text,
    is_current       boolean NOT NULL DEFAULT false,
    description      text,
    PRIMARY KEY (version_id, record_no),
    FOREIGN KEY (version_id, record_no) REFERENCES resume_record(version_id, record_no) ON DELETE CASCADE,
    CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date),
    CHECK (NOT (is_current AND end_date IS NOT NULL))
);

CREATE TABLE resume_project (
    version_id       bigint NOT NULL,
    record_no        integer NOT NULL,
    project_name     text NOT NULL,
    role             text,
    start_date       date,
    end_date         date,
    start_period_raw text,
    end_period_raw   text,
    is_current       boolean NOT NULL DEFAULT false,
    description      text,
    PRIMARY KEY (version_id, record_no),
    FOREIGN KEY (version_id, record_no) REFERENCES resume_record(version_id, record_no) ON DELETE CASCADE,
    CHECK (start_date IS NULL OR end_date IS NULL OR start_date <= end_date)
);

CREATE TABLE resume_skill (
    version_id       bigint NOT NULL,
    record_no        integer NOT NULL,
    skill_id         bigint NOT NULL REFERENCES skill(id),
    parent_record_no integer,
    PRIMARY KEY (version_id, record_no),
    FOREIGN KEY (version_id, record_no) REFERENCES resume_record(version_id, record_no) ON DELETE CASCADE,
    FOREIGN KEY (version_id, parent_record_no) REFERENCES resume_record(version_id, record_no)
);
CREATE INDEX idx_resume_skill_filter ON resume_skill(skill_id, version_id, parent_record_no);

-- ========= 3.6 原文片段与证据链 =========

CREATE TABLE resume_source_span (
    version_id        bigint NOT NULL REFERENCES resume_version(id) ON DELETE CASCADE,
    span_no           integer NOT NULL,
    span_kind         text NOT NULL DEFAULT 'text'
                      CHECK (span_kind IN ('text','heading','table','photo')),
    raw_text          text,
    locator           jsonb NOT NULL,
    extraction_method text NOT NULL CHECK (extraction_method IN
                      ('pdf_text','ocr','docx_paragraph','docx_table','layout_detection','manual')),
    is_sensitive      boolean NOT NULL DEFAULT false,
    ocr_confidence    real,
    reading_order     integer NOT NULL,
    PRIMARY KEY (version_id, span_no),
    CHECK (span_kind <> 'photo' OR raw_text IS NULL),
    CHECK (ocr_confidence IS NULL OR ocr_confidence BETWEEN 0 AND 1),
    CHECK (extraction_method <> 'ocr' OR ocr_confidence IS NOT NULL)
);

CREATE TABLE resume_evidence_link (
    version_id   bigint NOT NULL,
    record_no    integer NOT NULL,
    field_key    text NOT NULL,
    span_no      integer NOT NULL,
    start_offset integer,
    end_offset   integer,
    PRIMARY KEY (version_id, record_no, field_key, span_no),
    FOREIGN KEY (version_id, record_no) REFERENCES resume_record(version_id, record_no) ON DELETE CASCADE,
    FOREIGN KEY (version_id, span_no) REFERENCES resume_source_span(version_id, span_no) ON DELETE CASCADE
);

-- ========= 3.7 字段元数据与使用策略 =========

CREATE TABLE field_definition (
    field_key    text PRIMARY KEY,
    display_name text NOT NULL,
    value_type   text NOT NULL CHECK (value_type IN
                 ('text','number','date','period','boolean','enum','range','location','organization','unknown')),
    description  text,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','pending','retired'))
);

CREATE TABLE field_alias (
    alias_normalized text NOT NULL PRIMARY KEY,
    field_key        text NOT NULL REFERENCES field_definition(field_key) ON DELETE CASCADE
);

CREATE TABLE field_usage_policy (
    field_key      text NOT NULL REFERENCES field_definition(field_key) ON DELETE CASCADE,
    purpose        text NOT NULL CHECK (purpose IN
                   ('preview','filter','sort','search','export')),
    allowed        boolean NOT NULL DEFAULT false,
    reason         text,
    policy_version integer NOT NULL,
    granted_by     bigint REFERENCES app_user(id),
    PRIMARY KEY (field_key, purpose, policy_version)
);

-- ========= 3.8 长尾扩展值与抽取异常 =========

CREATE TABLE resume_extra_value (
    version_id   bigint NOT NULL REFERENCES resume_version(id) ON DELETE CASCADE,
    record_no    integer NOT NULL,
    field_key    text REFERENCES field_definition(field_key),
    raw_label    text NOT NULL,
    payload      jsonb NOT NULL,
    span_no      integer NOT NULL,
    rule_version text NOT NULL,
    PRIMARY KEY (version_id, record_no),
    FOREIGN KEY (version_id, span_no) REFERENCES resume_source_span(version_id, span_no)
);
CREATE INDEX idx_extra_raw_label ON resume_extra_value(raw_label);

CREATE TABLE resume_field_issue (
    version_id  bigint NOT NULL REFERENCES resume_version(id) ON DELETE CASCADE,
    record_no   integer NOT NULL,
    field_key   text NOT NULL,
    span_no     integer,
    reason      text NOT NULL CHECK (reason IN
                ('not_found','low_confidence','conflict','ambiguous_school','ambiguous_skill','pending_review')),
    PRIMARY KEY (version_id, record_no, field_key),
    FOREIGN KEY (version_id, span_no) REFERENCES resume_source_span(version_id, span_no)
);

-- ========= 3.9 经历来源与文本检索投影 =========

CREATE TABLE resume_record_source (
    version_id   bigint NOT NULL,
    record_no    integer NOT NULL,
    part_no      integer NOT NULL,
    span_no      integer NOT NULL,
    field_key    text REFERENCES field_definition(field_key),
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset   integer NOT NULL,
    PRIMARY KEY (version_id, record_no, part_no),
    FOREIGN KEY (version_id, record_no) REFERENCES resume_record(version_id, record_no) ON DELETE CASCADE,
    FOREIGN KEY (version_id, span_no) REFERENCES resume_source_span(version_id, span_no) ON DELETE CASCADE,
    CHECK (end_offset > start_offset)
);

CREATE TABLE resume_search_record (
    version_id     bigint NOT NULL,
    record_no      integer NOT NULL,
    policy_version integer NOT NULL,
    text_norm      text NOT NULL,
    PRIMARY KEY (version_id, record_no, policy_version),
    FOREIGN KEY (version_id, record_no) REFERENCES resume_record(version_id, record_no) ON DELETE CASCADE
);
CREATE INDEX idx_search_record_trgm ON resume_search_record USING gin (text_norm gin_trgm_ops);

CREATE TABLE search_state (
    singleton       boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    search_revision bigint NOT NULL DEFAULT 1,
    policy_version  integer NOT NULL DEFAULT 1
);
INSERT INTO search_state (singleton) VALUES (true);

CREATE TABLE search_term_group (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dict_version integer NOT NULL,
    section_type text,
    enabled      boolean NOT NULL DEFAULT true,
    created_by   bigint REFERENCES app_user(id),
    UNIQUE (id, dict_version)
);

CREATE TABLE search_term (
    group_id   bigint NOT NULL REFERENCES search_term_group(id) ON DELETE CASCADE,
    term_norm  text NOT NULL,
    is_primary boolean NOT NULL DEFAULT false,
    PRIMARY KEY (group_id, term_norm)
);
CREATE INDEX idx_search_term_lookup ON search_term(term_norm);

-- ========= 3.10 评审与审计 =========

CREATE TABLE application_review (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_id    bigint NOT NULL REFERENCES application(id) ON DELETE CASCADE,
    resume_version_id bigint NOT NULL REFERENCES resume_version(id),
    author_id         bigint NOT NULL REFERENCES app_user(id),
    decision          text NOT NULL CHECK (decision IN ('advance','reject','hold')),
    comment           text,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_review_application ON application_review(application_id, created_at);

-- 评审 author 必须是该申请当前处理部门的 HR（或超管授权流程产生）
CREATE FUNCTION check_review_author() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM application a
        JOIN department_admin da
          ON da.department_id = a.handling_department_id
        WHERE a.id = NEW.application_id
          AND da.user_id = NEW.author_id
    ) THEN
        RAISE EXCEPTION 'review author % is not an HR of the handling department',
            NEW.author_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_review_author
    BEFORE INSERT ON application_review
    FOR EACH ROW EXECUTE FUNCTION check_review_author();

CREATE TABLE audit_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id    bigint REFERENCES app_user(id),
    action      text NOT NULL,
    target_type text NOT NULL,
    target_id   text NOT NULL,
    result      text NOT NULL DEFAULT 'ok',
    detail      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_actor_time ON audit_log(actor_id, created_at);

-- ========= 3.11 集成账号 =========

CREATE TABLE integration_client (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           text NOT NULL UNIQUE,
    status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    allowed_fields text[] NOT NULL,
    perm_version   bigint NOT NULL DEFAULT 1,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE integration_job (
    client_id bigint NOT NULL REFERENCES integration_client(id) ON DELETE CASCADE,
    job_id    bigint NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    PRIMARY KEY (client_id, job_id)
);
