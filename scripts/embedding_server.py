"""Serve the local retrieval encoder over a Unix socket shared with the API."""
import argparse
from collections import OrderedDict
from fcntl import LOCK_EX, LOCK_NB, flock
import json
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingMixIn, UnixStreamServer
from threading import BoundedSemaphore, Condition

from app.core.vector_index import MODEL_VERSION
from scripts.backfill_retrieval import Encoder


class EmbeddingServer(ThreadingMixIn, UnixStreamServer):
    request_queue_size = 32
    daemon_threads = True

    def __init__(self, path, encoder):
        self.encoder = encoder
        self.cache = OrderedDict()
        self.catalog = OrderedDict()
        self.condition = Condition()
        self.predicting = False
        self.waiting_interactive = 0
        self.connections = BoundedSemaphore(48)
        super().__init__(str(path), EmbeddingRequest)

    def process_request(self, request, client_address):
        if self.connections.acquire(blocking=False):
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.connections.release()
                raise
        else:
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def _batch(self, values, allow_truncation, lane):
        cache = self.catalog if lane == "catalog" else self.cache
        interactive = lane != "background"
        keys = [(value, allow_truncation) for value in values]
        with self.condition:
            if interactive:
                self.waiting_interactive += 1
            try:
                self.condition.wait_for(lambda: all(key in cache for key in keys) or (
                    not self.predicting and (interactive or not self.waiting_interactive)))
                hits = {key: cache[key] for key in keys if key in cache}
                if len(hits) == len(set(keys)):
                    for key in hits:
                        cache.move_to_end(key)
                    return [hits[key] for key in keys]
                self.predicting = True
            finally:
                if interactive:
                    self.waiting_interactive -= 1
                    self.condition.notify_all()
        try:
            missing = [value for value, truncate in dict.fromkeys(keys) if (value, truncate) not in hits]
            vectors = self.encoder.encode(missing, allow_truncation=allow_truncation)
            hits.update(((value, allow_truncation), vector) for value, vector in vectors.items())
            with self.condition:
                cache.update(hits)
                while len(cache) > 512:
                    cache.popitem(last=False)
            return [hits[key] for key in keys]
        finally:
            with self.condition:
                self.predicting = False
                self.condition.notify_all()

    def vectors(self, values, allow_truncation, lane):
        # Yield between small background batches so a long resume cannot occupy the whole queue.
        batch_size = 2 if lane == "background" else 8
        return [vector for offset in range(0, len(values), batch_size)
                for vector in self._batch(values[offset:offset + batch_size], allow_truncation, lane)]

    def handle_error(self, request, client_address):
        # Keep request text out of logs, including malformed input.
        print("Embedding request failed", flush=True)


class EmbeddingRequest(StreamRequestHandler):
    timeout = 3

    def handle(self):
        request = json.loads(self.rfile.readline(1048577))
        values = request.get("texts")
        allow_truncation = request.get("allow_truncation", True)
        lane = request.get("lane", "interactive")
        if request.get("model") != MODEL_VERSION or not isinstance(values, list) or not 1 <= len(values) <= 32:
            return
        if lane not in ("interactive", "catalog", "background") or not isinstance(allow_truncation, bool) or not all(isinstance(value, str) and 0 < len(value) <= 16000 for value in values):
            return
        payload = json.dumps(self.server.vectors(values, allow_truncation, lane)).encode()
        self.wfile.write(payload + b"\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    args.socket.parent.mkdir(parents=True, exist_ok=True)
    with args.socket.with_suffix(".lock").open("a") as lock:
        flock(lock, LOCK_EX | LOCK_NB)
        args.socket.unlink(missing_ok=True)
        encoder = Encoder(args.model_dir, args.threads, batch_size=8)
        with EmbeddingServer(args.socket, encoder) as server:
            args.socket.chmod(0o660)
            print(f"Embedding service ready ({MODEL_VERSION})", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()
