"""Build the current retrieval index from published local resumes and job definitions."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.extraction import norm
from app.core.query_embedding import encode_queries
from app.core.vector_index import (
    DEFINITION_VERSION, DEGREES, EVIDENCE_KINDS, FIELD_LABELS, MAX_TOKENS, MODEL_VERSION,
    SLICE_VERSION, clean_source, digest, job_requirements, make_chunks,
)
from migrations.migrate import get_dsn

MODEL_FILES = {
    "pytorch_model.bin": "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",
    "config.json": "26159e7ad065073448460117eb24b7a4572f6f4e78eadff65dc0a11c052449fa",
    "tokenizer_config.json": "a62b2b6784f990259fddef5f16388693a8043be4f69179e6a5257eeb3f9abac4",
}
SOURCE_SQL = """
    SELECT p.version_id,p.record_no,p.part_no,p.span_no,p.field_key,p.start_offset,
           substr(s.raw_text,p.start_offset+1,p.end_offset-p.start_offset) AS value,
           s.span_kind,r.kind,w.employment_type
    FROM resume_record_source p
    JOIN resume_source_span s USING(version_id,span_no)
    JOIN resume_record r USING(version_id,record_no)
    LEFT JOIN resume_work w USING(version_id,record_no)
    JOIN field_usage_policy f ON f.field_key=p.field_key AND f.purpose='search'
      AND f.allowed AND f.policy_version=%s
    JOIN field_definition d ON d.field_key=p.field_key AND d.status='active'
    WHERE p.version_id=ANY(%s) AND p.field_key=ANY(%s)
      AND r.kind IN ('work','project') AND NOT s.is_sensitive AND s.span_kind<>'photo'
      AND (s.ocr_confidence IS NULL OR s.ocr_confidence>=.9)
      AND p.end_offset<=char_length(s.raw_text)
      AND NOT EXISTS (SELECT 1 FROM resume_field_issue i
          WHERE i.version_id=p.version_id AND i.record_no=p.record_no AND i.field_key=p.field_key)
    ORDER BY p.version_id,p.record_no,s.reading_order,p.start_offset,p.part_no
