"""Run the frozen M8 fixture against the actual PostgreSQL screening service."""
from collections import defaultdict
import hashlib
import json
from math import ceil, log2
from pathlib import Path
from time import perf_counter

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import AuthContext
from app.core.db import owner_engine
from app.core.resume_pipeline import prepare_extraction, publish, write_extraction
from app.core.screening import SearchInput, _rank, _terms, read_evidence
from app.parsing.ir import ParsedDocument, Span
from app.parsing.pipeline import emit_spans

ROOT = Path(__file__).parent / "baseline"


def rows(name):
    return [json.loads(line) for line in (ROOT / name).read_text().splitlines() if line]


def seed(connection, candidate):
    ident = candidate["id"]
    cid = connection.execute(text("INSERT INTO candidate DEFAULT VALUES RETURNING id")).scalar_one()
    did = connection.execute(text("""
        INSERT INTO resume_document(candidate_id,object_key,mime_type,sha256)
        VALUES(:c,:key,'application/pdf',:key) RETURNING id
    """), {"c": cid, "key": "m8-synthetic-" + ident}).scalar_one()
    version = connection.execute(text("""
        INSERT INTO resume_version(document_id,version_no,parser_version) VALUES(:d,1,'synthetic-ir-v1') RETURNING id
    """), {"d": did}).scalar_one()
    app = connection.execute(text("""
        INSERT INTO application(job_id,candidate_id,submitted_department_id,handling_department_id,submitted_version_id)
        VALUES(:job,:c,:dept,:dept,:v) RETURNING id
    """), {"c": cid, "dept": candidate["department"], "v": version, "job": 1 if candidate["department"] == 1 else 3}).scalar_one()
    document = ParsedDocument(spans=[Span("text", line, {"page": 1}, "pdf_text", reading_order=i)
                                     for i, line in enumerate(candidate["lines"], 1)]).finalize()
    result = prepare_extraction(connection, document)
    emit_spans(document, version, connection)
    write_extraction(connection, version, result)
    publish(connection, version)
    field_predictions = {
        "degree": next((r.fields["education.degree_level"] for r in result if "education.degree_level" in r.fields), None),
        "skill": connection.execute(text("""
            SELECT s.name FROM resume_skill k JOIN skill s ON s.id=k.skill_id
            JOIN resume_record r ON r.version_id=k.version_id AND r.record_no=k.parent_record_no
            WHERE k.version_id=:v AND r.kind='work' ORDER BY k.record_no
        """), {"v": version}).scalars().all(),
        "company": next((r.fields["work.company"] for r in result if "work.company" in r.fields), None),
    }
    return app, field_predictions


