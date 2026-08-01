"""Strict logical contracts for P34.3 controlled row mutations.

The DTOs contain only logical resource and column UUIDs.  Physical schemas,
table names, column names, locators, SQL fragments, and row tokens are not
accepted from callers.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX_MUTATION_ROWS = 100
MAX_MUTATION_TIMEOUT_MS = 5_000
MAX_MUTATION_PAYLOAD_BYTES = 262_144

IdempotencyKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]
MutationScalar = str | StrictInt | StrictFloat | StrictBool | None


class MutationModel(BaseModel):
    """Every mutation contract rejects undeclared scope and physical fields."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MutationCompare(MutationModel):
    kind: Literal["compare"] = "compare"
    column_id: UUID
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "is_null"]
    value: MutationScalar | list[MutationScalar] = None

    @model_validator(mode="after")
    def validate_operator_value(self) -> MutationCompare:
        if self.op == "in":
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= 50:
                raise ValueError("in requires between 1 and 50 scalar values")
        elif self.op == "is_null":
            if not isinstance(self.value, bool):
                raise ValueError("is_null requires a boolean value")
        elif isinstance(self.value, list):
            raise ValueError(f"{self.op} requires one scalar value")
        return self


class MutationBoolean(MutationModel):
    kind: Literal["and", "or"]
    clauses: list[Annotated[MutationCompare | MutationBoolean, Field(discriminator="kind")]] = (
        Field(min_length=1, max_length=10)
    )


MutationPredicate = Annotated[
    MutationCompare | MutationBoolean,
    Field(discriminator="kind"),
]


class MutationRequest(MutationModel):
    resource_id: UUID
    resource_version: StrictInt = Field(ge=1)
    idempotency_key: IdempotencyKey
    timeout_ms: StrictInt = Field(default=2_000, ge=1, le=MAX_MUTATION_TIMEOUT_MS)


class InsertMutationRequest(MutationRequest):
    kind: Literal["insert"] = "insert"
    rows: list[dict[UUID, MutationScalar]] = Field(
        min_length=1,
        max_length=MAX_MUTATION_ROWS,
    )

    @field_validator("rows")
    @classmethod
    def validate_rows(
        cls, rows: list[dict[UUID, MutationScalar]]
    ) -> list[dict[UUID, MutationScalar]]:
        for row in rows:
            if not 1 <= len(row) <= 128:
                raise ValueError("each inserted row requires between 1 and 128 columns")
        return rows


class FilteredMutationRequest(MutationRequest):
    predicate: MutationPredicate
    max_rows: StrictInt = Field(default=MAX_MUTATION_ROWS, ge=1, le=MAX_MUTATION_ROWS)

    @model_validator(mode="after")
    def bounded_predicate(self) -> FilteredMutationRequest:
        count = 0

        def walk(node: MutationCompare | MutationBoolean, depth: int) -> None:
            nonlocal count
            count += 1
            if depth > 4 or count > 32:
                raise ValueError("predicate exceeds the maximum depth or node count")
            if isinstance(node, MutationBoolean):
                for clause in node.clauses:
                    walk(clause, depth + 1)

        walk(self.predicate, 1)
        return self


class UpdateMutationRequest(FilteredMutationRequest):
    kind: Literal["update"] = "update"
    values: dict[UUID, MutationScalar] = Field(min_length=1, max_length=128)


class DeleteMutationRequest(FilteredMutationRequest):
    kind: Literal["delete"] = "delete"


MutationRequestUnion = Annotated[
    InsertMutationRequest | UpdateMutationRequest | DeleteMutationRequest,
    Field(discriminator="kind"),
]


MutationBoolean.model_rebuild()

__all__ = [
    "MAX_MUTATION_PAYLOAD_BYTES",
    "MAX_MUTATION_ROWS",
    "MAX_MUTATION_TIMEOUT_MS",
    "DeleteMutationRequest",
    "FilteredMutationRequest",
    "IdempotencyKey",
    "InsertMutationRequest",
    "MutationBoolean",
    "MutationCompare",
    "MutationModel",
    "MutationPredicate",
    "MutationRequest",
    "MutationRequestUnion",
    "MutationScalar",
    "UpdateMutationRequest",
]