"""


def json_digest(data):
    return digest(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def vector_literal(values):
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"


def verify_model_files(model_dir, names):
    for name in names:
        with (model_dir / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_FILES[name]:
                raise ValueError(f"{name} does not match {MODEL_VERSION}")


class Encoder:
    def __init__(self, model_dir, threads, batch_size):
        import torch
        from transformers import AutoModel, AutoTokenizer

        verify_model_files(model_dir, MODEL_FILES)
        torch.set_num_threads(threads)
        self.torch = torch
        self.batch_size = batch_size
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, use_fast=True)
        self.model = AutoModel.from_pretrained(model_dir, local_files_only=True, use_safetensors=False).eval()

    def encode(self, values, *, allow_truncation=False):
        vectors = {}
        sizes = sorted(
            ((len(self.tokenizer(value, verbose=False)["input_ids"]), value) for value in set(values)),
            key=lambda item: (item[0], item[1]),
        )
        for offset in range(0, len(sizes), self.batch_size):
            batch = sizes[offset:offset + self.batch_size]
            if not allow_truncation and batch[-1][0] > MAX_TOKENS:
                raise ValueError("resume embedding exceeds the verified token budget")
            limits = {"truncation": True, "max_length": MAX_TOKENS} if allow_truncation else {}
            inputs = self.tokenizer([value for _, value in batch], padding=True, return_tensors="pt", **limits)
            with self.torch.inference_mode():
                output = self.model(**inputs).last_hidden_state[:, 0]
                embeddings = self.torch.nn.functional.normalize(output, p=2, dim=1)
            if not self.torch.isfinite(embeddings).all():
                raise ValueError("encoder returned invalid values")
            vectors.update((value, vector_literal(embedding)) for (_, value), embedding in zip(batch, embeddings.tolist()))
        return vectors


class SharedEncoder:
    def __init__(self, model_dir, socket_path, batch_size):
        from transformers import AutoTokenizer

        verify_model_files(model_dir, ("tokenizer.json", "tokenizer_config.json", "config.json"))
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, use_fast=True)
        self.socket_path = socket_path
        self.batch_size = min(batch_size, 8)

    def encode(self, values, *, allow_truncation=False):
        values = list(dict.fromkeys(values))
        vectors = {}
        for offset in range(0, len(values), self.batch_size):
            vectors.update(encode_queries(values[offset:offset + self.batch_size], socket_path=self.socket_path,
                                          allow_truncation=allow_truncation, timeout=90, lane="background"))
        return vectors


def resume_sources(connection, policy):
    ids = [row["id"] for row in connection.execute("""
        SELECT v.id FROM resume_version v WHERE v.status='published'
        AND EXISTS (SELECT 1 FROM application a WHERE a.active_version_id=v.id
            AND a.deleted_at IS NULL AND a.status<>'closed') ORDER BY v.id
    """)]
    sources = {version: defaultdict(list) for version in ids}
    names = defaultdict(set)
    for row in connection.execute("SELECT version_id,name FROM resume_profile WHERE version_id=ANY(%s) AND nullif(btrim(name),'') IS NOT NULL", (ids,)):
        names[row["version_id"]].add(row["name"].strip())
    for row in connection.execute(SOURCE_SQL, (policy, ids, list(FIELD_LABELS))):
        version = row.pop("version_id")
        record = row.pop("record_no")
        safe = clean_source(row["value"], names[version])
        if not safe.strip() or "█" in safe:
            continue
        row["value"] = safe
        sources[version][record].append(row)
    return sources


def job_inputs(connection):
    return connection.execute("""
        SELECT j.id,j.snapshot,coalesce(a.rules,'{}') AS rules
        FROM job_retrieval_input j LEFT JOIN job_retrieval_approval a ON a.job_id=j.id AND a.snapshot=j.snapshot
        ORDER BY j.id
    """).fetchall()


def put_job(connection, job, source, requirements, vectors):
    connection.execute("DELETE FROM job_retrieval_index WHERE job_id=%s AND model_version=%s", (job["id"], MODEL_VERSION))
    connection.execute("""
        INSERT INTO job_retrieval_index(job_id,model_version,snapshot,rules,definition_version,source)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (job["id"], MODEL_VERSION, Jsonb(job["snapshot"]), Jsonb(job["rules"]), DEFINITION_VERSION, source))
    with connection.cursor() as cursor:
        cursor.executemany("""
            INSERT INTO job_requirement(job_id,model_version,requirement_no,kind,original_text,mandatory,
                weight,evidence_kinds,terms,minimum,embedding)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
        """, [(job["id"], MODEL_VERSION, number, item.kind, item.original, item.mandatory, item.weight,
               list(item.scopes), list(item.terms), item.minimum, vectors.get(item.query))
              for number, item in enumerate(requirements, 1)])


def put_resume(connection, policy, version, fingerprint, chunks, vectors):
    # An input change invalidates all embeddings derived from the old chunks.
    if connection.execute("""
        SELECT 1 FROM resume_retrieval_index WHERE version_id=%s AND policy_version=%s AND slice_version=%s
          AND source_hash<>%s LIMIT 1
    """, (version, policy, SLICE_VERSION, fingerprint)).fetchone():
        connection.execute("DELETE FROM resume_retrieval_chunk WHERE version_id=%s AND policy_version=%s AND slice_version=%s",
                           (version, policy, SLICE_VERSION))
        connection.execute("DELETE FROM resume_retrieval_index WHERE version_id=%s AND policy_version=%s AND slice_version=%s",
                           (version, policy, SLICE_VERSION))
    with connection.cursor() as cursor:
        for record, number, chunk in chunks:
            row = cursor.execute("""
                INSERT INTO resume_retrieval_chunk(version_id,record_no,chunk_no,evidence_kind,policy_version,slice_version,
                    field_keys,body,token_count)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (version_id,record_no,policy_version,slice_version,chunk_no)
                DO UPDATE SET body=excluded.body RETURNING id
            """, (version, record, number, chunk.kind, policy, SLICE_VERSION,
                  sorted({part.field_key for part in chunk.fragments}), chunk.body, chunk.token_count)).fetchone()
            chunk_id = row["id"]
            cursor.executemany("""
                INSERT INTO resume_chunk_source(chunk_id,version_id,record_no,source_no,part_no,span_no,start_offset,end_offset)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
            """, [(chunk_id, version, record, index, part.part_no, part.span_no, part.start, part.start + len(part.value))
                  for index, part in enumerate(chunk.fragments, 1)])
            cursor.execute("""
                INSERT INTO resume_chunk_embedding(chunk_id,model_version,input_hash,embedding)
                VALUES (%s,%s,%s,%s::vector) ON CONFLICT (chunk_id,model_version)
                DO UPDATE SET input_hash=excluded.input_hash,embedding=excluded.embedding
            """, (chunk_id, MODEL_VERSION, digest(chunk.body), vectors[chunk.body]))
        cursor.execute("""
            INSERT INTO resume_retrieval_index(version_id,policy_version,slice_version,model_version,source_hash)
            VALUES (%s,%s,%s,%s,%s)
        """, (version, policy, SLICE_VERSION, MODEL_VERSION, fingerprint))