def evaluate():
    corpus = rows("corpus.jsonl")
    queries = rows("queries.jsonl")
    qrels = {row["query_id"]: row for row in rows("qrels.jsonl")}
    manifest = json.loads((ROOT / "manifest.json").read_text())
    fields = defaultdict(lambda: [0, 0, 0])
    reports = []
    auth = AuthContext(2, "", "", "dept_hr", 1, [1])
    with owner_engine.connect() as connection:
        with connection.begin() as transaction:
            state = connection.execute(text("SELECT policy_version FROM search_state")).scalar_one()
            if state != manifest["policy_version"] or connection.execute(text(
                "SELECT coalesce(max(dict_version),0) FROM search_term_group WHERE enabled"
            )).scalar_one() != manifest["dictionary_version"]:
                raise ValueError("评测需要 manifest 中冻结的策略与词典")
            if connection.execute(text("SELECT count(*) FROM application")).scalar_one():
                raise ValueError("请在只含迁移和种子的空白测试数据库中运行")
            aliases = {}
            for candidate in corpus:
                if candidate["split"] != "holdout":
                    continue
                app, facts = seed(connection, candidate)
                aliases[app] = candidate["id"]
                if candidate["id"] == "outside":
                    continue
                for key, truth in candidate["facts"].items():
                    prediction = facts[key]
                    gold_set = set(truth if isinstance(truth, list) else [truth] if truth is not None else [])
                    predicted_set = set(prediction if isinstance(prediction, list) else [prediction] if prediction is not None else [])
                    fields[key][0] += len(gold_set & predicted_set)
                    fields[key][1] += len(predicted_set - gold_set)
                    fields[key][2] += len(gold_set - predicted_set)
            connection.execute(text("SET LOCAL ROLE rs_app"))
            connection.execute(text("SELECT set_config('app.departments','1',true),set_config('app.user_id','2',true)"))
            with Session(bind=connection) as session:
                for query in queries:
                    inputs = SearchInput(**query["input"], limit=max(manifest["k"]))
                    _, terms = _terms(session, inputs)
                    timings = []
                    for _ in range(5):
                        started = perf_counter()
                        ranked, _ = _rank(session, auth, inputs, state, terms)
                        timings.append((perf_counter()-started)*1000)
                    ids = [aliases[item[0]] for item in ranked]
                    if "outside" in ids:
                        raise AssertionError("授权隔离失败")
                    label = qrels[query["id"]]
                    grades = label["grades"]
                    relevant = sum(grade > 0 for grade in grades.values())
                    measures = {}
                    for k in manifest["k"]:
                        top = ids[:k]
                        found = sum(grades[ident] > 0 for ident in top)
                        dcg = sum((2**grades[ident]-1)/log2(i+2) for i, ident in enumerate(top))
                        ideal = sum((2**grade-1)/log2(i+2) for i, grade in enumerate(sorted(grades.values(), reverse=True)[:k]))
                        measures[str(k)] = {"precision": found/k, "recall": found/relevant if relevant else None,
                                            "ndcg": dcg/ideal if ideal else None}
                    span_hits = {}
                    for app, version, record in ranked:
                        if record is not None:
                            span_hits[aliases[app]] = {r["span_no"] for r in read_evidence(session, version, state, record)}
                    expected_spans = label.get("evidence_spans", {})
                    complete = (any(set(spans) <= span_hits.get(ident, set()) for ident, spans in expected_spans.items())
                                if expected_spans else None)
                    states = None
                    if label.get("states"):
                        states = {}
                        for bucket in ("match", "reject", "review"):
                            rows_in_bucket, _ = _rank(session, auth, inputs.model_copy(update={"status": bucket}), state, terms)
                            states.update({aliases[app]: bucket for app, _, _ in rows_in_bucket})
                        assert states == label["states"], (query["id"], states)
                    reports.append({"query_id":query["id"],"group":query["group"],"ranked":ids,"metrics":measures,
                                    "states":states,"complete_evidence_hit":complete,"false_positive_no_relevant":bool(ids) if not relevant else None,
                                    "sql_p95_ms":round(sorted(timings)[ceil(.95*len(timings))-1],3)})
            transaction.rollback()
    metrics = {}
    for key, (tp, fp, fn) in fields.items():
        precision = tp/(tp+fp) if tp+fp else 0
        recall = tp/(tp+fn) if tp+fn else 0
        metrics[key] = {"precision":precision,"recall":recall,"f1":2*precision*recall/(precision+recall) if precision+recall else 0,
                        "tp":tp,"fp":fp,"fn":fn}
    aggregate = {}
    for group in sorted({q["group"] for q in reports}):
        selected = [q for q in reports if q["group"] == group]
        for k in manifest["k"]:
            values = {metric: [q["metrics"][str(k)][metric] for q in selected if q["metrics"][str(k)][metric] is not None]
                      for metric in ("precision", "recall", "ndcg")}
            aggregate[f"{group}@{k}"] = {metric: sum(scores)/len(scores) if scores else None for metric,scores in values.items()}
            aggregate[f"{group}@{k}"]["query_count"] = len(selected)
    return {"manifest":manifest, "by_query_group":aggregate,
            "input_sha256":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                            for name in ("corpus.jsonl","queries.jsonl","qrels.jsonl","manifest.json")},
            "field_metrics":metrics,"queries":reports,"unauthorized_results":0}


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
