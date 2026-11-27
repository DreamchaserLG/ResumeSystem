"""bge-m3 CPU embedding service over a UNIX socket.

协议与 app/core/query_embedding.py 的 encode_queries 完全对齐：
  请求：一行 JSON {"model","texts","allow_truncation","lane"}
  响应：一行 JSON 字符串数组，元素是 "[v1,v2,...]" 形式的 vector literal
实现：CLS pooling + L2 归一化（与 scripts/backfill_retrieval.py 的 Encoder 一致）。
"""
import hashlib, json, os, socket, threading, time
import torch
from transformers import AutoModel, AutoTokenizer

# 与 scripts/backfill_retrieval.py 的 MODEL_FILES 完全一致，防止加载到不同权重
MODEL_FILES = {
    "pytorch_model.bin": "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",
    "config.json": "26159e7ad065073448460117eb24b7a4572f6f4e78eadff65dc0a11c052449fa",
    "tokenizer_config.json": "a62b2b6784f990259fddef5f16388693a8043be4f69179e6a5257eeb3f9abac4",
}

MODEL_DIR = os.environ.get("EMBED_MODEL_DIR", "/models/bge-m3")
SOCK = os.environ.get("EMBED_SOCKET", "/run/resume-embedding/model.sock")
THREADS = int(os.environ.get("EMBED_THREADS", "12"))
MAX_TOKENS = 512
BATCH = int(os.environ.get("EMBED_BATCH", "8"))

def verify_model_files(model_dir):
    for name, want in MODEL_FILES.items():
        path = os.path.join(model_dir, name)
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest() != want:
            raise SystemExit(f"model file {name} does not match MODEL_VERSION")
    print("[embed] model files verified", flush=True)


verify_model_files(MODEL_DIR)
torch.set_num_threads(THREADS)
tok = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True, use_fast=True)
model = AutoModel.from_pretrained(MODEL_DIR, local_files_only=True, use_safetensors=False).eval()
print(f"[embed] loaded {MODEL_DIR} threads={THREADS} batch={BATCH}", flush=True)

lock = threading.Lock()
stats = {"req": 0, "texts": 0, "sec": 0.0}


def encode(texts, allow_truncation):
    out = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        limits = {"truncation": True, "max_length": MAX_TOKENS} if allow_truncation else {}
        inputs = tok(batch, padding=True, return_tensors="pt", **limits)
        if not allow_truncation and inputs["input_ids"].shape[1] > MAX_TOKENS:
            raise ValueError("input exceeds verified token budget")
        with torch.inference_mode():
            emb = model(**inputs).last_hidden_state[:, 0]
            emb = torch.nn.functional.normalize(emb, p=2, dim=1)
        for row in emb.tolist():
            out.append("[" + ",".join(format(v, ".9g") for v in row) + "]")
    return out


def handle(conn):
    with conn:
        with conn.makefile("rb") as fh:
            line = fh.readline(1 << 22)
        if not line:
            return
        req = json.loads(line)
        t0 = time.perf_counter()
        with lock:
            vecs = encode(req["texts"], req.get("allow_truncation", True))
        dt = time.perf_counter() - t0
        stats["req"] += 1
        stats["texts"] += len(req["texts"])
        stats["sec"] += dt
        print(f"[embed] lane={req.get('lane')} n={len(req['texts'])} {dt*1000:.0f}ms "
              f"avg={stats['texts']/max(stats['sec'],1e-9):.2f}/s", flush=True)
        conn.sendall(json.dumps(vecs).encode() + b"\n")


def main():
    d = os.path.dirname(SOCK)
    if d:
        os.makedirs(d, exist_ok=True)
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o666)
    srv.listen(64)
    print(f"[embed] listening {SOCK}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
