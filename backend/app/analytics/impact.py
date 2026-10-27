"""Conservative source-line value analysis. A reporting gap is never confirmed loss."""
from decimal import Decimal, InvalidOperation
from sqlalchemy import select,func
from app.models import Incident, RawOrder, CleanOrder, DatasetImport, DatasetSourceRecord
from app.lineage.service import incident_lineage,start_read_snapshot
from app.lineage.graph import evidence_ref
from app.datasets.source import RetailSubset,map_line,OFFICIAL_WORKBOOK_SHA256,MAPPING_VERSION,DATASET_URL
from app.replay.inputs import fingerprint
from app.analytics.health import AnalyticsError

MAX_IMPACT_ROWS=10000

def verify_source(session,run_id):
    dataset=session.get(DatasetImport,run_id)
    if (dataset is None or dataset.workbook_sha256!=OFFICIAL_WORKBOOK_SHA256
        or dataset.provenance.get('mapping_version')!=MAPPING_VERSION or dataset.provenance.get('dataset_id')!=352):
        return None,{},'UNVERIFIED_CURRENCY_AND_MAPPING'
    rows=session.scalars(select(DatasetSourceRecord).where(DatasetSourceRecord.run_id==run_id).order_by(DatasetSourceRecord.source_row).limit(2001)).all()
    if len(rows)>2000 or len(rows)!=dataset.provenance.get('source_row_count'):
        return None,{},'INCOMPLETE_SOURCE_PROVENANCE'
    subset=RetailSubset(dataset.workbook_sha256,dataset.provenance.get('sheet'),tuple({'source_row':row.source_row,'original':row.original_values} for row in rows),'verification')
    if subset.digest!=dataset.subset_sha256:
        return None,{},'SOURCE_PROVENANCE_FINGERPRINT_MISMATCH'
    return subset,{row.raw_order_id:row for row in rows},None


def verified_line_value(raw,clean,original,subset):
    if original is None or clean is None: return None,'MISSING_VALIDATED_SOURCE'
    if original.source_identity!=f'{subset.workbook_sha256}:{original.source_row}': return None,'SOURCE_IDENTITY_MISMATCH'
    values=original.original_values
    try:
        mapped=map_line(subset,{'source_row':original.source_row,'original':values})
        if any(raw.original_data.get(key)!=value for key,value in mapped.items()): return None,'MAPPED_INPUT_DIFFERS_FROM_SOURCE'
        if (clean.order_id!=mapped['order_id'] or clean.customer_id!=mapped['customer_id']
            or clean.order_date.isoformat()!=mapped['order_date']): return None,'CLEAN_OUTPUT_DIFFERS_FROM_SOURCE'
        if str(values.get('InvoiceNo','')).upper().startswith('C'): return None,'CANCELLATION_VALUE_NOT_INTERPRETED'
        if values.get('CustomerID') is None: return None,'MISSING_CUSTOMER_REFERENCE'
        if isinstance(values.get('Quantity'),bool) or isinstance(values.get('UnitPrice'),bool): return None,'INVALID_SOURCE_VALUE'
        quantity,price=Decimal(str(values['Quantity'])),Decimal(str(values['UnitPrice']))
        if not quantity.is_finite() or not price.is_finite() or quantity<=0 or price<0 or quantity!=quantity.to_integral_value(): return None,'RETURN_OR_INVALID_VALUE_NOT_INTERPRETED'
        if quantity!=clean.quantity or price!=clean.unit_price: return None,'CLEAN_OUTPUT_DIFFERS_FROM_SOURCE'
        return (quantity*price).quantize(Decimal('0.01')),None
    except (KeyError,ValueError,TypeError,InvalidOperation): return None,'INVALID_SOURCE_VALUE'


