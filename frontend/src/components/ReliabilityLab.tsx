import {useEffect,useRef,useState} from "react";
import {decodeRuns,message,request} from "../api";
import {decodeEvaluation,decodeEvaluations,type Evaluation,type Scores,type LabCase} from "../analytics";
import {useResource} from "../hooks";
import {tr} from "../i18n";
import {human,short,date} from "../format";
import {Loading,Notice,Empty} from "../ui";
import {LineageView} from "./LineageView";
import {Comparison} from "./ReplayView";
const scenarios=["all","normal","missing_records","duplicate_outputs","schema_drift","late_arrival"];
function Scoreboard({scores}:{scores:Scores}){return <><div className="metrics analytics-metrics">{["true_positives","false_positives","false_negatives","precision","recall","f1"].map(k=><div className="metric" key={k}><span>{human(k)}</span><strong>{typeof scores[k as keyof Scores]==="number"?Number(scores[k as keyof Scores]).toLocaleString(undefined,{maximumFractionDigits:4}):tr("Unavailable")}</strong><small>{scores.undefined_reasons[k]?human(scores.undefined_reasons[k]!):""}</small></div>)}</div><details><summary>{tr("Per-type coverage")}</summary><div className="table-scroll"><table><thead><tr>{["Incident type","True positives","False positives","False negatives","Precision","Recall","F1"].map(k=><th key={k}>{tr(k)}</th>)}</tr></thead><tbody>{Object.entries(scores.per_type).map(([kind,s])=><tr key={kind}><td>{human(kind)}</td>{[s.true_positives,s.false_positives,s.false_negatives,s.precision,s.recall,s.f1].map((v,i)=><td key={i}>{v===null?tr("Unavailable"):v}</td>)}</tr>)}</tbody></table></div></details></>}
function CaseView({value}:{value:LabCase}){
 const [selected,setSelected]=useState(0);
 if(value.status!=="completed")return <Notice error>{human(value.failure_code??"LAB_CASE_EXECUTION_FAILED")}</Notice>;
 const incident=value.incidents[selected];
 return <section className="panel"><h3>{human(value.scenario)}</h3><p>{tr("Detector execution time")}: <bdi>{value.detector_execution_ms.toFixed(2)} ms</bdi> · {tr("Not historical detection latency")}</p><p>{tr(value.injection.details)}</p><p>{tr(value.injection.clock_semantics)}</p><Scoreboard scores={value.metrics}/><div className="lab-labels"><div><h4>{tr("Injected ground truth")}</h4>{value.ground_truth.length?<ul>{value.ground_truth.map(r=><li key={r.incident_type+r.source_identity}>{human(r.incident_type)} · <bdi>{r.source_identity}</bdi></li>)}</ul>:<p>{tr("No injected fault")}</p>}</div><div><h4>{tr("Detector predictions")}</h4>{value.predictions.length?<ul>{value.predictions.map(r=><li key={r.incident_type+r.source_identity}>{human(r.incident_type)} · <bdi>{r.source_identity}</bdi></li>)}</ul>:<p>{tr("No detected incident")}</p>}</div></div>
 <div className="tabs">{value.incidents.map((r,i)=><button key={r.id} onClick={()=>setSelected(i)} aria-pressed={selected===i}>{human(r.incident_type)}</button>)}</div>
 {incident&&<><Notice>{tr("Temporary incident and run IDs belong to this saved lab report, not development pipeline records.")}</Notice><LineageView key={incident.id} trace={incident.lineage}/><details><summary>{tr("Recorded incident evidence")}</summary><pre className="evidence-json" dir="ltr">{JSON.stringify(incident.evidence,null,2)}</pre></details><h4>{tr("Safe Replay simulation")}</h4>{incident.replay?<><Notice>{tr("Simulation only. Original source data was not corrected.")}</Notice><Comparison before={incident.replay.before} after={incident.replay.after} verification={incident.replay.verification}/></>:<Notice>{human(incident.replay_rejection??incident.replay_availability)}</Notice>}</>}
 </section>;
}
export function ReliabilityLab({revision,inspect}:{revision:number;inspect:(id:string)=>void}){
 const [kind,setKind]=useState("uci_real"),[runId,setRunId]=useState(""),[scenario,setScenario]=useState("all"),[seed,setSeed]=useState(42),[limit,setLimit]=useState(25),[result,setResult]=useState<Evaluation|null>(null),[error,setError]=useState<string|null>(null),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0),[offset,setOffset]=useState(0);
 const active=useRef<AbortController|null>(null);
 useEffect(()=>()=>active.current?.abort(),[]);
 const runs=useResource(`/pipeline-runs?limit=100&source_kind=${kind}`,decodeRuns,revision);
 const saved=useResource(`/reliability-lab/evaluations?limit=10&offset=${offset}`,decodeEvaluations,revision+refresh);
 const clear=()=>{active.current?.abort();active.current=null;setResult(null);setError(null);setBusy(false);};
 async function load(id?:string){
  active.current?.abort();const controller=new AbortController();active.current=controller;setBusy(true);setError(null);setResult(null);
  try{
   const body=await request(id?`/reliability-lab/evaluations/${id}`:"/reliability-lab/evaluations",decodeEvaluation,{signal:controller.signal,...(!id?{method:"POST",body:JSON.stringify({source_run_id:runId,scenario,record_limit:limit,seed})}:{} )});
   if(controller.signal.aborted)return;
   if((id&&body.id!==id)||(!id&&body.source_run_id!==runId))throw new Error("Evidence scope mismatch");
   setResult({...body,reused:Boolean(id)||body.reused});setRefresh(v=>v+1);
  }catch(e){if(!controller.signal.aborted)setError(message(e));}finally{if(active.current===controller)setBusy(false);}
 }
 return <section><Notice>{tr("Faults run on private in-memory copies. Only the audit report is saved. Development source records are never corrected or fault-injected.")}</Notice><section className="panel"><h2>{tr("Configure an isolated experiment")}</h2><div className="filters">
 <label className="field">{tr("Source category")}<select value={kind} onChange={e=>{clear();setRunId("");setKind(e.target.value);}}><option value="uci_real">{tr("Real UCI data")}</option><option value="local_demo">{tr("Local synthetic demonstrations")}</option><option value="uci_controlled">{tr("Controlled faults on real data")}</option></select></label>
 <label className="field">{tr("Source run")}<select value={runId} onChange={e=>{clear();setRunId(e.target.value);}}><option value="">{tr("Select a run")}</option>{runs.data?.items.filter(r=>r.status==="completed").map(r=><option key={r.id} value={r.id}>{short(r.id)} · {r.source_name}</option>)}</select></label>
 <label className="field">{tr("Scenario")}<select value={scenario} onChange={e=>{clear();setScenario(e.target.value);}}>{scenarios.map(k=><option value={k} key={k}>{human(k)}</option>)}</select></label>
 <label className="field">{tr("Seed")}<input type="number" min="0" max="2147483647" value={seed} onChange={e=>{clear();setSeed(Number(e.target.value));}}/></label><label className="field">{tr("Valid source records")}<input type="number" min="2" max="50" value={limit} onChange={e=>{clear();setLimit(Number(e.target.value));}}/></label>
 <button className="primary" disabled={!runId||busy||!Number.isInteger(seed)||seed<0||seed>2147483647||!Number.isInteger(limit)||limit<2||limit>50} onClick={()=>load()}>{tr("Run isolated evaluation")}</button></div>{runs.loading&&<Loading/>}{runs.error&&<Notice error>{runs.error}</Notice>}</section>
 {busy&&<Loading/>}{error&&<Notice error>{error}</Notice>}
 {result&&<section className="panel"><h2>{tr("Evaluation report")} · <bdi>{short(result.id)}</bdi></h2><p>{date(result.started_at)} · {human(result.status)} · {result.selected_record_count} {tr("Valid source records")}</p><Notice>{tr(result.reused?"Saved result reused; timing belongs to the original execution.":"A new isolated evaluation was saved.")}</Notice><Notice>{tr("Controlled fault results do not prove production accuracy. Natural source anomalies have no ground-truth labels.")}</Notice><Scoreboard scores={result.metrics}/><button onClick={()=>inspect(result.source_run_id)}>{tr("Inspect original source run")}</button>{result.cases.map(c=><CaseView key={result.id+c.scenario} value={c}/>)}<details><summary>{tr("Audit references")}</summary><p><bdi>{result.source_run_id}</bdi></p><p><bdi>{result.source_fingerprint}</bdi></p><p>{tr("Algorithm, seed and configuration are preserved in this saved evaluation.")}</p></details></section>}
 <section className="panel"><h2>{tr("Saved evaluations")}</h2>{saved.loading&&<Loading/>}{saved.error&&<Notice error>{saved.error}</Notice>}{saved.data&&!saved.data.total&&<Empty title={tr("No saved evaluations")}>{tr("Launch an isolated experiment to create an audit report.")}</Empty>}<div className="table-scroll"><table><thead><tr>{["Report","Source run","Scenario","Status","Started"].map(k=><th key={k}>{tr(k)}</th>)}</tr></thead><tbody>{saved.data?.items.map(r=><tr key={r.id}><td><button disabled={busy} onClick={()=>load(r.id)}>{short(r.id)}</button></td><td><bdi>{short(r.source_run_id)}</bdi></td><td>{human(r.config.scenario)}</td><td>{human(r.status)}</td><td>{date(r.started_at)}</td></tr>)}</tbody></table></div><div className="pager"><button disabled={!offset||busy} onClick={()=>setOffset(offset-10)}>{tr("Previous")}</button><button disabled={!saved.data||offset+10>=saved.data.total||busy} onClick={()=>setOffset(offset+10)}>{tr("Next")}</button></div></section>
 </section>;
}
