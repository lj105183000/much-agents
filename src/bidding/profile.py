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
                return
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

    def company_name(self) -> str:
        return resolve(self.company.name, "公司名称")

    def display_name(self) -> str:
        return self.company_name()
