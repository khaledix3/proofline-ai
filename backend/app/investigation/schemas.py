from pydantic import BaseModel, ConfigDict


class GroundedChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str
    evidence_ids: list[str]


class ProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary_fact_ids: list[str]
    observation_fact_ids: list[str]
    hypotheses: list[GroundedChoice]
    unknown_codes: list[str]
    diagnostic_actions: list[GroundedChoice]
