"""Bidder profile schema, loading and placeholder rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .errors import BidInputError


def placeholder(label: str) -> str:
    return f"【待填写：{label}】"


def resolve(value: str | None, label: str) -> str:
    text = (value or "").strip()
    return text if text else placeholder(label)


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class CompanyInfo(ProfileModel):
    name: str | None = None
    credit_code: str | None = None
    registered_capital: str | None = None
    company_type: str | None = None
    established_date: str | None = None
    business_term: str | None = None
    address: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    fax: str | None = None
    website: str | None = None
    qualification_name: str | None = None
    qualification_grade: str | None = None
    qualification_certificate: str | None = None
    safety_license: str | None = None
    bank_name: str | None = None
    bank_branch: str | None = None
    bank_account: str | None = None


class PersonInfo(ProfileModel):
    name: str | None = None
    gender: str | None = None
    age: str | None = None
    id_number: str | None = None
    position: str | None = None


class ProjectManager(PersonInfo):
    education: str | None = None
    title: str | None = None
    certificate_name: str | None = None
    certificate_grade: str | None = None
    certificate_number: str | None = None
    specialty: str | None = None
    safety_certificate: str | None = None
    social_security: str | None = None
    similar_projects: str | None = None


class PersonnelMember(ProfileModel):
    role: str | None = None
    name: str | None = None
    title: str | None = None
    certificate_name: str | None = None
    certificate_grade: str | None = None
    certificate_number: str | None = None
    specialty: str | None = None
    remarks: str | None = None


class ProjectRecord(ProfileModel):
    name: str | None = None
    owner: str | None = None
    location: str | None = None
    contract_amount: str | None = None
    scale: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    role: str | None = None
    evidence: str | None = None


class FinancialYear(ProfileModel):
    year: str | None = None
    revenue: str | None = None
    net_profit: str | None = None
    total_assets: str | None = None
    liabilities: str | None = None
    audit_status: str | None = None


class Financials(ProfileModel):
    years: list[FinancialYear] = Field(default_factory=list)
    liquid_funds: str | None = None
    line_of_credit: str | None = None


class CreditInfo(ProfileModel):
    rating: str | None = None
    authority: str | None = None
    certificates: list[str] = Field(default_factory=list)


class Certificate(ProfileModel):
    name: str | None = None
    number: str | None = None
    valid_until: str | None = None


class LitigationRecord(ProfileModel):
    year: str | None = None
    case: str | None = None
    amount: str | None = None
    status: str | None = None
    result: str | None = None


class ConsortiumInfo(ProfileModel):
    is_consortium: bool | None = None
    leader: str | None = None
    members: list[str] = Field(default_factory=list)


class SubcontractingInfo(ProfileModel):
    has_subcontracting: bool | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


class BidInfo(ProfileModel):
    bid_bond_amount: str | None = None
    bid_bond_method: str | None = None
    bid_validity_days: str | None = None
    planned_start: str | None = None
    planned_end: str | None = None
    quality_commitment: str | None = None


class BidderProfile(ProfileModel):
    company: CompanyInfo = Field(default_factory=CompanyInfo)
    legal_representative: PersonInfo = Field(default_factory=PersonInfo)
    authorized_agent: PersonInfo = Field(default_factory=PersonInfo)
    project_manager: ProjectManager = Field(default_factory=ProjectManager)
    personnel: list[PersonnelMember] = Field(default_factory=list)
    similar_projects: list[ProjectRecord] = Field(default_factory=list)
    ongoing_projects: list[ProjectRecord] = Field(default_factory=list)
    financials: Financials = Field(default_factory=Financials)
    credit: CreditInfo = Field(default_factory=CreditInfo)
    management_certificates: list[Certificate] = Field(default_factory=list)
    litigation: list[LitigationRecord] = Field(default_factory=list)
    consortium: ConsortiumInfo = Field(default_factory=ConsortiumInfo)
    subcontracting: SubcontractingInfo = Field(default_factory=SubcontractingInfo)
    bid: BidInfo = Field(default_factory=BidInfo)
    other: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def empty(cls) -> "BidderProfile":
        return cls()

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "BidderProfile":
        if not data:
            return cls()
        try:
            return cls.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - pydantic error text is useful
            raise BidInputError(f"invalid bidder profile: {exc}") from exc

    @classmethod
    def from_file(cls, path: str | Path) -> "BidderProfile":
        target = Path(path)
        if not target.is_file():
            raise BidInputError(f"bidder profile not found: {target}")
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BidInputError(f"cannot read bidder profile {target}: {exc}") from exc
        return cls.from_mapping(data)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def facts(self) -> dict[str, str]:
        """Return only explicitly supplied scalar facts (never placeholders)."""
        facts: dict[str, str] = {}

        def walk(prefix: str, value: Any) -> None:
            if isinstance(value, BaseModel):
                for name, child in value.__dict__.items():
                    walk(f"{prefix}.{name}" if prefix else name, child)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(
                        f"{prefix}[{index}]" if prefix else f"[{index}]",
                        child,
                    )
            elif isinstance(value, dict):
                for name, child in value.items():
                    walk(f"{prefix}.{name}" if prefix else name, child)
            elif value is not None and str(value).strip():
                facts[prefix] = str(value).strip()

        walk("", self)
        return facts

    def missing_required_fields(self) -> list[dict[str, str]]:
        checks = [
            (self.company.name, "company.name", "公司名称"),
            (self.company.address, "company.address", "公司地址"),
            (self.company.phone, "company.phone", "联系电话"),
            (self.legal_representative.name, "legal_representative.name", "法定代表人姓名"),
            (self.legal_representative.id_number, "legal_representative.id_number", "法定代表人身份证号"),
            (self.authorized_agent.name, "authorized_agent.name", "委托代理人姓名"),
            (self.authorized_agent.id_number, "authorized_agent.id_number", "委托代理人身份证号"),
            (self.project_manager.name, "project_manager.name", "项目经理姓名"),
            (self.project_manager.id_number, "project_manager.id_number", "项目经理身份证号"),
            (self.project_manager.certificate_number, "project_manager.certificate_number", "项目经理证书编号"),
            (self.company.bank_name, "company.bank_name", "开户银行"),
            (self.company.bank_account, "company.bank_account", "银行账号"),
            (self.bid.bid_bond_amount, "bid.bid_bond_amount", "投标保证金金额"),
            (self.bid.bid_validity_days, "bid.bid_validity_days", "投标有效期"),
        ]
        missing: list[dict[str, str]] = []
        for value, path, label in checks:
            if not (value or "").strip():
                missing.append({"path": path, "label": label})
        return missing

    def brief(self) -> str:
        """Return a non-sensitive digest for Writer/Reviewer prompts.

        Deliberately excludes identity numbers, bank details, the credit code
        and the raw ``other`` mapping.  This is the only profile data sent to
        the model, never the full ``profile.json`` snapshot.
        """

        lines: list[str] = []

        def join(*values: object) -> str:
            return "；".join(
                str(value).strip()
                for value in values
                if value is not None and str(value).strip()
            )

        def add(label: str, value: object) -> None:
            text = str(value or "").strip()
            if text:
                lines.append(f"{label}：{text}")

        company = self.company
        add("公司名称", company.name)
        add("公司地址", company.address)
        add("联系电话", company.phone)
        add("传真", company.fax)
        add("网站", company.website)
        add("注册资本", company.registered_capital)
        add("公司类型", company.company_type)
        add("成立日期", company.established_date)
        add("营业期限", company.business_term)
        add("资质名称", company.qualification_name)
        add("资质等级", company.qualification_grade)
        add("资质证书编号", company.qualification_certificate)
        add("安全生产许可证", company.safety_license)

        legal = join(self.legal_representative.name, self.legal_representative.position)
        if legal:
            lines.append(f"法定代表人：{legal}")
        agent = join(self.authorized_agent.name, self.authorized_agent.position)
        if agent:
            lines.append(f"委托代理人：{agent}")

        pm = self.project_manager
        manager = join(
            pm.name,
            pm.title,
            pm.certificate_name,
            pm.certificate_number,
            pm.specialty,
        )
        if manager:
            lines.append(f"项目经理：{manager}")

        if self.personnel:
            lines.append("拟投入人员：")
            for member in self.personnel:
                text = join(
                    member.role,
                    member.name,
                    member.title,
                    member.certificate_name,
                    member.certificate_number,
                )
                if text:
                    lines.append(f"- {text}")

        for title, records in (
            ("类似业绩：", self.similar_projects),
            ("在建业绩：", self.ongoing_projects),
        ):
            if not records:
                continue
            lines.append(title)
            for record in records:
                text = join(
                    record.name,
                    record.owner,
                    record.location,
                    record.contract_amount,
                    record.scale,
                    record.role,
                )
                if text:
                    lines.append(f"- {text}")

        if self.financials.years:
            lines.append("财务数据：")
            for year in self.financials.years:
                parts = [
                    str(value).strip()
                    for value in (
                        year.year,
                        f"营业收入 {year.revenue}" if year.revenue else "",
                        f"净利润 {year.net_profit}" if year.net_profit else "",
                        f"总资产 {year.total_assets}" if year.total_assets else "",
                        f"负债 {year.liabilities}" if year.liabilities else "",
                    )
                    if value and str(value).strip()
                ]
                if parts:
                    lines.append(f"- {'；'.join(parts)}")
        add("流动资金", self.financials.liquid_funds)
        add("授信额度", self.financials.line_of_credit)

        credit = join(self.credit.rating, self.credit.authority)
        if credit:
            lines.append(f"信用评价：{credit}")
        if self.credit.certificates:
            lines.append("信用/体系证书：" + "；".join(self.credit.certificates))

        if self.management_certificates:
            lines.append("管理体系认证：")
            for cert in self.management_certificates:
                text = join(cert.name, cert.number, cert.valid_until)
                if text:
                    lines.append(f"- {text}")

        bid = self.bid
        add("投标保证金", join(bid.bid_bond_amount, bid.bid_bond_method))
        add("投标有效期", bid.bid_validity_days)
        add("计划开工日期", bid.planned_start)
        add("计划竣工日期", bid.planned_end)
        add("质量承诺", bid.quality_commitment)

        if self.litigation:
            lines.append("诉讼及仲裁：")
            for item in self.litigation:
                text = join(item.year, item.case, item.amount, item.status, item.result)
                if text:
                    lines.append(f"- {text}")

        if self.consortium.is_consortium:
            add("联合体", join(self.consortium.leader, "、".join(self.consortium.members)))
        if self.subcontracting.has_subcontracting:
            lines.append("拟分包内容以投标文件分包计划为准。")

        return "\n".join(lines)

    def company_name(self) -> str:
        return resolve(self.company.name, "公司名称")

    def display_name(self) -> str:
        return self.company_name()
