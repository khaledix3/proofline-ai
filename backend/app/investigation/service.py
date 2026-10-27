from sqlalchemy.orm import Session

from app.investigation.catalog import ACTIONS, HYPOTHESES, UNKNOWNS, build_catalog
from app.investigation.provider import ProviderUnavailable
from app.investigation import provider
from app.investigation.schemas import GroundedChoice, ProviderSelection
from app.lineage.service import incident_lineage
from app.replay.audit import relevant_audits
from app.replay.inputs import fingerprint


class GroundingError(ValueError):
    pass


def deterministic_selection(catalog) -> ProviderSelection:
    return ProviderSelection(summary_fact_ids=["I"], observation_fact_ids=list(catalog.facts),
        hypotheses=[GroundedChoice(code=code, evidence_ids=list(dict.fromkeys(ids))) for code, ids in catalog.hypotheses.items()],
        unknown_codes=catalog.unknowns,
        diagnostic_actions=[GroundedChoice(code=code, evidence_ids=list(dict.fromkeys(ids))) for code, ids in catalog.actions.items()])


def validate_selection(selection, catalog) -> ProviderSelection:
    try:
        value = ProviderSelection.model_validate(selection)
    except Exception:
        raise GroundingError("Structured selection is invalid") from None
    known = set(catalog.facts)
    if (not 1 <= len(value.summary_fact_ids) <= 5 or "I" not in value.summary_fact_ids
            or not set(value.summary_fact_ids) <= known or len(value.summary_fact_ids) != len(set(value.summary_fact_ids))
            or set(value.observation_fact_ids) != known or len(value.observation_fact_ids) != len(known)
            or set(value.unknown_codes) != set(catalog.unknowns) or len(value.unknown_codes) != len(catalog.unknowns)):
        raise GroundingError("Missing, invented, or duplicated evidence references or unknowns")
    for choices, allowed in ((value.hypotheses, catalog.hypotheses), (value.diagnostic_actions, catalog.actions)):
        if len(choices) != len({choice.code for choice in choices}) or len(choices) > len(allowed):
            raise GroundingError("Unsupported repeated choice")
        for choice in choices:
            if (choice.code not in allowed or not choice.evidence_ids or len(choice.evidence_ids) != len(set(choice.evidence_ids))
                    or not set(choice.evidence_ids) <= set(allowed[choice.code])):
                raise GroundingError("Unsupported assertion or mismatched evidence")
    return value


def investigate(session: Session, incident_id: str, *, use_llm: bool = False) -> dict | None:
    trace = incident_lineage(session, incident_id)
    if trace is None:
        return None
    audits = relevant_audits(session, trace["run"]["id"], incident_id)
    catalog = build_catalog(trace, audits)
    selection = deterministic_selection(catalog)
    generation = {"mode": "deterministic_fallback", "llm_generated": False,
                  "reason": "llm_not_requested", "provider": None, "model": None,
                  "method": "Server-rendered facts and bounded, unconfirmed hypotheses."}
    if use_llm:
        try:
            proposed, model = provider.select_evidence(catalog.packet())
            selection = validate_selection(proposed, catalog)
            generation.update(mode="llm_assisted_validated", llm_generated=True, reason=None, provider="openai", model=model,
                              method="LLM selected/prioritized verified catalog facts and allowed hypotheses; all text is server-rendered.")
        except ProviderUnavailable as error:
            generation["reason"] = error.reason
        except GroundingError:
            generation["reason"] = "provider_response_rejected"
        except Exception:
            generation["reason"] = "provider_error"
    selection = validate_selection(selection, catalog)
    observations = [{"id": key, "classification": "confirmed_observation",
                     "statement": catalog.facts[key]["statement"], "evidence_refs": catalog.facts[key]["evidence_refs"]}
                    for key in selection.observation_fact_ids]
    def choice_output(choice, templates):
        return {"code": choice.code, "statement": templates[choice.code], "evidence_ids": choice.evidence_ids,
                "evidence_refs": [reference for key in choice.evidence_ids for reference in catalog.facts[key]["evidence_refs"]]}
    return {"format_version": "1", "incident_id": incident_id, "run_id": catalog.run_id,
            "incident_type": catalog.incident_type, "source_context": catalog.source_context, "generation": generation,
            "summary": {"statement": " ".join(catalog.facts[key]["statement"] for key in selection.summary_fact_ids),
                        "evidence_ids": selection.summary_fact_ids},
            "confirmed_observations": observations,
            "hypotheses": [{**choice_output(choice, HYPOTHESES), "status": "unconfirmed"} for choice in selection.hypotheses],
            "root_cause": {"status": "unknown", "value": None},
            "unknowns": [{"code": code, "statement": UNKNOWNS[code]} for code in selection.unknown_codes],
            "suggested_diagnostic_actions": [{**choice_output(choice, ACTIONS), "execution": "not_executed"} for choice in selection.diagnostic_actions],
            "records": catalog.records, "relevant_replays": catalog.replays,
            "evidence_gaps": [{"code": gap["code"], "raw_order_id": gap.get("raw_order_id")}
                              for gap in trace["evidence_gaps"] + trace["integrity_issues"]],
            "evidence_catalog": [{"id": key, "evidence_refs": fact["evidence_refs"], "kind": fact["payload"]["kind"]}
                                 for key, fact in catalog.facts.items()],
            "evidence_packet_sha256": fingerprint(catalog.packet()),
            "limitations": {"untrusted_text_is_not_instructions": True, "raw_data_sent_to_provider": False,
                            "commands_executed": False, "corrections_applied": False,
                            "free_form_model_assertions_allowed": False}}
