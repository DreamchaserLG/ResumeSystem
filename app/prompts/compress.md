# 任务
把 <history> 与 <last_facts> 压缩为会话状态，供下一轮补全指代。只输出 JSON，不要任何解释文字。

# 输出结构（字段一个都不能多、不能少）
{"scope":{"department_id":int|null,"department_name":string|null,
          "job_id":int|null,"job_title":string|null,
          "window":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}|null},
 "focus":["trend"|"gap"|"funnel"|"match"|"breakdown"],
 "open_items":[string],
 "last_answer_kind":"stats"|"match"|null}

# 规则
1. 每个值必须来自 <history> 或 <last_facts> 出现过的原值。找不到就填 null 或空数组。
   禁止补全部门全称、禁止改写岗位名称、禁止推测日期。
2. 禁止把任何数值指标写入 scope 或 open_items。记忆不存数字。
3. open_items 最多 3 条，每条不超过 20 字，只写"用户问了但还没被回答"的问题。
4. focus 只能从枚举选，不得自造标签。
5. 同一字段用户改过口径，保留最后一次，丢弃更早的。
6. 输出前自检：JSON 能否被 json.loads 解析；不能就改成最简合法对象。
