# CentOS 内网部署手册（含 CPU 向量服务）

目标机：`10.162.0.139`（CentOS Stream 9，VMware 虚机，24 核 / 31GB / 50GB 盘）
部署根目录：`/opt/resume-lan`
Compose 项目名：`resume_lan`

> 关键前提：**这台机器没有可用 GPU**（VMware SVGA 虚拟显卡、无 nvidia-smi、
> docker 只有 runc）。所有模型推理走 CPU。

---

## 一、目录结构

```
/opt/resume-lan/
├── app/                          代码检出（分支 codex/ftp-job-directory）
├── compose-lan.yml               内网覆盖：镜像标签、存储挂载、nginx 配置
├── docker-compose.embedding.yml  CPU 向量服务（本次新增）
├── storage/                      originals/ + previews/ 简历文件
├── embed-models/
│   ├── bge-m3/                   2.2GB，与本机 models/bge-m3 逐字节一致
│   ├── bge-small/                95MB，对照实验用，未启用
│   └── code/
│       ├── embed_server.py       socket 服务
│       └── backfill_retrieval.py 回填脚本副本
├── torch-2.9.1+cpu-...whl        离线 wheel（184MB，可删）
└── nginx.conf
```

---

## 二、启动 / 重启

```bash
cd /opt/resume-lan
export COMPOSE_PROJECT_NAME=resume_lan
COMPOSE="docker compose \
  -f app/docker-compose.yml \
  -f app/docker-compose.ftp.yml \
  -f compose-lan.yml \
  -f docker-compose.embedding.yml"

# 全量启动
$COMPOSE up -d --no-build

# 只重启某几个服务
docker restart resume_lan-embedding-1 resume_lan-api-1

# 健康检查
docker ps --format '{{.Names}}\t{{.Status}}'
```

服务清单（9 个）：

| 服务 | 作用 | 备注 |
|---|---|---|
| `pg` | PostgreSQL 16 + pgvector 0.8.7 | 已调优，见第六节 |
| `redis` | celery broker | |
| `migrate` | 迁移，一次性 | 可 `docker compose run --rm` |
| `api` | FastAPI，绑 `127.0.0.1:8001` | 通过 socket 用向量服务 |
| `frontend` | nginx，绑 `10.162.0.139:8080` | |
| `worker` | 解析 celery（PaddleOCR / pymupdf / docx） | |
| `ftp-worker` | `-Q ftp_catalog` | |
| `ftp-scheduler` | beat | |
| **`embedding`** | **bge-m3 CPU 推理，UNIX socket** | 本次新增 |

---

## 三、向量服务怎么工作

```
api / worker / 回填进程
        │  connect("/run/resume-embedding/model.sock")
        │  send    {"model","texts","allow_truncation","lane"}\n
        │  recv    ["[v1,v2,...]", ...]\n
        ▼
resume_lan-embedding-1  (torch 2.9.1+cpu, bge-m3 fp32, 12 线程, batch=16)
```

协议与 `app/core/query_embedding.py` 完全对齐，**没有改一行业务代码**。

### 为什么用命名卷而不是宿主目录

CentOS 是 SELinux `Enforcing`。即使宿主目录 `chmod 777`、容器以 `--user 0` 跑，
在绑定挂载的宿主目录里 `bind()` 仍然 `Permission denied`。必须用
docker 命名卷 `resume_lan_embed_sock`。

### 模型一致性保护

`embed_server.py` 启动时对 `pytorch_model.bin` / `tokenizer.json` /
`config.json` / `tokenizer_config.json` 做 sha256 校验，值与
`scripts/backfill_retrieval.py` 的 `MODEL_FILES` 完全相同。不匹配直接退出，
防止用错权重污染 `resume_chunk_embedding`。

---

## 四、重建 embedding 镜像

目标机不通 Docker Hub，也没有跨机器的 `buildx`。用 **commit 法**：

```bash
# 1) 本机下 CPU wheel（download.pytorch.org 直连约 50KB/s，本机 45 秒）
curl -sL -o /tmp/torch.whl \
  "https://download.pytorch.org/whl/cpu/torch-2.9.1%2Bcpu-cp312-cp312-manylinux_2_28_x86_64.whl"

# 2) 推到目标机
cat /tmp/torch.whl | ssh root@10.162.0.139 'cat > /opt/resume-lan/torch.whl'

# 3) 起一个临时容器装依赖
docker run -d --name tmpbase --network host resume-ftp-verify-api:latest sleep 3600
docker exec tmpbase pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  setuptools wheel jinja2 filelock networkx typing-extensions sympy fsspec transformers sentencepiece
docker cp /opt/resume-lan/torch.whl tmpbase:/tmp/
docker exec tmpbase pip install --no-cache-dir --no-index --no-deps /tmp/torch.whl

# 4) 提交成镜像
docker commit tmpbase resume-embed-cpu:latest
docker rm -f tmpbase
```

> 注意：PyPI 清华源上 `torch` 只有 CUDA 版（539MB），没有 `+cpu`。
> 必须用 `download.pytorch.org/whl/cpu`。

---

## 五、向量回填

首次部署或换模型后必须跑。**已完成的就不用再跑**（脚本自带 `source_hash`
比对，重跑只会跳过）。

