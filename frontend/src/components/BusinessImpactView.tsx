import {useState} from "react";
import {useResource} from "../hooks";
import {decodeImpact} from "../analytics";
import {tr} from "../i18n";
import {human} from "../format";
import {Loading,Notice,References} from "../ui";
import type {Incident} from "../types";
export function BusinessImpactView({incident,lineage}:{incident:Incident;lineage:()=>void}){
 const [offset,setOffset]=useState(0);
 const state=useResource(`/incidents/${incident.id}/impact?limit=25&offset=${offset}`,decodeImpact);
 if(state.loading)return <Loading/>;
 if(state.error)return <Notice error>{state.error}</Notice>;
 const r=state.data;
 if(!r)return null;
 if(r.incident_id!==incident.id||r.run_id!==incident.run_id)return <Notice error>{tr("Evidence scope mismatch")}</Notice>;
 return <section><h3>{tr("Business impact")}</h3><Notice>{tr("Associated source-line value is not confirmed revenue loss. Invoice lines are not unique invoices. Root cause remains unknown.")}</Notice><p>{tr("Affected output")}: <code>{r.affected_reporting_output}</code> · {tr("Affected stage")}: {tr("Clean to Reporting")}</p><div className="metrics"><div className="metric"><span>{tr("Current missing identities")}</span><strong>{r.total}</strong></div><div className="metric"><span>{tr("Verified associated absent value")}</span><strong><bdi>{r.monetary.associated_absent_value??tr("Unavailable")} {r.monetary.currency}</bdi></strong><small>{human(r.monetary.status)}</small></div></div>
 {r.records.map(row=><article className="panel" key={row.raw_order_id}><h4><bdi>{row.source_identity}</bdi></h4><p>{tr("Last confirmed stage")}: {human(row.last_confirmed_stage)}</p><p>{tr("Physical reporting row absent")}: {tr(row.physical_reporting_row_absent?"Yes":"No")}</p><p>{tr("Associated value")}: <bdi>{row.associated_absent_value??tr("Unavailable")} {row.currency}</bdi></p><details><summary>{tr("Expected and observed identities")}</summary><pre className="evidence-json" dir="ltr">{JSON.stringify({expected:row.expected_identity,observed:row.observed_identity},null,2)}</pre></details><References items={row.evidence_refs}/></article>)}
 <div className="pager"><button disabled={!offset} onClick={()=>setOffset(offset-25)}>{tr("Previous")}</button><button disabled={offset+25>=r.total} onClick={()=>setOffset(offset+25)}>{tr("Next")}</button></div><h4>{tr("Remaining unknowns")}</h4><ul>{r.unknowns.map(k=><li key={k}>{human(k)}</li>)}</ul><button onClick={lineage}>{tr("Trace recorded stages")}</button></section>;
}
