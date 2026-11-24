CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE resume_retrieval_index (
    version_id bigint NOT NULL REFERENCES resume_version(id) ON DELETE CASCADE,
    policy_version integer NOT NULL,
    slice_version text NOT NULL,
    model_version text NOT NULL,
    source_hash text NOT NULL,
    PRIMARY KEY (version_id,policy_version,slice_version,model_version)
);

CREATE TABLE resume_retrieval_chunk (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version_id bigint NOT NULL REFERENCES resume_version(id) ON DELETE CASCADE,
    record_no integer NOT NULL,
    chunk_no integer NOT NULL CHECK (chunk_no > 0),
    evidence_kind text NOT NULL CHECK (evidence_kind IN ('work','internship','project','campus_project')),
    policy_version integer NOT NULL,
    slice_version text NOT NULL,
    field_keys text[] NOT NULL CHECK (cardinality(field_keys) > 0),
    body text NOT NULL CHECK (body <> ''),
    token_count integer NOT NULL CHECK (token_count BETWEEN 1 AND 512),
    UNIQUE (version_id,record_no,policy_version,slice_version,chunk_no),
    UNIQUE (id,version_id,record_no),
    FOREIGN KEY (version_id,record_no) REFERENCES resume_record(version_id,record_no) ON DELETE CASCADE
);

CREATE TABLE resume_chunk_source (
    chunk_id bigint NOT NULL,
    version_id bigint NOT NULL,
    record_no integer NOT NULL,
    source_no integer NOT NULL CHECK (source_no > 0),
    part_no integer NOT NULL,
    span_no integer NOT NULL,
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL CHECK (end_offset > start_offset),
    PRIMARY KEY (chunk_id,source_no),
    FOREIGN KEY (chunk_id,version_id,record_no) REFERENCES resume_retrieval_chunk(id,version_id,record_no) ON DELETE CASCADE,
    FOREIGN KEY (version_id,record_no,part_no) REFERENCES resume_record_source(version_id,record_no,part_no) ON DELETE CASCADE,
    FOREIGN KEY (version_id,span_no) REFERENCES resume_source_span(version_id,span_no) ON DELETE CASCADE
);

CREATE TABLE resume_chunk_embedding (
    chunk_id bigint NOT NULL REFERENCES resume_retrieval_chunk(id) ON DELETE CASCADE,
    model_version text NOT NULL,
    input_hash text NOT NULL,
    embedding vector(1024) NOT NULL,
    PRIMARY KEY (chunk_id,model_version)
);

CREATE VIEW job_retrieval_input WITH (security_invoker=true) AS
SELECT id, department_id, jsonb_build_object('title',title,'jd',jd,'requirements',requirements,
    'required_skills',required_skills,'experience_tags',experience_tags) AS snapshot
FROM job;

CREATE TABLE job_retrieval_approval (
    job_id bigint PRIMARY KEY REFERENCES job(id) ON DELETE CASCADE,
    snapshot jsonb NOT NULL,
    rules jsonb NOT NULL
);

CREATE TABLE job_retrieval_index (
    job_id bigint NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    model_version text NOT NULL,
    snapshot jsonb NOT NULL,
    rules jsonb NOT NULL,
    definition_version text NOT NULL,
    source text NOT NULL CHECK (source IN ('title','jd','reviewed')),
    PRIMARY KEY (job_id,model_version)
);

CREATE TABLE job_requirement (
    job_id bigint NOT NULL,
    model_version text NOT NULL,
    requirement_no integer NOT NULL CHECK (requirement_no > 0),
    kind text NOT NULL CHECK (kind IN ('role','duty','skill','degree','months')),
    original_text text NOT NULL CHECK (original_text <> ''),
    mandatory boolean NOT NULL DEFAULT false,
    weight real NOT NULL CHECK (weight > 0),
    evidence_kinds text[] NOT NULL,
    terms text[] NOT NULL,
    minimum integer,
    embedding vector(1024),
    PRIMARY KEY (job_id,model_version,requirement_no),
    FOREIGN KEY (job_id,model_version) REFERENCES job_retrieval_index(job_id,model_version) ON DELETE CASCADE,
    CHECK ((kind IN ('role','duty')) = (embedding IS NOT NULL)),
    CHECK ((kind IN ('degree','months')) = (minimum IS NOT NULL))
);

