"""Deterministic Markdown templates for bid forms and attachments."""

from __future__ import annotations

from typing import Callable

from .context import TenderContext
from .models import BidOutline, OutlineSection
from .profile import BidderProfile, placeholder, resolve


def _v(value: str | None, label: str) -> str:
    return resolve(value, label)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines)


def _company(profile: BidderProfile) -> str:
    return _v(profile.company.name, "公司名称")


def render_evaluation_index(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    section_titles: dict[tuple[str, str], list[str]] = {}
    for section in outline.sections:
        for factor in section.evaluation_factors:
            key = (str(factor.get("category") or ""), str(factor.get("factor") or ""))
            section_titles.setdefault(key, []).append(section.title)
    rows = []
    for index, factor in enumerate(context.extraction.evaluation_factors, start=1):
        key = (factor.category or "", factor.factor or "")
        targets = "；".join(dict.fromkeys(section_titles.get(key, ["十一、其他资料"])))
        score = f"{factor.max_score:g}" if factor.max_score is not None else "符合性"
        rows.append([str(index), factor.category or "", factor.factor or "", score, targets, placeholder("页码范围")])
    return (
        "本表用于索引投标文件对评标因素的响应位置，页码在正式排版后补充。\n\n"
        + _table(["序号", "评审类别", "评审因素", "分值", "对应章节", "页码范围"], rows)
    )


def render_bid_letter(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    facts = context.facts
    return f"""致：{facts.tenderer}

1. 我方已仔细研究 {facts.project_name}（标段：{facts.bid_section}）招标文件的全部内容，愿意按照招标文件及合同约定承担本工程的施工、竣工及缺陷修复工作。
2. 我方投标总报价为：{placeholder("投标总价（大写）")}（小写：{placeholder("投标总价（小写）")}）。
3. 计划工期：{facts.duration_days} 日历天；工程质量：{facts.quality_requirement}。
4. 我方承诺在投标有效期内不撤销投标文件；投标有效期为：{_v(profile.bid.bid_validity_days, "投标有效期")}。
5. 一旦我方中标，我方将按要求提交履约保证金，并与招标人签订合同。
6. 我方投标保证金为：{_v(profile.bid.bid_bond_amount, "投标保证金金额")}，形式：{_v(profile.bid.bid_bond_method, "投标保证金形式")}。

投标人：{_company(profile)}
法定代表人或其委托代理人：{placeholder("签字")}
地址：{_v(profile.company.address, "公司地址")}
电话：{_v(profile.company.phone, "联系电话")}
日期：{placeholder("日期")}

### 投标函附录

{_table(
    ["条款", "约定内容"],
    [
        ["项目名称", facts.project_name],
        ["标段名称", facts.bid_section],
        ["投标总价", placeholder("投标总价")],
        ["计划工期", f"{facts.duration_days} 日历天"],
        ["工程质量", facts.quality_requirement],
        ["投标有效期", _v(profile.bid.bid_validity_days, "投标有效期")],
        ["投标保证金", _v(profile.bid.bid_bond_amount, "投标保证金金额")],
        ["项目经理", _v(profile.project_manager.name, "项目经理姓名")],
    ],
)}"""


def render_legal_representative(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    person = profile.legal_representative
    return f"""法定代表人身份证明

投标人名称：{_company(profile)}
统一社会信用代码：{_v(profile.company.credit_code, "统一社会信用代码")}
姓名：{_v(person.name, "法定代表人姓名")}
性别：{_v(person.gender, "性别")}
年龄：{_v(person.age, "年龄")}
职务：{_v(person.position, "职务")}
身份证号：{_v(person.id_number, "法定代表人身份证号")}

特此证明。

投标人：{_company(profile)}（盖单位章）
日期：{placeholder("日期")}

附：法定代表人身份证复印件{placeholder("身份证复印件")}"""


def render_authorization(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    agent = profile.authorized_agent
    return f"""授权委托书

本人 {_v(profile.legal_representative.name, "法定代表人姓名")}（系 {_company(profile)} 法定代表人），现委托 {_v(agent.name, "委托代理人姓名")} 为我方代理人。
代理人根据授权，以我方名义签署、澄清、说明、补正、递交、撤回、修改 {context.facts.project_name} 投标文件、签订合同和处理有关事宜，其法律后果由我方承担。
委托期限：{placeholder("委托期限")}
代理人无转委托权。

投标人：{_company(profile)}（盖单位章）
法定代表人：{placeholder("法定代表人签字")}
身份证号：{_v(profile.legal_representative.id_number, "法定代表人身份证号")}
委托代理人：{_v(agent.name, "委托代理人姓名")}
身份证号：{_v(agent.id_number, "委托代理人身份证号")}
日期：{placeholder("日期")}"""


def render_consortium(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    state = profile.consortium.is_consortium
    if state is False:
        return "本项目我方不以联合体形式投标。\n\n联合体协议书：不适用。"
    if state is None:
        return (
            "【待填写：是否以联合体形式投标】\n\n"
            "如以联合体投标，请按招标文件格式提交联合体协议书，明确牵头人、各方工作分工、"
            "责任划分、资质与业绩证明材料。\n\n"
            f"联合体牵头人：{_v(profile.consortium.leader, '联合体牵头人')}\n"
            f"联合体成员：{placeholder('联合体成员名单')}"
        )
    return f"""联合体协议书

牵头人：{_v(profile.consortium.leader, "联合体牵头人")}
成员：{"、".join(profile.consortium.members) if profile.consortium.members else placeholder("联合体成员名单")}

各方就 {context.facts.project_name} 投标及合同实施的分工、责任、资质和业绩证明达成一致，具体内容：{placeholder("联合体协议书正文")}。
本协议书自各方盖章之日起生效。

牵头人：{placeholder("牵头人盖章/签字")}
成员：{placeholder("成员盖章/签字")}
日期：{placeholder("日期")}"""


def render_bid_bond(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    return f"""投标保证金

投标人：{_company(profile)}
项目名称：{context.facts.project_name}
标段：{context.facts.bid_section}
投标保证金金额：{_v(profile.bid.bid_bond_amount, "投标保证金金额")}
缴纳形式：{_v(profile.bid.bid_bond_method, "投标保证金形式")}
缴纳时间：{placeholder("缴纳时间")}
收款账户：{placeholder("收款账户信息")}

附：银行回单/保函/保险凭证{placeholder("投标保证金证明材料")}"""


def render_priced_boq(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    return f"""{placeholder("已标价工程量清单，须由造价工程师按招标文件工程量清单填报并签字盖章")}

说明：
- 本清单必须与招标文件第五章工程量清单的项目编码、项目名称、计量单位和工程量保持一致。
- 综合单价、合价、暂列金额、规费、税金等由投标人依据企业定额和市场价自主报价。
- 投标总价不得超过招标文件规定的最高投标限价（{context.facts.max_price_text}）。

{_table(
    ["序号", "项目编码", "项目名称", "计量单位", "工程量", "综合单价", "合价"],
    [["1", placeholder("项目编码"), placeholder("项目名称"), placeholder("单位"), placeholder("工程量"), placeholder("综合单价"), placeholder("合价")]],
)}"""


def render_project_management(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    pm = profile.project_manager
    rows = [
        [
            _v(pm.name, "项目经理姓名"),
            "项目经理",
            _v(pm.title, "项目经理职称"),
            _v(pm.certificate_name, "项目经理证书名称"),
            _v(pm.certificate_number, "项目经理证书编号"),
            _v(pm.similar_projects, "项目经理类似业绩"),
        ]
    ]
    for member in profile.personnel:
        rows.append(
            [
                _v(member.name, "人员姓名"),
                _v(member.role, "岗位"),
                _v(member.title, "职称"),
                _v(member.certificate_name, "证书名称"),
                _v(member.certificate_number, "证书编号"),
                _v(member.remarks, "备注"),
            ]
        )
    if len(rows) == 1 and not profile.personnel:
        rows.append([placeholder("姓名"), placeholder("岗位"), placeholder("职称"), placeholder("证书名称"), placeholder("证书编号"), placeholder("备注")])
    return (
        "项目管理机构及主要人员配备如下：\n\n"
        + _table(["姓名", "岗位", "职称", "证书名称", "证书编号", "类似业绩/备注"], rows)
        + "\n\n项目经理及主要管理人员简历、社保、证书扫描件："
        + placeholder("人员证书及社保证明")
    )


def render_subcontracting(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    state = profile.subcontracting.has_subcontracting
    if state is False:
        return "本项目我方不进行分包。\n\n拟分包项目情况表：无。"
    if state is None:
        return "【待填写：是否拟分包】\n\n如拟分包，请按招标文件格式填写分包项目、分包人及资质情况。"
    rows = []
    for item in profile.subcontracting.items:
        rows.append(
            [
                str(item.get("name") or placeholder("分包项目")),
                str(item.get("contractor") or placeholder("分包人")),
                str(item.get("qualification") or placeholder("分包人资质")),
                str(item.get("amount") or placeholder("分包金额")),
            ]
        )
    if not rows:
        rows = [[placeholder("分包项目"), placeholder("分包人"), placeholder("分包人资质"), placeholder("分包金额")]]
    return _table(["分包项目", "分包人", "资质", "金额"], rows)


def render_qualification_basic(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    company = profile.company
    return _table(
        ["项目", "内容"],
        [
            ["投标人名称", _v(company.name, "公司名称")],
            ["统一社会信用代码", _v(company.credit_code, "统一社会信用代码")],
            ["注册地址", _v(company.address, "公司地址")],
            ["法定代表人", _v(profile.legal_representative.name, "法定代表人姓名")],
            ["注册资本", _v(company.registered_capital, "注册资本")],
            ["企业类型", _v(company.company_type, "企业类型")],
            ["成立日期", _v(company.established_date, "成立日期")],
            ["营业期限", _v(company.business_term, "营业期限")],
            ["资质名称/等级", f"{_v(company.qualification_name, '资质名称')} / {_v(company.qualification_grade, '资质等级')}"],
            ["资质证书编号", _v(company.qualification_certificate, "资质证书编号")],
            ["安全生产许可证", _v(company.safety_license, "安全生产许可证编号")],
            ["联系电话", _v(company.phone, "联系电话")],
            ["开户银行", _v(company.bank_name, "开户银行")],
            ["银行账号", _v(company.bank_account, "银行账号")],
        ],
    )


def render_qualification_financial(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    rows = [
        [
            _v(year.year, "年度"),
            _v(year.revenue, "营业收入"),
            _v(year.net_profit, "净利润"),
            _v(year.total_assets, "资产总额"),
            _v(year.liabilities, "负债总额"),
            _v(year.audit_status, "审计情况"),
        ]
        for year in profile.financials.years
    ]
    if not rows:
        rows = [[placeholder("年度"), placeholder("营业收入"), placeholder("净利润"), placeholder("资产总额"), placeholder("负债总额"), placeholder("审计情况")]]
    return (
        _table(["年度", "营业收入", "净利润", "资产总额", "负债总额", "审计情况"], rows)
        + f"\n\n流动资产：{placeholder('流动资产')}；授信额度：{placeholder('授信额度')}。\n"
        + f"附：经审计财务报表及审计报告{placeholder('财务报表扫描件')}"
    )


def _project_table(projects) -> str:
    rows = [
        [
            _v(item.name, "项目名称"),
            _v(item.owner, "业主"),
            _v(item.location, "地点"),
            _v(item.contract_amount, "合同金额"),
            _v(item.scale, "规模"),
            _v(item.start_date, "开工日期"),
            _v(item.end_date, "完工日期"),
            _v(item.evidence, "证明材料"),
        ]
        for item in projects
    ]
    if not rows:
        rows = [[placeholder("项目名称"), placeholder("业主"), placeholder("地点"), placeholder("合同金额"), placeholder("规模"), placeholder("开工日期"), placeholder("完工日期"), placeholder("证明材料")]]
    return _table(["项目名称", "业主", "地点", "合同金额", "规模", "开工", "完工", "证明材料"], rows)


def render_qualification_similar(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    return _project_table(profile.similar_projects)


def render_qualification_ongoing(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    return _project_table(profile.ongoing_projects)


def render_qualification_litigation(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    rows = [
        [
            _v(item.year, "年度"),
            _v(item.case, "案件"),
            _v(item.amount, "金额"),
            _v(item.status, "状态"),
            _v(item.result, "结果"),
        ]
        for item in profile.litigation
    ]
    if not rows:
        rows = [[placeholder("年度"), placeholder("案件"), placeholder("金额"), placeholder("状态"), placeholder("结果")]]
    return _table(["年度", "案件", "金额", "状态", "结果"], rows)


def render_qualification_self_check(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    return _table(
        ["审查因素", "审查标准", "投标人响应", "证明材料"],
        [
            ["营业执照", "具备有效营业执照", placeholder("响应说明"), placeholder("营业执照扫描件")],
            ["安全生产许可证", "具备有效许可证", placeholder("响应说明"), placeholder("许可证扫描件")],
            ["资质等级", "符合招标文件要求", placeholder("响应说明"), placeholder("资质证书扫描件")],
            ["财务状况", "符合招标文件要求", placeholder("响应说明"), placeholder("财务报表")],
            ["业绩", "符合招标文件要求", placeholder("响应说明"), placeholder("中标通知书/合同/验收证明")],
            ["信誉", "无不良记录", placeholder("响应说明"), placeholder("信用证明")],
            ["项目经理", "资格符合要求", placeholder("响应说明"), placeholder("证书/社保")],
            ["其他要求", "符合招标文件要求", placeholder("响应说明"), placeholder("相关证明")],
        ],
    )


def render_qualification_bribery(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    return f"""投标人行贿犯罪档案查询结果

投标人：{_company(profile)}
查询结果：{placeholder("行贿犯罪档案查询结果")}
查询机构：{placeholder("查询机构")}
查询日期：{placeholder("查询日期")}

附：查询告知函/结果证明{placeholder("行贿犯罪档案查询证明")}"""


def render_qualification_integrity(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    return f"""投标人合格性及廉政声明书

致：{context.facts.tenderer}

我方 {_company(profile)} 郑重声明：
1. 我方具备履行本项目合同所需的资质、人员、设备和财务能力。
2. 我方未处于被责令停业、投标资格被取消、财产被接管或冻结等状态。
3. 我方在本项目投标及合同实施过程中遵守廉洁自律规定，不向招标人、评标委员会及其他相关方行贿或提供不正当利益。
4. 我方所提交的资料真实、合法、有效；如有虚假，愿承担相应法律责任。

投标人：{_company(profile)}（盖单位章）
法定代表人或委托代理人：{placeholder("签字")}
日期：{placeholder("日期")}"""


def render_qualification_other(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    return f"""{placeholder("其他资格审查资料清单及内容")}

可附：质量管理体系认证、环境管理体系认证、职业健康安全管理体系认证、信用等级证书、
获奖证书、重合同守信用证明等。所有证书编号与有效期：{placeholder("证书编号及有效期")}"""


def render_originals(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    items = [
        "营业执照副本", "资质证书副本", "安全生产许可证", "法定代表人身份证明",
        "授权委托书", "项目经理证书及社保", "类似项目业绩证明", "财务审计报告",
        "信用证明", "行贿犯罪档案查询结果", "投标保证金证明", "其他招标文件要求的原件",
    ]
    rows = [[str(index), item, placeholder("原件位置/页码"), placeholder("是否已提供")] for index, item in enumerate(items, start=1)]
    return "以下原件扫描件按招标文件要求装订，原件备查：\n\n" + _table(
        ["序号", "原件名称", "位置/页码", "备注"], rows
    )


def render_other_materials(
    context: TenderContext, profile: BidderProfile, outline: BidOutline
) -> str:
    return f"""其他资料及承诺

1. 扬尘污染防治承诺：{placeholder("扬尘污染防治承诺内容")}
2. 保障农民工工资支付承诺：{placeholder("保障农民工工资支付承诺内容")}
3. 非道路移动机械排放标准承诺：{placeholder("非道路移动机械排放承诺内容")}
4. 廉政承诺：{placeholder("廉政承诺内容")}
5. 其他补充资料：{placeholder("其他补充资料")}

投标人：{_company(profile)}
日期：{placeholder("日期")}"""


def _attachment_equipment(context, profile, outline) -> str:
    return _table(
        ["序号", "设备名称", "型号规格", "数量", "进场时间", "备注"],
        [["1", placeholder("设备名称"), placeholder("型号规格"), placeholder("数量"), placeholder("进场时间"), placeholder("备注")]],
    )


def _attachment_instruments(context, profile, outline) -> str:
    return _table(
        ["序号", "仪器设备名称", "型号规格", "数量", "用途", "备注"],
        [["1", placeholder("仪器设备名称"), placeholder("型号规格"), placeholder("数量"), placeholder("用途"), placeholder("备注")]],
    )


def _attachment_labor(context, profile, outline) -> str:
    return _table(
        ["工种", "人数", "进场时间", "备注"],
        [[placeholder("工种"), placeholder("人数"), placeholder("进场时间"), placeholder("备注")]],
    )


def _attachment_schedule(context, profile, outline) -> str:
    facts = context.facts
    return f"""计划开工日期：{facts.planned_start}
计划完工日期：{facts.planned_end}
计划工期：{facts.duration_days} 日历天

施工进度网络图：{placeholder("施工进度网络图/横道图")}

关键线路及里程碑：{placeholder("关键线路及里程碑")}"""


def _attachment_site_plan(context, profile, outline) -> str:
    return f"""施工总平面图：{placeholder("施工总平面图")}

说明：{placeholder("施工总平面布置说明")}"""


def _attachment_land(context, profile, outline) -> str:
    return _table(
        ["用途", "面积（m²）", "位置", "需用时间"],
        [[placeholder("用途"), placeholder("面积"), placeholder("位置"), placeholder("需用时间")]],
    )


Renderer = Callable[[TenderContext, BidderProfile, BidOutline], str]

TEMPLATES: dict[str, Renderer] = {
    "evaluation_index": render_evaluation_index,
    "bid_letter": render_bid_letter,
    "legal_representative": render_legal_representative,
    "authorization": render_authorization,
    "consortium": render_consortium,
    "bid_bond": render_bid_bond,
    "priced_boq": render_priced_boq,
    "project_management": render_project_management,
    "subcontracting": render_subcontracting,
    "qualification_basic": render_qualification_basic,
    "qualification_financial": render_qualification_financial,
    "qualification_similar": render_qualification_similar,
    "qualification_ongoing": render_qualification_ongoing,
    "qualification_litigation": render_qualification_litigation,
    "qualification_self_check": render_qualification_self_check,
    "qualification_bribery": render_qualification_bribery,
    "qualification_integrity": render_qualification_integrity,
    "qualification_other": render_qualification_other,
    "originals": render_originals,
    "other_materials": render_other_materials,
    "attachment_equipment": _attachment_equipment,
    "attachment_instruments": _attachment_instruments,
    "attachment_labor": _attachment_labor,
    "attachment_schedule": _attachment_schedule,
    "attachment_site_plan": _attachment_site_plan,
    "attachment_land": _attachment_land,
}


def render_template(
    section: OutlineSection,
    context: TenderContext,
    profile: BidderProfile,
    outline: BidOutline,
) -> str:
    if not section.template_key:
        return ""
    renderer = TEMPLATES.get(section.template_key)
    if renderer is None:
        return f"{placeholder(section.title + '内容')}"
    return renderer(context, profile, outline)
