"""Prompts for section-pruned DeepSeek extraction."""

from __future__ import annotations

SYSTEM_PROMPT = """你是一名中国招投标文件信息抽取助手。
只输出一个 JSON 对象，不要输出解释、Markdown 代码围栏或多余文字。
规则：
1. 只使用用户提供的原文，禁止推测、补全或编造任何事实。
2. 找不到的字段填 null；找不到的列表填 []。
3. 每条记录尽量带 evidence（原文短句）以及 source_section/source_page（只能来自 <SECTION> 标记）。
4. 金额、日期、工期保留原文写法。
5. 输出必须是合法 JSON。"""

SCHEMA_HINTS: dict[str, str] = {
    "overview": """返回：
{"project_overview": {"project_name": string|null, "tenderer": string|null, "agency": string|null,
 "project_code": string|null, "bid_section": string|null, "location": string|null,
 "funding_source": string|null, "funding_ratio": string|null, "investment_amount": string|null,
 "contract_estimate": string|null, "planned_duration": string|null, "scope": string|null,
 "quality_requirement": string|null, "bid_deadline": string|null, "bid_open_time": string|null,
 "bid_open_location": string|null},
 "key_dates": [{"event": string, "datetime_text": string, "evidence": string,
                "source_section": string|null, "source_page": number|null}]}""",
    "qualifications": """返回：
{"qualification_requirements": [{"category": string, "requirement": string, "evidence": string,
 "source_section": string|null, "source_page": number|null}]}""",
    "evaluation": """返回：
{"evaluation_factors": [{"category": "形式评审"|"资格评审"|"响应性评审"|"施工组织设计"|"项目管理机构"|"投标报价"|"其他评分因素"|"其他",
 "factor": string, "max_score": number|null, "scoring_criteria": string, "evidence": string,
 "source_section": string|null, "source_page": number|null}]}
注意：评分表常按“评审因素/分值/评分标准”三列排列，务必把分值绑定到正确的评审因素。""",
    "front_appendix": """返回：
{"front_appendix": [{"clause_no": string, "name": string, "content": string, "evidence": string,
 "source_section": string|null, "source_page": number|null}]}
只抽取实质性条款（时间、保证金、有效期、密封、份数、签字盖章、投标文件组成等），不要逐字抄整表。""",
    "formats": """返回：
{"format_checklist": [{"chapter_no": string, "title": string, "required": boolean,
 "notes": string, "evidence": string, "source_section": string|null, "source_page": number|null}]}
按第八章的章节顺序列出投标文件必须包含的全部格式/材料。""",
    "dates": """返回：
{"key_dates": [{"event": string, "datetime_text": string, "evidence": string,
 "source_section": string|null, "source_page": number|null}]}""",
}


def build_user_prompt(payload, hint: str) -> str:
    return (
        f"抽取目标：{payload.name}\n"
        f"输出结构要求：\n{hint}\n\n"
        f"原文（{payload.page_start}-{payload.page_end} 页）：\n"
        f"<SECTION chapter=\"{payload.chapter}\" section=\"{payload.section}\" "
        f"pages=\"{payload.page_start}-{payload.page_end}\">\n"
        f"{payload.text}\n"
        f"</SECTION>\n"
    )