ALTER TABLE resume_retrieval_index ENABLE ROW LEVEL SECURITY;
ALTER TABLE resume_retrieval_chunk ENABLE ROW LEVEL SECURITY;
ALTER TABLE resume_chunk_source ENABLE ROW LEVEL SECURITY;
ALTER TABLE resume_chunk_embedding ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_retrieval_approval ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_retrieval_index ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_requirement ENABLE ROW LEVEL SECURITY;

CREATE POLICY current_version ON resume_retrieval_index FOR SELECT TO rs_app USING (
    app_client_id() IS NULL AND policy_version=(SELECT policy_version FROM search_state WHERE singleton)
    AND EXISTS (SELECT 1 FROM application a WHERE a.active_version_id=resume_retrieval_index.version_id
        AND a.deleted_at IS NULL AND a.status<>'closed' AND a.handling_department_id=ANY(app_departments()))
);
CREATE POLICY current_fields ON resume_retrieval_chunk FOR SELECT TO rs_app USING (
    EXISTS (SELECT 1 FROM resume_retrieval_index i
        WHERE i.version_id=resume_retrieval_chunk.version_id
          AND i.policy_version=resume_retrieval_chunk.policy_version
          AND i.slice_version=resume_retrieval_chunk.slice_version)
    AND NOT EXISTS (
        SELECT 1 FROM unnest(field_keys) AS keys(field_key)
        WHERE NOT EXISTS (
            SELECT 1 FROM field_usage_policy p JOIN field_definition d USING(field_key)
            WHERE p.field_key=keys.field_key AND p.policy_version=resume_retrieval_chunk.policy_version
              AND p.purpose='search' AND p.allowed AND d.status='active'
        )
    )
);
CREATE POLICY current_sources ON resume_chunk_source FOR SELECT TO rs_app USING (
    EXISTS (SELECT 1 FROM resume_retrieval_chunk c WHERE c.id=resume_chunk_source.chunk_id)
);
CREATE POLICY current_embeddings ON resume_chunk_embedding FOR SELECT TO rs_app USING (
    EXISTS (SELECT 1 FROM resume_retrieval_chunk c WHERE c.id=resume_chunk_embedding.chunk_id)
);

CREATE POLICY job_scope ON job_retrieval_approval FOR SELECT TO rs_app USING (
    app_client_id() IS NULL AND (
        EXISTS (SELECT 1 FROM job j WHERE j.id=job_id AND j.department_id=ANY(app_departments()))
        OR EXISTS (SELECT 1 FROM application a WHERE a.job_id=job_retrieval_approval.job_id
            AND a.deleted_at IS NULL AND a.handling_department_id=ANY(app_departments()))
    )
);
CREATE POLICY current_job ON job_retrieval_index FOR SELECT TO rs_app USING (
    app_client_id() IS NULL
    AND EXISTS (SELECT 1 FROM job_retrieval_input j WHERE j.id=job_retrieval_index.job_id
        AND j.snapshot=job_retrieval_index.snapshot AND (
            j.department_id=ANY(app_departments()) OR EXISTS (
                SELECT 1 FROM application a WHERE a.job_id=j.id AND a.deleted_at IS NULL
                    AND a.handling_department_id=ANY(app_departments())
            )
        )
    )
    AND rules=coalesce((SELECT r.rules FROM job_retrieval_approval r
        WHERE r.job_id=job_retrieval_index.job_id AND r.snapshot=job_retrieval_index.snapshot), '{}'::jsonb)
);
CREATE POLICY current_requirements ON job_requirement FOR SELECT TO rs_app USING (
    EXISTS (SELECT 1 FROM job_retrieval_index j
        WHERE j.job_id=job_requirement.job_id AND j.model_version=job_requirement.model_version)
);

GRANT SELECT ON job_retrieval_input, job_retrieval_approval, job_retrieval_index, job_requirement,
    resume_retrieval_index, resume_retrieval_chunk, resume_chunk_source, resume_chunk_embedding TO rs_app;
