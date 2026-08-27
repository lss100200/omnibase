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
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5

from omnibase.production.phase5_skill_contract import SkillDefinition, SkillVersion

_EMPTY_LOCK_BYTES = b'{"dependencies":[],"schema_version":1}\n'
_EMPTY_SBOM_BYTES = b'{"components":[],"format":"omnibase-preview-sbom","schema_version":1}\n'
_LOCK_DIGEST = hashlib.sha256(_EMPTY_LOCK_BYTES).hexdigest()
_SBOM_DIGEST = hashlib.sha256(_EMPTY_SBOM_BYTES).hexdigest()
_CATALOG_SCHEMA_VERSION = 1
_CATALOG_SIZE = 15
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
_CATEGORIES = frozenset(
    {
        "api",
        "context",
        "data",
        "dependency",
        "documentation",
        "engineering",
        "observability",
        "performance",
        "planning",
        "release",
        "research",
        "security",
        "testing",
        "ux",
    }
)
_ROLE_IDS = (
    "parent",
    "product",
    "ux",
    "frontend",
    "backend",
    "data",
    "security",
    "qa",
    "operations",
    "docs",
)


@dataclass(frozen=True, slots=True)
class NativeSkillCatalogItem:
    definition: SkillDefinition
    version: SkillVersion
    category: str
    summary: str
    tags: tuple[str, ...]
    recommended_roles: tuple[str, ...]
    instructions_bytes: int


def _item(
    *,
    definition_id: str,
    version_id: str,
    stable_key: str,
    display_name: str,
    description: str,
    category: str,
    tags: tuple[str, ...],
    recommended_roles: tuple[str, ...],
    instructions: str,
) -> NativeSkillCatalogItem:
    normalized = instructions.strip()
    if category not in _CATEGORIES:
        raise ValueError("native_skill_category_invalid")
    if not 2 <= len(tags) <= 5 or tags != tuple(sorted(set(tags))):
        raise ValueError("native_skill_tags_invalid")
    if any(_TAG_RE.fullmatch(tag) is None for tag in tags):
        raise ValueError("native_skill_tag_invalid")
    if not 1 <= len(recommended_roles) <= 4 or len(set(recommended_roles)) != len(
        recommended_roles
    ):
        raise ValueError("native_skill_recommended_roles_invalid")
    if any(role not in _ROLE_IDS for role in recommended_roles):
        raise ValueError("native_skill_recommended_role_invalid")
    if recommended_roles != tuple(sorted(recommended_roles, key=_ROLE_IDS.index)):
        raise ValueError("native_skill_recommended_roles_not_canonical")
    instructions_bytes = len(normalized.encode("utf-8"))
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
        tags=tags,
        recommended_roles=recommended_roles,
        instructions_bytes=instructions_bytes,
    )