def business_impact(session,incident_id,limit=25,offset=0):
    start_read_snapshot(session)
    incident=session.get(Incident,incident_id)
    if incident is None: raise AnalyticsError('INCIDENT_NOT_FOUND',404)
    base={'format_version':'1','incident_id':incident_id,'run_id':incident.run_id,'incident_type':incident.incident_type,
          'root_cause':{'status':'unknown','value':None},'financial_loss':{'status':'not_established','amount':None},
          'affected_reporting_output':'reported_orders','lineage_path':f'/incidents/{incident_id}/lineage',
          'evidence_refs':[evidence_ref('incidents',id=incident.id,run_id=incident.run_id)],
          'semantics':'Invoice lines are not unique invoices. Associated source value is not confirmed revenue loss; reported_orders stores identities, not monetary totals.'}
    if incident.incident_type!='MISSING_RECORDS':
        return {**base,'status':'not_applicable','records':[],'total':0,'limit':limit,'offset':offset,
                'monetary':{'status':'unavailable','currency':None,'associated_absent_value':None,'verified_subtotal':None},'unknowns':['NO_MISSING_RECORD_IMPACT_MODEL_FOR_THIS_TYPE']}
    count=session.scalar(select(func.count()).select_from(RawOrder).where(RawOrder.run_id==incident.run_id))
    if count>MAX_IMPACT_ROWS: raise AnalyticsError('IMPACT_SCOPE_TOO_LARGE')
    trace=incident_lineage(session,incident_id)
    rows=[row for row in trace['records'] if row['reporting']['missing_expected_identity'] is True]
    subset,originals,source_error=verify_source(session,incident.run_id)
    ids=[row['raw_order_id'] for row in rows]
    raw={row.id:row for row in session.scalars(select(RawOrder).where(RawOrder.run_id==incident.run_id,RawOrder.id.in_(ids)))}
    clean={row.raw_order_id:row for row in session.scalars(select(CleanOrder).where(CleanOrder.run_id==incident.run_id,CleanOrder.raw_order_id.in_(ids)))}
    records=[];subtotal=Decimal('0.00');verified=0;unknowns=set()
    for row in rows:
        raw_id=row['raw_order_id'];original=originals.get(raw_id)
        value,reason=verified_line_value(raw[raw_id],clean.get(raw_id),original,subset) if subset else (None,source_error)
        if row['integrity_issues'] or trace['integrity_issues']:
            value,reason=None,'INCONSISTENT_LINEAGE'
        physically_absent=row['reporting']['observed_identity'] is None
        if value is not None and physically_absent: subtotal+=value;verified+=1
        if reason: unknowns.add(reason)
        if not physically_absent: unknowns.add('IDENTITY_MISMATCH_IS_NOT_PROOF_OF_ABSENT_VALUE')
        refs=row['raw']['evidence_refs']+row['clean']['evidence_refs']+row['reporting']['expectation_refs']
        if original: refs.append(evidence_ref('dataset_source_records',raw_order_id=raw_id,run_id=incident.run_id))
        records.append({'raw_order_id':raw_id,'source_identity':original.source_identity if original else row['source_order_id'],
            'worksheet_row':original.source_row if original else None,'expected_identity':row['reporting']['expected_identity'],
            'observed_identity':row['reporting']['observed_identity'],'physical_reporting_row_absent':physically_absent,
            'last_confirmed_stage':row['last_confirmed_stage'],'verified_source_line_value':str(value) if value is not None else None,
            'associated_absent_value':str(value) if value is not None and physically_absent else None,
            'currency':'GBP' if value is not None else None,'unavailable_reason':reason,
            'evidence_refs':refs})
    complete=bool(rows) and verified==len(rows)
    if not rows: unknowns.add('NO_CURRENT_MISSING_IDENTITIES_SNAPSHOT_IS_HISTORICAL')
    unknowns.update(['REPORTING_OUTPUT_HAS_NO_AMOUNT_COLUMNS','FINANCIAL_LOSS_NOT_ESTABLISHED','ROOT_CAUSE_UNKNOWN'])
    return {**base,'status':'current_discrepancy' if rows else 'no_current_discrepancy',
        'source_context':trace['run'].get('source'),
        'confirmed':{'stored_missing_count':incident.affected_count,'current_missing_identity_count':len(rows),
                     'affected_stage':'clean_to_reporting','reporting_stage_recorded':trace['reporting_stage'] is not None},
        'records':records[offset:offset+limit],'total':len(records),'limit':limit,'offset':offset,
        'monetary':{'status':'verified_associated_value' if complete else 'partial' if verified else 'unavailable',
            'currency':'GBP' if verified else None,'associated_absent_value':str(subtotal) if complete else None,
            'verified_subtotal':str(subtotal) if verified else None,'verified_record_count':verified,'unverifiable_record_count':len(rows)-verified,
            'currency_evidence':{'source_url':DATASET_URL,'field':'UnitPrice','documented_unit':'sterling','currency':'GBP'} if verified else None,
            'label':'Associated source-line value for absent reporting identities; NOT confirmed revenue loss.'},
        'unknowns':sorted(unknowns)}
