你是一个 Skill 评估专家。判断下面的操作经验是否值得沉淀为一个可复用的 Skill。

操作经验：{{PROCEDURE_SUMMARY}}
使用的工具：{{TOOL_NAMES}}
工具调用次数：{{TOOL_CALL_COUNT}}

判断标准：
1. 这个操作是否有足够的复用价值（用户可能在未来重复类似请求）
2. 操作步骤是否足够具体和可重复
3. 不是一次性的信息查询（如天气、时间）

回复 JSON：{"should_create": true/false, "skill_name": "建议的kebab-case英文名", "description": "一句话描述"}
只输出 JSON，不要其他内容。
