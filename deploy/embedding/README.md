# CPU 版 bge-m3 向量化服务

`embed_server.py` 在 UNIX socket 上提供 bge-m3 编码，协议与 `app/core/query_embedding.py`
的 `encode_queries()` 完全对齐（一行 JSON 请求 / 一行 JSON 字符串数组响应）。

## 为什么是 CPU

目标机是 VMware 虚机，`nvidia-smi` 不存在、无 `/dev/nvidia*`、docker 只有 `runc`
无 nvidia runtime，paddle 也是 `is_compiled_with_cuda() == False`。GPU 不可用，
因此走 CPU 推理。

## 关键约束

- **权重必须与本机一致**：启动时按 `scripts/backfill_retrieval.py` 的 `MODEL_FILES`
  做 sha256 校验，`pytorch_model.bin` 不匹配直接退出。不要换成别的 bge 变体，
  否则 `resume_chunk_embedding.model_version` 会与 `MODEL_VERSION` 语义不一致。
- **构建镜像时需要一个装了 torch 的基底**。目标机不通 Docker Hub，
  做法是在已有 `resume-ftp-verify-api` 容器里用 `download.pytorch.org/whl/cpu`
  装 `torch==2.9.1+cpu` + `transformers` + `sentencepiece`，再 `docker commit`。
- **socket 目录用 docker 命名卷**，不要用宿主绑定目录：CentOS 是 SELinux Enforcing，
  绑定目录里 root 也 bind 不了 unix socket。

## 实测性能（CentOS，24 核，5500 chunk / 69 万 token 语料）

| 模型 | batch=1 | batch=16 | 全量建索引 |
|---|---|---|---|
| bge-m3 fp32 | 0.77 chunk/s | 0.92 chunk/s | ≈100 min |
| bge-m3 int8 动态量化 | 0.63 chunk/s | 1.27 chunk/s | ≈73 min |
| bge-small-zh-v1.5 | 9.05 chunk/s | 11.63 chunk/s | ≈8 min |

单条查询编码延迟：bge-m3 ≈330 ms，bge-small ≈110 ms。