```bash
DU=$(docker exec resume_lan-api-1 printenv DATABASE_URL)

docker run -d --name backfill \
  --network resume_lan_default \
  -e "DATABASE_URL=$DU" \
  -e PYTHONPATH=/srv:/home/resume/.local/lib/python3.12/site-packages \
  -e PYTHONUNBUFFERED=1 --user 0 \
  -v /opt/resume-lan/embed-models/code:/code:ro \
  -v /opt/resume-lan/embed-models/bge-m3:/srv/models/bge-m3:ro \
  -v resume_lan_embed_sock:/run/resume-embedding \
  -w /srv resume-embed-cpu:latest \
  python3 /code/backfill_retrieval.py \
    --model-dir /srv/models/bge-m3 \
    --embedding-socket /run/resume-embedding/model.sock \
    --batch-size 8 --commit-size 12

# 观察
docker logs -f backfill
docker exec resume_lan-pg-1 psql -U resume -d resume -Atc \
  "select count(*) from resume_chunk_embedding"
```

**实测耗时**：5564 chunk / 69.4 万 token，**约 2 小时 5 分钟**，吞吐 1.2 chunk/s。

回填完的验收数字：

```
resume_chunk_embedding    5564   （= resume_retrieval_chunk 行数）
job_requirement.embedding  295 / 398
resume_retrieval_index    1136   （= 已发布版本总数）
job_retrieval_index         27   （= 岗位总数）
```

### 增量索引

新简历发布后，`worker` 提交事务会发 `NOTIFY resume_retrieval_refresh`。
要让新简历自动编码，需要常驻 `--follow`：

```bash
# 把上面命令里的末尾改成：
python3 /code/backfill_retrieval.py --model-dir ... --embedding-socket ... --follow
```
容器建议 `--restart unless-stopped`。不跑 `--follow` 也不会坏，
只是新简历在下次手动回填前没有向量（`status=unindexed`）。

---

## 六、数据库调优（已应用，2026-11-27）

原配置 `shared_buffers = 128MB`，而机器有 31GB、库只有 110MB，严重浪费。

| 参数 | 原值 | 现值 |
|---|---|---|
| `shared_buffers` | 128MB | **4GB** |
| `effective_cache_size` | 4GB | **12GB** |
| `work_mem` | 4MB | **16MB** |
| `maintenance_work_mem` | 64MB | **512MB** |
| `random_page_cost` | 4.0 | **1.1**（SSD） |
| `effective_io_concurrency` | 1 | **200** |

改法：

```bash
docker cp resume_lan-pg-1:/var/lib/postgresql/data/postgresql.conf /tmp/pg.conf
# 改完
docker cp /tmp/pg.conf resume_lan-pg-1:/var/lib/postgresql/data/postgresql.conf
docker restart resume_lan-pg-1
docker restart resume_lan-api-1 resume_lan-worker-1
```

原文件备份在 `/tmp/pg.conf.bak`。

---

## 七、验收清单

```bash
# 1. 向量服务活着
docker logs resume_lan-embedding-1 | tail -3
#    → [embed] model files verified / [embed] listening .../model.sock

# 2. api 能看到 socket
docker exec resume_lan-api-1 ls -la /run/resume-embedding/
#    → srw-rw-rw- ... model.sock

# 3. api 侧真实调用
docker exec -w /srv -e PYTHONPATH=/srv resume_lan-api-1 python3 -c "
from app.core.query_embedding import encode_queries
v = encode_queries(['Java 后端开发'])
print('ok', len(v), len(list(v.values())[0].split(',')))"
#    → ok 1 1024

# 4. 端到端检索（需要登录 token，见 /tmp/e2e.py 之类脚本）
#    GET /api/v1/jobs/{id}/candidates?ranking=hybrid
#    → retrieval.source='jd', indexed=N, 候选人带 match_percent
```

实测延迟（单岗位全量候选人）：

| 候选人数 | 接口耗时 | 状态分布 |
|---|---|---|
| 1 | 417 ms | ranked 1 |
| 4 | 482 ms | ranked 4 |
| 18 | 659 ms | ranked 17 / no_evidence 1 |

其中编码约 300ms（单次 socket 往返），其余是 RLS + 候选池 + 打分。

---

## 八、踩过的坑

| 现象 | 原因 | 解法 |
|---|---|---|
| `socket.bind` Permission denied | SELinux Enforcing 阻止绑定挂载目录写 | 用 docker 命名卷 |
| `registry-1.docker.io` 超时 | 目标机不通 Docker Hub | 用已有镜像 + commit |
| `paddlex` 装不上 | `docker build` 默认 target 是 worker | `--target api` |
| 模型文件 PytorchStreamReader 失败 | 传输被截断（`cat \| ssh` 偶尔丢尾巴） | 传完必须 `md5sum`/`sha256sum` 比对 |
| `/tmp` 写满 | `cat \| base64 -d` 往 `/tmp` 写了两份 | 直接写到 `/opt` |
| 回填容器 `No module named 'app'` | 缺 PYTHONPATH | `-e PYTHONPATH=/srv` |
| 回填容器连不上 pg | 缺 DATABASE_URL | 从 api 容器 `printenv` 取 |
| `docker exec pg python3` 不存在 | pg 镜像没有 python | 从 api 容器连 pg |
