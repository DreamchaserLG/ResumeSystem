# M7 · 外部 API 验收与使用

按 `方案设计/07-外部API设计.md` 实现。使用已发布的结构化简历与证据表，M3–M6 可以继续独立开发。

| 接口 | 结果 |
|---|---|
| `GET /api/v1/applications` | 授权岗位和处理部门下的申请列表，默认 20 条。传 `cursor` 继续分页。支持获准字段 `degree_level`、`school`、`company`、`city`、`skill` 的精确过滤 |
| `GET /api/v1/applications/{id}` | 当前简历版本、结构化字段、证据引用与异常状态。可传 `version_id` 查询该申请明确关联的历史发布版本 |
| `GET /api/v1/applications/{id}/evidence/{span_no}?version_id=17` | 返回获准字段对应的字符范围与定位。原文证据偏移采用 Unicode 字符零基右开区间 |

认证使用 `Authorization: Bearer …`。每次读取取账号字段白名单与当前 `export` 策略的交集；筛选字段同时要求 `filter` 权限。岗位授权可限制当前处理部门，迁移时为既有授权保留岗位原部门范围。交付前用新事务复查凭证、授权和版本。返回 `X-Policy-Version`、`X-RateLimit-Remaining`。默认每账号每秒 10 次、每 UTC 日 1000 次，超额返回 429 和 `Retry-After`。缺失或越权的申请、版本、证据统一返回 404，组装期间权限变更时按情况返回 401、404 或 409。成功读取、跨岗位连续分页和配额告警写审计。

## 在目标环境启用

1. 让 API 运行新代码，并执行 `python -m migrations.migrate`。迁移新增版本关联与密钥表，用既有申请的投递版本、当前版本建立初始关联。迁移前已退役且缺少申请归属的版本，需要有可核实的关联后再对外开放。
2. 为已配置岗位、处理部门和字段白名单的集成账号签发密钥，命令输出一次明文。数据库仅存摘要。

   ```bash
   python -m app.integration_keys rotate 1
   python -m app.integration_keys revoke 1
   ```

3. 使用签发的密钥请求列表，再按响应中的申请 ID 与版本 ID 查询详情和证据。

   ```bash
   curl -H "Authorization: Bearer ${RESUME_INTEGRATION_KEY}" \
     'http://localhost:8001/api/v1/applications?limit=20'
   ```

## 验收

在独立的 PostgreSQL 16 与 Redis 7 环境中，用合成结构化简历复跑以下测试。通过项覆盖迁移幂等、部门 RLS、既有会话鉴权、游标分页、权限筛选、跨岗位与历史版本隔离、字段策略收紧、证据裁剪、原文敏感内容、密钥轮换/撤销、QPS/日配额与审计。

```bash
python -m pytest -q tests/test_migrations.py tests/test_rls.py tests/test_auth.py tests/test_m7_integration.py
```

本次结果为 **19 passed**。数据库、Redis 和测试数据均位于临时容器。
