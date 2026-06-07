from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field


Modality = Literal["text", "json", "image", "video", "audio", "file", "url"]
ModelCapability = Literal["text", "image", "video", "audio", "music", "judge"]
ParamType = Literal["string", "integer", "number", "boolean", "array", "object", "any"]


class ModelParamSpec(BaseModel):
    type: ParamType = "any"
    enum: list[Any] | None = None
    min: float | int | None = None
    max: float | int | None = None

    def validate_value(self, key: str, value: Any, *, context: str) -> None:
        if self.type == "string" and not isinstance(value, str):
            raise ValueError(f"{context}.{key} must be a string")
        if self.type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"{context}.{key} must be an integer")
        if self.type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"{context}.{key} must be a number")
        if self.type == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{context}.{key} must be a boolean")
        if self.type == "array" and not isinstance(value, list):
            raise ValueError(f"{context}.{key} must be an array")
        if self.type == "object" and not isinstance(value, dict):
            raise ValueError(f"{context}.{key} must be an object")

        if self.enum is not None and value not in self.enum:
            allowed = ", ".join(str(item) for item in self.enum)
            raise ValueError(f"{context}.{key} must be one of: {allowed}; got {value!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.min is not None and value < self.min:
                raise ValueError(f"{context}.{key} must be >= {self.min}; got {value}")
            if self.max is not None and value > self.max:
                raise ValueError(f"{context}.{key} must be <= {self.max}; got {value}")


class ModelSpec(BaseModel):
    id: str = ""
    provider: str | None = None
    family: str | None = None
    capability: ModelCapability
    input_modalities: list[Modality] = Field(default_factory=list)
    output_modalities: list[Modality] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    params_schema: dict[str, ModelParamSpec] = Field(default_factory=dict)

    @property
    def provider_name(self) -> str:
        if self.provider:
            return self.provider
        provider, separator, _model = self.id.partition(":")
        return provider if separator else ""

    @property
    def provider_model_name(self) -> str:
        _provider, separator, model_name = self.id.partition(":")
        return model_name if separator else self.id

    def validate_params(self, params: Mapping[str, Any], *, context: str) -> None:
        unsupported = sorted(set(params).difference(self.params_schema))
        if unsupported:
            supported = ", ".join(sorted(self.params_schema)) or "-"
            raise ValueError(
                f"{context} contains unsupported model parameter(s) for {self.id}: "
                f"{', '.join(unsupported)}. Supported parameters: {supported}"
            )
        for key, value in params.items():
            self.params_schema[key].validate_value(key, value, context=context)

    def validate_refs(self, refs: list[Any] | None, *, context: str) -> None:
        if not refs:
            return

        counts = {
            "max_reference_images": 0,
            "max_reference_audio": 0,
            "max_reference_videos": 0,
        }
        for ref in refs:
            ref_type = str(getattr(ref, "type", "") or "")
            if ref_type == "image":
                counts["max_reference_images"] += 1
            elif ref_type == "audio":
                counts["max_reference_audio"] += 1
            elif ref_type == "video":
                counts["max_reference_videos"] += 1

        for key, actual in counts.items():
            limit = self._int_limit(key)
            if limit is not None and actual > limit:
                raise ValueError(f"{context} passes {actual} refs for {key}; model {self.id} allows {limit}")

    def validate_duration(self, duration: Any, *, context: str) -> None:
        if duration is None:
            return
        try:
            value = float(duration)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context}.duration must be numeric; got {duration!r}") from exc

        min_duration = self._float_limit("min_duration_seconds")
        max_duration = self._float_limit("max_duration_seconds")
        if min_duration is not None and value < min_duration:
            raise ValueError(f"{context}.duration must be >= {min_duration:g}s for {self.id}; got {value:g}s")
        if max_duration is not None and value > max_duration:
            raise ValueError(f"{context}.duration must be <= {max_duration:g}s for {self.id}; got {value:g}s")

    def _int_limit(self, key: str) -> int | None:
        value = self.limits.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _float_limit(self, key: str) -> float | None:
        value = self.limits.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class ModelCatalog(BaseModel):
    models: dict[str, ModelSpec] = Field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ModelCatalog":
        data = data or {}
        raw_models = data.get("models", data)
        if not isinstance(raw_models, Mapping):
            raise ValueError("model catalog must contain a mapping named 'models'")

        models: dict[str, ModelSpec] = {}
        for model_id, payload in raw_models.items():
            if not isinstance(payload, Mapping):
                raise ValueError(f"model catalog entry {model_id!r} must be an object")
            spec = ModelSpec.model_validate({"id": str(model_id), **dict(payload)})
            models[spec.id] = spec
        return cls(models=models)

    def require(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise ValueError(f"Unknown model in node configuration: {model_id}") from exc

    def validate_node_settings(self, node_name: str, settings: "NodeModelSettings") -> ModelSpec:
        spec = self.require(settings.model)
        spec.validate_params(settings.params, context=f"nodes.{node_name}.params")
        return spec


class NodeModelSettings(BaseModel):
    model: str
    params: dict[str, Any] = Field(default_factory=dict)


class ModelBinding(BaseModel):
    node_name: str
    model_id: str
    provider: str
    capability: ModelCapability
    params: dict[str, Any] = Field(default_factory=dict)
    spec: ModelSpec

    @property
    def provider_model_name(self) -> str:
        return self.spec.provider_model_name
