import {beforeEach,it,expect,vi} from "vitest";
import {render,screen,fireEvent,waitFor} from "@testing-library/react";
import {HealthCenter} from "../components/HealthCenter";
import {BusinessImpactView} from "../components/BusinessImpactView";
import {ReliabilityLab} from "../components/ReliabilityLab";
import {setLanguage} from "../i18n";
import type {Incident} from "../types";
const score={true_positives:0,false_positives:0,false_negatives:0,precision:null,recall:null,f1:null,undefined_reasons:{precision:"NO_POSITIVE_PREDICTIONS",recall:"NO_POSITIVE_GROUND_TRUTH",f1:"NO_POSITIVE_LABELS_OR_PREDICTIONS"},per_type:{}};
const run={id:"source-run",status:"completed",source_name:"real source",started_at:"2026-01-01T10:00:00Z",counts:{source_count:10,accepted_count:8,rejected_count:2,duplicate_count:0},acceptance_percentage:80,rejection_percentage:20,incidents:[]};
const health={scope:{source_kind:"uci_real"},summary:{run_count:1,source_count:10,processed_record_count:10,accepted_count:8,rejected_count:2,acceptance_percentage:80,rejection_percentage:20,late_arriving_record_count:null,incident_breakdown:{}},items:[run],total:1,history:{status:"insufficient_data",points:[run],observations:1}};
const evaluation={id:"eval-one",source_run_id:run.id,source_applied:false,status:"completed",started_at:run.started_at,selected_record_count:2,source_fingerprint:"private-free-fingerprint",config:{scenario:"normal",seed:42,record_limit:2},metrics:score,cases:[{scenario:"normal",status:"completed",detector_execution_ms:1.2,ground_truth:[],predictions:[],metrics:score,injection:{details:"No injected fault; clean baseline at lateness boundary.",clock_semantics:"simulated clock"},incidents:[]}],limitations:[]};
function mock(handler:(path:string,options?:RequestInit)=>unknown){return vi.spyOn(globalThis,"fetch").mockImplementation(async(input,options)=>new Response(JSON.stringify(handler(String(input),options)),{status:200,headers:{"Content-Type":"application/json"}}));}
beforeEach(()=>{vi.restoreAllMocks();setLanguage("en");});
it("shows real health ratios and unknown monitoring without invented trend",async()=>{
 mock(()=>health);render(<HealthCenter sourceKind="uci_real" revision={0} inspect={()=>{}}/>);
 expect(await screen.findByText("80%")).toBeInTheDocument();expect(screen.getByText("20%")).toBeInTheDocument();
 expect(screen.getAllByText("Insufficient history").length).toBeGreaterThan(0);
 expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
 expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
it("health navigation retains the selected run",async()=>{
 const inspect=vi.fn();mock(()=>health);render(<HealthCenter sourceKind="uci_real" revision={0} inspect={inspect}/>);
 fireEvent.click(await screen.findByRole("button",{name:"source-r"}));expect(inspect).toHaveBeenCalledWith(run.id);
});
it("rejects malformed health responses with an error state",async()=>{
 mock(()=>({summary:"broken"}));render(<HealthCenter sourceKind="" revision={0} inspect={()=>{}}/>);
 expect(await screen.findByText("Unsupported analytics response")).toBeInTheDocument();
});
it("impact is scoped and labels associated value without confirmed loss",async()=>{
 const incident={id:"incident-one",run_id:run.id} as Incident;const lineage=vi.fn();
 mock(()=>({incident_id:incident.id,run_id:run.id,status:"current_discrepancy",total:1,affected_reporting_output:"reported_orders",records:[],monetary:{status:"verified_associated_value",associated_absent_value:"15.30",currency:"GBP"},unknowns:["ROOT_CAUSE_UNKNOWN"]}));
 render(<BusinessImpactView incident={incident} lineage={lineage}/>);
 expect(await screen.findByText("15.30 GBP")).toBeInTheDocument();
 expect(screen.getByText(/not confirmed revenue loss/)).toBeInTheDocument();fireEvent.click(screen.getByRole("button",{name:/Trace recorded stages/}));expect(lineage).toHaveBeenCalledOnce();
});
it("does not display impact belonging to another run",async()=>{
 mock(()=>({incident_id:"incident-one",run_id:"other-run",monetary:{},records:[],unknowns:[]}));
 render(<BusinessImpactView incident={{id:"incident-one",run_id:run.id} as Incident} lineage={()=>{}}/>);
 expect(await screen.findByText("Evidence scope mismatch")).toBeInTheDocument();
});
it("lab does not launch automatically and submits only bounded supported configuration",async()=>{
 const fetcher=mock((path,options)=>options?.method==="POST"?evaluation:path.includes("pipeline-runs")?{items:[run],total:1}:{items:[],total:0});
 render(<ReliabilityLab revision={0} inspect={()=>{}}/>);
 await screen.findByRole("option",{name:/real source/});expect(fetcher.mock.calls.every(([,o])=>o?.method!=="POST")).toBe(true);
 fireEvent.change(screen.getByLabelText("Source run"),{target:{value:run.id}});fireEvent.change(screen.getByLabelText("Scenario"),{target:{value:"normal"}});fireEvent.change(screen.getByLabelText("Valid source records"),{target:{value:"2"}});
 fireEvent.click(screen.getByRole("button",{name:"Run isolated evaluation"}));
 await screen.findByText(/A new isolated evaluation was saved/);
 const call=fetcher.mock.calls.find(([,o])=>o?.method==="POST");expect(JSON.parse(call![1]!.body as string)).toEqual({source_run_id:run.id,scenario:"normal",record_limit:2,seed:42});
 expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);expect(screen.getByText(/do not prove production accuracy/)).toBeInTheDocument();
});
it("retrieves an audit report without launching another experiment",async()=>{
 const fetcher=mock(path=>path.endsWith("/eval-one")?evaluation:path.includes("pipeline-runs")?{items:[run],total:1}:{items:[evaluation],total:1});
 render(<ReliabilityLab revision={0} inspect={()=>{}}/>);fireEvent.click(await screen.findByRole("button",{name:"eval-one"}));
 await screen.findByText(/Evaluation report/);expect(fetcher.mock.calls.every(([,o])=>o?.method!=="POST")).toBe(true);
});
it("prevents out-of-bounds lab requests",async()=>{
 mock(path=>path.includes("pipeline-runs")?{items:[run],total:1}:{items:[],total:0});render(<ReliabilityLab revision={0} inspect={()=>{}}/>);
 await screen.findByRole("option",{name:/real source/});fireEvent.change(screen.getByLabelText("Source run"),{target:{value:run.id}});fireEvent.change(screen.getByLabelText("Valid source records"),{target:{value:"51"}});
 expect(screen.getByRole("button",{name:"Run isolated evaluation"})).toBeDisabled();
});
it("renders new controls in Arabic with the existing RTL language system",async()=>{
 setLanguage("ar");mock(path=>path.includes("pipeline-runs")?{items:[],total:0}:{items:[],total:0});render(<ReliabilityLab revision={0} inspect={()=>{}}/>);
 await waitFor(()=>expect(screen.getByText("التقييمات المحفوظة")).toBeInTheDocument());expect(screen.getByRole("button",{name:"تشغيل تقييم معزول"})).toBeDisabled();expect(document.documentElement.dir).toBe("rtl");setLanguage("en");
});
