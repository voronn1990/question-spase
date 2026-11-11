"""Integration with selected immutable synthesis, current plan and final audit."""
from __future__ import annotations
import copy
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from test_iceberg_contracts import CompleteIcebergModel, _refresh_hash_bindings
from test_model_contracts import model_validation as model, load_yaml, write, digest
from prep_validation import canonical_yaml_bytes
from architecture_validation import architecture_graph_sha256, validate_architecture_graph


def attach_architecture(fixture):
    """Extend the existing full model fixture, preserving its real guard checks."""
    from test_architecture import graph_for_context
    stages = {identifier: fixture.docs[identifier] for identifier in ('S-000','S-001')}
    for stage in stages.values():
        for direction in ('inputs','outputs'):
            for contract in stage['inputs_contracts'][direction]:
                contract['contract']='fixture Celsius payload v1 with explicit fields and accepted units'
                contract['version']='1'
                contract['format']='application/json'
    for identifier in stages:fixture.sync(identifier)
    _refresh_hash_bindings(fixture)
    snapshot=load_yaml(fixture.run/'input/snapshot.yaml')
    snapshot['methodology_features']=['DOUBLE_ICEBERG_V1','ARCHITECTURE_GRAPH_V1']
    write(fixture.run/'input/snapshot.yaml',snapshot)
    context=model.architecture_context(fixture.run,fixture.bank,fixture.results,fixture.docs,stages,load_yaml=load_yaml,sha=digest)
    graph=graph_for_context(context)
    synthesis=fixture.docs['SYNTHESIS-001']
    synthesis['architecture_graph']=graph
    synthesis['chosen_mechanism_id']=graph['selected_mechanism_id']
    fixture.sync('SYNTHESIS-001')
    bind_architecture(fixture)


def bind_architecture(fixture):
    graph=fixture.docs['SYNTHESIS-001']['architecture_graph']
    fixture.sync('SYNTHESIS-001')
    reference={'synthesis_id':'SYNTHESIS-001','synthesis_sha256':fixture.results['SYNTHESIS-001']['content_hash'],'graph_sha256':architecture_graph_sha256(graph)}
    for identifier in ('S-000','S-001'):
        fixture.docs[identifier]['architecture_ref']=copy.deepcopy(reference)
        fixture.sync(identifier)
    resolved=model.resolve_architecture_model(fixture.run,fixture.bank,fixture.results,fixture.docs,load_yaml=load_yaml,sha=digest)
    audit=fixture.docs['AUDIT-001']
    audit['architecture_ref']=copy.deepcopy(reference)
    audit['architecture_review']={'structural_report_sha256':digest(canonical_yaml_bytes(resolved.get('report'))),'semantic_outcome':'PASS','procedure':'execute bounded fixture composition and inspect required transitions','observation':'test fixture only; actual methodology qualification is outside this check','evidence_ids':['E-001'],'findings':[],'limits':['STRUCTURAL_FIXTURE_ONLY; no native semantic qualification']}
    fixture.sync('AUDIT-001')
    _refresh_hash_bindings(fixture)
    return reference


class CompleteArchitectureModel(CompleteIcebergModel):
    def __init__(self, base):
        super().__init__(base)
        attach_architecture(self)


class ArchitectureIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.fixture=CompleteArchitectureModel(Path(self.tmp.name))
    def tearDown(self):self.tmp.cleanup()
    def errors(self):
        f=self.fixture
        return model.validate_model(f.run,f.bank,f.results,f.docs,'final',load_yaml,digest)
    def test_complete_selected_graph_plan_and_audit_pass(self):
        self.assertEqual(self.errors(),[])
    def test_root_plan_must_explicitly_select_synthesis(self):
        self.fixture.docs['S-000'].pop('architecture_ref');self.fixture.sync('S-000')
        self.assertTrue(any('architecture_ref:contract' in e for e in self.errors()))
    def test_all_current_stages_bind_same_graph(self):
        self.fixture.docs['S-001']['architecture_ref']['graph_sha256']='0'*64
        self.assertTrue(any('reference_mismatch' in e for e in self.errors()))
    def test_connected_graph_does_not_supply_semantic_ready(self):
        review=self.fixture.docs['AUDIT-001']['architecture_review'];review['semantic_outcome']='NOT_VERIFIED';review['evidence_ids']=[]
        self.assertTrue(any('graph_connectivity_does_not_establish_ready' in e for e in self.errors()))
    def test_semantic_pass_without_accepted_evidence_fails(self):
        self.fixture.docs['AUDIT-001']['architecture_review']['evidence_ids']=[]
        self.assertTrue(any('semantic_pass_without_evidence' in e for e in self.errors()))
    def test_stale_selected_graph_never_falls_back_to_other_synthesis(self):
        f=self.fixture;reference=f.docs['S-000']['architecture_ref']
        resolved=model.resolve_architecture_model(f.run,f.bank,f.results,f.docs,current={'stale_result_ids':['SYNTHESIS-001']},load_yaml=load_yaml)
        self.assertIn('architecture:selected_model_stale',resolved['errors']);self.assertEqual(resolved['architecture_ref'],reference)
        observed=model.resolve_architecture_model(f.run,f.bank,f.results,f.docs,current={'stale_result_ids':['SYNTHESIS-001']},allow_stale=True,load_yaml=load_yaml)
        self.assertEqual(observed['selection_status'],'NEEDS_RECHECK')
    def test_stale_existing_graph_resource_is_not_available(self):
        f=self.fixture
        resolved=model.resolve_architecture_model(f.run,f.bank,f.results,f.docs,current={'stale_result_ids':['E-001']},load_yaml=load_yaml)
        self.assertTrue(any('existing input is stale' in e for e in resolved['errors']))
    def test_malformed_audit_semantic_outcome_is_addressed_failure(self):
        self.fixture.docs['AUDIT-001']['architecture_review']['semantic_outcome']=[]
        self.assertTrue(any('architecture:audit:semantic_outcome' in e for e in self.errors()))
    def test_graph_hash_and_exact_synthesis_bytes_are_bound(self):
        f=self.fixture;path=f.run/f.results['SYNTHESIS-001']['path'];path.write_bytes(path.read_bytes()+b'\n')
        self.assertTrue(any('synthesis_hash' in e for e in self.errors()))
    def test_reserved_canonical_graph_cannot_be_owned_by_other_role(self):
        f=self.fixture;doc=copy.deepcopy(f.docs['SYNTHESIS-001']);doc['author_role']='OP-PROJECTION'
        errors=model.validate_intake_candidate(f.run,f.bank,f.results,doc,'OPERATOR',load_yaml)
        self.assertTrue(any('architecture_graph_owner' in e for e in errors))
    def test_initial_synthesis_does_not_require_future_plan_artifacts(self):
        f=self.fixture;results={k:v for k,v in f.results.items() if v['artifact_type']!='PLAN'}
        errors=model.validate_intake_candidate(f.run,f.bank,results,f.docs['SYNTHESIS-001'],'SYNTHESIS',load_yaml)
        self.assertEqual(errors,[])
    def test_audit_intake_rejects_malformed_review_before_immutable_commit(self):
        f=self.fixture
        for field in ('structural_report_sha256','semantic_outcome','procedure','observation','evidence_ids','findings','limits'):
            for value in ({'bad':'mapping'},None,True,19):
                with self.subTest(field=field,value=value):
                    doc=copy.deepcopy(f.docs['AUDIT-001']);doc['architecture_review'][field]=value
                    self.assertTrue(model.validate_intake_candidate(f.run,f.bank,f.results,doc,'FINAL_AUDIT',load_yaml))
    def test_stale_semantic_evidence_outside_graph_cannot_support_audit(self):
        f=self.fixture
        doc=copy.deepcopy(f.docs['E-001']);doc.update({'id':'E-SEMANTIC-OLD','assignment_id':'ASG-E-SEMANTIC-OLD','observation':'stale old semantic observation'})
        f.add_result('E-SEMANTIC-OLD','EVIDENCE',doc['author_role'],doc['agent_id'],'evidence/E-SEMANTIC-OLD.yaml',doc)
        f.docs['SYNTHESIS-001']['execution_edges'].append({'from_type':'E','from_id':'E-SEMANTIC-OLD','to_type':'Q','to_id':'Q-001'})
        bind_architecture(f)
        write(f.run/'state/current.yaml',{'stale_result_ids':['E-SEMANTIC-OLD']})
        f.docs['AUDIT-001']['architecture_review']['evidence_ids']=['E-SEMANTIC-OLD'];f.sync('AUDIT-001');_refresh_hash_bindings(f)
        self.assertIn('architecture:audit:evidence_binding',self.errors())
        self.assertTrue(any('evidence_binding' in e for e in model.validate_intake_candidate(f.run,f.bank,f.results,f.docs['AUDIT-001'],'FINAL_AUDIT',load_yaml)))
    def test_lost_requirement_is_rejected_after_hashes_rebound(self):
        f=self.fixture;g=f.docs['SYNTHESIS-001']['architecture_graph'];g['nodes']=[n for n in g['nodes'] if n['kind']!='R'];g['edges']=[e for e in g['edges'] if e['from_id']!='R-001' and e['to_id']!='R-001'];bind_architecture(f)
        self.assertTrue(any('requirement' in e.lower() for e in self.errors()))

if __name__=='__main__':unittest.main()