_NATIVE_SKILLS = (
    _item(
        definition_id="61000000-0000-0000-0000-000000000001",
        version_id="61000000-0000-0000-0000-000000000101",
        stable_key="omnibase.requirement-clarifier",
        display_name="需求澄清师",
        description="把模糊需求收敛为目标、约束、验收标准和最小缺失信息。",
        category="planning",
        tags=("acceptance", "planning", "requirements"),
        recommended_roles=("parent", "product"),
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
        tags=("evidence", "research", "sources"),
        recommended_roles=("parent", "product", "docs"),
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
        tags=("code-review", "engineering", "risk"),
        recommended_roles=("parent", "frontend", "backend", "security"),
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
        tags=("checklist", "release", "rollback"),
        recommended_roles=("parent", "operations"),
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
        tags=("context", "continuity", "summary"),
        recommended_roles=("parent", "docs"),
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
        tags=("authorization", "personal", "security"),
        recommended_roles=("parent", "security"),
        instructions="""
个人版唯一人类所有者可以批准自己的操作，但 Agent 不能自批、扩大范围或绕过服务端约束。
检查操作是否会读取秘密、修改外部状态、访问业务数据库、执行任意命令或越过 Workspace 范围。
对破坏性、外部写入、付费或权限扩张动作要求明确授权；普通本地只读检查和范围内修改可继续。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000007",
        version_id="61000000-0000-0000-0000-000000000107",
        stable_key="omnibase.bug-triager",
        display_name="故障分诊员",
        description="把模糊故障收敛为可复现症状、证据、故障域和最小诊断顺序。",
        category="engineering",
        tags=("debugging", "evidence", "triage"),
        recommended_roles=("parent", "frontend", "backend", "qa"),
        instructions="""
先区分用户观察、日志或测试证据、推测和仍未知的信息，再给出最小可复现路径与候选故障域。
诊断顺序应优先使用范围内、只读、低成本检查，并为每一步说明能排除或确认什么。
不得把推测写成根因，不得声称未执行的修复有效，也不得建议通过删除数据、关闭安全校验或扩大权限来试错。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000008",
        version_id="61000000-0000-0000-0000-000000000108",
        stable_key="omnibase.test-strategist",
        display_name="测试策略师",
        description="依据风险和变更边界规划最小但充分的自动化验证矩阵。",
        category="testing",
        tags=("coverage", "risk", "testing"),
        recommended_roles=("frontend", "backend", "qa"),
        instructions="""
从变更目标、失败模式和安全不变量推导测试，不按文件数量机械堆叠用例。
分别说明单元、集成、端到端、负向和恢复测试的必要性，并标注哪些是计划、哪些已有直接执行证据。
优先覆盖权限跨线、作用域串线、幂等、并发、预算、未知结果和回滚；不得把跳过、未运行或模拟结果写成通过。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000009",
        version_id="61000000-0000-0000-0000-000000000109",
        stable_key="omnibase.api-contract-reviewer",
        display_name="API 契约审阅员",
        description="审查认证、租户边界、DTO、兼容性、幂等和错误语义。",
        category="api",
        tags=("api", "compatibility", "contract"),
        recommended_roles=("frontend", "backend", "security", "qa"),
        instructions="""
检查请求和响应是否具有封闭字段、稳定错误结构、明确版本与兼容边界，并确认认证后仍重新验证实时作用域。
对变更操作检查幂等、事务、审计和未知结果处理；对读取操作检查分页、预算和敏感字段最小化。
不得建议在公共 DTO、日志或错误中暴露密钥、物理数据库定位、内部授权对象或未绑定的资源标识。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000010",
        version_id="61000000-0000-0000-0000-000000000110",
        stable_key="omnibase.data-change-planner",
        display_name="数据变更规划师",
        description="规划 forward-only 数据结构变更、备份、验证和恢复边界。",
        category="data",
        tags=("data", "migration", "recovery"),
        recommended_roles=("backend", "data", "operations"),
        instructions="""
把结构变更、数据回填、兼容窗口、备份、验证和恢复拆成可审计步骤，并区分普通项目数据与一次性测试数据。
优先使用 forward fix 或恢复到新身份，明确锁、事务、并发写入和旧版本兼容风险。
本 Skill 只生成计划，不执行 SQL、不访问数据库、不读取凭据，也不得把未验证的备份写成可恢复。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000011",
        version_id="61000000-0000-0000-0000-000000000111",
        stable_key="omnibase.documentation-maintainer",
        display_name="文档维护员",
        description="让 README、路线图、交接和工程证据与当前实现保持一致。",
        category="documentation",
        tags=("accuracy", "documentation", "handover"),
        recommended_roles=("product", "docs"),
        instructions="""
先以当前源码、合同、测试输出和版本状态为事实来源，再修正文档中的过期、矛盾或夸大表述。
面向人类的内容应简洁说明产品价值、当前能力、限制和下一步；面向维护者的内容保留精确入口、约束和恢复方式。
不得复制秘密、内部物理定位或未公开材料，也不得把计划、预览、本地测试或候选状态写成正式发布和生产证明。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000012",
        version_id="61000000-0000-0000-0000-000000000112",
        stable_key="omnibase.ux-accessibility-reviewer",
        display_name="UX 与可访问性审阅员",
        description="检查字号、对比度、键盘、状态反馈、中文化和响应式体验。",
        category="ux",
        tags=("accessibility", "localization", "ux"),
        recommended_roles=("product", "ux", "frontend"),
        instructions="""
按主要用户旅程检查信息层级、字号与行高、颜色对比、键盘焦点、触控目标、加载、空、错误和成功状态。
区分从截图或代码直接观察到的问题与仍需真实浏览器验证的事项，并优先提出不破坏现有设计系统的最小修复。
不得仅以审美偏好判定缺陷，也不得声称未实际测试的设备、浏览器、读屏器或语言环境已经通过。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000013",
        version_id="61000000-0000-0000-0000-000000000113",
        stable_key="omnibase.performance-budget-reviewer",
        display_name="性能预算审阅员",
        description="用明确预算审查延迟、内存、存储、构建体积和 Token 成本。",
        category="performance",
        tags=("budget", "performance", "profiling"),
        recommended_roles=("frontend", "backend", "operations"),
        instructions="""
先确定用户可感知指标、测量环境、输入规模和可接受预算，再识别最可能的瓶颈与最小测量方案。
将基准实测、静态估算和假设分开，说明冷启动、缓存、并发和失败重试对结果的影响。
不得虚构 benchmark、硬件或流量，不得通过关闭安全检查、丢弃持久化或隐藏失败来换取表面性能。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000014",
        version_id="61000000-0000-0000-0000-000000000114",
        stable_key="omnibase.dependency-risk-reviewer",
        display_name="依赖风险审阅员",
        description="依据现有 lockfile、许可证和漏洞证据评估依赖变更风险。",
        category="dependency",
        tags=("dependency", "license", "supply-chain"),
        recommended_roles=("backend", "security", "operations"),
        instructions="""
检查依赖来源、锁定状态、版本跨度、传递依赖、许可证、维护状态和已提供的漏洞证据。
将已确认风险、需要联网核验的风险和纯推测分开，并为升级、替换或暂缓给出兼容与回退条件。
本 Skill 不自行联网、安装或升级依赖，不得把没有检索到漏洞写成安全，也不得建议绕过锁文件或签名校验。
""",
    ),
    _item(
        definition_id="61000000-0000-0000-0000-000000000015",
        version_id="61000000-0000-0000-0000-000000000115",
        stable_key="omnibase.observability-planner",
        display_name="可观测性规划师",
        description="规划有界日志、指标、Trace、告警和脱敏要求。",
        category="observability",
        tags=("logging", "observability", "telemetry"),
        recommended_roles=("backend", "security", "operations"),
        instructions="""
围绕用户旅程和失败模式定义少量可行动的日志、指标、Trace 与告警，并明确采样、保留和成本预算。
所有观测字段必须避免密钥、令牌、密码、提示词正文、用户文件内容和物理数据库定位，作用域标识也应最小化。
本 Skill 只规划可观测性，不发送遥测、不连接外部服务，也不得把日志存在等同于问题已经被监控或处理。
""",
    ),
)