def approved_item(raw):
    kind = raw["kind"]
    original = raw["text"].strip()
    if not original or len(original) > 4000:
        raise ValueError("approved requirement text must have 1–4000 characters")
    mandatory = raw.get("mandatory", True)
    if not isinstance(mandatory, bool):
        raise ValueError("mandatory must be a boolean")
    if kind == "duty":
        scopes = raw.get("scopes", list(EVIDENCE_KINDS))
        if not isinstance(scopes, list) or not scopes or not set(scopes) <= set(EVIDENCE_KINDS):
            raise ValueError("invalid duty evidence scopes")
        weight = float(raw.get("weight", 1))
        if not 0 < weight <= 100:
            raise ValueError("duty weight must be between 0 and 100")
        terms = raw.get("terms", [original])
        minimum = None
    elif kind == "skill":
        scopes = raw.get("scopes", [])
        if not isinstance(scopes, list) or not set(scopes) <= set(EVIDENCE_KINDS):
            raise ValueError("invalid skill evidence scopes")
        terms = raw.get("alternatives", [original])
        weight, minimum = (2 if mandatory else 1), None
    elif kind in ("degree", "months"):
        minimum = DEGREES.index(raw["minimum"]) if kind == "degree" else raw["minimum"]
        if type(minimum) is not int or (kind == "months" and not 1 <= minimum <= 720):
            raise ValueError("invalid minimum experience in months")
        scopes, terms, weight, mandatory = [], [], 1, True
    else:
        raise ValueError(f"unknown approved requirement kind: {kind}")
    if not isinstance(terms, list) or len(terms) > 20 or any(not isinstance(term, str) or not norm(term) for term in terms):
        raise ValueError("invalid requirement terms")
    terms = list(dict.fromkeys(map(norm, terms)))
    if kind == "skill" and not terms:
        raise ValueError("skill must have at least one alternative")
    return {"kind": kind, "text": original, "mandatory": mandatory, "weight": weight,
            "scopes": scopes, "terms": terms, "minimum": minimum}


def apply_approvals(connection, path):
    for job in json.loads(path.read_text()):
        row = connection.execute("SELECT snapshot FROM job_retrieval_input WHERE id=%s", (job["job_id"],)).fetchone()
        if row is None or job["snapshot"] != row["snapshot"]:
            raise ValueError(f"approval {job['job_id']} does not match the current job definition")
        if not 1 <= len(job["requirements"]) <= 50:
            raise ValueError("approved rules must contain 1–50 requirements")
        rules = {"items": [approved_item(raw) for raw in job["requirements"]]}
        connection.execute("""
            INSERT INTO job_retrieval_approval(job_id,snapshot,rules) VALUES (%s,%s,%s)
            ON CONFLICT (job_id) DO UPDATE SET snapshot=excluded.snapshot,rules=excluded.rules
        """, (job["job_id"], Jsonb(job["snapshot"]), Jsonb(rules)))


