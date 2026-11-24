"""Client for the local encoder used by the resume retrieval index."""
import json
import socket
from time import monotonic

from app.core.vector_index import MODEL_VERSION


class EmbeddingUnavailable(RuntimeError):
    pass


def encode_queries(values, *, socket_path=None, allow_truncation=True, timeout=12, lane="interactive"):
    values = list(dict.fromkeys(values))
    if socket_path is None:
        from app.core.config import get_settings

        socket_path = get_settings().embedding_socket
    result = {}
    deadline = monotonic() + timeout
    for offset in range(0, len(values), 32):
        batch = values[offset:offset + 32]
        payload = json.dumps({"model": MODEL_VERSION, "texts": batch, "allow_truncation": allow_truncation, "lane": lane},
                             ensure_ascii=False).encode()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError
                client.settimeout(remaining)
                client.connect(str(socket_path))
                client.sendall(payload + b"\n")
                with client.makefile("rb") as response:
                    vectors = json.loads(response.readline(1048576))
            if not isinstance(vectors, list) or len(vectors) != len(batch) or not all(isinstance(v, str) for v in vectors):
                raise ValueError("invalid response")
            result.update(zip(batch, vectors, strict=True))
        except (OSError, ValueError) as exc:
            raise EmbeddingUnavailable("本地向量服务暂不可用") from exc
    return result