def _validate_catalog(items: tuple[NativeSkillCatalogItem, ...]) -> None:
    if len(items) != _CATALOG_SIZE:
        raise ValueError("native_skill_catalog_size_invalid")
    definition_ids = [item.definition.skill_definition_id for item in items]
    version_ids = [item.version.skill_version_id for item in items]
    stable_keys = [item.definition.stable_logical_key for item in items]
    if len(definition_ids) != len(set(definition_ids)):
        raise ValueError("native_skill_definition_id_duplicate")
    if len(version_ids) != len(set(version_ids)):
        raise ValueError("native_skill_version_id_duplicate")
    if len(stable_keys) != len(set(stable_keys)):
        raise ValueError("native_skill_stable_key_duplicate")
    for item in items:
        if item.instructions_bytes != len(item.version.instructions.encode("utf-8")):
            raise ValueError("native_skill_instructions_bytes_drifted")


_validate_catalog(_NATIVE_SKILLS)


def _snapshot_item(item: NativeSkillCatalogItem) -> NativeSkillCatalogItem:
    """Return a detached snapshot so callers cannot mutate the source catalog."""

    return NativeSkillCatalogItem(
        definition=item.definition,
        version=deepcopy(item.version),
        category=item.category,
        summary=item.summary,
        tags=item.tags,
        recommended_roles=item.recommended_roles,
        instructions_bytes=item.instructions_bytes,
    )


def list_native_skills() -> tuple[NativeSkillCatalogItem, ...]:
    return tuple(
        _snapshot_item(item)
        for item in sorted(_NATIVE_SKILLS, key=lambda item: item.definition.stable_logical_key)
    )


def native_skill_catalog_digest() -> str:
    payload = [
        {
            "category": item.category,
            "definition": item.definition.to_dict(),
            "instructions_bytes": item.instructions_bytes,
            "recommended_roles": list(item.recommended_roles),
            "summary": item.summary,
            "tags": list(item.tags),
            "version": item.version.to_dict(),
        }
        for item in list_native_skills()
    ]
    encoded = json.dumps(
        {"items": payload, "schema_version": _CATALOG_SCHEMA_VERSION},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def native_skill_categories() -> tuple[str, ...]:
    return tuple(sorted({item.category for item in _NATIVE_SKILLS}))


def filter_native_skills(
    *, q: str | None = None, category: str | None = None, role: str | None = None
) -> tuple[NativeSkillCatalogItem, ...]:
    if category is not None and category not in _CATEGORIES:
        raise ValueError("native_skill_category_invalid")
    if role is not None and role not in _ROLE_IDS:
        raise ValueError("native_skill_role_invalid")
    query = q.strip().casefold() if q is not None else None
    if q is not None and not query:
        raise ValueError("native_skill_query_invalid")
    items = list_native_skills()
    if category is not None:
        items = tuple(item for item in items if item.category == category)
    if role is not None:
        items = tuple(item for item in items if role in item.recommended_roles)
    if query is not None:
        items = tuple(
            item
            for item in items
            if query
            in "\n".join(
                (
                    item.definition.stable_logical_key,
                    item.definition.display_name,
                    item.summary,
                    item.category,
                    *item.tags,
                )
            ).casefold()
        )
    return items


def get_native_skill(stable_key: str) -> NativeSkillCatalogItem:
    for item in _NATIVE_SKILLS:
        if item.definition.stable_logical_key == stable_key:
            return _snapshot_item(item)
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
    return NativeSkillCatalogItem(
        definition=definition,
        version=version,
        category=item.category,
        summary=item.summary,
        tags=item.tags,
        recommended_roles=item.recommended_roles,
        instructions_bytes=item.instructions_bytes,
    )


__all__ = [
    "NativeSkillCatalogItem",
    "filter_native_skills",
    "get_native_skill",
    "list_native_skills",
    "materialize_native_skill",
    "native_skill_catalog_digest",
    "native_skill_categories",
]