def backfill(connection, args, should_replan=None):
    encoder = None

    def get_encoder():
        nonlocal encoder
        if encoder is None:
            encoder = (SharedEncoder(args.model_dir, args.embedding_socket, args.batch_size) if args.embedding_socket
                       else Encoder(args.model_dir, args.threads, args.batch_size))
        return encoder

    with connection.transaction():
        if not connection.execute("SELECT pg_try_advisory_lock(721379,28) AS acquired").fetchone()["acquired"]:
            if should_replan is not None:
                return False
            raise SystemExit("another retrieval backfill is already running")
        if args.approve:
            apply_approvals(connection, args.approve)
        policy = connection.execute("SELECT policy_version FROM search_state WHERE singleton").fetchone()["policy_version"]
        jobs = job_inputs(connection)
        existing_jobs = {row["job_id"]: (row["snapshot"], row["rules"]) for row in connection.execute(
            "SELECT job_id,snapshot,rules FROM job_retrieval_index WHERE model_version=%s AND definition_version=%s",
            (MODEL_VERSION, DEFINITION_VERSION),
        )}
        resumes = resume_sources(connection, policy)
        indexed = {row["version_id"]: row["source_hash"] for row in connection.execute("""
            SELECT version_id,source_hash FROM resume_retrieval_index
            WHERE model_version=%s AND policy_version=%s AND slice_version=%s
        """, (MODEL_VERSION, policy, SLICE_VERSION))}
    todo_jobs = [(job, *job_requirements(job["snapshot"], job["rules"]))
                 for job in jobs if existing_jobs.get(job["id"]) != (job["snapshot"], job["rules"])]
    todo_resumes = [(version, records, json_digest(records)) for version, records in resumes.items()]
    todo_resumes = [(version, records, fingerprint) for version, records, fingerprint in todo_resumes
                    if indexed.get(version) != fingerprint]
    if should_replan is not None:
        todo_resumes.reverse()
    print(f"policy={policy} jobs={len(jobs)} jobs_to_encode={len(todo_jobs)} resumes={len(resumes)} "
          f"resumes_to_index={len(todo_resumes)}", flush=True)
    if todo_jobs:
        queries = [req.query for _, _, requirements in todo_jobs for req in requirements if req.query]
        vectors = get_encoder().encode(queries, allow_truncation=True) if queries else {}
        with connection.transaction():
            for job, source, requirements in todo_jobs:
                put_job(connection, job, source, requirements, vectors)
        print(f"jobs_indexed={len(todo_jobs)} query_vectors={len(vectors)}", flush=True)
    chunk_count = 0
    for offset in range(0, len(todo_resumes), args.commit_size):
        batch = todo_resumes[offset:offset + args.commit_size]
        prepared = []
        for version, records, fingerprint in batch:
            chunks = [(record, number, chunk) for record, rows in records.items()
                      for number, chunk in enumerate(make_chunks(get_encoder().tokenizer, rows), 1)]
            prepared.append((version, fingerprint, chunks))
        bodies = [chunk.body for _, _, chunks in prepared for _, _, chunk in chunks]
        vectors = get_encoder().encode(bodies) if bodies else {}
        with connection.transaction():
            for version, fingerprint, chunks in prepared:
                put_resume(connection, policy, version, fingerprint, chunks, vectors)
        chunk_count += len(bodies)
        print(f"resumes_indexed={offset + len(batch)}/{len(todo_resumes)} chunks_indexed={chunk_count}", flush=True)
        if should_replan is not None and should_replan():
            return True
    return False


def follow_publications(args):
    with psycopg.connect(get_dsn(), autocommit=True) as notifications:
        notifications.execute("LISTEN resume_retrieval_refresh")
        print("Listening for committed resume and job updates", flush=True)

        def updated():
            # Drain the notifications together so imports can be indexed as a batch.
            return bool(list(notifications.notifies(timeout=0)))

        while True:
            updated()
            with psycopg.connect(get_dsn(), row_factory=dict_row, autocommit=True) as connection:
                if backfill(connection, args, should_replan=updated):
                    continue
            # Periodic reconciliation also covers changes made while the service was offline.
            list(notifications.notifies(timeout=180, stop_after=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path("../models/bge-m3"))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--commit-size", type=int, default=12)
    parser.add_argument("--embedding-socket", type=Path, help="reuse the running local encoder")
    parser.add_argument("--follow", action="store_true", help="index after committed publication events")
    parser.add_argument("--approve", type=Path, help="version-bound job requirements JSON confirmed by HR")
    parser.add_argument("--export-jobs", type=Path, help="export current job snapshots for approval, then exit")
    args = parser.parse_args()
    if (min(args.threads, args.batch_size, args.commit_size) <= 0 or (args.approve and args.export_jobs)
            or (args.follow and (args.approve or args.export_jobs))):
        parser.error("invalid batch arguments or conflicting job options")
    if args.follow:
        follow_publications(args)
        return
    with psycopg.connect(get_dsn(), row_factory=dict_row, autocommit=True) as connection:
        if args.export_jobs:
            rows = [{"job_id": row["id"], "snapshot": row["snapshot"], "requirements": []}
                    for row in job_inputs(connection)]
            args.export_jobs.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        else:
            backfill(connection, args)


if __name__ == "__main__":
    main()
