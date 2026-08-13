# ruff: noqa: RUF001
"""P6.1 first-party native instruction-Skill catalog.

The catalog is source-owned, immutable and deliberately instruction-only.  It
does not scan arbitrary user directories, download packages or grant tools,
network, secrets, MCP, Planner or Multi-Agent authority.  Browser installation
persists the exact existing P5.6P SkillDefinition/SkillVersion contracts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5

from omnibase.production.phase5_skill_contract import SkillDefinition, SkillVersion

_EMPTY_LOCK_BYTES = b'{"dependencies":[],"schema_version":1}\n'
_EMPTY_SBOM_BYTES = b'{"components":[],"format":"omnibase-preview-sbom","schema_version":1}\n'
_LOCK_DIGEST = hashlib.sha256(_EMPTY_LOCK_BYTES).hexdigest()
_SBOM_DIGEST = hashlib.sha256(_EMPTY_SBOM_BYTES).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeSkillCatalogItem:
    definition: SkillDefinition
    version: SkillVersion
    category: str
    summary: str


def _item(
    *,
    definition_id: str,
    version_id: str,
    stable_key: str,
    display_name: str,
    description: str,
    category: str,
    instructions: str,
) -> NativeSkillCatalogItem:
    normalized = instructions.strip()
    source_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    definition = SkillDefinition.from_mapping(
        {
            "skill_definition_id": definition_id,
            "stable_logical_key": stable_key,
            "display_name": display_name,
            "description": description,
            "definition_state": "active",
            "allowed_installation_scopes": ["workspace"],
            "first_party": True,
        }
    )
    version = SkillVersion.from_mapping(
        {
            "skill_version_id": version_id,
            "skill_definition_id": definition_id,
            "version": "1.0.0",
            "version_state": "tested",
            "kind": "instruction",
            "instructions": normalized,
            "instructions_digest": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "input_schema": {"type": "object", "additionalProperties": False},
            "output_schema": {"type": "object", "additionalProperties": False},
            "required_tool_ids": [],
            "capability_requirements": [],
            "supported_agent_version_digests": [],
            "risk_level": "low",
            "budget": {
                "max_context_tokens": 4096,
                "max_output_tokens": 2048,
                "max_tool_calls": 0,
                "max_wall_clock_seconds": 120,
                "max_cost_units": 5000,
            },
            "network_policy": "deny",
            "secrets_allowed": False,
            "source_sha256": source_digest,
            "dependency_lock_sha256": _LOCK_DIGEST,
            "sbom_sha256": _SBOM_DIGEST,
            "signature_status": "unverified",
            "verification_commands": [
                {
                    "command_id": "native-catalog-contract",
                    "profile": "pytest",
                    "arguments": ["tests/test_p6_1_native_skill_catalog.py", "-q"],
                    "network_allowed": False,
                }
            ],
            "rollback_version_id": None,
        }
    )
    return NativeSkillCatalogItem(
        definition=definition,
        version=version,
        category=category,
        summary=description,
    )


_NATIVE_SKILLS = (
    _item(
        definition_id="61000000-0000-0000-0000-000000000001",
        version_id="61000000-0000-0000-0000-000000000101",
        stable_key="omnibase.requirement-clarifier",
        display_name="需求澄清师",
        description="把模糊需求收敛为目标、约束、验收标准和最小缺失信息。",
        category="planning",
        instructions="""
先复述用户真正要完成的结果，再列出已知约束、验收标准与会改变实现方向的缺失信息。
只询问最小必要问题；能够从当前上下文安全推断的内容直接注明假设并继续。
不要把偏好升级为安全要求，也不要虚构进度、文件、接口或验证结果。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000002",
        version_id="61000000-0000-0000-0000-000000000102",
        stable_key="omnibase.evidence-first-researcher",
        display_name="证据优先研究员",
        description="区分事实、推断和未知信息，输出可核验的研究结论。",
        category="research",
        instructions="""
将结论分为直接证据、合理推断和仍未证明三类。
只引用当前会话实际提供或检索到的来源；来源冲突时明确指出冲突，不得用措辞掩盖。
证据不足时缩小结论或说明缺失项，不要把没有找到证据写成事实上的不存在。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000003",
        version_id="61000000-0000-0000-0000-000000000103",
        stable_key="omnibase.change-reviewer",
        display_name="变更审阅员",
        description="面向个人项目的代码与文档变更风险审阅。",
        category="engineering",
        instructions="""
