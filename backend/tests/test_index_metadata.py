"""Tests for omnibase.rag.index_metadata — the active-index identity seam."""

from __future__ import annotations

import pytest

from omnibase.rag.index_metadata import (
    ACTIVE_METADATA,
    INDEX_REGISTRY,
    DimensionMismatchError,
    IndexMetadata,
    IndexVersion,
    get_active_metadata,
    get_index_lane,
    get_index_metadata,
    validate_dimension,
)


class TestIndexMetadataConstruction:
    """Given valid field values, When IndexMetadata is constructed,
    Then all fields are accessible and the object is frozen."""

    def test_fields_resolve_correctly(self) -> None:
        """Given explicit field values,
        When constructing IndexMetadata,
        Then model_name, dimension, version match inputs."""
        meta = IndexMetadata(
            model_name="BAAI/bge-small-zh-v1.5",
            dimension=512,
            version=1,
        )
        assert meta.model_name == "BAAI/bge-small-zh-v1.5"
        assert meta.dimension == 512
        assert meta.version == 1

    def test_frozen_configuration(self) -> None:
        """Given a constructed IndexMetadata,
        When inspecting its dataclass params,
        Then frozen is True (mutation would raise FrozenInstanceError)."""
        meta = IndexMetadata(
            model_name="BAAI/bge-small-zh-v1.5",
            dimension=512,
            version=1,
        )
        assert meta.__dataclass_params__.frozen is True


class TestActiveMetadata:
    """Given the module singleton ACTIVE_METADATA,
    When accessed directly or through get_active_metadata(),
    Then it resolves to BAAI/bge-small-zh-v1.5, dim 512, version 1."""

    def test_active_model_is_bge_small_zh(self) -> None:
        """Given the active metadata,
        When checking model_name,
        Then it is BAAI/bge-small-zh-v1.5."""
        assert ACTIVE_METADATA.model_name == "BAAI/bge-small-zh-v1.5"

    def test_active_dimension_is_512(self) -> None:
        """Given the active metadata,
        When checking dimension,
        Then it is 512."""
        assert ACTIVE_METADATA.dimension == 512

    def test_active_version_is_1(self) -> None:
        """Given the active metadata,
        When checking version,
        Then it is 1."""
        assert ACTIVE_METADATA.version == 1

    def test_get_active_metadata_returns_same_object(self) -> None:
        """Given the module's get_active_metadata function,
        When called,
        Then it returns the ACTIVE_METADATA singleton."""
        result = get_active_metadata()
        assert result is ACTIVE_METADATA

    def test_get_active_metadata_fields_match(self) -> None:
        """Given get_active_metadata return value,
        When inspecting fields,
        Then all three fields match the expected constants."""
        meta = get_active_metadata()
        assert meta.model_name == "BAAI/bge-small-zh-v1.5"
        assert meta.dimension == 512
        assert meta.version == 1


class TestValidateDimension:
    """Given the validate_dimension function,
    When called with various dimension values,
    Then it passes for 512 and raises DimensionMismatchError otherwise."""

    def test_passes_when_dimension_matches_512(self) -> None:
        """Given expected=512 matching active,
        When validate_dimension(512),
        Then no error is raised."""
        validate_dimension(512)  # should not raise

    def test_raises_on_dimension_mismatch_1024(self) -> None:
        """Given expected=1024 conflicting with active 512,
        When validate_dimension(1024),
        Then DimensionMismatchError is raised with expected=512, actual=1024."""
        with pytest.raises(DimensionMismatchError) as exc_info:
            validate_dimension(1024)
        assert exc_info.value.expected == 512
        assert exc_info.value.actual == 1024

    def test_raises_on_dimension_mismatch_768(self) -> None:
        """Given expected=768 conflicting with active 512,
        When validate_dimension(768),
        Then DimensionMismatchError is raised."""
        with pytest.raises(DimensionMismatchError) as exc_info:
            validate_dimension(768)
        assert exc_info.value.expected == 512
        assert exc_info.value.actual == 768

    def test_error_message_contains_both_values(self) -> None:
        """Given a dimension mismatch,
        When the exception is stringified,
        Then the message mentions both expected and actual values."""
        with pytest.raises(DimensionMismatchError) as exc_info:
            validate_dimension(384)
        msg = str(exc_info.value)
        assert "512" in msg
        assert "384" in msg

    def test_error_is_subclass_of_valueerror(self) -> None:
        """Given a DimensionMismatchError,
        When checking its type hierarchy,
        Then it is a ValueError subclass for compatibility."""
        assert issubclass(DimensionMismatchError, ValueError)


class TestVersionRegistry:
    def test_registry_is_closed_and_immutable(self) -> None:
        assert tuple(INDEX_REGISTRY) == (IndexVersion.V1, IndexVersion.V2)
        with pytest.raises(TypeError):
            INDEX_REGISTRY[IndexVersion.V1] = ACTIVE_METADATA  # type: ignore[index]

    def test_v1_contract_preserves_existing_identity(self) -> None:
        metadata = get_index_metadata("v1")

        assert metadata is ACTIVE_METADATA
        assert metadata.model_name == "BAAI/bge-small-zh-v1.5"
        assert metadata.dimension == 512
        assert metadata.version == 1

    def test_v2_contract_is_bge_m3_1024(self) -> None:
        lane = get_index_lane(IndexVersion.V2)
        metadata = lane.metadata

        assert lane.version is IndexVersion.V2
        assert metadata.model_name == "BAAI/bge-m3"
        assert metadata.dimension == 1024
        assert metadata.version == 2

    def test_metadata_version_remains_permissive_for_legacy_reports(self) -> None:
        metadata = IndexMetadata(model_name="custom", dimension=512, version=99)

        assert metadata.version == 99

    def test_unknown_version_fails_explicitly(self) -> None:
        with pytest.raises(ValueError, match="Unsupported index version"):
            get_index_metadata("v3")

    def test_validate_dimension_can_target_v2(self) -> None:
        validate_dimension(1024, IndexVersion.V2)

        with pytest.raises(DimensionMismatchError) as exc_info:
            validate_dimension(512, IndexVersion.V2)
        assert exc_info.value.expected == 1024
        assert exc_info.value.actual == 512

    @pytest.mark.parametrize(
        ("model_name", "dimension"),
        [("", 512), ("BAAI/test", 0), ("BAAI/test", -1)],
    )
    def test_metadata_rejects_invalid_contracts(
        self,
        model_name: str,
        dimension: int,
    ) -> None:
        with pytest.raises(ValueError):
            IndexMetadata(model_name=model_name, dimension=dimension, version=1)