先检查变更是否满足用户目标，再检查安全边界、兼容性、失败恢复和验证证据。
发现问题时给出严重度、可复现条件、影响和最小修复建议；没有问题时明确说明检查范围。
不得把未运行的测试写成通过，也不得建议用关闭校验、隐藏错误或扩大权限来解决问题。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000004",
        version_id="61000000-0000-0000-0000-000000000104",
        stable_key="omnibase.release-checklist",
        display_name="发布清单助手",
        description="生成可执行、可回滚且不夸大证据的个人版发布清单。",
        category="release",
        instructions="""
发布清单必须覆盖版本、构建产物、依赖、配置形状、数据兼容、健康检查、回滚和未证明项。
本地工程验证、预览、真实部署和生产证据必须分别表述。
任何凭据、根 .env、用户文件和数据库材料都不得进入发布包或诊断输出。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000005",
        version_id="61000000-0000-0000-0000-000000000105",
        stable_key="omnibase.context-curator",
        display_name="上下文整理师",
        description="压缩会话与文件上下文，同时保留决策、约束和未完成事项。",
        category="context",
        instructions="""
优先保留当前目标、用户明确决定、安全约束、关键文件、验证结果、阻塞项和下一步。
删除重复叙述、过时尝试和不影响后续工作的细节；不得丢失否定条件或把未完成写成完成。
敏感内容只保留类别和必要状态，不复述密钥、令牌、密码或物理数据库定位信息。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000006",
        version_id="61000000-0000-0000-0000-000000000106",
        stable_key="omnibase.personal-security-checker",
        display_name="个人版安全检查员",
        description="以单用户产品边界检查权限、数据和外部副作用。",
        category="security",
        instructions="""
个人版唯一人类所有者可以批准自己的操作，但 Agent 不能自批、扩大范围或绕过服务端约束。
检查操作是否会读取秘密、修改外部状态、访问业务数据库、执行任意命令或越过 Workspace 范围。
对破坏性、外部写入、付费或权限扩张动作要求明确授权；普通本地只读检查和范围内修改可继续。
""",
    ),
)


def list_native_skills() -> tuple[NativeSkillCatalogItem, ...]:
    return tuple(sorted(_NATIVE_SKILLS, key=lambda item: item.definition.stable_logical_key))


def get_native_skill(stable_key: str) -> NativeSkillCatalogItem:
    for item in _NATIVE_SKILLS:
        if item.definition.stable_logical_key == stable_key:
            return item
    raise KeyError("native_skill_not_found")


def materialize_native_skill(
    item: NativeSkillCatalogItem, *, tenant_id: str
) -> NativeSkillCatalogItem:
    """Project catalog source identity into tenant-owned global primary keys."""

    definition_id = str(
        uuid5(
            NAMESPACE_URL,
            f"omnibase:native-skill-definition:{tenant_id}:{item.definition.stable_logical_key}",
        )
    )
    version_id = str(
        uuid5(
            NAMESPACE_URL,
            f"omnibase:native-skill-version:{tenant_id}:{item.definition.stable_logical_key}:{item.version.version}",
        )
    )
    definition = replace(item.definition, skill_definition_id=definition_id)
    version = replace(
        item.version,
        skill_version_id=version_id,
        skill_definition_id=definition_id,
    )
    # Force canonical serialization here so a future dataclass extension cannot
    # silently introduce a non-JSON tenant identity.
    json.dumps(version.to_dict(), sort_keys=True, separators=(",", ":"))
    return NativeSkillCatalogItem(definition, version, item.category, item.summary)


__all__ = [
    "NativeSkillCatalogItem",
    "get_native_skill",
    "list_native_skills",
    "materialize_native_skill",
]
