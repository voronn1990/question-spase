#!/usr/bin/env python3
"""Question Space v4 local YAML control-plane helper.

This is deliberately not an agent runtime and does not assess research quality.
It stores exact accepted YAML bytes, maintains a small coordinator index, and
checks structural provenance, closure and release bindings.
Requires PyYAML >= 6.0 (declared in requirements.txt).
"""
from __future__ import annotations
import argparse, base64, binascii, errno, hashlib, json, os, re, shutil, stat, sys, tempfile, time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, NamedTuple
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from command_admission import CapturedInput, Committed, Request, admit, cached_loader, capture_file, command_cut, publish_bytes, publish_locked
from model_validation import (
    current_iceberg_consumer_result_ids,
    current_iceberg_view_projection,
    model_revision_scope_for_run,
    double_iceberg_enabled,
    external_cache_errors,
    resolve_architecture_model,
    validate_blind_iceberg_position,
    validate_double_iceberg,
    validate_intake_candidate,
    validate_model,
    ledger_gap_outcome,
    _immutable_input_hashes,
    _review_contract,
    _validate_stages,
)
from architecture_validation import architecture_impact, derive_architecture_views, render_architecture_mermaid
from orchestration import ADVISER_ROLE, dispatch_resource_conflict, ordinary_adviser_assignment, validate_orchestration
from prep_validation import (
    approved_import_for_framing, frontier_transition_valid, plan_projection_transition_valid,
    resolve_admission_state, validate_prep,
)
from question_admission import Arm, prepare_question_receipt, replay_question_receipts
from question_ledger import (
    Admission, AdviceAssignment, AdviceContext, Alias, Assignment, Contribution,
    Disposition, Gap, HiddenWork, LedgerError, Operation, Procedure, Question, Ref, Transition, compare,
)

try:
    import fcntl  # type: ignore
except ImportError:  # native Windows
    fcntl = None

SCHEMA = "question-space-v4"
ROLES = {*(f"P-{i:02d}" for i in range(1, 11)), "OP-VECTOR", "OP-PROJECTION", "OP-DIVERGENCE", "OP-REVERSE", "OP-INDULGENCE", "OP-SYNTHESIS", "PLAN-AUTHOR", "FINAL-AUDITOR"}
ROLE_KINDS = {r: (["FRAMING", "PRIMARY"] if r.startswith("P-") else ["SYNTHESIS"] if r == "OP-SYNTHESIS" else ["PLAN"] if r == "PLAN-AUTHOR" else ["FINAL_AUDIT"] if r == "FINAL-AUDITOR" else ["OPERATOR"]) for r in ROLES}
COMMON = {"schema_version", "id", "run_id", "input_snapshot", "author_role", "agent_id", "knowledge_status", "workflow_status", "source_refs", "related_ids", "revision", "created_at", "updated_at"}
KNOWLEDGE = {"OBSERVED", "DERIVED", "HYPOTHESIS", "REFUTED", "UNKNOWN"}
WORKFLOW = {"RUNNING", "ACCEPTED", "BLOCKED", "REJECTED", "STALE", "NOT_RUN", "NEEDS_RECHECK"}
ARTIFACT_TYPES = {"FRAMING", "PRIMARY", "OPERATOR", "ANSWER", "SOURCE_OUTCOMES", "EVIDENCE", "HYPOTHESIS", "DECISION", "REVIEW", "EXPERIMENT", "SYNTHESIS", "PLAN", "FINAL_AUDIT"}
OUTCOME = {"PASS", "FAIL", "NOT_VERIFIED"}
VERDICTS = {"READY", "REPAIR_REQUIRED", "RESEARCH_REQUIRED", "BLOCKED"}
ROOT_DIRS = {"positions", "answers", "hypotheses", "evidence", "decisions", "reviews", "operators", "experiments", "synthesis", "plan", "audit"}
AMBIGUOUS = re.compile(r"^(?:[-+]?\d+|[-+]?\d*\.\d+|[-+]?\.inf|\.nan|true|false|yes|no|on|off|null|~|\d{4}-\d\d-\d\d(?:[Tt ].*)?)$", re.I)

class QSError(Exception): pass

def sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def emit(x: dict[str, Any], stream=sys.stdout) -> None: print(json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":")), file=stream)
class CanonDumper(yaml.SafeDumper):
    """Quote all strings: files produced here pass the strict lexical loader."""
    def ignore_aliases(self, data: Any) -> bool:
        # Strict input rejects aliases.  Coordinator snapshots must therefore
        # duplicate shared containers instead of emitting YAML anchors.
        return True
def _quoted(dumper: yaml.Dumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')
CanonDumper.add_representer(str, _quoted)
def canon_yaml(x: Any) -> bytes:
    return yaml.dump(x, Dumper=CanonDumper, allow_unicode=True, sort_keys=True,
                     default_flow_style=False).encode("utf-8")

class StrictLoader(yaml.SafeLoader): pass
def _mapping(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result: raise QSError("yaml_duplicate_or_nonstring_key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result
StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)

def _parse_yaml(path: CapturedInput) -> Any:
    try: text = path.raw.decode("utf-8")
    except UnicodeDecodeError as exc: raise QSError("yaml_read_error:" + str(path)) from exc
    try:
        for event in yaml.parse(text):
            if getattr(event, "anchor", None) is not None or isinstance(event, yaml.AliasEvent) or getattr(event, "tag", None) is not None: raise QSError("yaml_tags_or_anchors_forbidden")
            if isinstance(event, yaml.ScalarEvent) and event.style is None and AMBIGUOUS.fullmatch(event.value): raise QSError("yaml_implicit_scalar_forbidden")
        return yaml.load(text, Loader=StrictLoader)
    except (yaml.YAMLError, QSError) as exc: raise QSError("invalid_strict_yaml:" + str(exc)) from exc

_cached_parse_yaml = cached_loader(_parse_yaml)


def load_yaml(path: Path | CapturedInput) -> Any:
    try:
        source=path if isinstance(path,CapturedInput) else CapturedInput(str(path),path.read_bytes())
    except OSError as exc:
        raise QSError("yaml_read_error:" + str(path)) from exc
    return _cached_parse_yaml(source)


def atomic(path: Path, data: bytes, *, replace: bool=True) -> None:
    if os.name=="posix":
        publish_bytes(path,data,replace=replace)
        return
    path.parent.mkdir(parents=True, exist_ok=True); fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f: f.write(data); f.flush(); os.fsync(f.fileno())
        if replace:
            os.replace(tmp,path)
        else:
            os.link(tmp,path)
            os.unlink(tmp)
        flag = getattr(os, "O_DIRECTORY", None)
        if flag is not None:
            d = os.open(str(path.parent), flag)
            try: os.fsync(d)
            finally: os.close(d)
    except BaseException:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise
def save_yaml(path: Path, value: Any) -> None: atomic(path, canon_yaml(value))
def ident(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", value): raise QSError("invalid_" + name)
    return value
def opaque_agent(value: Any) -> str:
    """Native ids are opaque; `/root/foo` is valid and is never used as a path."""
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value:
        raise QSError("invalid_agent_id")
    return value
def root(raw: str) -> Path:
    p = Path(raw).absolute()
    if p.is_symlink(): raise QSError("unsafe_root_symlink")
    return p
def child(base: Path, rel: str) -> Path:
    part = Path(rel)
    if part.is_absolute() or ".." in part.parts: raise QSError("unsafe_path")
    current = base
    for name in part.parts:
        current = current / name
        if current.exists() and current.is_symlink(): raise QSError("unsafe_symlink")
    return current
def run_dir(workspace: Path, run_id: str) -> Path: return child(workspace / "runs", ident(run_id, "run_id"))
def state_file(run: Path, name: str) -> Path: return child(run / "state", name + ".yaml")
def load_state(run: Path, name: str) -> dict[str, Any]:
    data = load_yaml(state_file(run, name))
    if not isinstance(data, dict): raise QSError("invalid_state_" + name)
    return data

@contextmanager
def lock(run: Path):
    control = run / "state"; control.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        marker = control / "coordinator.lock.exclusive"
        try: fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc: raise QSError("coordinator_lock_busy_or_stale_manual_recovery_required") from exc
        try: os.write(fd, str(os.getpid()).encode()); yield
        finally:
            os.close(fd)
            try: marker.unlink()
            except FileNotFoundError: pass
    else:
        marker=control/"coordinator.lock"
        fd=os.open(marker,os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"r+b") as f:
            marker_state=os.fstat(f.fileno())
            if not stat.S_ISREG(marker_state.st_mode) or marker_state.st_nlink!=1:
                raise QSError("coordinator_lock_file_unsafe")
            try: fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}: raise QSError("coordinator_lock_busy") from exc
                raise
            try: yield
            finally: fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def captured_argument(path: Path, maximum: int = 16 * 1024**2) -> CapturedInput:
    """Read an ordinary command argument once without following symlinks."""
    path = path.absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        raw = capture_file(fd, path.relative_to(path.anchor).as_posix(), maximum)
    finally:
        os.close(fd)
    return CapturedInput(str(path), raw)


def load_bound_yaml(run: Path, relative: str, expected: str, error: str) -> Any:
    """Parse only the captured bytes that match the admitted source digest."""
    if not isinstance(relative,str) or not relative or not isinstance(expected,str):
        raise QSError(error)
    try:
        source=captured_argument(child(run,relative),48 * 1024**2)
    except OSError as exc:
        raise QSError(error) from exc
    if sha(source.raw)!=expected:
        raise QSError(error)
    return load_yaml(source)


def locked_command(args: argparse.Namespace, operation, *operation_args):
    run = run_dir(root(args.root), args.run_id)
    if not (run / "state").is_dir():
        raise QSError("command_run_state_missing")
    with command_cut(lock(run)):
        return operation(args, run, *operation_args)


def common(obj: Any, run_id: str, snapshot: str) -> list[str]:
    errors=[]
    if not isinstance(obj, dict): return ["object_not_mapping"]
    missing=COMMON-set(obj)
    if missing: errors.append("missing_common:"+",".join(sorted(missing)))
    if obj.get("schema_version") != SCHEMA or obj.get("run_id") != run_id or obj.get("input_snapshot") != snapshot: errors.append("common_binding")
    if obj.get("knowledge_status") not in KNOWLEDGE or obj.get("workflow_status") not in WORKFLOW: errors.append("common_status")
    for key in ("id", "author_role", "agent_id", "revision", "created_at", "updated_at"):
        if not isinstance(obj.get(key), str) or not obj[key]: errors.append("common_type:"+key)
    for key in ("source_refs", "related_ids"):
        if not isinstance(obj.get(key), list) or any(not isinstance(x,str) for x in obj[key]): errors.append("common_type:"+key)
    return errors

def init_legacy(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    rid=ident(args.run_id,"run_id")
    bank_source=captured_argument(Path(args.bank)); bank_raw=bank_source.raw; bank=load_yaml(bank_source)
    if not isinstance(bank,dict) or not isinstance(bank.get("questions"),list) or not isinstance(bank.get("requirements"),list) or not bank["questions"] or not bank["requirements"]: raise QSError("invalid_bank")
    qids=[]
    for q in bank["questions"]:
        if not isinstance(q,dict) or not isinstance(q.get("id"),str) or not isinstance(q.get("text"),str) or not isinstance(q.get("source_offset"),str): raise QSError("invalid_bank_question")
        qids.append(q["id"])
    if len(qids)!=len(set(qids)) or any(not isinstance(x,str) or not x for x in bank["requirements"]): raise QSError("invalid_bank_ids")
    run=run_dir(workspace,rid)
    if run.exists(): raise QSError("run_already_exists")
    for directory in ("input","state","gates","intents","transactions",*ROOT_DIRS): (run/directory).mkdir(parents=True,exist_ok=True)
    original_info: dict[str, Any] = {"provided":"false"}
    if not args.original_bank: raise QSError("exact_original_bank_required")
    if args.original_bank:
        original=Path(args.original_bank).absolute(); original_raw=captured_argument(original).raw
        original_rel="input/original-bank"+original.suffix
        atomic(run/original_rel,original_raw)
        original_info={"provided":"true","path":original_rel,"sha256":sha(original_raw)}
    atomic(run/"input/bank.yaml",bank_raw)
    requirements_raw=canon_yaml(bank["requirements"])
    bank_sha=sha(bank_raw)
    descriptor={"schema_version":SCHEMA,"id":"SNAP-"+bank_sha[:16],"run_id":rid,"bank_path":"input/bank.yaml","bank_sha256":bank_sha,"requirements_sha256":sha(requirements_raw),"question_ids":qids,"original_bank":original_info}
    descriptor_raw=canon_yaml(descriptor)
    snapshot=sha(descriptor_raw)
    atomic(run/"input/snapshot.yaml", descriptor_raw)
    save_yaml(state_file(run,"assignments"), {"schema_version":SCHEMA,"run_id":rid,"input_snapshot":snapshot,"results":{},"attempts":{},"registered":{}})
    save_yaml(state_file(run,"events"), {"schema_version":SCHEMA,"run_id":rid,"events":[]})
    save_yaml(state_file(run,"launch-intents"), {"schema_version":SCHEMA,"run_id":rid,"input_snapshot":snapshot,"records":{}})
    save_yaml(state_file(run,"current"), {"schema_version":SCHEMA,"run_id":rid,"input_snapshot":snapshot,"revision":"1","status":"INITIALIZED"})
    return {"status":"OK","run_id":rid,"questions":len(qids),"input_snapshot":snapshot,"scope":"STRUCTURAL_ONLY"}

NEW_MODES={"GOAL_TO_PLAN","BANK_TO_PLAN"}
ICEBERG_FEATURE="DOUBLE_ICEBERG_V1"
ARCHITECTURE_FEATURE="ARCHITECTURE_GRAPH_V1"
ICEBERG_VIEW_OWNERS={"CURRENT_REALITY":"OP-PROJECTION","DESIRED_REALITY":"OP-REVERSE"}
PREP_STAGES={"PREP-1_VECTOR","PREP-2_PERSPECTIVES","PREP-3_EXPAND_NORMALIZE","PREP-4_ADMIT"}
REVISION_SELECTIONS={
    "SOURCE_OUTCOMES": ("current_source_outcomes", "answers/source-outcomes", "answers/source-outcomes.yaml"),
    "FINAL_AUDIT": ("current_final_audit", "audit/final", "audit/final.yaml"),
}


def _copy_input(run: Path, source: Path, relative: str) -> dict[str,str]:
    raw=captured_argument(source).raw
    target=child(run,relative)
    atomic(target,raw)
    return {"path":relative,"sha256":sha(raw)}


def _new_snapshot_init(args: argparse.Namespace, workspace: Path, rid: str, mode: str) -> dict[str,Any]:
    if not args.goal: raise QSError("goal_required_for_new_mode")
    run=run_dir(workspace,rid)
    if run.exists(): raise QSError("run_already_exists")
    for directory in ("input","state","gates","intents","transactions","transactions/admissions","transactions/current",*ROOT_DIRS):
        (run/directory).mkdir(parents=True,exist_ok=True)
    goal=Path(args.goal).absolute()
    goal_rel="input/goal"+goal.suffix
    goal_info=_copy_input(run,goal,goal_rel)
    materials=[]
    seen=set()
    for number,raw_path in enumerate(getattr(args,"material",[]) or [],start=1):
        source=Path(raw_path).absolute(); name=source.name
        if not name or name in seen: name="material-"+str(number)+source.suffix
        seen.add(name)
        materials.append(_copy_input(run,source,"input/materials/"+str(number).zfill(3)+"-"+name))
    descriptor={"schema_version":SCHEMA,"id":"SNAP-"+goal_info["sha256"][:16],"run_id":rid,
                "input_mode":mode,"goal_path":goal_info["path"],"goal_sha256":goal_info["sha256"],
                "materials":materials,"bank_binding":"DEFERRED_TO_PREP_ADMISSION" if mode=="GOAL_TO_PLAN" else "BOUND_AT_INIT",
                "methodology_features":[ICEBERG_FEATURE,ARCHITECTURE_FEATURE]}
    if getattr(args,"test_fixture",False):
        # Strict YAML forbids unquoted implicit booleans.  `fixture_only: YES`
        # is the accepted explicit fixture marker consumed by prep_validation.
        descriptor["test_fixture"]="true"; descriptor["fixture_only"]="YES"
    effort=getattr(args,"effort",None)
    half=getattr(args,"half_budget",None)
    procedure=getattr(args,"question_procedure",None)
    if any(value is not None for value in (effort,half,procedure)):
        if effort not in {"FAST","DETAILED"} or not isinstance(half,int) or half <= 0 or not procedure:
            raise QSError("question_arm_inputs_required")
        source=captured_argument(Path(procedure),64 * 1024)
        document=load_yaml(source)
        if (not isinstance(document,dict) or set(document)!=QUESTION_PROCEDURE_FIELDS
            or any(not isinstance(value,str) or not value.strip() for value in document.values())):
            raise QSError("question_procedure_contract")
        atomic(child(run,"input/question-procedure.yaml"),source.raw)
        descriptor["question_arm"]={"effort_mode":effort,"half_budget":str(half),
                                    "procedure_sha256":sha(source.raw)}
    if mode=="BANK_TO_PLAN":
        if not args.bank: raise QSError("bank_input_missing")
        bank_source=captured_argument(Path(args.bank)); bank_raw=bank_source.raw; bank=load_yaml(bank_source)
        if not isinstance(bank,dict) or not isinstance(bank.get("questions"),list) or not isinstance(bank.get("requirements"),list): raise QSError("invalid_bank")
        atomic(run/"input/bank.yaml",bank_raw)
        foreign=(args.approved_vector,args.vector_approval,args.approval_statement)
        if all(foreign) and not args.external_bank:
            import_id="IMP-"+sha(bank_raw)[:16]
            import_root="input/imports/"+import_id
            vector_info=_copy_input(run,Path(args.approved_vector).absolute(),import_root+"/vector.yaml")
            approval_info=_copy_input(run,Path(args.vector_approval).absolute(),import_root+"/approval.yaml")
            statement=Path(args.approval_statement).absolute()
            statement_info=_copy_input(run,statement,import_root+"/approval-statement"+statement.suffix)
            vector=load_yaml(run/vector_info["path"]); approval=load_yaml(run/approval_info["path"])
            if not isinstance(vector,dict) or not isinstance(approval,dict): raise QSError("foreign_import_not_mapping")
            binding={"schema_version":"question-space-import-v1","import_id":import_id,"current_run_id":rid,
                     "source_run_id":vector.get("run_id"),"source_input_snapshot":vector.get("input_snapshot"),
                     "source_vector_result_id":vector.get("id"),"source_vector_sha256":vector_info["sha256"],
                     "source_vector_revision":vector.get("revision"),"source_vector_author_role":vector.get("author_role",vector.get("role_id")),
                     "source_vector_agent_id":vector.get("agent_id"),"vector_path":vector_info["path"],"vector_sha256":vector_info["sha256"],
                     "source_approval_id":approval.get("approval_id"),"source_approval_decision":approval.get("decision"),
                     "source_approval_mode":approval.get("approval_mode"),"source_approver_id":approval.get("approver_id"),
                     "source_authority_basis":approval.get("authority_basis"),"source_observation_provenance":approval.get("observation_provenance"),
                     "approval_path":approval_info["path"],"approval_sha256":approval_info["sha256"],
                     "statement_path":statement_info["path"],"statement_sha256":statement_info["sha256"]}
            binding["import_payload_sha256"]=sha(canon_yaml(binding))
            save_yaml(run/(import_root+"/binding.yaml"),binding)
            descriptor["bank_entry_kind"]="APPROVED_RUN_IMPORT"
            descriptor["import_binding"]=binding
        elif args.external_bank and not any(foreign):
            original=Path(args.external_bank).absolute()
            original_info=_copy_input(run,original,"input/external-bank/original"+original.suffix)
            binding={"schema_version":"question-space-external-bank-v1","source_kind":"EXTERNAL_READY_BANK",
                     "current_run_id":rid,"original_path":original_info["path"],"original_sha256":original_info["sha256"],
                     "normalized_path":"input/bank.yaml","normalized_sha256":sha(bank_raw)}
            binding["external_payload_sha256"]=sha(canon_yaml(binding))
            save_yaml(run/"input/external-bank/binding.yaml",binding)
            descriptor["bank_entry_kind"]="EXTERNAL_READY_BANK"
            descriptor["external_binding"]=binding
        else:
            raise QSError("bank_entry_contract_requires_exact_foreign_import_or_external_bank")
        descriptor["bank_path"]="input/bank.yaml"; descriptor["bank_sha256"]=sha(bank_raw)
        descriptor["requirements_sha256"]=sha(canon_yaml(bank.get("requirements",[])))
    descriptor_raw=canon_yaml(descriptor); snapshot=sha(descriptor_raw)
    atomic(run/"input/snapshot.yaml",descriptor_raw)
    save_yaml(state_file(run,"assignments"),{"schema_version":SCHEMA,"run_id":rid,"input_snapshot":snapshot,"results":{},"attempts":{},"registered":{},"current_attempts":{}})
    save_yaml(state_file(run,"events"),{"schema_version":SCHEMA,"run_id":rid,"events":[]})
    save_yaml(state_file(run,"launch-intents"),{"schema_version":SCHEMA,"run_id":rid,"input_snapshot":snapshot,"records":{}})
    save_yaml(state_file(run,"current"),{"schema_version":SCHEMA,"run_id":rid,"input_snapshot":snapshot,"input_mode":mode,"revision":"0","status":"PREP_OPEN"})
    return {"status":"OK","run_id":rid,"input_mode":mode,"input_snapshot":snapshot,"questions":len(bank.get("questions",[])) if mode=="BANK_TO_PLAN" else "DEFERRED","scope":"STRUCTURAL_ONLY"}


def init(args: argparse.Namespace) -> dict[str,Any]:
    mode=getattr(args,"mode",None)
    if mode is None:
        if any(getattr(args,key,None) is not None for key in ("effort","half_budget","question_procedure")):
            raise QSError("question_arm_requires_new_mode")
        if not getattr(args,"bank",None) or not getattr(args,"original_bank",None): raise QSError("legacy_bank_and_original_required")
    elif mode not in NEW_MODES:
        raise QSError("input_mode")
    workspace=root(args.root)
    rid=ident(args.run_id,"run_id")
    run=run_dir(workspace,rid)
    run.parent.mkdir(parents=True,exist_ok=True)
    with command_cut(lock(child(workspace,".init"))):
        if run.exists() or run.is_symlink(): raise QSError("run_already_exists")
        with tempfile.TemporaryDirectory(prefix=".qs-init-",dir=run.parent) as scratch:
            stage=Path(scratch)
            result=(init_legacy(args,stage) if mode is None else _new_snapshot_init(args,stage,rid,mode))
            os.rename(run_dir(stage,rid),run)
            if os.name=="posix":
                descriptor=os.open(run.parent,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
        return result


def output_path(run: Path, relative: str) -> Path:
    path=Path(relative)
    if len(path.parts)<2 or path.parts[0] not in ROOT_DIRS or path.suffix not in {".yaml",".yml"}: raise QSError("invalid_output_path")
    return child(run,relative)

def expected_root(artifact_type: str) -> set[str]:
    return {
        "FRAMING":{"positions"}, "PRIMARY":{"positions"}, "OPERATOR":{"operators"},
        "ANSWER":{"answers"}, "SOURCE_OUTCOMES":{"answers"}, "EVIDENCE":{"evidence"},
        "HYPOTHESIS":{"hypotheses"}, "DECISION":{"decisions"}, "REVIEW":{"reviews"},
        "EXPERIMENT":{"experiments"}, "SYNTHESIS":{"synthesis"}, "PLAN":{"plan"},
        "FINAL_AUDIT":{"audit"},
    }[artifact_type]

def _new_mode_assignment_guard(run: Path, current: dict[str,Any], assignment: dict[str,Any]) -> None:
    snapshot=load_yaml(run/"input/snapshot.yaml")
    if not isinstance(snapshot,dict) or snapshot.get("input_mode") not in NEW_MODES: return
    scope=assignment.get("lifecycle_scope")
    if scope not in {"PREP","CORE"}: raise QSError("new_mode_lifecycle_scope_required")
    if scope=="PREP":
        if assignment.get("prep_stage") not in PREP_STAGES: raise QSError("new_mode_prep_stage")
        if "prep_admission_sha256" in assignment: raise QSError("new_mode_prep_cannot_bind_admission")
        if assignment.get("prep_stage")=="PREP-1_VECTOR" and (assignment.get("role_id")!="OP-VECTOR" or assignment.get("artifact_type")!="OPERATOR"): raise QSError("prep_vector_role_contract")
        if current.get("status") in {"CORE_ADMITTED","SEALED"}: raise QSError("prep_after_admission_forbidden")
    else:
        if current.get("prep_admission_sha256") is None or assignment.get("prep_admission_sha256")!=current.get("prep_admission_sha256"): raise QSError("core_prep_admission_binding")
        if current.get("status") not in {"CORE_ADMITTED","STALE","SEALED"}: raise QSError("core_before_admission")

def core_metadata(assignment: dict[str,Any]) -> dict[str,str]:
    """Return the explicit admission boundary carried by every CORE surface."""
    if assignment.get("lifecycle_scope") != "CORE":
        return {}
    admission=assignment.get("prep_admission_sha256")
    if not isinstance(admission,str) or not admission:
        raise QSError("core_prep_admission_binding")
    return {"lifecycle_scope":"CORE","prep_admission_sha256":admission}


def core_metadata_matches(value: Any, assignment: dict[str,Any]) -> bool:
    expected=core_metadata(assignment)
    return isinstance(value,dict) and all(value.get(key)==item for key,item in expected.items())


def _classify_committed_new_mode_transaction(run: Path, tx_path: Path, tx: dict[str,Any], registered: dict[str,Any]) -> tuple[str|None,str|None,dict[str,Any]|None,bytes|None,list[str]]:
    """Classify a committed result before deciding whether Core checks apply.

    The canonical assignment bytes and their embedded decoded copy are required
    claims.  Other persisted surfaces may add a claim, but cannot erase or
    contradict the canonical lifecycle classification.  This intentionally
    precedes the Core branch: a record cannot evade it by deleting the field
    that would otherwise select that branch.
    """
    raw_assignment: Any=None; raw: bytes|None=None; embedded=tx.get("assignment")
    try:
        raw=base64.b64decode(tx.get("assignment_bytes_b64",""),validate=True)
        raw_assignment=yaml.load(raw.decode("utf-8"),Loader=StrictLoader)
    except (ValueError,UnicodeDecodeError,yaml.YAMLError,QSError,binascii.Error):
        return None,None,None,None,["core_propagation:transaction_classification:"+tx_path.name]
    if not isinstance(raw_assignment,dict) or not isinstance(embedded,dict):
        return None,None,None,raw,["core_propagation:transaction_classification:"+tx_path.name]
    raw_id=raw_assignment.get("assignment_id"); raw_attempt=raw_assignment.get("attempt_id")
    embedded_id=embedded.get("assignment_id"); embedded_attempt=embedded.get("attempt_id")
    label=raw_id if isinstance(raw_id,str) and raw_id else embedded_id if isinstance(embedded_id,str) and embedded_id else "transaction:"+tx_path.name
    prefix="core_propagation:"+label
    errors: list[str]=[]
    if (not isinstance(raw_id,str) or not raw_id or not isinstance(raw_attempt,str) or not raw_attempt
        or raw_id!=embedded_id or raw_attempt!=embedded_attempt):
        errors.append(prefix+":classification")
        return None,None,None,raw,errors
    pair=raw_id+"\0"+raw_attempt
    # The raw hash and parsed duplicate describe one canonical assignment.  A
    # digest mismatch is classification-integrity failure, not a reason to
    # omit this transaction from later validation.
    if tx.get("assignment_sha256")!=sha(raw) or embedded!=raw_assignment:
        errors.append(prefix+":classification")
    scope=raw_assignment.get("lifecycle_scope")
    embedded_scope=embedded.get("lifecycle_scope")
    if scope not in {"PREP","CORE"} or embedded_scope not in {"PREP","CORE"} or scope!=embedded_scope:
        errors.append(prefix+":classification")
        return None,pair,raw_assignment,raw,errors

    claims=[scope,embedded_scope]
    def add_claim(container: Any, key: str="lifecycle_scope", required_assignment: bool=False) -> None:
        if not isinstance(container,dict):
            if required_assignment: errors.append(prefix+":classification")
            return
        if key not in container:
            if required_assignment: errors.append(prefix+":classification")
            return
        claims.append(container.get(key))

    # Transaction and result entry fields are optional for PREP records but,
    # when present, are immutable classification claims.  CORE's mandatory
    # mirrors are enforced again by the Core provenance branch below.
    add_claim(tx)
    add_claim(tx.get("entry"))
    registry=registered.get(pair)
    if isinstance(registry,dict):
        add_claim(registry)
        add_claim(registry.get("assignment"), required_assignment=True)
    intent_path=run/"intents"/(sha(pair.encode()).upper()+".yaml")
    if intent_path.exists():
        try:
            intent=load_yaml(intent_path)
        except QSError:
            intent=None
        add_claim(intent)
        add_claim(intent.get("assignment") if isinstance(intent,dict) else None, required_assignment=True)
    for claim in claims:
        if claim not in {"PREP","CORE"} or claim!=scope:
            errors.append(prefix+":classification")
            break
    return scope,pair,raw_assignment,raw,errors


def core_propagation_errors(run: Path, state: dict[str,Any], current: dict[str,Any], snapshot_doc: dict[str,Any], result_overrides=None) -> list[str]:
    """Classify every committed new-mode record, then validate all Core ones."""
    if snapshot_doc.get("input_mode") not in NEW_MODES:
        return []
    errors: list[str]=[]; snapshot=current.get("input_snapshot")
    if not isinstance(snapshot,str) or not snapshot:
        return ["core_propagation:input_snapshot"]
    registered=state.get("registered",{}); results=state.get("results",{}); attempts=state.get("attempts",{})
    if not isinstance(registered,dict) or not isinstance(results,dict) or not isinstance(attempts,dict):
        return ["core_propagation:registry_invalid"]
    try:
        launches=load_launch_intents(run,snapshot).get("records",{})
    except QSError:
        return ["core_propagation:launch_journal_invalid"]
    if not isinstance(launches,dict):
        return ["core_propagation:launch_journal_invalid"]

    candidates: list[tuple[str,dict[str,Any],bytes,dict[str,Any],Path]]=[]
    for tx_path in sorted((run/"transactions").glob("*.yaml")):
        try:
            tx=load_yaml(tx_path)
        except QSError:
            errors.append("core_propagation:transaction_classification:"+tx_path.name)
            continue
        # These direct transaction records are immutable result commits.  Check
        # their transport header before classification: otherwise changing a
        # committed record to an unknown status, run, or snapshot makes it
        # silently disappear from the Core inventory.  Admission/current
        # recovery records live in subdirectories and are intentionally outside
        # this direct-result commit loop.
        header_prefix="core_propagation:transaction_header:"+tx_path.name
        if not isinstance(tx,dict):
            errors.append(header_prefix+":mapping")
            continue
        header_errors=[]
        if tx.get("status")!="COMMITTED": header_errors.append("status")
        if tx.get("run_id")!=run.name: header_errors.append("run_id")
        if tx.get("input_snapshot")!=snapshot: header_errors.append("input_snapshot")
        if header_errors:
            errors.extend(header_prefix+":"+field for field in header_errors)
            continue
        scope,pair,assignment,raw,row_errors=_classify_committed_new_mode_transaction(run,tx_path,tx,registered)
        errors.extend(row_errors)
        if scope=="CORE" and pair is not None and isinstance(assignment,dict) and isinstance(raw,bytes):
            candidates.append((pair,assignment,raw,tx,tx_path))
    # During crash recovery the current image can still be the exact PREP_OPEN
    # predecessor of its immutable PREP_ADMISSION transaction.  The replay
    # routine installs the admitted image immediately afterwards; do not reject
    # that verified two-image recovery state merely because accepted Core work
    # already has durable transactions.
    if candidates and current.get("status") not in {"CORE_ADMITTED","STALE","SEALED"}:
        replayable=False
        now=(run/"state/current.yaml").read_bytes()
        for admission_path in sorted((run/"transactions/admissions").glob("*.yaml")):
            try:
                admission_tx=load_yaml(admission_path)
                expected=admission_tx.get("expected_current") if isinstance(admission_tx,dict) else None
                if (isinstance(expected,dict) and admission_tx.get("transaction_type")=="PREP_ADMISSION"
                    and admission_tx.get("input_snapshot")==snapshot
                    and isinstance(expected.get("bytes_b64"),str)
                    and base64.b64decode(expected["bytes_b64"],validate=True)==now
                    and sha(now)==expected.get("sha256")):
                    replayable=True; break
            except (QSError,ValueError,binascii.Error):
                continue
        if replayable:
            return sorted(set(errors))
    expected_admission=current.get("prep_admission_sha256")
    if candidates and (current.get("status") not in {"CORE_ADMITTED","STALE","SEALED"} or not isinstance(expected_admission,str) or not expected_admission):
        errors.append("core_propagation:admission_missing")

    seen_pairs:set[str]=set()
    for pair,assignment,raw,tx,tx_path in candidates:
        assignment_id=assignment["assignment_id"]; result_id=assignment.get("result_id")
        prefix="core_propagation:"+assignment_id
        if pair in seen_pairs:
            errors.append(prefix+":duplicate_transaction")
            continue
        seen_pairs.add(pair)
        if assignment.get("prep_admission_sha256")!=expected_admission:
            errors.append(prefix+":assignment")
        if (not core_metadata_matches(tx,assignment)
            or tx.get("assignment")!=assignment
            or tx.get("assignment_sha256")!=sha(raw)
            or tx.get("assignment_bytes_b64")!=base64.b64encode(raw).decode("ascii")):
            errors.append(prefix+":transaction")

        registry=registered.get(pair)
        if not isinstance(registry,dict):
            errors.append(prefix+":registry_missing")
        else:
            if (registry.get("assignment")!=assignment
                or registry.get("assignment_sha256")!=sha(raw)
                or not core_metadata_matches(registry,assignment)):
                errors.append(prefix+":registry")

        intent_path=run/"intents"/(sha(pair.encode()).upper()+".yaml")
        try:
            intent=load_yaml(intent_path)
        except QSError:
            intent=None
        if (not isinstance(intent,dict)
            or intent.get("run_id")!=run.name or intent.get("input_snapshot")!=snapshot
            or intent.get("assignment")!=assignment
            or intent.get("assignment_sha256")!=sha(raw)
            or intent.get("assignment_bytes_b64")!=base64.b64encode(raw).decode("ascii")
            or not core_metadata_matches(intent,assignment)):
            errors.append(prefix+":intent")

        launch=launches.get(pair)
        if (not isinstance(launch,dict) or launch.get("assignment_id")!=assignment_id
            or launch.get("attempt_id")!=assignment.get("attempt_id")
            or launch.get("assignment_sha256")!=sha(raw)
            or launch.get("state") not in LAUNCH_STATES or not isinstance(launch.get("revision"),str)
            or not core_metadata_matches(launch,assignment)):
            errors.append(prefix+":launch")

        if not isinstance(result_id,str):
            errors.append(prefix+":result_id")
            continue
        tx_entry=tx.get("entry")
        entry=results.get(result_id)
        if not isinstance(tx_entry,dict) or not isinstance(entry,dict) or entry!=tx_entry:
            errors.append(prefix+":result_registry")
        else:
            if (entry.get("assignment_id")!=assignment_id or entry.get("attempt_id")!=assignment.get("attempt_id")
                or entry.get("input_snapshot")!=snapshot or not core_metadata_matches(entry,assignment)):
                errors.append(prefix+":result_registry")
            result_path=entry.get("path")
            try:
                pending_raw=(result_overrides or {}).get(result_id)
                result_doc=(load_yaml(CapturedInput(str(result_path),pending_raw)) if pending_raw is not None
                            else load_yaml(child(run,result_path if isinstance(result_path,str) else "")))
            except QSError:
                result_doc=None
            if (not isinstance(result_doc,dict) or result_doc.get("id")!=result_id
                or result_doc.get("assignment_id")!=assignment_id
                or result_doc.get("attempt_id")!=assignment.get("attempt_id")
                or result_doc.get("input_snapshot")!=snapshot
                or not core_metadata_matches(result_doc,assignment)):
                errors.append(prefix+":result")
        attempt=attempts.get(pair)
        if (not isinstance(attempt,dict) or attempt.get("result_id")!=result_id
            or not isinstance(tx_entry,dict) or attempt.get("content_hash")!=tx_entry.get("content_hash")):
            errors.append(prefix+":attempt")

        try:
            if isinstance(result_doc,dict) and "context_packet_sha256" in result_doc:
                digest=result_doc["context_packet_sha256"]
                if not isinstance(digest,str):
                    raise QSError("result_context_binding")
                context_doc=resolve_context_revision(run,assignment,sha(raw),state,digest).packet
            else:
                chain=context_revision_chain(run,assignment,sha(raw),state)
                context_doc=chain[0].packet if len(chain)==1 else None
        except (QSError,OSError,ValueError):
            context_doc=None
        if (not isinstance(context_doc,dict) or context_doc.get("assignment_id")!=assignment_id
            or context_doc.get("attempt_id")!=assignment.get("attempt_id")
            or context_doc.get("role_id")!=assignment.get("role_id")
            or context_doc.get("artifact_type")!=assignment.get("artifact_type")
            or context_doc.get("input_snapshot")!=snapshot
            or context_doc.get("output_path")!=assignment.get("output_path")
            or not core_metadata_matches(context_doc,assignment)):
            errors.append(prefix+":context")
    return sorted(set(errors))


def _revision_candidates(run: Path, state: dict[str,Any], artifact_type: str) -> tuple[dict[str,dict[str,Any]],dict[str,dict[str,Any]]]:
    """Load one immutable revision family from the accepted registry."""
    if artifact_type not in REVISION_SELECTIONS: raise QSError("revision_selection_type")
    _field,directory,legacy=REVISION_SELECTIONS[artifact_type]
    entries={rid:entry for rid,entry in state.get("results",{}).items() if isinstance(rid,str) and isinstance(entry,dict) and entry.get("artifact_type")==artifact_type}
    docs:dict[str,dict[str,Any]]={}
    for rid,entry in entries.items():
        expected=directory+"/"+rid+".yaml"
        if entry.get("path") not in {legacy,expected}: raise QSError(artifact_type.lower()+"_versioned_path")
        doc=load_bound_yaml(run,entry["path"],entry.get("content_hash"),artifact_type.lower()+"_revision_drift")
        if not isinstance(doc,dict) or doc.get("id")!=rid: raise QSError(artifact_type.lower()+"_revision_contract")
        docs[rid]=doc
    return entries,docs


def _selector_for(result_id: str, entry: dict[str,Any]) -> dict[str,str]:
    selector={"result_id":result_id,"sha256":entry.get("content_hash"),"path":entry.get("path"),
              "assignment_id":entry.get("assignment_id"),"attempt_id":entry.get("attempt_id")}
    if any(not isinstance(value,str) or not value for value in selector.values()): raise QSError("revision_selector_entry")
    return selector


def resolve_current_selection(run: Path, state: dict[str,Any], current: dict[str,Any], artifact_type: str, *, allow_stale: bool=False) -> tuple[dict[str,Any]|None,dict[str,Any]|None,dict[str,str]|None]:
    """Resolve an exact current immutable revision; singleton legacy is read-only compatible."""
    field,_,_=REVISION_SELECTIONS[artifact_type]
    entries,docs=_revision_candidates(run,state,artifact_type)
    if not entries: return None,None,None
    raw=current.get(field)
    if raw is None:
        if len(entries)!=1: raise QSError(field+"_selection_required")
        result_id=next(iter(entries)); selector=_selector_for(result_id,entries[result_id])
    else:
        if not isinstance(raw,dict) or set(raw)!={"result_id","sha256","path","assignment_id","attempt_id"}: raise QSError(field+"_selector_contract")
        result_id=raw.get("result_id")
        if not isinstance(result_id,str) or result_id not in entries or raw!=_selector_for(result_id,entries[result_id]): raise QSError(field+"_selector_binding")
        selector=raw
    chain:set[str]=set(); cursor:str|None=result_id
    while cursor is not None:
        if cursor in chain: raise QSError(field+"_supersession_cycle")
        chain.add(cursor); previous=docs[cursor].get("supersedes")
        if previous is None: cursor=None
        elif not isinstance(previous,str) or previous not in entries: raise QSError(field+"_supersedes")
        else: cursor=previous
    if chain!=set(entries): raise QSError(field+"_ambiguous_history")
    stale=current.get("stale_result_ids",[])
    if not allow_stale and isinstance(stale,list) and result_id in stale: raise QSError(field+"_needs_recheck")
    return entries[result_id],docs[result_id],selector


def _revision_output_guard(assignment: dict[str,Any]) -> None:
    artifact_type=assignment.get("artifact_type")
    if artifact_type not in REVISION_SELECTIONS: return
    _field,directory,legacy=REVISION_SELECTIONS[artifact_type]
    result_id=ident(assignment.get("result_id"),"result_id")
    if assignment.get("output_path") not in {legacy,directory+"/"+result_id+".yaml"}: raise QSError(artifact_type.lower()+"_versioned_path")


def _validate_revision_candidate(run: Path, state: dict[str,Any], current: dict[str,Any], result: dict[str,Any], artifact_type: str) -> None:
    """Reject a branch before any immutable result bytes are committed."""
    if artifact_type not in REVISION_SELECTIONS: return
    result_id=ident(result.get("id"),"result_id")
    entries,_docs=_revision_candidates(run,state,artifact_type)
    if result_id in entries: return
    _entry,_doc,selector=resolve_current_selection(run,state,current,artifact_type,allow_stale=True)
    previous_id=selector.get("result_id") if isinstance(selector,dict) else None
    supersedes=result.get("supersedes")
    if previous_id is None:
        if supersedes is not None: raise QSError(REVISION_SELECTIONS[artifact_type][0]+"_unexpected_supersedes")
    elif supersedes!=previous_id:
        raise QSError(REVISION_SELECTIONS[artifact_type][0]+"_supersedes_current_required")


def _select_revision_locked(run: Path, state: dict[str,Any], current: dict[str,Any], result_id: str, artifact_type: str) -> dict[str,str]:
    field,_,_=REVISION_SELECTIONS[artifact_type]
    entries,docs=_revision_candidates(run,state,artifact_type)
    entry=entries.get(result_id); doc=docs.get(result_id)
    if not isinstance(entry,dict) or not isinstance(doc,dict): raise QSError(field+"_candidate_missing")
    raw=current.get(field); previous_id=raw.get("result_id") if isinstance(raw,dict) else None
    if raw is None and len(entries)>1:
        predecessors=set(entries)-{result_id}
        if len(predecessors)==1: previous_id=next(iter(predecessors))
        else: raise QSError(field+"_selection_required")
    if previous_id==result_id:
        expected=_selector_for(result_id,entry)
        if raw!=expected: raise QSError(field+"_selector_binding")
        return expected
    if previous_id is None:
        if doc.get("supersedes") is not None: raise QSError(field+"_unexpected_supersedes")
    elif doc.get("supersedes")!=previous_id:
        raise QSError(field+"_supersedes_current_required")
    selector=_selector_for(result_id,entry)
    stale=[item for item in current.get("stale_result_ids",[]) if isinstance(item,str) and item!=result_id]
    after={**current,field:selector,**({"stale_result_ids":stale} if current.get("stale_result_ids") is not None else {})}
    mutation="SOURCE_OUTCOMES_SELECT" if artifact_type=="SOURCE_OUTCOMES" else "FINAL_AUDIT_SELECT"
    commit_current_transition(run,current,after,mutation,[{"kind":artifact_type,"path":selector["path"],"sha256":selector["sha256"]}])
    return selector

def _immutable_accepted_attempt_result(run: Path, state: dict[str,Any], assignment_id: str, attempt_id: str) -> str | None:
    """Return the result id only for a fully bound immutable accepted commit.

    A DISPATCH_UNKNOWN launch remains unknown. This proof only establishes
    that exact result bytes were nevertheless committed and accepted, which is
    the narrow condition under which a separately assigned repair attempt may
    supersede the completed result without replaying the old launch.
    """
    pair=assignment_id+"\0"+attempt_id
    registered=state.get("registered",{}).get(pair)
    assignment=registered.get("assignment") if isinstance(registered,dict) else None
    result_id=assignment.get("result_id") if isinstance(assignment,dict) else None
    entry=state.get("results",{}).get(result_id) if isinstance(result_id,str) else None
    attempt=state.get("attempts",{}).get(pair)
    if (not isinstance(registered,dict) or registered.get("status")!="ACCEPTED"
        or not isinstance(assignment,dict) or assignment.get("assignment_id")!=assignment_id
        or assignment.get("attempt_id")!=attempt_id or not isinstance(result_id,str)
        or not isinstance(entry,dict) or entry.get("assignment_id")!=assignment_id
        or entry.get("attempt_id")!=attempt_id or entry.get("result_id")!=result_id
        or entry.get("role_id")!=assignment.get("role_id") or entry.get("agent_id")!=assignment.get("agent_id")
        or entry.get("artifact_type")!=assignment.get("artifact_type")
        or not isinstance(attempt,dict) or attempt.get("result_id")!=result_id
        or attempt.get("content_hash")!=entry.get("content_hash")):
        return None
    try:
        tx=load_yaml(run/"transactions"/(ident(result_id,"result_id")+".yaml"))
        assignment_raw=base64.b64decode(tx.get("assignment_bytes_b64", ""),validate=True) if isinstance(tx,dict) else b""
        result_raw=base64.b64decode(tx.get("result_bytes_b64", ""),validate=True) if isinstance(tx,dict) else b""
        parsed_assignment=yaml.load(assignment_raw.decode("utf-8"),Loader=StrictLoader)
        parsed_result=yaml.load(result_raw.decode("utf-8"),Loader=StrictLoader)
        target=child(run,entry.get("path","") if isinstance(entry.get("path"),str) else "")
        intent=load_yaml(run/"intents"/(sha(pair.encode()).upper()+".yaml"))
    except (QSError,ValueError,UnicodeDecodeError,binascii.Error,yaml.YAMLError):
        return None
    assignment_hash=sha(assignment_raw); result_hash=sha(result_raw)
    if (not isinstance(tx,dict) or tx.get("status")!="COMMITTED" or tx.get("run_id")!=run.name
        or tx.get("input_snapshot")!=entry.get("input_snapshot") or tx.get("assignment")!=assignment
        or tx.get("assignment_sha256")!=assignment_hash or parsed_assignment!=assignment
        or tx.get("entry")!=entry or tx.get("result_sha256")!=entry.get("content_hash")
        or result_hash!=entry.get("content_hash") or not target.is_file() or target.read_bytes()!=result_raw
        or not isinstance(parsed_result,dict) or parsed_result.get("id")!=result_id
        or parsed_result.get("workflow_status")!="ACCEPTED" or parsed_result.get("assignment_id")!=assignment_id
        or parsed_result.get("attempt_id")!=attempt_id or parsed_result.get("author_role")!=assignment.get("role_id")
        or parsed_result.get("agent_id")!=assignment.get("agent_id") or parsed_result.get("artifact_type")!=assignment.get("artifact_type")
        or not isinstance(intent,dict) or intent.get("assignment")!=assignment
        or intent.get("assignment_sha256")!=assignment_hash
        or intent.get("assignment_bytes_b64")!=base64.b64encode(assignment_raw).decode("ascii")
        or registered.get("assignment_sha256")!=assignment_hash):
        return None
    return result_id

def _orchestration_proofs(run: Path, state: dict[str,Any], current: dict[str,Any]) -> tuple[dict[str,str],dict[str,dict[str,str]]]:
    """Bind orchestration exceptions to immutable public-state proofs.

    The pure router may consume these values, but it cannot manufacture them:
    completed unknown launches require the exact intent/transaction/result-byte
    chain above, and current question ownership requires the exact selected
    SOURCE_OUTCOMES revision plus its current accepted answer assignment.
    """
    completed:dict[str,str]={}
    registered=state.get("registered",{})
    if not isinstance(registered,dict): raise QSError("assignment_registry_corrupt")
    for pair,value in registered.items():
        assignment=value.get("assignment") if isinstance(value,dict) else None
        if (not isinstance(pair,str) or not isinstance(assignment,dict)
            or value.get("launch_state",assignment.get("launch_state","NOT_LAUNCHED"))!="DISPATCH_UNKNOWN"):
            continue
        assignment_id=assignment.get("assignment_id"); attempt_id=assignment.get("attempt_id")
        if not isinstance(assignment_id,str) or not isinstance(attempt_id,str) or pair!=assignment_id+"\0"+attempt_id:
            continue
        if state.get("current_attempts",{}).get(assignment_id)==attempt_id:
            continue
        result_id=_immutable_accepted_attempt_result(run,state,assignment_id,attempt_id)
        if isinstance(result_id,str): completed[pair]=result_id

    _source_entry,source_doc,source_selector=resolve_current_selection(
        run,state,current,"SOURCE_OUTCOMES",allow_stale=True
    )
    if source_doc is None or source_selector is None: return completed,{}
    claims:dict[str,dict[str,str]]={}
    for record in source_doc.get("records",[]):
        if not isinstance(record,dict) or record.get("record_kind")!="SOURCE_OUTCOME" or record.get("answer_status")!="ANSWERED":
            continue
        question_ids=record.get("original_q_ids")
        assignment_id=record.get("author_assignment_id"); result_id=record.get("answer_result_id")
        entry=state.get("results",{}).get(result_id) if isinstance(result_id,str) else None
        attempt_id=entry.get("attempt_id") if isinstance(entry,dict) else None
        pair=assignment_id+"\0"+attempt_id if isinstance(assignment_id,str) and isinstance(attempt_id,str) else ""
        registered_answer=registered.get(pair)
        assignment=registered_answer.get("assignment") if isinstance(registered_answer,dict) else None
        if (
            not isinstance(question_ids,list) or not question_ids
            or any(not isinstance(question_id,str) or not question_id for question_id in question_ids)
            or not isinstance(assignment,dict)
            or registered_answer.get("status")!="ACCEPTED"
            or state.get("current_attempts",{}).get(assignment_id)!=attempt_id
            or assignment.get("result_id")!=result_id
            or assignment.get("artifact_type") not in {"PRIMARY","ANSWER"}
            or entry.get("assignment_id")!=assignment_id
            or entry.get("artifact_type")!=assignment.get("artifact_type")
            or entry.get("input_snapshot")!=current.get("input_snapshot")
        ):
            raise QSError("current_question_claim_binding")
        assigned=assignment.get("assigned_ids",{}).get("questions",[])
        answer_doc=load_yaml(child(run,entry.get("path","") if isinstance(entry.get("path"),str) else ""))
        document_questions:set[str]=set()
        if isinstance(answer_doc,dict):
            for field in ("addressed_q_ids","original_q_ids"):
                values=answer_doc.get(field,[])
                if isinstance(values,list): document_questions.update(item for item in values if isinstance(item,str))
            outcomes=answer_doc.get("per_question_outcomes",[])
            if isinstance(outcomes,list):
                document_questions.update(item.get("q_id") for item in outcomes if isinstance(item,dict) and isinstance(item.get("q_id"),str))
        if (
            not isinstance(assigned,list)
            or not isinstance(answer_doc,dict)
            or answer_doc.get("id")!=result_id or answer_doc.get("workflow_status")!="ACCEPTED"
            or answer_doc.get("assignment_id")!=assignment_id or answer_doc.get("attempt_id")!=attempt_id
        ):
            raise QSError("current_question_claim_result_binding")
        claim={"assignment_id":assignment_id,"attempt_id":attempt_id,"result_id":result_id}
        for question_id in question_ids:
            if question_id not in assigned or question_id not in document_questions or question_id in claims:
                raise QSError("current_question_claim_question_binding")
            claims[question_id]=claim
    return completed,claims

def assign(args: argparse.Namespace) -> dict[str, Any]:
    return locked_command(args, _assign_locked)


def assignment_receipt(assignment):
    return {"status":"ASSIGNED","assignment_id":assignment["assignment_id"],"scope":"STRUCTURAL_ONLY"}


def _assignment_role_guard(run, current, assignment):
    role=assignment["role_id"]
    if assignment["artifact_type"] not in ARTIFACT_TYPES:
        raise QSError("assignment_role_or_type")
    if role in ROLES and "adviser" not in assignment:
        return
    if ordinary_adviser_assignment(assignment) and _question_arm(run,current) is not None:
        return
    raise QSError("assignment_role_or_type")


def _bank_framing_exposed(run,state,actor):
    journal=command_journal(run)
    for registered in state["registered"].values():
        prior=registered["assignment"]
        if prior["agent_id"]!=actor or prior["artifact_type"]=="FRAMING":
            continue
        chain=context_revision_chain(run,prior,registered["assignment_sha256"],state,journal=journal)
        if chain:
            packet=chain[-1].packet
            if (any(packet.get(field) for field in ("questions","accepted_dependencies","evidence","decisions"))
                or _bank_readset_exposed(run,packet.get("readset",[]))):
                return True
    return False


def _bank_blind_question_guard(bank,goal,materials):
    forbidden={value for row in bank["questions"] for field in ("id","text")
               if isinstance(value:=row.get(field),str) and value}
    def disclosed(value):
        if isinstance(value,str):
            return any(value==item or len(item)>2 and re.search(
                r"(?<![\w-])"+re.escape(item)+r"(?![\w-])",value
            ) for item in forbidden)
        if isinstance(value,dict):
            return any(disclosed(item) for item in value.values())
        return isinstance(value,list) and any(disclosed(item) for item in value)
    if disclosed(goal) or disclosed([row["content"] for row in materials]):
        raise QSError("blind_goal_bank_disclosure")


def _bank_readset_exposed(run,readset):
    inputs=[row for row in readset if not row["path"].startswith("references/")]
    if not inputs:
        return False
    bank_source=captured_argument(child(run,"input/bank.yaml"))
    for row in inputs:
        path=Path(row["path"])
        if (row["sha256"]==sha(bank_source.raw) or path.parent.name=="input"
            and (path.name=="bank.yaml" or path.name.startswith("original-bank"))):
            return True
    try:
        _bank_blind_question_guard(load_yaml(bank_source),{},inputs)
    except QSError:
        return True
    return False


def _require_bank_framing(run,state,current,assignment):
    if current.get("input_mode")!="BANK_TO_PLAN" or not re.fullmatch(r"P-(0[1-9]|10)",assignment["role_id"]):
        return
    for registered in state["registered"].values():
        prior=registered["assignment"]
        if (prior["artifact_type"]=="FRAMING" and prior["role_id"]==assignment["role_id"]
            and prior["agent_id"]==assignment["agent_id"]
            and state["current_attempts"].get(prior["assignment_id"])==prior["attempt_id"]):
            result_id=_immutable_accepted_attempt_result(run,state,prior["assignment_id"],prior["attempt_id"])
            if result_id is not None:
                require_current_assignment(run,state,current,prior)
                _,document=_admitted_document(run,state,result_id,"FRAMING")
                packet=_question_result_context(run,(state,current,None,None),registered,document)
                if packet.get("blind")=="true":
                    return
    raise QSError("bank_research_requires_accepted_blind_framing")


def _assign_locked(args: argparse.Namespace, run: Path) -> dict[str, Any]:
    source=captured_argument(Path(args.assignment)); assignment=load_yaml(source)
    if not isinstance(assignment,dict): raise QSError("invalid_assignment")
    state,current,bank=accepted(run)  # fail closed before adding work to a drifted run
    snapshot=current.get("input_snapshot")
    required={"assignment_id","attempt_id","role_id","agent_id","input_snapshot","artifact_type","output_path","dependency_hashes","result_id"}
    if not required.issubset(assignment): raise QSError("assignment_missing_fields")
    for key in ("assignment_id","attempt_id","role_id","artifact_type","result_id"): ident(assignment[key],key)
    opaque_agent(assignment["agent_id"])
    occurrence=assignment.get("logical_occurrence_id")
    if occurrence is not None: ident(occurrence,"logical_occurrence_id")
    _assignment_role_guard(run,current,assignment)
    auxiliary=assignment["role_id"]==ADVISER_ROLE
    if any((registered["assignment"]["role_id"]==ADVISER_ROLE)!=auxiliary
           and registered["assignment"]["agent_id"]==assignment["agent_id"]
           for registered in state["registered"].values()):
        raise QSError("adviser_requires_distinct_actor")
    if "group_id" in assignment and assignment["group_id"] not in {"1","2","3"}: raise QSError("assignment_group_limit")
    if "selection_authority" in assignment and assignment["selection_authority"]!="ROOT": raise QSError("assignment_selection_authority")
    if "launch_state" in assignment and assignment["launch_state"] not in {"NOT_LAUNCHED","READY_TO_DISPATCH","DISPATCH_UNKNOWN"}: raise QSError("assignment_launch_state")
    if "assigned_ids" in assignment:
        assigned=assignment["assigned_ids"]
        if not isinstance(assigned,dict) or any(key not in {"questions","requirements","decisions","evidence"} or not isinstance(value,list) or any(not isinstance(item,str) for item in value) for key,value in assigned.items()): raise QSError("assignment_assigned_ids")
    if assignment["input_snapshot"] != snapshot or not isinstance(assignment["dependency_hashes"],list) or any(not isinstance(x,str) for x in assignment["dependency_hashes"]): raise QSError("assignment_snapshot_or_dependencies")
    raw=source.raw
    key=assignment["assignment_id"]+"\0"+assignment["attempt_id"]
    intent=child(run,"intents/"+sha(key.encode()).upper()+".yaml")
    entry={"assignment":assignment,"assignment_sha256":sha(raw),"status":"ASSIGNED","launch_state":assignment.get("launch_state","NOT_LAUNCHED"),**core_metadata(assignment)}
    existing=state.setdefault("registered",{}).get(key)
    if intent.is_file():
        recorded=load_yaml(captured_argument(intent))
        if (not isinstance(recorded,dict) or recorded.get("assignment")!=assignment
            or recorded.get("assignment_sha256")!=sha(raw)
            or recorded.get("assignment_bytes_b64")!=base64.b64encode(raw).decode("ascii")):
            raise QSError("assignment_pair_conflict")
        receipt=assignment_receipt(assignment)
        journal_first=recorded.get("publication")=="JOURNAL_FIRST"
        if journal_first and (
            recorded.get("schema_version")!=SCHEMA or recorded.get("run_id")!=run.name
            or recorded.get("input_snapshot")!=snapshot or recorded.get("status")!="ASSIGNED"
            or recorded.get("intent_id")!="INT-"+sha(key.encode())[:16]
            or not core_metadata_matches(recorded,assignment)
            or recorded.get("predecessor_attempt_id")!=assignment.get("supersedes_attempt_id","ABSENT")
            or recorded.get("receipt")!=receipt
        ):
            raise QSError("assignment_intent_receipt_binding")
        if existing is None:
            recovered=_recover_locked(args,run)
            if recovered.get("status")!="RECOVERED": raise QSError("assignment_projection_recovery")
            existing=load_state(run,"assignments").get("registered",{}).get(key)
        if (not isinstance(existing,dict) or existing.get("assignment")!=assignment
            or existing.get("assignment_sha256")!=sha(raw)):
            raise QSError("assignment_pair_conflict")
        if current.get("input_mode") in NEW_MODES and journal_first: return receipt
        return {"status":"IDEMPOTENT","assignment_id":assignment["assignment_id"],"scope":"STRUCTURAL_ONLY"}
    if existing is not None: raise QSError("assignment_intent_missing")
    _new_mode_assignment_guard(run,current,assignment)
    _revision_output_guard(assignment)
    if assignment.get("artifact_type")=="FRAMING":
        if (current.get("input_mode")=="BANK_TO_PLAN" and _question_arm(run,current) is not None
            and _bank_framing_exposed(run,state,assignment["agent_id"])):
            raise QSError("bank_framing_actor_already_exposed")
        if assignment.get("lifecycle_scope")=="PREP": validate_prep_framing_assignment(assignment,run,state,current)
        else: validate_blind_assignment(assignment,bank)
    target=output_path(run,assignment["output_path"])
    if target.relative_to(run).parts[0] not in expected_root(assignment["artifact_type"]): raise QSError("assignment_output_type_mismatch")
    current_attempts=state.setdefault("current_attempts",{})
    if not isinstance(current_attempts,dict): raise QSError("current_attempts_corrupt")
    previous=current_attempts.get(assignment["assignment_id"])
    if previous is not None and previous != assignment["attempt_id"]:
        old_key=assignment["assignment_id"]+"\0"+str(previous); old=state["registered"].get(old_key)
        terminal=isinstance(old,dict) and old.get("status") in {"ACCEPTED","REJECTED","BLOCKED","STALE","SUPERSEDED"}
        unknown_completed=(isinstance(old,dict) and old.get("launch_state")=="DISPATCH_UNKNOWN"
                           and _immutable_accepted_attempt_result(run,state,assignment["assignment_id"],str(previous)) is not None)
        terminal=terminal or terminal_attempt(run,assignment["assignment_id"],previous)
        if not terminal or (old.get("launch_state")=="DISPATCH_UNKNOWN" and not unknown_completed): raise QSError("retry_predecessor_not_terminal")
        if old.get("assignment",{}).get("logical_occurrence_id")!=occurrence:
            raise QSError("retry_occurrence_changed")
        if occurrence is not None and occurrence_effect(run,occurrence)=="UNKNOWN":
            raise QSError("retry_occurrence_unknown")
        if assignment.get("supersedes_attempt_id") != previous: raise QSError("retry_supersession_missing")
    elif previous is None and assignment.get("supersedes_attempt_id") is not None: raise QSError("retry_supersession_unknown")
    receipt=assignment_receipt(assignment)
    intent_doc={"schema_version":SCHEMA,"intent_id":"INT-"+sha(key.encode())[:16],"run_id":args.run_id,"input_snapshot":snapshot,"assignment_sha256":sha(raw),"assignment_bytes_b64":base64.b64encode(raw).decode("ascii"),"assignment":assignment,"status":"ASSIGNED","publication":"JOURNAL_FIRST","predecessor_attempt_id":previous or "ABSENT","receipt":receipt,**core_metadata(assignment)}
    launches=load_launch_intents(run,snapshot)
    records=launches["records"]
    if key in records: raise QSError("launch_intent_pair_conflict")
    records[key]=initial_launch_record(assignment,sha(raw))
    state["registered"][key]=entry
    current_attempts[assignment["assignment_id"]]=assignment["attempt_id"]
    events=load_state(run,"events")
    event={"event":"ASSIGNMENT_REGISTERED","assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],"assignment_sha256":sha(raw)}
    event["event_hash"]=sha(canon_yaml(event))
    events["events"].append(event)
    writes=tuple((state_file(run,name),canon_yaml(value))
                 for name,value in (("launch-intents",launches),("assignments",state),("events",events)))
    if occurrence is not None:
        directory=child(run,"state/occurrences/"+sha(occurrence.encode())+"/state")
        directory.mkdir(parents=True,exist_ok=True)
    publish_locked((intent,canon_yaml(intent_doc)),writes,atomic)
    return receipt


LAUNCH_STATES={"NOT_LAUNCHED","READY_TO_DISPATCH","DISPATCH_UNKNOWN"}
def initial_launch_record(assignment, digest):
    return {"assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],
            "assignment_sha256":digest,"state":assignment.get("launch_state","NOT_LAUNCHED"),
            "revision":"1",**core_metadata(assignment)}


def load_launch_intents(run: Path, snapshot: str) -> dict[str,Any]:
    path=state_file(run,"launch-intents")
    if not path.is_file():
        # Compatibility is deliberately narrow: old runs retain only the
        # immutable initial assignment declaration until a new intent is saved.
        return {"schema_version":SCHEMA,"run_id":run.name,"input_snapshot":snapshot,"records":{}}
    value=load_state(run,"launch-intents")
    if value.get("schema_version")!=SCHEMA or value.get("run_id")!=run.name or value.get("input_snapshot")!=snapshot or not isinstance(value.get("records"),dict): raise QSError("launch_intents_corrupt")
    return value

def launch_intent(args: argparse.Namespace) -> dict[str,Any]:
    return locked_command(args, _launch_intent_locked)


def launch_receipt(record):
    return {"status":"LAUNCH_INTENT_RECORDED","assignment_id":record["assignment_id"],
            "attempt_id":record["attempt_id"],"launch_state":record["state"],"scope":"STRUCTURAL_ONLY"}


def _launch_intent_locked(args: argparse.Namespace, run: Path) -> dict[str,Any]:
    state,current,_=accepted(run); snapshot=current["input_snapshot"]
    assignment_id=ident(args.assignment_id,"assignment_id"); attempt_id=ident(args.attempt_id,"attempt_id"); desired=args.state
    if desired not in LAUNCH_STATES: raise QSError("launch_state")
    key=assignment_id+"\0"+attempt_id
    state=load_state(run,"assignments"); entry=state.get("registered",{}).get(key)
    if not isinstance(entry,dict) or not isinstance(entry.get("assignment"),dict): raise QSError("unknown_assignment_attempt")
    _new_mode_assignment_guard(run,current,entry["assignment"])
    if entry.get("status")!="ASSIGNED" or terminal_attempt(run,assignment_id,attempt_id): raise QSError("launch_intent_nonpending")
    launches=load_launch_intents(run,snapshot); records=launches["records"]; prior=records.get(key)
    chain=checked_launch_chain(run,snapshot,key,entry,prior)
    if chain and prior!=chain[-1]:
        raise QSError("launch_transition_recovery_required")
    initial=entry["assignment"].get("launch_state","NOT_LAUNCHED")
    old=prior.get("state") if isinstance(prior,dict) else initial
    if old not in LAUNCH_STATES: raise QSError("launch_intent_corrupt")
    allowed={"NOT_LAUNCHED":{"NOT_LAUNCHED","READY_TO_DISPATCH","DISPATCH_UNKNOWN"},"READY_TO_DISPATCH":{"READY_TO_DISPATCH","DISPATCH_UNKNOWN"},"DISPATCH_UNKNOWN":{"DISPATCH_UNKNOWN"}}
    if desired not in allowed[old]: raise QSError("launch_intent_transition")
    record={"assignment_id":assignment_id,"attempt_id":attempt_id,"assignment_sha256":entry.get("assignment_sha256"),"state":desired,"revision":str(int(prior.get("revision","0")) + 1) if isinstance(prior,dict) and str(prior.get("revision","0")).isdigit() else "1",**core_metadata(entry["assignment"])}
    if not isinstance(record["assignment_sha256"],str): raise QSError("launch_intent_binding")
    receipt=launch_receipt(record)
    if isinstance(prior,dict) and old==desired: return receipt
    if not isinstance(prior,dict): raise QSError("launch_intent_predecessor_missing")
    journal=child(run,"transactions/launch/"+sha(key.encode())+"/"+record["revision"].zfill(8)+".yaml")
    transition={"schema_version":SCHEMA,"run_id":run.name,"input_snapshot":snapshot,
                "before_sha256":sha(canon_yaml(prior)),"before":prior,"after":record,"receipt":receipt}
    transition["record_sha256"]=sha(canon_yaml(transition))
    if journal.exists():
        if captured_argument(journal).raw!=canon_yaml(transition): raise QSError("launch_transition_conflict")
    records[key]=record
    entry["launch_state"]=desired
    state["registered"][key]=entry
    events=load_state(run,"events")
    event={"event":"LAUNCH_INTENT_RECORDED","assignment_id":assignment_id,"attempt_id":attempt_id,"assignment_sha256":record["assignment_sha256"],"state":desired}
    event["event_hash"]=sha(canon_yaml(event))
    events["events"].append(event)
    writes=tuple((state_file(run,name),canon_yaml(value))
                 for name,value in (("launch-intents",launches),("assignments",state),("events",events)))
    publish_locked(None if journal.exists() else (journal,canon_yaml(transition)),writes,atomic)
    return receipt



QUESTION_PROCEDURE_FIELDS={"inventory_rule","evidence_cutoff","tools","routing_rules","methodology","review_and_audit"}
QUESTION_COMMAND_KINDS={
    "question-register":"REVIEW",
    "question-observe":"EVIDENCE",
    "question-close":"REVIEW",
    "question-gaps":"REVIEW",
    "adviser-contribute":"EVIDENCE",
    "adviser-dispose":"REVIEW",
}
QUESTION_COMMANDS=set(QUESTION_COMMAND_KINDS)|{"adviser-assign"}
COMMAND_JOURNAL_OPERATIONS={"evidence-intake","evidence-disposition","terminal-author-failure","claim-effect","reconcile-effect"}|QUESTION_COMMANDS


def command_journal(run: Path) -> list[dict[str,Any]]:
    directory=child(run,"transactions/commands")
    rows=[]; keys=set(); prior="GENESIS"
    paths=sorted(directory.glob("*.yaml"))
    snapshot=load_state(run,"current")["input_snapshot"] if paths else None
    for number,path in enumerate(paths,1):
        raw=captured_argument(path,1024**2).raw
        row=load_yaml(CapturedInput(str(path),raw))
        if (path.name!=f"{number:08d}.yaml" or not isinstance(row,dict)
            or row.get("previous_sha256")!=prior or row.get("run_id")!=run.name or row.get("input_snapshot")!=snapshot
            or row.get("operation") not in COMMAND_JOURNAL_OPERATIONS
            or row.get("record_sha256")!=sha(canon_yaml({key:value for key,value in row.items() if key!="record_sha256"}))
            or not isinstance(row.get("key"),str) or row["key"] in keys
            or not isinstance(row.get("receipt"),dict) or not isinstance(row.get("fingerprint"),str)):
            raise QSError("command_journal_chain")
        keys.add(row["key"])
        prior=sha(raw)
        rows.append({**row,"raw_sha256":prior})
    return rows


def result_request_owner(run: Path, key: str) -> Path | None:
    """Find explicit commit keys, including committed but unprojected results."""
    token=('"request_key": "'+key+'"').encode("ascii")
    found=None
    for path in sorted(child(run,"transactions").glob("*.yaml")):
        source=captured_argument(path,48 * 1024**2)
        if token not in source.raw: continue
        marker=load_yaml(source)
        if not isinstance(marker,dict) or marker.get("request_key")!=key: continue
        if found is not None: raise QSError("command_request_key_ambiguous")
        found=path
    return found


class _JournalAdmission:
    """A command whose immutable journal receipt is its entire projection."""

    def __init__(self, run: Path):
        self.run=run
        self.created=False

    def lock(self):
        return lock(self.run)

    def occurrence_lock(self, occurrence):
        # A stable directory name makes the existing coordinator lock portable.
        directory=child(self.run,"state/occurrences/"+occurrence)
        if not child(directory,"state").is_dir(): raise QSError("command_occurrence_unregistered")
        return lock(directory)

    def read_committed_locked(self):
        state,current,bank=accepted(self.run)
        return state,current,bank,command_journal(self.run)

    def authenticate_locked(self, view, request):
        state,current,_bank,_journal=view
        payload=load_yaml(CapturedInput("command request",request.payload))
        if request.run_id!=self.run.name or request.snapshot_sha256!=current["input_snapshot"]:
            raise QSError("command_authority_binding")
        if request.operation in {"evidence-intake","evidence-disposition","reconcile-effect","terminal-author-failure"}|QUESTION_COMMAND_KINDS.keys():
            authority_id=payload["authority_result_id"]
            authority=state.get("results",{}).get(authority_id)
            expected_kind=QUESTION_COMMAND_KINDS.get(
                request.operation,"REVIEW" if request.operation=="evidence-disposition" else "EVIDENCE")
            if (not isinstance(authority,dict) or authority.get("artifact_type")!=expected_kind
                or authority.get("content_hash")!=request.authority_sha256 or authority.get("agent_id")!=request.actor):
                raise QSError("command_authority_binding")
        if request.occurrence:
            pair=payload["assignment_id"]+"\0"+payload["attempt_id"]
            entry=state.get("registered",{}).get(pair)
            assignment=entry.get("assignment") if isinstance(entry,dict) else None
            if (not isinstance(assignment,dict)
                or not isinstance(assignment.get("logical_occurrence_id"),str)
                or sha(assignment["logical_occurrence_id"].encode())!=request.occurrence):
                raise QSError("command_occurrence_binding")
            if request.operation in {"claim-effect","adviser-assign"} and (request.actor!=assignment.get("agent_id")
                    or request.authority_sha256!=entry.get("assignment_sha256")):
                raise QSError("command_authority_binding")

    def lookup_committed_locked(self, view, key):
        if result_request_owner(self.run,key) is not None: raise QSError("command_request_key_conflict")
        for row in view[3]:
            if row["key"]==key:
                return Committed(row["fingerprint"],canon_yaml(row["receipt"]))
        return None

    def check_new_locked(self, view, request):
        if command_revision(self.run,view[3])!=request.expected_revision:
            raise QSError("command_request_revision_conflict")
        if view[1].get("status")=="SEALED":
            raise QSError("sealed_run_command_refused")
        if frontier_missing_scopes(view[1],view[3]):
            raise QSError("command_frontier_recovery_required")
        payload=load_yaml(CapturedInput("command request",request.payload))
        authority_id=payload.get("authority_result_id")
        if authority_id is not None:
            pending,withdrawn=intake_affected_results(self.run,view[0],view[3])
            if authority_id in view[1].get("stale_result_ids",[]) or authority_id in pending|withdrawn:
                raise QSError("command_authority_stale")

    def commit_locked(self, view, request, committed, staged):
        if staged: raise QSError("command_unexpected_projection")
        journal=view[3]
        row={"schema_version":SCHEMA,"run_id":request.run_id,"input_snapshot":request.snapshot_sha256,
             "operation":request.operation,"key":request.key,"actor":request.actor,
             "authority_sha256":request.authority_sha256,"expected_revision":request.expected_revision,
             "fingerprint":committed.fingerprint,"previous_sha256":journal[-1]["raw_sha256"] if journal else "GENESIS",
             "receipt":load_yaml(CapturedInput("command receipt",committed.receipt))}
        row["record_sha256"]=sha(canon_yaml(row))
        path=f"transactions/commands/{len(journal)+1:08d}.yaml"
        target=child(self.run,path)
        if target.exists() or target.is_symlink(): raise QSError("command_journal_target_exists")
        if "frontier" in row["receipt"]:
            state,current,_bank,_journal=view
            receipt=row["receipt"]
            rid=receipt["evidence_result_id"]
            raw=canon_yaml(row)
            anchor={"position":str(len(journal)+1),"sha256":sha(raw),
                    "affected_result_ids":receipt["affected_result_ids"]}
            after={**current,"applicability_frontier":{**current.get("applicability_frontier",{}),rid:anchor}}
            command={"path":path,"sha256":sha(raw),"bytes_b64":base64.b64encode(raw).decode("ascii"),
                     "evidence_result_id":rid}
            ids=[rid]+([receipt["review_result_id"]] if "review_result_id" in receipt else [])
            backing=[{"kind":state["results"][identifier]["artifact_type"],
                      "path":state["results"][identifier]["path"],"sha256":state["results"][identifier]["content_hash"]}
                     for identifier in ids]
            commit_current_transition(self.run,current,after,"APPLICABILITY_FRONTIER_ADVANCE",backing,
                                      frontier_command=command)
        else:
            save_yaml(target,row)
        self.created=True


def _admitted_document(run, state, result_id, kind, *, verify_cache=False):
    entry=state.get("results",{}).get(ident(result_id,"result_id"))
    if not isinstance(entry,dict) or entry.get("artifact_type")!=kind:
        raise QSError("command_result_authority")
    source=captured_argument(child(run,entry["path"]))
    if sha(source.raw)!=entry.get("content_hash"): raise QSError("command_result_drift")
    doc=load_yaml(source)
    if not isinstance(doc,dict) or doc.get("id")!=result_id:
        raise QSError("command_result_identity")
    if kind=="EVIDENCE" and verify_cache:
        cache_errors=external_cache_errors(run,doc,f"external_evidence:{result_id}")
        if cache_errors:
            raise QSError(cache_errors[0])
    return entry,doc


def _intake_receipt(run, operation, payload, view):
    state,current,_bank,journal=view
    evidence_id=payload["evidence_result_id"]
    entry,doc=_admitted_document(run,state,evidence_id,"EVIDENCE",verify_cache=True)
    scope=doc.get("command_intake_scope")
    if (not isinstance(scope,list) or not scope or any(not isinstance(key,str) for key in scope)
        or len(scope)!=len(set(scope)) or not set(scope).issubset(state["results"])):
        raise QSError("evidence_intake_scope_unverified")
    prior=[row["receipt"] for row in journal if row["receipt"].get("evidence_result_id")==evidence_id]
    base={"evidence_result_id":evidence_id,"evidence_sha256":entry["content_hash"],
          "affected_result_ids":sorted(scope),"scope":"STRUCTURAL_ONLY"}
    version=doc.get("applicability_scope_version")
    if version is not None:
        ident(version,"applicability_scope_version")
    if operation=="evidence-intake":
        if payload["authority_result_id"]!=evidence_id or prior:
            raise QSError("evidence_intake_identity_conflict")
        if version is not None:
            base["frontier"]={"scope_version":version,"cursor":str(len(journal)+1),
                              "intake_cut":payload["expected_revision"]}
        return {**base,"status":"EVIDENCE_PENDING"}
    review_entry,review=_admitted_document(run,state,payload["authority_result_id"],"REVIEW")
    if len(prior)!=1 or prior[0].get("status")!="EVIDENCE_PENDING":
        raise QSError("evidence_disposition_pending_required")
    disposition=review.get("evidence_disposition")
    if (not isinstance(disposition,str) or disposition not in {"SAME","NEW","DISJOINT","UNKNOWN"}
        or review.get("target_result_id")!=evidence_id or review.get("target_hash")!=entry["content_hash"]
        or review_entry.get("agent_id")==entry.get("agent_id") or review.get("outcome")!="PASS"
        or review.get("reviewed_affected_result_ids")!=base["affected_result_ids"]):
        raise QSError("evidence_disposition_authority")
    if version is not None:
        frontier=prior[0].get("frontier")
        proof=review.get("applicability_frontier")
        cursor=frontier.get("cursor") if isinstance(frontier,dict) else None
        cutoff=proof.get("cutoff") if isinstance(proof,dict) else None
        def position(value):
            return int(value) if isinstance(value,str) and re.fullmatch(r"[1-9][0-9]{0,7}",value) else 0
        first,last=position(cursor),position(cutoff)
        if (not isinstance(proof,dict) or set(proof)!={"scope_version","cursor","cutoff","cut_sha256","reason","consumers","lineage"}
            or not first or not first<=last==len(journal)
            or proof["scope_version"]!=version or proof["cursor"]!=cursor
            or proof["cut_sha256"]!=journal[last-1]["raw_sha256"]
            or journal[first-1]["receipt"]!=prior[0] or proof["consumers"]!=base["affected_result_ids"]
            or not isinstance(proof["reason"],str) or not proof["reason"].strip()
            or evidence_id in current.get("stale_result_ids",[])):
            raise QSError("evidence_frontier_authority")
        lineage=proof["lineage"]
        if disposition=="NEW":
            if (not isinstance(lineage,dict) or set(lineage)!={"question_or_alternative_id","decision_id","discriminator"}
                or not all(isinstance(value,str) and value.strip() for value in lineage.values())):
                raise QSError("evidence_frontier_new_lineage")
            _admitted_document(run,state,lineage["decision_id"],"DECISION")
        elif lineage!={}:
            raise QSError("evidence_frontier_unexpected_lineage")
        base["frontier"]={**frontier,"proof":proof}
    return {**base,"status":"EVIDENCE_DISPOSED","disposition":disposition,
            "review_result_id":review_entry["result_id"],"review_sha256":review_entry["content_hash"]}


EFFECT_ASSIGNMENT_FIELDS=("assignment_id","attempt_id","logical_occurrence_id","assignment_sha256")
EFFECT_RESOURCE_FIELDS=("resource_keys","sole_writer_paths")
EFFECT_TERMINAL_STATES=("COMPLETED","FAILED","CANCELLED")


def _effect_resource_scope(assignment):
    scope={}
    for field in EFFECT_RESOURCE_FIELDS:
        values=assignment.get(field,[])
        if not isinstance(values,list) or any(
            not isinstance(value,str) or not value.strip() or value!=value.strip() or "\0" in value
            for value in values
        ):
            raise QSError("effect_resource_scope:"+field)
        scope[field]=sorted(set(values))
    paths=set()
    for value in scope["sole_writer_paths"]:
        parts=[part for part in value.replace("\\","/").split("/") if part not in {"","."}]
        if not parts or ".." in parts:
            raise QSError("effect_resource_scope:sole_writer_paths")
        paths.add("/".join(parts))
    scope["sole_writer_paths"]=sorted(paths)
    return scope


def _live_effect_claims(journal):
    active={}
    for row in journal:
        receipt=row["receipt"]
        status=receipt.get("status")
        if row["operation"]=="claim-effect" and status=="EFFECT_CLAIMED":
            occurrence=receipt["logical_occurrence_id"]
            if occurrence in active or receipt.get("claim_key")!=row["key"]:
                raise QSError("effect_resource_journal_binding")
            active[occurrence]=receipt
        elif row["operation"]=="reconcile-effect" and status in {"EFFECT_RECONCILED","EFFECT_RESOURCE_RELEASED"}:
            occurrence=receipt["logical_occurrence_id"]
            holder=active.get(occurrence)
            if (holder is None or any(holder[field]!=receipt.get(field) for field in EFFECT_ASSIGNMENT_FIELDS)
                or receipt.get("claim_key",holder["claim_key"])!=holder["claim_key"]):
                raise QSError("effect_resource_journal_binding")
            if (status=="EFFECT_RESOURCE_RELEASED" or "resource_terminal_state" in receipt
                or receipt.get("effect_state")=="NOT_SENT"):
                del active[occurrence]
            else:
                held={**holder,"effect_state":receipt["effect_state"]}
                if "context_packet_sha256" in receipt:
                    held["context_packet_sha256"]=receipt["context_packet_sha256"]
                active[occurrence]=held
    return active


def _legacy_effect_resources(run,state,current,holder):
    pair=holder["assignment_id"]+"\0"+holder["attempt_id"]
    path=child(run,"intents/"+sha(pair.encode()).upper()+".yaml")
    try:
        intent=load_yaml(captured_argument(path))
        raw=base64.b64decode(intent["assignment_bytes_b64"],validate=True)
        assignment=load_yaml(CapturedInput(str(path),raw))
        registered=state["registered"][pair]
        if (sha(raw)!=holder["assignment_sha256"] or intent["assignment_sha256"]!=sha(raw)
            or intent["run_id"]!=run.name or intent["input_snapshot"]!=current["input_snapshot"]
            or intent["status"]!="ASSIGNED" or intent["assignment"]!=assignment
            or registered["assignment_sha256"]!=sha(raw) or registered["assignment"]!=assignment
            or any(assignment[key]!=holder[key] for key in EFFECT_ASSIGNMENT_FIELDS[:-1])):
            raise QSError("effect_legacy_assignment_binding")
        return _effect_resource_scope(assignment)
    except (QSError,ValueError,TypeError,KeyError,binascii.Error) as exc:
        raise QSError("effect_legacy_resource_scope:"+holder["logical_occurrence_id"]) from exc


def _require_effect_resources(run,state,current,journal,occurrence,scope):
    if not any(scope.values()):
        return
    for identifier,holder in _live_effect_claims(journal).items():
        if identifier==occurrence:
            continue
        if all(field in holder for field in EFFECT_RESOURCE_FIELDS):
            resources=_effect_resource_scope(holder)
        else:
            resources=_legacy_effect_resources(run,state,current,holder)
        if dispatch_resource_conflict(scope,resources):
            raise QSError(f"effect_resource_occupied:{identifier}:{holder['claim_key']}:{holder['effect_state']}")


def _effect_terminal_release(run,state,journal,assignment,binding,holder,proof_entry,evidence):
    if (not isinstance(evidence,dict) or holder is None
        or any(evidence.get(key)!=value or holder.get(key)!=value for key,value in binding.items())
        or evidence.get("claim_key")!=holder["claim_key"]
        or evidence.get("recipient_agent_id")!=assignment["agent_id"]
        or evidence.get("execution_state") not in EFFECT_TERMINAL_STATES
        or not isinstance(evidence.get("authoritative_receipt"),str) or not evidence["authoritative_receipt"].strip()
        or _immutable_accepted_attempt_result(run,state,proof_entry["assignment_id"],proof_entry["attempt_id"])
           !=proof_entry["result_id"]):
        raise QSError("effect_terminal_proof")
    assignment_hash=binding["assignment_sha256"]
    result_witness=_context_result_witness(run,assignment,assignment_hash,state)
    receipt_witness=holder.get("context_packet_sha256")
    if result_witness is not None and receipt_witness is not None and result_witness!=receipt_witness:
        raise QSError("effect_terminal_context_binding")
    chain=context_revision_chain(run,assignment,assignment_hash,state,journal=journal,
                                 result_witness=result_witness or receipt_witness)
    digest=sha(chain[-1].raw) if chain else None
    if evidence.get("context_packet_sha256")!=digest:
        raise QSError("effect_terminal_context_binding")
    release={"claim_key":holder["claim_key"],"resource_terminal_state":evidence["execution_state"],
             "recipient_agent_id":assignment["agent_id"],"terminal_authoritative_receipt":evidence["authoritative_receipt"]}
    if digest is not None:
        release["context_packet_sha256"]=digest
    return release


def _effect_receipt(run, operation, payload, view):
    state,current,_bank,journal=view
    assignment_id,attempt_id=payload["assignment_id"],payload["attempt_id"]
    pair=assignment_id+"\0"+attempt_id
    entry=state["registered"][pair]
    assignment=entry["assignment"]
    occurrence=assignment["logical_occurrence_id"]
    claims=[row["receipt"] for row in journal if row["receipt"].get("logical_occurrence_id")==occurrence]
    last_effect=next((row for row in reversed(claims) if row["status"] in {"EFFECT_CLAIMED","EFFECT_RECONCILED"}),None)
    base={"assignment_id":assignment_id,"attempt_id":attempt_id,"logical_occurrence_id":occurrence,
          "assignment_sha256":entry["assignment_sha256"],"scope":"STRUCTURAL_ONLY"}
    if operation=="claim-effect":
        require_current_assignment(run,state,current,assignment)
        if (entry.get("status")!="ASSIGNED" or pair in state["attempts"]
            or any(row["status"]=="AUTHOR_FAILURE_TERMINAL" and row["assignment_id"]==assignment_id
                   and row["attempt_id"]==attempt_id for row in claims)):
            raise QSError("effect_attempt_not_pending")
        registry=state["results"]
        known={result["content_hash"] for result in registry.values()}
        record=load_launch_intents(run,current["input_snapshot"])["records"].get(pair)
        chain=checked_launch_chain(run,current["input_snapshot"],pair,entry,record)
        if chain and (record!=chain[-1] or entry.get("launch_state")!=chain[-1]["state"]):
            raise QSError("launch_transition_projection_conflict")
        if (entry.get("launch_state")!="READY_TO_DISPATCH"
            or not set(assignment["dependency_hashes"]).issubset(known)
            or not set(assignment.get("dependency_result_ids") or []).issubset(registry)):
            raise QSError("effect_assignment_not_ready")
        if last_effect is not None and (last_effect.get("effect_state")!="NOT_SENT"
                or (last_effect.get("assignment_id"),last_effect.get("attempt_id"))==(assignment_id,attempt_id)):
            raise QSError("effect_occurrence_fenced")
        chain=context_revision_chain(run,assignment,entry["assignment_sha256"],state,journal=journal)
        if len(chain)>1:
            base["context_packet_sha256"]=sha(chain[-1].raw)
        resources=_effect_resource_scope(assignment)
        _require_effect_resources(run,state,current,journal,occurrence,resources)
        return {**base,**resources,"status":"EFFECT_CLAIMED","effect_state":"UNKNOWN","claim_key":payload["key"]}
    proof_entry,proof=_admitted_document(run,state,payload["authority_result_id"],"EVIDENCE")
    proof_key="effect_reconciliation" if operation=="reconcile-effect" else "terminal_author_failure"
    evidence=proof.get(proof_key)
    binding={key:base[key] for key in EFFECT_ASSIGNMENT_FIELDS}
    proof_ref={"evidence_result_id":proof_entry["result_id"],"evidence_sha256":proof_entry["content_hash"]}
    terminal=proof.get("effect_terminal") if operation=="reconcile-effect" else None
    if terminal is not None:
        if (proof_entry.get("agent_id")==assignment.get("agent_id") or proof.get("knowledge_status")!="OBSERVED"):
            raise QSError("effect_evidence_binding")
        holder=_live_effect_claims(journal).get(occurrence)
        release=_effect_terminal_release(run,state,journal,assignment,binding,holder,proof_entry,terminal)
        if proof_key not in proof:
            if holder["effect_state"]!="RECIPIENT_CONFIRMED":
                raise QSError("effect_terminal_ack_required")
            return {**base,**proof_ref,**release,"status":"EFFECT_RESOURCE_RELEASED"}
    if (not isinstance(evidence,dict) or any(evidence.get(key)!=value for key,value in binding.items())
        or proof_entry.get("agent_id")==assignment.get("agent_id") or proof.get("knowledge_status")!="OBSERVED"):
        raise QSError("effect_evidence_binding")
    if operation=="reconcile-effect":
        if (last_effect is None or last_effect["status"]!="EFFECT_CLAIMED"
            or evidence.get("claim_key")!=last_effect.get("claim_key")
            or any(last_effect.get(key)!=value for key,value in binding.items())
            or evidence.get("disposition") not in ("NOT_SENT","RECIPIENT_CONFIRMED")
            or not isinstance(evidence.get("authoritative_receipt"),str) or not evidence["authoritative_receipt"]):
            raise QSError("effect_reconciliation_proof")
        digest=last_effect.get("context_packet_sha256")
        observed=evidence.get("context_packet_sha256")
        if digest is not None or (evidence["disposition"]=="RECIPIENT_CONFIRMED" and observed is not None):
            if not isinstance(observed,str) or (digest is not None and observed!=digest):
                raise QSError("effect_reconciliation_context_binding")
            chain=context_revision_chain(run,assignment,entry["assignment_sha256"],state,journal=journal)
            if not chain or sha(chain[-1].raw)!=observed:
                raise QSError("effect_reconciliation_context_binding")
            base["context_packet_sha256"]=observed
        if terminal is not None:
            if evidence["disposition"]!="RECIPIENT_CONFIRMED":
                raise QSError("effect_terminal_ack_required")
            base.update(release)
        return {**base,**proof_ref,"status":"EFFECT_RECONCILED","effect_state":evidence["disposition"]}
    if (entry.get("status")!="ASSIGNED" or pair in state["attempts"]
        or state.get("current_attempts",{}).get(assignment_id)!=attempt_id
        or any(row["status"]=="AUTHOR_FAILURE_TERMINAL" and row["assignment_id"]==assignment_id
               and row["attempt_id"]==attempt_id for row in claims)):
        raise QSError("terminal_attempt_not_pending")
    if (entry.get("launch_state")=="DISPATCH_UNKNOWN"
        or last_effect is not None and last_effect.get("effect_state")!="NOT_SENT"):
        raise QSError("terminal_effect_unknown_or_sent")
    if (evidence.get("author_finished")!="YES" or evidence.get("external_dispatch")!="NOT_SENT"
        or not isinstance(evidence.get("reason"),str) or not evidence["reason"]):
        raise QSError("terminal_author_or_dispatch_proof")
    return {**base,**proof_ref,"status":"AUTHOR_FAILURE_TERMINAL","reason":evidence["reason"],"owner":assignment["agent_id"]}


def occurrence_effect(run: Path, occurrence: str) -> str | None:
    for row in reversed(command_journal(run)):
        receipt=row["receipt"]
        if receipt.get("logical_occurrence_id")==occurrence and receipt.get("status") in {"EFFECT_CLAIMED","EFFECT_RECONCILED"}:
            return receipt.get("effect_state")
    return None


def _question_projection(journal):
    try:
        return replay_question_receipts(row["receipt"] for row in journal)
    except LedgerError as exc:
        raise QSError("question_journal:"+str(exc)) from exc


def _question_arm(run,current):
    snapshot=load_bound_yaml(run,"input/snapshot.yaml",current["input_snapshot"],"immutable_input_snapshot_drift")
    arm=snapshot.get("question_arm")
    if arm is None:
        return None
    procedure_path="input/question-procedure.yaml"
    raw=captured_argument(child(run,procedure_path),64 * 1024).raw
    if sha(raw)!=arm["procedure_sha256"]:
        raise QSError("question_procedure_drift")
    policy=load_yaml(CapturedInput(procedure_path,raw))
    mode=snapshot["input_mode"]
    if mode=="BANK_TO_PLAN":
        external=snapshot.get("external_binding")
        original=Ref(external["original_path"],external["original_sha256"]) if external else Ref("input/bank.yaml",snapshot["bank_sha256"])
    else:
        original=Ref(snapshot["goal_path"],snapshot["goal_sha256"])
    pin=lambda field: Ref(procedure_path+"#"+field,sha(canon_yaml(policy[field])))
    procedure=Procedure(mode,original,pin("inventory_rule"),pin("evidence_cutoff"),pin("tools"),
                        pin("routing_rules"),pin("methodology"),
                        Ref("required_roles_and_phases",sha(canon_yaml([sorted(ROLES),sorted(PREP_STAGES),ROLE_KINDS]))),
                        pin("review_and_audit"))
    return Arm(procedure,arm["effort_mode"],int(arm["half_budget"]))


def _question_current_result(run,view,result_id,kind,*,historical=False):
    state,current,_bank,journal=view
    entry,document=_admitted_document(run,state,result_id,kind,verify_cache=True)
    pair=entry["assignment_id"]+"\0"+entry["attempt_id"]
    registered=state["registered"].get(pair)
    if not registered or _immutable_accepted_attempt_result(run,state,entry["assignment_id"],entry["attempt_id"])!=result_id:
        raise QSError("question_result_attempt_missing")
    if entry.get("workflow_status")!="ACCEPTED" and document.get("workflow_status")!="ACCEPTED":
        raise QSError("question_result_not_accepted")
    if not historical:
        require_current_assignment(run,state,current,registered["assignment"])
    return Ref(result_id,entry["content_hash"]),document,registered


def _question_assignment(entry):
    assignment=entry["assignment"]
    return Assignment(Ref(assignment["assignment_id"],entry["assignment_sha256"]),
                      assignment["attempt_id"],assignment["agent_id"],assignment["role_id"])


def _question_result_context(run,view,entry,document):
    digest=document.get("context_packet_sha256")
    if not isinstance(digest,str):
        raise QSError("question_result_context_binding")
    return resolve_context_revision(run,entry["assignment"],entry["assignment_sha256"],view[0],digest).packet


def _question_review(run,view,review,review_entry,kind,*,retirement=False):
    reference,document,entry=_question_current_result(
        run,view,review.get("target_result_id"),kind,historical=retirement,
    )
    if (review.get("outcome")!="PASS" or review.get("target_hash")!=reference.sha256
        or entry["assignment"]["agent_id"]==review_entry["assignment"]["agent_id"]):
        raise QSError("question_review_binding")
    blind_id=review.get("blind_input_result_id",review.get("blind_result_id"))
    blind,blind_doc,blind_entry=_question_current_result(run,view,blind_id,"FRAMING")
    if _question_result_context(run,view,blind_entry,blind_doc).get("blind")!="true":
        raise QSError("question_review_blind_context")
    errors=[]
    _review_contract(review["id"],view[0]["results"],{review["id"]:review,reference.id:document,blind.id:blind_doc},{},errors)
    if errors:
        raise QSError("question_review_contract:"+"|".join(errors))
    packet=_question_result_context(run,view,review_entry,review)
    if packet.get("blind")!="false" or _question_context_exposes(packet,reference)==retirement:
        raise QSError("question_review_target_exposure")
    return reference,document,entry


def _question_context_exposes(packet,reference):
    return any(row.get("source",{}).get("result_id")==reference.id
               and row["source"].get("sha256")==reference.sha256
               and row.get("projection",{}).get("view",{}).get("kind")=="FULL_LINKED_EXPANSION"
               for row in packet.get("accepted_dependencies",[]))


def _question_framing_ref(run,view):
    state,current,_bank,_journal=view
    snapshot=load_bound_yaml(run,"input/snapshot.yaml",current["input_snapshot"],"immutable_input_snapshot_drift")
    if snapshot.get("bank_entry_kind")=="APPROVED_RUN_IMPORT":
        _,imported=_approved_framing_import(run,snapshot)
        return Ref(imported["source_vector_result_id"],imported["vector_sha256"])
    approval=load_yaml(captured_argument(child(run,"input/vector-approval.yaml")))
    ref,document,entry=_question_current_result(run,view,approval.get("vector_result_id"),"OPERATOR")
    if approval.get("vector_sha256")!=ref.sha256 or entry["assignment"]["role_id"]!="OP-VECTOR" or not document.get("G") or not document.get("NOT_G"):
        raise QSError("question_approved_framing_required")
    return ref


def _question_inventory_ids(bank):
    return {question["id"] for question in bank["questions"]}


def _question_registration(run,view,projection,arm,review_ref,review,review_entry):
    source,document,_=_question_review(run,view,review,review_entry,"EVIDENCE")
    inventory=document.get("question_inventory")
    derived=document.get("derived_question")
    ledger=projection.ledger
    if isinstance(inventory,dict) and derived is None:
        if ledger.questions or ledger.aliases:
            raise QSError("question_inventory_already_bound")
        framing=_question_framing_ref(run,view)
        snapshot=load_bound_yaml(run,"input/snapshot.yaml",view[1]["input_snapshot"],"immutable_input_snapshot_drift")
        expected_source=snapshot.get("bank_sha256",framing.sha256)
        if inventory.get("source_sha256")!=expected_source or inventory.get("original_sha256")!=arm.procedure.original.sha256:
            raise QSError("question_inventory_source")
        questions=inventory.get("questions")
        aliases=inventory.get("aliases",[])
        if not isinstance(questions,list) or not questions or not isinstance(aliases,list):
            raise QSError("question_inventory_contract")
        events=[(_question_row(row,arm.procedure.original),None) for row in questions]
        for row in aliases:
            if not isinstance(row,dict) or set(row)!={"id","canonical_id"}:
                raise QSError("question_alias_contract")
            events.append((Alias(ident(row["id"],"question_id"),ident(row["canonical_id"],"canonical_question_id"),
                                 arm.procedure.original,review_ref),None))
        if view[2]["questions"] and {event.id for event,_ in events}!=_question_inventory_ids(view[2]):
            raise QSError("question_inventory_bank_mismatch")
        for row in questions:
            if row.get("exclusion"):
                events.append((Gap(row["id"],"EXCLUDED: "+row["exclusion"],review_ref),None))
        return events
    if not isinstance(derived,dict) or inventory is not None:
        raise QSError("question_registration_authority")
    if not isinstance(derived.get("text"),str) or not derived["text"].strip():
        raise QSError("question_derived_text")
    decision,_,_=_question_current_result(run,view,derived.get("decision"),"DECISION")
    parent=ident(derived.get("parent"),"parent_question")
    if parent not in document.get("related_ids",[]) or decision.id not in document.get("related_ids",[]):
        raise QSError("question_derived_lineage")
    return [(_question_row(derived,source,parent),None)]


def _question_row(row,source,parent=None):
    fields={"id","decision","scope","evidence_route","priority","mandatory"}
    optional={"exclusion"} if parent is None else {"parent","text"}
    if (not isinstance(row,dict) or not fields<=row.keys()<=fields|optional
        or row["mandatory"] not in {"YES","NO"} or not isinstance(row["priority"],list)
        or len(row["priority"])!=3 or any(not isinstance(value,str) or not re.fullmatch(r"-?[0-9]{1,6}",value)
                                         for value in row["priority"])
        or "exclusion" in row and (not isinstance(row["exclusion"],str) or not row["exclusion"].strip())):
        raise QSError("question_inventory_row")
    return Question(*(ident(row[key],key) for key in ("id","decision","scope","evidence_route")),
                    source,tuple(map(int,row["priority"])),row["mandatory"]=="YES",parent)


def _question_attempt(ledger,proof):
    if not isinstance(proof,dict) or not isinstance(proof.get("operation_id"),str):
        raise QSError("question_observation_contract")
    attempt=ledger.attempts.get(proof["operation_id"])
    if not attempt or proof.get("question_id")!=attempt.question_id or proof.get("claim_key")!=attempt.operation.fence:
        raise QSError("question_observation_binding")
    return attempt


def _question_claim_events(run,view,projection,arm,payload,receipt):
    state,current,_bank,_journal=view
    entry=state["registered"][payload["assignment_id"]+"\0"+payload["attempt_id"]]
    assignment=entry["assignment"]
    lead,support=_question_work_ids(assignment,projection.ledger)
    _question_current_sources(run,view,projection.ledger,lead|support)
    if lead and assignment["artifact_type"] not in {"PRIMARY","ANSWER"}:
        raise QSError("question_primary_owner_artifact_required")
    if assignment.get("adviser"):
        _adviser_current_issuance(run,view,arm,projection,entry)
        if assignment["adviser"]["work_kind"]=="SUBSTANTIVE" and not support:
            raise QSError("adviser_question_operation_unbound")
        if assignment["adviser"]["work_kind"]=="ROUTING_ONLY" and "question_work" in assignment:
            raise QSError("adviser_routing_only_question_work")
    if support and not support<=_question_confirmed_work(projection.ledger):
        raise QSError("question_support_requires_started")
    if current.get("approved_map_sha256") and lead:
        approved=load_bound_yaml(run,"state/approved-map.yaml",current["approved_map_sha256"],"approved_map_drift")
        routes=approved.get("question_routes",{})
        for identifier in assignment.get("assigned_ids",{}).get("questions",[]):
            if (projection.ledger._canonical(identifier) in lead and
                routes.get(identifier,{}).get("primary_assignment_id")!=assignment["assignment_id"]):
                raise QSError("question_claim_approved_primary_owner")
    proof=Ref(payload["key"],sha(canon_yaml(receipt)))
    events=[]
    for question in projection.ledger.eligible():
        if question.id in lead:
            op=Operation(payload["key"]+":"+question.id,payload["key"],_question_assignment(entry))
            events.extend(((Admission(question.id,op),None),(Transition(op,"RESERVED",proof),None)))
    if len(events)!=2*len(lead):
        raise QSError("question_claim_not_eligible")
    return events


def _question_requires_work(assignment):
    return ("question_work" in assignment
            or assignment["artifact_type"] in {"PRIMARY","ANSWER","EXPERIMENT"}
            and bool(assignment.get("assigned_ids",{}).get("questions")))


def _question_work_ids(assignment,ledger):
    work=assignment.get("question_work",{"lead":[],"support":[]})
    if not isinstance(work,dict) or set(work)!={"lead","support"}:
        raise QSError("question_work_contract")
    addressed=set(assignment.get("assigned_ids",{}).get("questions",[]))
    if any(not isinstance(ids,list) or any(not isinstance(qid,str) or qid not in addressed for qid in ids)
           or len(set(ids))!=len(ids) for ids in work.values()):
        raise QSError("question_work_scope")
    lead,support=({ledger._canonical(qid) for qid in work[key]} for key in ("lead","support"))
    if lead & support or any(len(ids)!=len(work[key]) for key,ids in zip(("lead","support"),(lead,support))):
        raise QSError("question_work_duplicate")
    if _question_requires_work(assignment) and (
        not (lead or support) or not {ledger._canonical(qid) for qid in addressed}<=lead|support
    ):
        raise QSError("question_substantive_context_unreserved")
    return lead,support


def _question_current_sources(run,view,ledger,identifiers):
    for identifier in identifiers:
        question=ledger.questions.get(identifier)
        if question and question.parent is not None:
            source,_,_=_question_current_result(run,view,question.source.id,"EVIDENCE")
            if source!=question.source:
                raise QSError("question_derived_source_drift")


def _question_confirmed_work(ledger,owner=None):
    return {attempt.question_id for attempt in ledger.attempts.values()
            if attempt.status in {"CONFIRMED_SUBSTANTIVE","CLOSED_AFTER_WORK"}
            and (owner is None or attempt.operation.assignment==owner)}


def _question_effect_events(run,view,projection,payload,receipt):
    state,_current,_bank,journal=view
    entry=state["registered"][payload["assignment_id"]+"\0"+payload["attempt_id"]]
    actor=_question_assignment(entry)
    claims=[row["receipt"] for row in journal if row["receipt"].get("status")=="EFFECT_CLAIMED"
            and row["receipt"].get("assignment_id")==payload["assignment_id"]
            and row["receipt"].get("attempt_id")==payload["attempt_id"]]
    if not claims:
        return []
    key=claims[-1]["claim_key"]
    attempts=[attempt for attempt in projection.ledger.attempts.values() if attempt.operation.assignment==actor and attempt.operation.fence==key]
    if not attempts:
        return []
    ref,document,_=_question_current_result(run,view,payload["authority_result_id"],"EVIDENCE")
    observation=document["effect_reconciliation"]
    if receipt["effect_state"]=="NOT_SENT":
        if observation.get("question_starts"):
            raise QSError("question_not_sent_with_substantive_start")
        return [(Transition(attempt.operation,"CANCELLED_BEFORE_START",ref),None) for attempt in attempts]
    rows=observation.get("question_starts")
    if (not isinstance(rows,list)
        or any(not isinstance(row,dict) or set(row)!={"question_id","substantive_action"} or not isinstance(row["question_id"],str)
               or not isinstance(row["substantive_action"],str)
               or not row["substantive_action"].strip() for row in rows)
        or len({row["question_id"] for row in rows})!=len(rows)
        or not {row["question_id"] for row in rows}<={attempt.question_id for attempt in attempts}
        or observation.get("recipient_agent_id")!=actor.actor):
        raise QSError("question_substantive_start_proof")
    started={row["question_id"] for row in rows}
    return [(Transition(attempt.operation,"CONFIRMED_SUBSTANTIVE" if attempt.question_id in started else "UNKNOWN_START",ref),None)
            for attempt in attempts if attempt.status=="RESERVED" or attempt.question_id in started]


def _question_observe(view,projection,ref,document,entry):
    proof=document.get("question_observation")
    if not isinstance(proof,dict) or document.get("knowledge_status")!="OBSERVED":
        raise QSError("question_observation_required")
    state=proof.get("state")
    if state=="HIDDEN_WORK":
        subject=view[0]["registered"].get(str(proof.get("assignment_id"))+"\0"+str(proof.get("attempt_id")))
        if (not subject or subject["assignment_sha256"]!=proof.get("assignment_sha256")
            or subject["assignment"]["agent_id"]==entry["assignment"]["agent_id"]
            or not isinstance(proof.get("scope"),str) or not proof["scope"].strip()
            or not isinstance(proof.get("substantive_action"),str) or not proof["substantive_action"].strip()):
            raise QSError("question_hidden_work_proof")
        return HiddenWork(proof["scope"],ref)
    attempt=_question_attempt(projection.ledger,proof)
    op=attempt.operation
    if (entry["assignment"]["agent_id"]==op.assignment.actor or proof.get("recipient_agent_id")!=op.assignment.actor
        or not isinstance(proof.get("authoritative_receipt"),str) or not proof["authoritative_receipt"].strip()
        or not isinstance(proof.get("observation"),str) or not proof["observation"].strip()
        or state not in {"UNKNOWN_START","ABORTED_AFTER_START","CONFIRMED_SUBSTANTIVE","CANCELLED_BEFORE_START"}):
        raise QSError("question_observation_independence")
    if state in {"CONFIRMED_SUBSTANTIVE","CANCELLED_BEFORE_START"}:
        assignment=op.assignment
        accepted_ack=any(row["operation"]=="reconcile-effect" and row["receipt"].get("effect_state")=="RECIPIENT_CONFIRMED"
                         and row["receipt"].get("assignment_id")==assignment.assignment.id
                         and row["receipt"].get("attempt_id")==assignment.attempt
                         and row["receipt"].get("assignment_sha256")==assignment.assignment.sha256 for row in view[3])
        if not accepted_ack:
            raise QSError("question_late_observation_effect_unbound")
    return Transition(op,state,ref)


def _question_close(run,view,projection,arm,ref,document,entry,proof):
    attempt=_question_attempt(projection.ledger,proof)
    kind=view[0]["results"].get(document.get("target_result_id"),{}).get("artifact_type")
    if kind not in {"ANSWER","PRIMARY"}:
        raise QSError("question_close_answer_required")
    answer,answer_doc,answer_entry=_question_review(run,view,document,entry,kind)
    q=projection.ledger.questions[attempt.question_id]
    answered=set(answer_doc.get("addressed_q_ids",answer_doc.get("original_q_ids",[])))
    answered.update(row["q_id"] for row in answer_doc.get("per_question_outcomes",[])
                    if isinstance(row,dict) and isinstance(row.get("q_id"),str))
    if (proof.get("methodology_sha256")!=arm.procedure.methodology.sha256
        or proof.get("review_and_audit_sha256")!=arm.procedure.review_and_audit.sha256
        or proof.get("evidence_route")!=q.evidence_route or proof.get("decision")!=q.decision
        or q.id not in {projection.ledger._canonical(identifier) for identifier in answered}
        or _question_assignment(answer_entry)!=attempt.operation.assignment
        or not isinstance(proof.get("depth_observation"),str) or not proof["depth_observation"].strip()
        or answer.sha256 not in entry["assignment"]["dependency_hashes"]):
        raise QSError("question_independent_close_proof")
    _question_result_context(run,view,answer_entry,answer_doc)
    return Transition(attempt.operation,"CLOSED_AFTER_WORK",ref,proof.get("outcome"),proof.get("remaining_gap"))


def _question_current_closes(run,view,ledger):
    for result_id in sorted({row.proof.id for row in ledger.closed.values()}):
        ref,review,entry=_question_current_result(run,view,result_id,"REVIEW")
        kind=view[0]["results"].get(review.get("target_result_id"),{}).get("artifact_type")
        if kind not in {"ANSWER","PRIMARY"}:
            raise QSError("question_close_answer_required")
        _question_review(run,view,review,entry,kind)
        if any(row.proof!=ref for row in ledger.closed.values() if row.proof.id==result_id):
            raise QSError("question_close_authority_drift")


def _question_current_gap(run,view,gap):
    ref,review,entry=_question_current_result(run,view,gap.proof.id,"REVIEW")
    if ref!=gap.proof:
        raise QSError("question_gap_authority_drift")
    _question_review(run,view,review,entry,"EVIDENCE")
    return ref


def _question_gap_events(run,view,projection,ref,document,entry):
    _,target,_=_question_review(run,view,document,entry,"EVIDENCE")
    gaps=target.get("question_gaps")
    ledger=projection.ledger
    if (not isinstance(gaps,dict) or not gaps or ledger.active or
        any(not isinstance(reason,str) or not reason.strip() or reason.startswith("EXCLUDED: ")
            for reason in gaps.values())):
        raise QSError("question_terminal_gap_proof")
    cancelled={attempt.question_id for attempt in ledger.attempts.values() if attempt.status=="CANCELLED_BEFORE_START"}
    if len(ledger.started)<ledger.capacity and not set(gaps)<=cancelled:
        raise QSError("question_gap_before_capacity")
    return [(Gap(qid,reason,ref),None) for qid,reason in sorted(gaps.items())]


def _adviser_recipient(run,view,assignment):
    state,current,_bank,_journal=view
    if not ordinary_adviser_assignment(assignment):
        raise QSError("adviser_assignment_scope")
    recipient_id=ident(assignment["adviser"]["recipient_assignment_id"],"advice_recipient")
    attempt_id=state["current_attempts"].get(recipient_id)
    recipient=state["registered"].get(recipient_id+"\0"+str(attempt_id))
    if not recipient or recipient["assignment"]["agent_id"]==assignment["agent_id"]:
        raise QSError("adviser_independent_recipient")
    recipient_assignment=recipient["assignment"]
    require_current_assignment(run,state,current,recipient_assignment)
    if (recipient_assignment["lifecycle_scope"]!=assignment["lifecycle_scope"]
        or recipient_assignment["artifact_type"]=="FRAMING"
        or recipient_assignment["role_id"] not in ROLES):
        raise QSError("adviser_recipient_lifecycle")
    return recipient


def _adviser_packet(run,view,arm,entry):
    state,current,bank,journal=view
    assignment=entry["assignment"]
    configuration=assignment.get("adviser")
    lifecycle=assignment.get("lifecycle_scope")
    require_current_assignment(run,state,current,assignment)
    recipient=_adviser_recipient(run,view,assignment)
    recipient_assignment=recipient["assignment"]
    recipient_id=recipient_assignment["assignment_id"]
    attempt_id=recipient_assignment["attempt_id"]
    framing=_question_framing_ref(run,view)
    address=ident(configuration["address"],"advice_address")
    exposed=assignment.get("assigned_ids",{})
    recipient_ids=recipient_assignment.get("assigned_ids",{})
    if any(not set(exposed.get(kind,[]))<=set(recipient_ids.get(kind,[])) for kind in ("questions","requirements","decisions","evidence")):
        raise QSError("adviser_exposure_expansion")
    if lifecycle=="PREP":
        stage=recipient_assignment.get("prep_stage")
        if (assignment.get("prep_stage") not in {"PREP-3_EXPAND_NORMALIZE","PREP-4_ADMIT"}
            or stage not in {"PREP-3_EXPAND_NORMALIZE","PREP-4_ADMIT"}
            or stage!=address or exposed.get("requirements")):
            raise QSError("adviser_prep_boundary")
        ledger=_question_projection(journal).ledger
        for identifier in exposed.get("questions",[]):
            question=ledger.questions.get(ledger._canonical(identifier)) if ledger else None
            if not question or question.scope!=address or question.id in ledger.gaps:
                raise QSError("adviser_prep_question_scope")
        allowed=set(recipient_assignment.get("dependency_result_ids",[]))
        if not (set(exposed.get("evidence",[]))|set(exposed.get("decisions",[])))<=allowed:
            raise QSError("adviser_prep_unlinked_material")
        selected={"framing":framing.id,"framing_sha256":framing.sha256,"stage":stage}
        edges=[]
        addressed=allowed
    else:
        if address not in set(exposed.get("questions",[]))|set(exposed.get("requirements",[])):
            raise QSError("adviser_graph_address_unassigned")
        if any(identifier not in set(exposed.get("evidence",[]))|set(exposed.get("decisions",[]))
               for identifier in assignment.get("dependency_result_ids",[])):
            raise QSError("adviser_dependency_exposure")
        selected_view=_graph_query_projection(run,assignment,state,bank,
                                              "COVERAGE" if address in exposed.get("requirements",[]) else "KNOWLEDGE",address,current=current)
        if (selected_view["blockers"] or selected_view.get("branch_holds")
            or selected_view.get("actionable",{}).get("status") in {"HELD","UNKNOWN"}):
            raise QSError("adviser_graph_inapplicable")
        selected=selected_view["architecture_ref"]
        edges=[{key:value for key,value in edge.items() if value is not None} for edge in selected_view["edges"]]
        addressed=set(selected_view["artifacts"])|{endpoint for edge in edges for endpoint in (edge["from_id"],edge["to_id"])}
    refs={}
    packet_refs={}
    for field,kind in (("evidence","EVIDENCE"),("decisions","DECISION")):
        refs[field]=[]
        packet_refs[field]=[]
        for identifier in sorted(exposed.get(field,[])):
            if identifier not in addressed:
                raise QSError("adviser_material_unlinked")
            reference,document,_=_question_current_result(run,view,identifier,kind)
            refs[field].append(reference)
            row={"id":reference.id,"sha256":reference.sha256}
            if configuration["work_kind"]=="SUBSTANTIVE":
                fields=("observation","scope","procedure","source_refs") if kind=="EVIDENCE" else ("grounds","scope","alternatives")
                row["operative_fields"]={key:document[key] for key in fields if key in document}
            packet_refs[field].append(row)
    graph=Ref(address,sha(canon_yaml([selected,edges])))
    evidence=tuple(refs["evidence"]); decisions=tuple(refs["decisions"])
    packet={"schema_version":SCHEMA,"packet_type":"ADVISER_CONTEXT","run_id":run.name,
            "input_snapshot":current["input_snapshot"],"assignment_id":assignment["assignment_id"],
            "attempt_id":assignment["attempt_id"],"role_id":assignment["role_id"],"artifact_type":"EVIDENCE",
            **core_metadata(assignment),"lifecycle_scope":lifecycle,"blind":"false","scope":address,
            **({"output_path":assignment["output_path"]} if lifecycle=="CORE" else {}),
            "work_kind":configuration["work_kind"],
            "recipient_assignment_id":recipient_id,"recipient_attempt_id":attempt_id,
            "original":{"id":arm.procedure.original.id,"sha256":arm.procedure.original.sha256},
            "framing":{"id":framing.id,"sha256":framing.sha256},
            "selected_graph":selected,"scoped_edges":edges,**packet_refs}
    exposure=Ref("adviser-readset",sha(canon_yaml([exposed,packet["work_kind"],packet["recipient_assignment_id"],attempt_id])))
    context=AdviceContext(lifecycle,arm.procedure.input_mode,arm.effort_mode,arm.procedure.original,
                          framing,graph,exposure,address,address,evidence,decisions,
                          Ref("context",sha(canon_yaml(packet))))
    return packet,context,recipient


def _adviser_current_issuance(run,view,arm,projection,entry):
    actor=_question_assignment(entry)
    admitted=projection.advice.assignments.get(actor) if projection.advice else None
    if admitted is None:
        raise QSError("adviser_assignment_receipt_required")
    packet,context,_recipient=_adviser_packet(run,view,arm,entry)
    if admitted.context!=context:
        raise QSError("adviser_context_unissued_or_stale")
    return packet


def _adviser_event(run,operation,view,projection,arm,ref,document,entry):
    state,_current,_bank,_journal=view
    if operation=="adviser-contribute":
        actor=_question_assignment(entry)
        packet,context,recipient=_adviser_packet(run,view,arm,entry)
        proof=document.get("advice_contribution")
        configuration=entry["assignment"]["adviser"]
        dispatch=next((row["receipt"] for row in reversed(view[3])
                       if row["operation"]=="reconcile-effect"
                       and row["receipt"].get("status")=="EFFECT_RECONCILED"
                       and row["receipt"].get("assignment_sha256")==actor.assignment.sha256
                       and row["receipt"].get("attempt_id")==actor.attempt),None)
        if not dispatch or dispatch["effect_state"]!="RECIPIENT_CONFIRMED":
            raise QSError("adviser_contribution_dispatch_unconfirmed")
        effect_ref,effect_doc,_=_question_current_result(run,view,dispatch["evidence_result_id"],"EVIDENCE")
        if (effect_ref.sha256!=dispatch["evidence_sha256"]
            or effect_doc["effect_reconciliation"].get("recipient_agent_id")!=actor.actor):
            raise QSError("adviser_contribution_dispatch_binding")
        if (not isinstance(proof,dict) or proof.get("material") not in {"YES","NO"}
            or document.get("context_packet_sha256")!=context.packet.sha256
            or not isinstance(proof.get("rationale"),str) or not proof["rationale"].strip()):
            raise QSError("adviser_contribution_proof")
        stored=resolve_context_revision(run,entry["assignment"],entry["assignment_sha256"],state,context.packet.sha256)
        if stored.raw!=canon_yaml(packet):
            raise QSError("adviser_context_stale")
        work_kind=configuration["work_kind"]
        op=None
        if work_kind=="SUBSTANTIVE":
            attempt=_question_attempt(projection.ledger,proof)
            lead,support=_question_work_ids(entry["assignment"],projection.ledger)
            if (attempt.question_id not in lead|support
                or attempt.operation.assignment!=actor and attempt.question_id not in support):
                raise QSError("adviser_question_operation_unbound")
            op=attempt.operation
        elif "operation_id" in proof:
            raise QSError("adviser_routing_work_cannot_claim_operation")
        return Contribution(actor,context.packet,ref,proof.get("kind"),proof["material"]=="YES",work_kind,op),context
    return _adviser_disposition(run,view,projection,arm,ref,document,entry),None


def _adviser_disposition(run,view,projection,arm,ref,document,entry):
    proof=document.get("advice_disposition")
    decision=None
    if proof is None:
        _,decision_doc,decision=_question_review(run,view,document,entry,"EVIDENCE")
        proof=_adviser_decision_fields(decision_doc)
    if not isinstance(proof,dict) or not isinstance(proof.get("rationale"),str):
        raise QSError("adviser_disposition_proof")
    retirement=proof.get("status")=="REJECTED" and "retired_context_sha256" in proof
    if decision is None:
        target,target_doc,source=_question_review(run,view,document,entry,"EVIDENCE",retirement=retirement)
    else:
        target,target_doc,source=_question_current_result(run,view,proof["target_result_id"],"EVIDENCE",historical=retirement)
    contribution=projection.advice.contributions.get(target.id) if projection.advice else None
    if contribution is None or contribution.result!=target:
        raise QSError("adviser_disposition_contribution_binding")
    if retirement:
        _question_result_context(run,view,source,target_doc)
        if proof["retired_context_sha256"]!=contribution.context_packet.sha256:
            raise QSError("adviser_retired_context_binding")
        recipient=_adviser_recipient(run,view,source["assignment"])
    else:
        _packet,context,recipient=_adviser_packet(run,view,arm,source)
        if target_doc.get("context_packet_sha256")!=context.packet.sha256:
            raise QSError("adviser_disposition_context_stale")
    recipient_assignment=recipient["assignment"]
    author=(decision or entry)["assignment"]
    if any(author.get(key)!=recipient_assignment.get(key) for key in
           ("role_id","agent_id","lifecycle_scope","prep_stage","prep_admission_sha256")):
        raise QSError("adviser_disposition_recipient_binding")
    if decision is None:
        authority=_question_assignment(entry)
    else:
        review_author=entry["assignment"]
        if (not re.fullmatch(r"P-(0[1-9]|10)",review_author["role_id"])
            or review_author["agent_id"] in {author["agent_id"],source["assignment"]["agent_id"]}):
            raise QSError("adviser_recipient_decision_binding")
        _adviser_recipient_decision(run,view,decision,decision_doc,recipient,contribution,retirement)
        review_packet=_question_result_context(run,view,entry,document)
        if _question_context_exposes(review_packet,target)==retirement:
            raise QSError("adviser_recipient_review_exposure")
        authority=_question_assignment(recipient)
    return Disposition(target,authority,proof.get("status"),proof["rationale"],ref)


def _adviser_decision_fields(document):
    proof=document.get("advice_disposition")
    required={"target_result_id","target_sha256","context_sha256","recipient_assignment_id",
              "recipient_attempt_id","status","rationale"}
    if (not isinstance(proof,dict) or not required<=proof.keys()<=required|{"retired_context_sha256"}
        or proof["status"] not in {"ACCEPTED","REJECTED","QUALIFIED","PENDING"}
        or not isinstance(proof["rationale"],str) or not proof["rationale"].strip()
        or "retired_context_sha256" in proof and proof["status"]!="REJECTED"):
        raise QSError("adviser_recipient_decision_contract")
    return proof


def _adviser_recipient_decision(run,view,decision,document,recipient,contribution,retirement):
    proof=_adviser_decision_fields(document)
    author=decision["assignment"]
    assigned=recipient["assignment"]
    target=contribution.result
    if (any(author.get(key)!=assigned.get(key) for key in
            ("role_id","agent_id","lifecycle_scope","prep_stage","prep_admission_sha256"))
        or proof["recipient_assignment_id"]!=assigned["assignment_id"]
        or proof["recipient_attempt_id"]!=assigned["attempt_id"]
        or proof["target_result_id"]!=target.id or proof["target_sha256"]!=target.sha256
        or proof["context_sha256"]!=contribution.context_packet.sha256):
        raise QSError("adviser_recipient_decision_binding")
    packet=_question_result_context(run,view,decision,document)
    if packet.get("blind")!="false" or _question_context_exposes(packet,target)==retirement:
        raise QSError("adviser_recipient_decision_exposure")


def _question_command_receipt(run,operation,payload,view):
    state,current,_bank,journal=view
    if operation=="terminal-author-failure":
        return _effect_receipt(run,operation,payload,view)
    arm=_question_arm(run,current)
    if arm is None:
        if operation in QUESTION_COMMANDS:
            raise QSError("question_arm_required")
        return _effect_receipt(run,operation,payload,view)
    projection=_question_projection(journal)
    events=[]
    if projection.ledger is None:
        projection.apply(arm)
        events.append((arm,None))
    if operation in {"claim-effect","reconcile-effect"}:
        receipt=_effect_receipt(run,operation,payload,view)
        if operation=="claim-effect":
            events.extend(_question_claim_events(run,view,projection,arm,payload,receipt))
        elif receipt["status"]!="EFFECT_RESOURCE_RELEASED":
            events.extend(_question_effect_events(run,view,projection,payload,receipt))
    elif operation=="adviser-assign":
        entry=state["registered"][payload["assignment_id"]+"\0"+payload["attempt_id"]]
        _,context,recipient=_adviser_packet(run,view,arm,entry)
        events.append((AdviceAssignment(_question_assignment(entry),context,recipient["assignment"]["role_id"]),context))
        receipt={"status":"ADVISER_ASSIGNED","assignment_id":payload["assignment_id"],"attempt_id":payload["attempt_id"]}
    else:
        ref,document,entry=_question_current_result(run,view,payload["authority_result_id"],QUESTION_COMMAND_KINDS[operation])
        receipt={"status":operation.replace("-","_").upper(),"authority_result_id":ref.id,"authority_sha256":ref.sha256}
        if operation=="question-register":
            events.extend(_question_registration(run,view,projection,arm,ref,document,entry))
        elif operation=="question-observe":
            events.append((_question_observe(view,projection,ref,document,entry),None))
        elif operation=="question-close":
            proofs=document.get("question_close")
            if isinstance(proofs,dict):
                proofs=[proofs]
            if not isinstance(proofs,list) or not proofs:
                raise QSError("question_close_proof_required")
            events.extend((_question_close(run,view,projection,arm,ref,document,entry,proof),None) for proof in proofs)
        elif operation=="question-gaps":
            events.extend(_question_gap_events(run,view,projection,ref,document,entry))
        else:
            event,context=_adviser_event(run,operation,view,projection,arm,ref,document,entry)
            events.append((event,context))
            if operation=="adviser-dispose" and "advice_disposition" not in document:
                receipt.update(recipient_assignment_id=event.authority.assignment.id,
                               recipient_assignment_sha256=event.authority.assignment.sha256,
                               recipient_attempt_id=event.authority.attempt,
                               decision_result_id=document["target_result_id"],decision_sha256=document["target_hash"])
    if not events:
        return receipt
    try:
        return prepare_question_receipt((row["receipt"] for row in journal),
                                        {**receipt,"scope":"STRUCTURAL_ONLY"},events)
    except LedgerError as exc:
        raise QSError("question_command:"+str(exc)) from exc


def _question_context(run,view,arm,entry,expanded):
    state,_current,_bank,journal=view
    assignment=entry["assignment"]
    if assignment["artifact_type"]=="FRAMING":
        if assignment.get("adviser") or assignment.get("question_work"):
            raise QSError("question_context_blind_boundary")
        return None
    projection=_question_projection(journal)
    if assignment.get("adviser"):
        if expanded:
            raise QSError("adviser_context_no_expansion")
        packet=_adviser_current_issuance(run,view,arm,projection,entry)
        actor=_question_assignment(entry)
        effect=next((row["receipt"] for row in reversed(journal) if row["receipt"].get("status") in {"EFFECT_CLAIMED","EFFECT_RECONCILED"}
                     and row["receipt"].get("assignment_sha256")==actor.assignment.sha256
                     and row["receipt"].get("attempt_id")==actor.attempt),None)
        if not effect or effect.get("effect_state") not in {"UNKNOWN","RECIPIENT_CONFIRMED"}:
            raise QSError("adviser_context_dispatch_claim_required")
        if assignment["adviser"]["work_kind"]=="SUBSTANTIVE":
            identifiers=_question_work_guard(assignment,entry,projection)
            _question_current_sources(run,view,projection.ledger,identifiers)
        elif assignment.get("question_work"):
            raise QSError("adviser_routing_only_question_work")
        return packet
    if _question_requires_work(assignment):
        identifiers=_question_work_guard(assignment,entry,projection)
        _question_current_sources(run,view,projection.ledger,identifiers)
    return None


def _question_work_guard(assignment,entry,projection):
    ledger=projection.ledger
    if ledger is None:
        raise QSError("question_inventory_required")
    lead,support=_question_work_ids(assignment,ledger)
    actor=_question_assignment(entry)
    reserved={attempt.question_id for attempt in ledger.attempts.values()
              if attempt.operation.assignment==actor
              and attempt.status in {"RESERVED","UNKNOWN_START","CONFIRMED_SUBSTANTIVE","CLOSED_AFTER_WORK"}}
    if not (lead or support) or not lead<=reserved or not support<=_question_confirmed_work(ledger):
        raise QSError("question_substantive_context_unreserved")
    return lead|support


def _adviser_dependency_guard(run,view,arm,assignment,expanded):
    adviser_ids={rid for rid in expanded if view[0]["results"].get(rid,{}).get("role_id")==ADVISER_ROLE}
    reviewable_plans=set()
    expanded=set(expanded)
    advice=_question_projection(view[3]).advice
    for result_id in sorted(adviser_ids):
        contribution=advice.contributions.get(result_id) if advice else None
        if contribution is None or not _assignment_links(assignment,contribution.result):
            raise QSError("adviser_dependency_not_admitted")
        assigned=advice.assignments[contribution.assignment]
        disposition=advice.dispositions.get(result_id)
        plan_grant=(_adviser_plan_read_grant(run,view,assignment,expanded,assigned,contribution,disposition)
                    if disposition else None)
        if plan_grant is not None:
            reviewable_plans.update(plan_grant)
            continue
        ref,document,source=_question_current_result(run,view,result_id,"EVIDENCE")
        if contribution.result!=ref:
            raise QSError("adviser_dependency_not_admitted")
        _packet,live,recipient=_adviser_packet(run,view,arm,source)
        if live.packet!=contribution.context_packet or document.get("context_packet_sha256")!=live.packet.sha256:
            raise QSError("adviser_dependency_context_stale")
        if _adviser_same_recipient(assignment,recipient["assignment"]):
            continue
        if _adviser_decision_review_grant(run,view,assignment,expanded,live,recipient,contribution):
            continue
        raise QSError("adviser_dependency_recipient")
    return reviewable_plans


def _assignment_links(assignment,reference):
    return (reference.id in assignment.get("dependency_result_ids",[])
            and reference.sha256 in assignment["dependency_hashes"])


def _adviser_same_recipient(assignment,recipient):
    return all(assignment.get(key)==recipient.get(key) for key in
               ("role_id","agent_id","lifecycle_scope","prep_stage","prep_admission_sha256"))


def _adviser_assigned_scope(assignment,context):
    if assignment.get("lifecycle_scope")!=context.lifecycle:
        return False
    if context.lifecycle=="PREP":
        return assignment.get("prep_stage")==context.scope
    ids=assignment.get("assigned_ids",{})
    return any(context.scope in ids.get(kind,[]) for kind in ("questions","requirements"))


def _adviser_independent_reader(assignment,context,*excluded):
    return (assignment["artifact_type"]=="REVIEW"
            and re.fullmatch(r"P-(0[1-9]|10)",assignment["role_id"])
            and assignment["agent_id"] not in excluded and _adviser_assigned_scope(assignment,context))


def _adviser_decision_review_grant(run,view,assignment,expanded,context,recipient,contribution):
    if not _adviser_independent_reader(assignment,context,recipient["assignment"]["agent_id"],contribution.assignment.actor):
        return False
    for result_id in sorted(expanded-{contribution.result.id}):
        if view[0]["results"].get(result_id,{}).get("artifact_type")!="EVIDENCE":
            continue
        reference,document,decision=_question_current_result(run,view,result_id,"EVIDENCE")
        proof=document.get("advice_disposition")
        if (isinstance(proof,dict) and proof.get("target_result_id")==contribution.result.id
            and "retired_context_sha256" not in proof and _assignment_links(assignment,reference)):
            _adviser_recipient_decision(run,view,decision,document,recipient,contribution,False)
            return True
    return False


def _adviser_plan_read_grant(run,view,assignment,expanded,advised,contribution,disposition):
    context=advised.context
    if (context.lifecycle!="CORE" or not _adviser_assigned_scope(assignment,context)
        or disposition.status not in {"ACCEPTED","QUALIFIED"}
        or disposition.proof.id not in expanded or not _assignment_links(assignment,disposition.proof)):
        return None
    if assignment["role_id"]=="PLAN-AUTHOR" and assignment["artifact_type"]=="PLAN":
        return set() if _adviser_retained_basis(run,view,context,contribution,disposition) else None
    if not _adviser_independent_reader(assignment,context,contribution.assignment.actor):
        return None
    reviewable=set()
    for result_id in sorted(expanded):
        if view[0]["results"].get(result_id,{}).get("artifact_type")!="PLAN":
            continue
        stage,document,entry=_question_current_result(run,view,result_id,"PLAN")
        if (assignment["agent_id"]!=entry["assignment"]["agent_id"] and _assignment_links(assignment,stage)
            and _adviser_stage_claim(document,entry,context,contribution,disposition)):
            packet=_question_result_context(run,view,entry,document)
            if all(_question_context_exposes(packet,ref) for ref in (contribution.result,disposition.proof)):
                reviewable.add(stage.id)
    return reviewable if reviewable and _adviser_retained_basis(run,view,context,contribution,disposition) else None


def _adviser_freeze_blockers(run,view,lifecycle,scopes=None):
    state,current,_bank,journal=view
    projection=_question_projection(journal)
    if projection.advice is None:
        return ()
    arm=_question_arm(run,current)
    addressed={row.context.scope for row in projection.advice.assignments.values()
               if row.context.lifecycle==lifecycle and (scopes is None or row.context.scope in scopes)}
    def is_current(assignment,contribution,disposition):
        if assignment.context.lifecycle!=lifecycle:
            return True
        try:
            proof,review_doc,review=_question_current_result(run,view,disposition.proof.id,"REVIEW")
            if _adviser_disposition(run,view,projection,arm,proof,review_doc,review)==disposition:
                return True
        except QSError:
            pass
        return lifecycle=="CORE" and _adviser_plan_adopted(run,view,assignment,contribution,disposition)
    blockers=projection.freeze_blockers(addressed,is_current)
    pending=[identifier for identifier in blockers
             if projection.advice.assignments[projection.advice.contributions[identifier].assignment].context.lifecycle==lifecycle]
    contributors={item.assignment for item in projection.advice.contributions.values()}
    for actor,assignment in projection.advice.assignments.items():
        if assignment.context.lifecycle==lifecycle and assignment.context.scope in addressed and actor not in contributors:
            registered=state["registered"].get(actor.assignment.id+"\0"+actor.attempt,{})
            if registered.get("status")=="ACCEPTED":
                pending.append(registered["assignment"]["result_id"])
    return tuple("adviser_disposition_required:"+identifier for identifier in sorted(set(pending)))


def _adviser_plan_stage_scopes(stage):
    return {value for unit in (stage,*stage.get("tasks",[]),*stage.get("acceptance_exit",[]))
            for field in ("q_refs","r_refs","question_ids","requirement_ids")
            for value in unit.get(field,[]) if isinstance(value,str)}


def _adviser_stage_claim(document,entry,context,contribution,disposition):
    expected={"advice_result_id":contribution.result.id,"advice_sha256":contribution.result.sha256,
              "disposition_result_id":disposition.proof.id,"disposition_sha256":disposition.proof.sha256,
              "context_sha256":contribution.context_packet.sha256,"scope":context.scope}
    claims=document.get("advice_consumption",[])
    stage_assignment=entry["assignment"]
    return (isinstance(claims,list) and claims.count(expected)==1
            and context.scope in _adviser_plan_stage_scopes(document)
            and all(_assignment_links(stage_assignment,ref) for ref in (contribution.result,disposition.proof)))


def _adviser_retained_basis(run,view,context,contribution,disposition):
    state,current,_bank,_journal=view
    try:
        source,source_doc,source_entry=_question_current_result(run,view,contribution.result.id,"EVIDENCE",historical=True)
        proof,proof_doc,proof_entry=_question_current_result(run,view,disposition.proof.id,"REVIEW",historical=True)
        if source!=contribution.result or proof!=disposition.proof:
            return False
        original_packet=_question_result_context(run,view,source_entry,source_doc)
        _question_result_context(run,view,proof_entry,proof_doc)
        pending,withdrawn=intake_affected_results(run,state)
        invalid=set(current.get("stale_result_ids",[]))|pending|withdrawn
        original_ids={source.id,proof.id,proof_doc.get("target_result_id")}
        original_ids.update(ref.id for ref in (*context.evidence,*context.decisions))
        original_ids.update(endpoint for edge in original_packet.get("scoped_edges",[])
                            for endpoint in (edge["from_id"],edge["to_id"]))
        if invalid & original_ids:
            return False
        for ref,kind in (*((ref,"EVIDENCE") for ref in context.evidence),
                         *((ref,"DECISION") for ref in context.decisions)):
            if _question_current_result(run,view,ref.id,kind)[0]!=ref:
                return False
    except QSError:
        return False
    return True


def _adviser_plan_adopted(run,view,assignment,contribution,disposition):
    state,current,_bank,_journal=view
    if disposition.status not in {"ACCEPTED","QUALIFIED"}:
        return False
    projection=current.get("current_plan_projection",{})
    if not isinstance(projection,dict) or not projection.get("stages"):
        return False
    context=assignment.context
    if not _adviser_retained_basis(run,view,context,contribution,disposition):
        return False
    for row in projection["stages"]:
        try:
            stage,document,entry=_question_current_result(run,view,row["result_id"],"PLAN")
            if not _adviser_stage_claim(document,entry,context,contribution,disposition):
                continue
            author_packet=_question_result_context(run,view,entry,document)
            if not all(_question_context_exposes(author_packet,ref) for ref in (contribution.result,disposition.proof)):
                continue
        except QSError:
            continue
        for result_id,metadata in state["results"].items():
            if metadata["artifact_type"]!="REVIEW" or not re.fullmatch(r"P-(0[1-9]|10)",metadata["role_id"]):
                continue
            try:
                _,review,review_entry=_question_current_result(run,view,result_id,"REVIEW")
                if (_adviser_independent_reader(review_entry["assignment"],context,contribution.assignment.actor)
                    and review.get("target_result_id")==stage.id and review.get("target_hash")==stage.sha256
                    and {contribution.result.id,disposition.proof.id}<=set(review.get("related_ids",[]))
                    and _question_review(run,view,review,review_entry,"PLAN")[0]==stage):
                    packet=_question_result_context(run,view,review_entry,review)
                    if all(_question_context_exposes(packet,ref) for ref in (contribution.result,disposition.proof)):
                        return True
            except QSError:
                continue
    return False


def _plan_advice_scopes(run,state,current,projection):
    previous={row["logical_stage_id"]:row["result_id"] for row in current.get("current_plan_projection",{}).get("stages",[])}
    proposed={row["logical_stage_id"]:row["result_id"] for row in projection["stages"]}
    scopes=set()
    for logical_id in sorted(previous.keys()|proposed.keys()):
        old,new=previous.get(logical_id),proposed.get(logical_id)
        if old==new:
            continue
        for result_id in (old,new):
            if result_id is not None:
                _,stage=_admitted_document(run,state,result_id,"PLAN")
                scopes.update(_adviser_plan_stage_scopes(stage))
    return scopes


def _require_plan_advice_resolution(run,state,current,after,bank,projection):
    if projection is None or _question_arm(run,current) is None:
        return
    affected=_plan_advice_scopes(run,state,current,projection)
    holds=_adviser_freeze_blockers(run,(state,after,bank,command_journal(run)),"CORE",affected)
    if holds:
        raise QSError("plan_selection_adviser_hold:"+"|".join(holds))


def _scoped_question_coverage(projection,visible):
    ledger=projection.ledger
    coverage=[]
    for question_id in sorted(visible & (ledger.questions.keys()|ledger.aliases.keys())):
        canonical=ledger._canonical(question_id)
        close=ledger.closed.get(canonical)
        gap=ledger.gaps.get(canonical)
        state=("CLOSED_AFTER_WORK" if close else "UNINVESTIGATED" if gap else
               "STARTED_INCOMPLETE" if canonical in ledger.started else "NOT_STARTED")
        record={"question_id":question_id,"canonical_question_id":canonical,"state":state}
        if close or gap:
            proof=(close or gap).proof
            record.update(proof_id=proof.id,proof_sha256=proof.sha256)
        if gap:
            record["reason"]=gap.reason
        coverage.append(record)
    advice=[]
    for identifier,contribution in sorted(projection.advice.contributions.items()):
        context=projection.advice.assignments[contribution.assignment].context
        if context.scope in visible:
            disposition=projection.advice.dispositions.get(identifier)
            row={"result_id":identifier,"result_sha256":contribution.result.sha256,"scope":context.scope,
                 "context_sha256":context.packet.sha256,"disposition":disposition.status if disposition else "PENDING"}
            if disposition:
                row.update(disposition_id=disposition.proof.id,disposition_sha256=disposition.proof.sha256)
            advice.append(row)
    return {"questions":coverage,"advice":advice}


def _question_route_proofs(run,view):
    arm=_question_arm(run,view[1])
    if arm is None:
        return {},None,set()
    ledger=_question_projection(view[3]).ledger
    if ledger is None:
        return {},{},set()
    gaps={}
    owners={}
    retired=set()
    retired_by_question={}
    for attempt in ledger.attempts.values():
        if attempt.status in {"CANCELLED_BEFORE_START","ABORTED_AFTER_START"}:
            retired_by_question.setdefault(attempt.question_id,[]).append(attempt.operation.assignment)
    attempts={attempt.question_id:attempt for attempt in ledger.attempts.values()
              if attempt.status in {"RESERVED","UNKNOWN_START","CONFIRMED_SUBSTANTIVE","CLOSED_AFTER_WORK"}}
    for identifier in ledger.questions.keys()|ledger.aliases.keys():
        canonical=ledger._canonical(identifier)
        if canonical in ledger.gaps:
            gap=ledger.gaps[canonical]
            ref=_question_current_gap(run,view,gap)
            gaps[identifier]={"proof_id":ref.id,"proof_sha256":ref.sha256,"reason":gap.reason}
        elif canonical in attempts:
            owners[identifier]=attempts[canonical].operation.assignment.assignment.id
        retired.update((identifier,actor.assignment.id,actor.attempt)
                       for actor in retired_by_question.get(canonical,[]))
    return gaps,owners,retired


def terminal_attempt(run: Path, assignment_id: str, attempt_id: str) -> bool:
    return any(row["operation"]=="terminal-author-failure" and row["receipt"].get("assignment_id")==assignment_id
               and row["receipt"].get("attempt_id")==attempt_id for row in command_journal(run))


def journal_command(args: argparse.Namespace, effect_sender=None) -> dict[str,Any]:
    run=run_dir(root(args.root),args.run_id)
    if not (run/"state").is_dir(): raise QSError("command_run_state_missing")
    captured=captured_argument(Path(args.request),64 * 1024)
    payload=load_yaml(captured)
    fields={"key","actor","authority_sha256","run_id","input_snapshot","expected_revision"}
    evidence_operation=args.command in {"evidence-intake","evidence-disposition"}
    occurrence_operation=args.command in {"claim-effect","reconcile-effect","terminal-author-failure","adviser-assign"}
    if args.command not in {"claim-effect","adviser-assign"}: fields.add("authority_result_id")
    if evidence_operation:
        fields.add("evidence_result_id")
    if occurrence_operation:
        fields.update(("assignment_id","attempt_id","logical_occurrence_id"))
    if not isinstance(payload,dict) or set(payload)!=fields or any(not isinstance(v,str) or not v for v in payload.values()):
        raise QSError("command_request_contract")
    ident(payload["key"],"command_request_key")
    if re.fullmatch(r"commit-[0-9a-f]{64}",payload["key"]): raise QSError("command_request_key_reserved")
    occurrence=sha(ident(payload["logical_occurrence_id"],"logical_occurrence_id").encode()) if occurrence_operation else ""
    request=Request(args.command,payload["key"],payload["actor"],payload["authority_sha256"],payload["run_id"],
                    payload["input_snapshot"],payload["expected_revision"],captured.raw,occurrence)
    prepare=_intake_receipt if evidence_operation else _question_command_receipt
    adapter=_JournalAdmission(run)
    receipt=admit(adapter,request,lambda cut: canon_yaml(prepare(run,args.command,payload,cut.state)))
    result=load_yaml(CapturedInput("committed command receipt",receipt))
    if args.command=="claim-effect" and adapter.created and effect_sender is not None:
        effect_sender(result)
    if args.command=="claim-effect" and getattr(args,"external_dispatch",False):
        result={**result,"dispatch_action":"SEND_ONCE" if adapter.created else "HOLD_RECONCILE"}
    return result


def frontier_missing_scopes(current, journal):
    pending=set()
    frontier=current.get("applicability_frontier",{})
    if not isinstance(frontier,dict):
        raise QSError("applicability_frontier_shape")
    for identifier,anchor in frontier.items():
        if (not isinstance(anchor,dict) or not isinstance(anchor.get("position"),str)
            or not re.fullmatch(r"[1-9][0-9]{0,7}",anchor["position"])
            or not isinstance(anchor.get("affected_result_ids"),list)
            or any(not isinstance(item,str) for item in anchor["affected_result_ids"])):
            raise QSError("applicability_frontier_shape")
        position=int(anchor["position"])
        if (position>len(journal) or journal[position-1]["raw_sha256"]!=anchor.get("sha256")
            or journal[position-1]["receipt"].get("evidence_result_id")!=identifier):
            pending.update(anchor["affected_result_ids"])
    return pending


def intake_affected_results(run: Path, state: dict[str,Any], journal=None, *, documents=None) -> tuple[set[str],set[str]]:
    decisions={}
    journal=command_journal(run) if journal is None else journal
    current=load_state(run,"current")
    pending=frontier_missing_scopes(current,journal); stale=set()
    anchors=current.get("applicability_frontier",{})
    for row in journal:
        if row["operation"] in {"evidence-intake","evidence-disposition"}:
            receipt=row["receipt"]
            rid=receipt["evidence_result_id"]
            anchor=anchors.get(rid)
            if ("frontier" in receipt and row["operation"]=="evidence-disposition"
                and (not isinstance(anchor,dict) or anchor.get("sha256")!=row["raw_sha256"])):
                continue
            decisions[rid]=receipt
    for receipt in decisions.values():
        frontier=receipt.get("frontier")
        if frontier is not None:
            _admitted_document(run,state,receipt["evidence_result_id"],"EVIDENCE")
            if receipt.get("status")=="EVIDENCE_DISPOSED":
                _admitted_document(run,state,receipt["review_result_id"],"REVIEW")
        if receipt.get("status")=="EVIDENCE_PENDING":
            pending.update(receipt["affected_result_ids"])
        elif (receipt.get("disposition") in {"NEW","UNKNOWN"}
              or frontier is not None and receipt.get("disposition")=="SAME"):
            stale.update(receipt["affected_result_ids"])
    if not pending and not stale: return pending,stale
    docs=_prep_documents(run,state,strict=True) if documents is None else documents
    results=state["results"]
    return stale_result_closure(results,docs,sorted(pending)),stale_result_closure(results,docs,sorted(stale))


def command_revision(run: Path, journal=None) -> str:
    """Bind a command to both selected state and the accepted/attempt index."""
    images=[sha(captured_argument(state_file(run,name),64 * 1024**2).raw)
            for name in ("current","assignments")]
    rows=command_journal(run) if journal is None else journal
    return sha(canon_yaml(images+[rows[-1]["raw_sha256"] if rows else "GENESIS"]))


def _read_command_cut(_args: argparse.Namespace, run: Path) -> dict[str,Any]:
    _,current,_=accepted(run)
    journal=command_journal(run)
    projection=_question_projection(journal)
    result={"status":"COMMAND_CUT","run_id":run.name,"input_snapshot":current["input_snapshot"],
            "expected_revision":command_revision(run,journal),"scope":"STRUCTURAL_ONLY"}
    if projection.ledger is not None:
        ledger=projection.ledger
        result.update(question_capacity=ledger.capacity,question_start_count=len(ledger.started),
                      question_close_count=len(ledger.closed),question_count_blocker=ledger.count_blocker())
    return result


def _question_compare(args):
    workspace=root(args.root)
    first=run_dir(workspace,args.run_id)
    second=run_dir(workspace,args.other_run_id)
    if first==second or any(not (run/"state").is_dir() for run in (first,second)):
        raise QSError("question_comparison_run_pair")
    with ExitStack() as locks:
        for run in sorted((first,second)):
            locks.enter_context(command_cut(lock(run)))
        ledgers=[]
        cuts=[]
        for run in (first,second):
            state,current,bank=accepted(run)
            arm=_question_arm(run,current)
            journal=command_journal(run)
            ledger=_question_projection(journal).ledger
            if arm is None or ledger is None:
                raise QSError("question_comparison_arm_required")
            view=(state,current,bank,journal)
            _question_current_closes(run,view,ledger)
            for gap in ledger.gaps.values():
                _question_current_gap(run,view,gap)
            ledgers.append(ledger)
            cuts.append(command_revision(run,journal))
        status=compare(*ledgers)
    return {"status":"QUESTION_COMPARISON","count_outcome":status,"fast_cut":cuts[0],
            "detailed_cut":cuts[1],"scope":"STRUCTURAL_ONLY"}


def _commit_request(args: argparse.Namespace, run: Path, assignment: dict[str,Any],
                    assignment_raw: bytes, result_raw: bytes, snapshot: str) -> Request | None:
    source=getattr(args,"request",None)
    if source is None: return None
    captured=captured_argument(Path(source),64 * 1024)
    obj=load_yaml(captured)
    fields={"key","actor","authority_sha256","run_id","input_snapshot","expected_revision","result_sha256"}
    if (not isinstance(obj,dict) or set(obj)!=fields or obj["run_id"]!=run.name
        or obj["input_snapshot"]!=snapshot or obj["actor"]!=assignment.get("agent_id")
        or obj["authority_sha256"]!=sha(assignment_raw) or obj["result_sha256"]!=sha(result_raw)):
        raise QSError("command_request_authority_binding")
    ident(obj["key"],"command_request_key")
    if re.fullmatch(r"commit-[0-9a-f]{64}",obj["key"]): raise QSError("command_request_key_reserved")
    if not isinstance(obj["expected_revision"],str) or not re.fullmatch(r"[0-9a-f]{64}",obj["expected_revision"]):
        raise QSError("command_request_revision_contract")
    return Request("commit",obj["key"],obj["actor"],obj["authority_sha256"],run.name,
                   snapshot,obj["expected_revision"],captured.raw)


def accept(args: argparse.Namespace) -> dict[str, Any]:
    return locked_command(args, _accept_locked)


def result_commit_receipt(entry: dict[str,Any]) -> dict[str,Any]:
    result_id=entry["result_id"]
    receipt={"status":"ACCEPTED","result_id":result_id,"content_hash":entry["content_hash"],"scope":"STRUCTURAL_ONLY"}
    if entry["artifact_type"] in REVISION_SELECTIONS:
        receipt["current_selection"]=_selector_for(result_id,entry)
    return receipt


def _accept_locked(args: argparse.Namespace, run: Path) -> dict[str, Any]:
    assignment_input = captured_argument(Path(args.assignment))
    result_input = captured_argument(Path(args.result))
    assignment_raw, result_raw = assignment_input.raw, result_input.raw
    assignment, result = load_yaml(assignment_input), load_yaml(result_input)
    if not isinstance(assignment,dict) or not isinstance(result,dict): raise QSError("invalid_assignment_or_result")
    result_id=ident(result.get("id"),"result_id")
    transaction=child(run,"transactions/"+result_id+".yaml")
    _,current,bank=accepted(run,recovering=transaction.is_file())
    snapshot=current.get("input_snapshot"); rid=ident(args.run_id,"run_id")
    required={"assignment_id","attempt_id","role_id","agent_id","input_snapshot","artifact_type","output_path","dependency_hashes"}
    if not required.issubset(assignment) or common(result,rid,snapshot): raise QSError("invalid_assignment_or_result")
    for key in ("assignment_id","attempt_id","role_id","artifact_type"): ident(assignment[key],key)
    opaque_agent(assignment["agent_id"])
    _assignment_role_guard(run,current,assignment)
    if assignment.get("lifecycle_scope") in {"PREP","CORE"} and result.get("lifecycle_scope")!=assignment.get("lifecycle_scope"): raise QSError("result_lifecycle_scope_binding")
    if assignment.get("lifecycle_scope")=="CORE" and result.get("prep_admission_sha256")!=assignment.get("prep_admission_sha256"): raise QSError("result_prep_admission_binding")
    if assignment.get("lifecycle_scope")=="PREP" and result.get("prep_stage")!=assignment.get("prep_stage"):
        raise QSError("result_prep_stage_binding")
    if assignment.get("lifecycle_scope")=="PREP" and assignment.get("prep_stage")=="PREP-2_PERSPECTIVES":
        if (current.get("input_mode")=="BANK_TO_PLAN" and _question_arm(run,current) is not None
            and result.get("initial_questions",[])!=[]):
            raise QSError("bank_framing_cannot_generate_questions")
        if result.get("vector_result_id")!=assignment.get("goal_result_id"):
            raise QSError("result_prep_framing_binding")
        for field in ("perspective_profile_hash","vector_sha256","approval_sha256"):
            if result.get(field)!=assignment.get(field): raise QSError("result_prep_framing_binding")
        blind_input=result.get("blind_input")
        required_blind={"contains_bank","contains_foreign_p_results","contains_preferred_solution","allowed"}
        allowed=blind_input.get("allowed") if isinstance(blind_input,dict) else None
        if (not isinstance(blind_input,dict) or not required_blind.issubset(blind_input)
            or any(blind_input.get(key)!="NO" for key in ("contains_bank","contains_foreign_p_results","contains_preferred_solution"))
            or not isinstance(allowed,list) or not allowed
            or any(not isinstance(item,str) or not item for item in allowed) or len(allowed)!=len(set(allowed))):
            raise QSError("result_prep_blind_input")
    _revision_output_guard(assignment)
    if assignment["input_snapshot"]!=snapshot or result.get("id")!=assignment.get("result_id",result.get("id")) or result.get("author_role")!=assignment["role_id"] or result.get("agent_id")!=assignment["agent_id"] or result.get("assignment_id")!=assignment["assignment_id"] or result.get("attempt_id")!=assignment["attempt_id"] or result.get("artifact_type")!=assignment["artifact_type"] or result.get("workflow_status")!="ACCEPTED": raise QSError("provenance_binding")
    if not isinstance(assignment["dependency_hashes"],list) or any(not isinstance(x,str) for x in assignment["dependency_hashes"]): raise QSError("invalid_dependencies")
    target=output_path(run,assignment["output_path"]); content_hash=sha(result_raw); pair=assignment["assignment_id"]+"\0"+assignment["attempt_id"]
    if target.relative_to(run).parts[0] not in expected_root(assignment["artifact_type"]): raise QSError("assignment_output_type_mismatch")
    entry={"result_id":result_id,"content_hash":content_hash,"path":assignment["output_path"],"assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],"role_id":assignment["role_id"],"agent_id":assignment["agent_id"],"artifact_type":assignment["artifact_type"],"input_snapshot":snapshot,"dependency_hashes":assignment["dependency_hashes"],**({"lifecycle_scope":assignment["lifecycle_scope"]} if "lifecycle_scope" in assignment else {}),**({"prep_admission_sha256":assignment["prep_admission_sha256"]} if "prep_admission_sha256" in assignment else {})}
    receipt=result_commit_receipt(entry)
    request=_commit_request(args,run,assignment,assignment_raw,result_raw,snapshot)
    state_before=load_state(run,"assignments")
    marker=None
    if transaction.is_file():
        marker=load_yaml(captured_argument(transaction,48 * 1024**2))
        if (not isinstance(marker,dict)
            or marker.get("assignment_bytes_b64")!=base64.b64encode(assignment_raw).decode("ascii")
            or marker.get("result_bytes_b64")!=base64.b64encode(result_raw).decode("ascii")):
            raise QSError("idempotency_conflicting_bytes")
    intent_path=child(run,"intents/"+sha(pair.encode()).upper()+".yaml")
    intent_doc=load_yaml(captured_argument(intent_path))
    if (not isinstance(intent_doc,dict) or intent_doc.get("assignment")!=assignment
        or intent_doc.get("assignment_sha256")!=sha(assignment_raw)
        or intent_doc.get("assignment_bytes_b64")!=base64.b64encode(assignment_raw).decode("ascii")):
        raise QSError("assignment_intent_missing_or_changed")
    if marker is not None and "context_packet_sha256" in result:
        digest=result["context_packet_sha256"]
        if not isinstance(digest,str):
            raise QSError("result_context_binding")
        resolve_context_revision(run,assignment,sha(assignment_raw),state_before,digest)
    explicit_request=request is not None
    if request is None and marker is not None and marker.get("request_origin")=="EXPLICIT":
        raise QSError("command_request_required_for_replay")
    if request is None and (marker is None or marker.get("request_origin")=="REGISTERED_ASSIGNMENT"):
        key="commit-"+sha(pair.encode())
        revision=marker["expected_command_revision"] if marker is not None else command_revision(run)
        payload=canon_yaml({"assignment_sha256":sha(assignment_raw),"result_sha256":content_hash})
        request=Request("commit",key,assignment["agent_id"],sha(assignment_raw),rid,snapshot,revision,payload)
    selection_pending=(marker is not None
        and isinstance(marker.get("selection_before_current_sha256"),str)
        and marker["selection_before_current_sha256"]==sha(captured_argument(state_file(run,"current")).raw))
    if request is not None:
        owner=result_request_owner(run,request.key) if explicit_request else None
        if (owner is not None and owner!=transaction
            or any(row["key"]==request.key for row in command_journal(run))):
            raise QSError("command_request_key_conflict")
        if marker is not None and marker.get("request_key")==request.key:
            if (marker.get("request_fingerprint")!=request.fingerprint()
                or marker.get("entry")!=entry or marker.get("receipt")!=receipt):
                raise QSError("command_request_conflict")
            if selection_pending or pair not in state_before.get("attempts",{}):
                replay=_recover_locked(args,run)
                if replay.get("status")!="RECOVERED": raise QSError("cannot_recover_before_redelivery")
            if explicit_request or current.get("input_mode") in NEW_MODES:
                return receipt
            return {"status":"IDEMPOTENT","result_id":result_id,"content_hash":content_hash,"scope":"STRUCTURAL_ONLY"}
        if request.expected_revision!=command_revision(run): raise QSError("command_request_revision_conflict")
        if marker is not None: raise QSError("attempt_already_committed")
    if marker is not None and (selection_pending or pair not in state_before.get("attempts",{})):
        replay=_recover_locked(args, run)
        if replay.get("status")!="RECOVERED": raise QSError("cannot_recover_before_redelivery")
    state=load_state(run,"assignments"); latest_current=load_state(run,"current"); known={v.get("content_hash") for v in state.get("results",{}).values() if isinstance(v,dict)}
    registered=state.get("registered",{}).get(pair)
    if not isinstance(registered,dict) or registered.get("assignment") != assignment: raise QSError("assignment_not_registered_or_changed")
    if not set(assignment["dependency_hashes"]).issubset(known): raise QSError("missing_accepted_dependency")
    prior=state["attempts"].get(pair)
    if prior:
        if prior.get("content_hash")!=content_hash: raise QSError("idempotency_conflicting_bytes")
        return {"status":"IDEMPOTENT","result_id":prior["result_id"],"content_hash":content_hash,"scope":"STRUCTURAL_ONLY"}
    _new_mode_assignment_guard(run,latest_current,assignment)
    require_current_assignment(run,state,latest_current,assignment)
    arm=_question_arm(run,latest_current)
    if arm is not None and latest_current.get("input_mode")=="BANK_TO_PLAN" and assignment["artifact_type"]=="FRAMING":
        if _bank_framing_exposed(run,state,assignment["agent_id"]):
            raise QSError("bank_framing_actor_already_exposed")
    needs_question_context=arm is not None and (_question_requires_work(assignment) or assignment.get("adviser"))
    needs_framing_context=(assignment.get("lifecycle_scope")=="PREP" and assignment.get("prep_stage")=="PREP-2_PERSPECTIVES")
    journal=command_journal(run)
    chain=context_revision_chain(run,assignment,registered["assignment_sha256"],state,journal=journal)
    if needs_question_context or needs_framing_context or "context_packet_sha256" in result or len(chain)>1:
        if not chain or result.get("context_packet_sha256")!=sha(chain[-1].raw):
            error=("question_work_result_context" if needs_question_context else
                   "result_prep_context_binding" if needs_framing_context else "result_context_binding")
            raise QSError(error)
    if needs_question_context:
        view=(state,latest_current,bank,journal)
        _question_context(run,view,arm,registered,[])
        if assignment.get("question_work"):
            ledger=_question_projection(view[3]).ledger
            lead,support=_question_work_ids(assignment,ledger)
            if (not lead<=_question_confirmed_work(ledger,_question_assignment(registered))
                or not support<=_question_confirmed_work(ledger)):
                raise QSError("question_work_result_start_unconfirmed")
    if assignment.get("lifecycle_scope") != "PREP":
        gaps={}
        if assignment["artifact_type"]=="SOURCE_OUTCOMES":
            gaps,_,_=_question_route_proofs(run,(state,latest_current,bank,command_journal(run)))
            for record in result.get("records",[]):
                if isinstance(record,dict) and record.get("record_kind")=="DERIVED_Q" and "question_gap" in record:
                    if not ledger_gap_outcome(record,[record.get("q_id")],gaps):
                        raise QSError("question_derived_gap_proof")
        intake_errors=validate_intake_candidate(run,bank,state["results"],result,assignment["artifact_type"],load_yaml,gaps)
        if intake_errors: raise QSError("intake_candidate_invalid:"+"|".join(sorted(set(intake_errors))))
    _validate_revision_candidate(run,state,latest_current,result,assignment["artifact_type"])
    _validate_iceberg_candidate(run,state,latest_current,result,entry)
    if target.exists() and sha(target.read_bytes())!=content_hash: raise QSError("immutable_result_path_conflict")
    if result_id in state["results"] and state["results"][result_id]!=entry: raise QSError("result_id_conflict")
    # Immutable commit record is the recovery source of truth. It precedes mutable indexes/events.
    tx={"schema_version":SCHEMA,"transaction_id":"TX-"+result_id,"run_id":rid,"input_snapshot":snapshot,"assignment_sha256":sha(assignment_raw),"assignment_bytes_b64":base64.b64encode(assignment_raw).decode("ascii"),"assignment":assignment,"result_sha256":content_hash,"result_bytes_b64":base64.b64encode(result_raw).decode("ascii"),"entry":entry,"status":"COMMITTED","result_publication":"JOURNAL_FIRST",
        "before_assignments_sha256":sha(captured_argument(state_file(run,"assignments"),64 * 1024**2).raw),**core_metadata(assignment)}
    if assignment["artifact_type"] in REVISION_SELECTIONS and (
        explicit_request or assignment["output_path"]!=REVISION_SELECTIONS[assignment["artifact_type"]][2]
    ):
        tx["selection_before_current_sha256"]=sha(captured_argument(state_file(run,"current")).raw)
    if request is not None:
        tx.update(request_key=request.key,request_fingerprint=request.fingerprint(),receipt=receipt,
                  request_origin="EXPLICIT" if explicit_request else "REGISTERED_ASSIGNMENT",
                  expected_command_revision=request.expected_revision)
    tx_raw=canon_yaml(tx)
    if transaction.exists() and captured_argument(transaction,48 * 1024**2).raw!=tx_raw:
        raise QSError("transaction_conflict")
    state["results"][result_id]=entry
    state["attempts"][pair]={"result_id":result_id,"content_hash":content_hash}
    state["registered"][pair]["status"]="ACCEPTED"
    events=load_state(run,"events")
    event={"event":"RESULT_ACCEPTED","result_id":result_id,"content_hash":content_hash,"assignment_id":assignment["assignment_id"]}
    event["event_hash"]=sha(canon_yaml(event))
    events["events"].append(event)
    writes=[] if target.exists() else [(target,result_raw)]
    writes.extend(((state_file(run,"assignments"),canon_yaml(state)),(state_file(run,"events"),canon_yaml(events))))
    publish_locked(None if transaction.exists() else (transaction,tx_raw),writes,atomic)
    if assignment["artifact_type"] in REVISION_SELECTIONS:
        _select_revision_locked(run,state,latest_current,result_id,assignment["artifact_type"])
    return receipt


def approve_vector(args: argparse.Namespace) -> dict[str,Any]:
    return locked_command(args, _approve_vector_locked)


def _approve_vector_locked(args: argparse.Namespace, run: Path) -> dict[str,Any]:
    source=captured_argument(Path(args.approval)); raw=source.raw; approval=load_yaml(source)
    if not isinstance(approval,dict): raise QSError("approval_not_mapping")
    target=run/"input/vector-approval.yaml"
    state,current,_=accepted(run); snap=load_yaml(run/"input/snapshot.yaml")
    external_bank=isinstance(snap,dict) and snap.get("input_mode")=="BANK_TO_PLAN" and snap.get("bank_entry_kind")=="EXTERNAL_READY_BANK"
    if not isinstance(snap,dict) or (snap.get("input_mode")!="GOAL_TO_PLAN" and not external_bank): raise QSError("approve_vector_local_vector_mode_only")
    if current.get("status")!="PREP_OPEN": raise QSError("approve_vector_phase")
    if target.exists() and target.read_bytes()!=raw: raise QSError("approval_rewrite_forbidden")
    # Validate exact candidate bytes in the pure input view before making
    # them immutable. Rejected candidates must leave the run unchanged.
    _,_,_,inputs,bank,stored,admission,errors=prep_state(run,state,current,approval_candidate=(raw,approval))
    if stored!=approval or errors: raise QSError("approval_invalid:"+"|".join(sorted(errors)))
    if not target.exists(): atomic(target,raw)
    return {"status":"VECTOR_APPROVED","approval_sha256":sha(raw),"input_snapshot":current["input_snapshot"],"scope":"STRUCTURAL_ONLY"}


PERSPECTIVE_PROFILE_FIELDS=("role_id","agent_id","title","competence","analysis_scope","unique_contribution","boundary","plausible_blindspots")
CORE_BLIND_REQUIREMENT_DEFINITION_FIELDS=("id","text","definition","hard_requirement","approved_contract","working_assumption")

def _perspective_profile_hash(profile: dict[str,Any]) -> str:
    """Hash only the eight normative profile fields, never controller metadata."""
    if not isinstance(profile,dict) or set(profile) != set(PERSPECTIVE_PROFILE_FIELDS):
        raise QSError("prep_perspective_profile")
    if any(not isinstance(profile.get(key),str) or not profile[key] for key in PERSPECTIVE_PROFILE_FIELDS):
        raise QSError("prep_perspective_profile")
    return sha(canon_yaml(profile))

def _validate_perspective_map_for_publish(run: Path, state: dict[str,Any], current: dict[str,Any], value: Any) -> dict[str,dict[str,Any]]:
    """Validate the immutable Root map before it can authorize a P readset.

    This is intentionally a small controller boundary.  The pure admission
    validator repeats the complete map checks when there is an admission; this
    gate only prevents an unbound or rewritten map from dispatching P work.
    """
    if not isinstance(value,dict): raise QSError("perspective_map_not_mapping")
    required={"schema_version","id","run_id","input_snapshot","vector_result_id","vector_sha256","approval_sha256","selection_authority","perspectives"}
    if not required.issubset(value) or value.get("run_id")!=run.name or value.get("input_snapshot")!=current.get("input_snapshot") or value.get("selection_authority")!="ROOT":
        raise QSError("perspective_map_binding")
    snapshot=load_yaml(run/"input/snapshot.yaml")
    mode=snapshot.get("input_mode") if isinstance(snapshot,dict) else None
    if mode=="GOAL_TO_PLAN" or (mode=="BANK_TO_PLAN" and snapshot.get("bank_entry_kind")=="EXTERNAL_READY_BANK"):
        approval_path=run/"input/vector-approval.yaml"
        if not approval_path.is_file(): raise QSError("perspective_map_vector_not_approved")
        approval=load_yaml(approval_path)
        if not isinstance(approval,dict) or value.get("approval_sha256")!=sha(approval_path.read_bytes()): raise QSError("perspective_map_approval_binding")
        vector_id=approval.get("vector_result_id"); vector=state.get("results",{}).get(vector_id)
        if not isinstance(vector_id,str) or not isinstance(vector,dict) or value.get("vector_result_id")!=vector_id or value.get("vector_sha256")!=vector.get("content_hash"):
            raise QSError("perspective_map_vector_binding")
    elif mode=="BANK_TO_PLAN":
        binding=snapshot.get("import_binding")
        if not isinstance(binding,dict) or value.get("vector_result_id")!=binding.get("source_vector_result_id") or value.get("vector_sha256")!=binding.get("vector_sha256") or value.get("approval_sha256")!=binding.get("approval_sha256"):
            raise QSError("perspective_map_import_binding")
    else:
        raise QSError("perspective_map_mode")
    rows=value.get("perspectives")
    if not isinstance(rows,list) or len(rows)!=10: raise QSError("perspective_map_count")
    by_role={}
    agents=set()
    for row in rows:
        if not isinstance(row,dict): raise QSError("perspective_map_row")
        profile={key:row.get(key) for key in PERSPECTIVE_PROFILE_FIELDS}
        digest=_perspective_profile_hash(profile)
        role=profile["role_id"]
        if role not in {"P-%02d" % number for number in range(1,11)} or role in by_role or profile["agent_id"] in agents or row.get("profile_sha256")!=digest or "preferred_mechanism" in row:
            raise QSError("perspective_map_rows")
        by_role[role]={**profile,"profile_sha256":digest}
        agents.add(profile["agent_id"])
    if set(by_role)!={"P-%02d" % number for number in range(1,11)}: raise QSError("perspective_map_roles")
    return by_role

def core_blind_vector_projection(run: Path, state: dict[str,Any], current: dict[str,Any], snapshot: dict[str,Any], assignment: dict[str,Any], goal_id: str, goal_entry: dict[str,Any], goal_doc: dict[str,Any]) -> dict[str,Any]:
    """Project only the admitted Vector contract and this P's published profile.

    Core blind framing must be able to name genuine approved requirements, but
    it must not receive source-bank provenance, acceptance locators, hidden
    current-state or research content, or another perspective's profile.
    """
    if assignment.get("lifecycle_scope")!="CORE" or snapshot.get("input_mode") not in NEW_MODES:
        raise QSError("core_blind_projection_scope")
    admission_path=run/"input/prep-admission.yaml"
    admission_hash=assignment.get("prep_admission_sha256")
    if (not admission_path.is_file() or not isinstance(admission_hash,str)
        or current.get("prep_admission_sha256")!=admission_hash or sha(admission_path.read_bytes())!=admission_hash):
        raise QSError("core_blind_admission_binding")
    admission=load_yaml(admission_path)
    if not isinstance(admission,dict) or admission.get("input_snapshot")!=current.get("input_snapshot") or admission.get("status")!="ADMITTED":
        raise QSError("core_blind_admission_binding")

    vector_hash=goal_entry.get("content_hash")
    vector_binding=admission.get("vector_binding")
    if (not isinstance(vector_hash,str) or not isinstance(vector_binding,dict)
        or vector_binding.get("result_id")!=goal_id
        or vector_binding.get("sha256",vector_binding.get("content_hash"))!=vector_hash):
        raise QSError("core_blind_vector_binding")

    requirements=goal_doc.get("requirements")
    definitions=goal_doc.get("requirement_definitions")
    if (not isinstance(requirements,list) or not requirements
        or any(not isinstance(item,str) or not item for item in requirements)
        or not isinstance(definitions,list)):
        raise QSError("core_blind_vector_requirements")
    if len(requirements)!=len(set(requirements)): raise QSError("core_blind_vector_requirements")
    projected_definitions,definition_ids=_blind_requirement_definitions(definitions)
    if len(definition_ids)!=len(set(definition_ids)) or set(definition_ids)!=set(requirements):
        raise QSError("core_blind_vector_requirements")
    scope=goal_doc.get("scope"); constraints=goal_doc.get("hard_constraints"); authority=goal_doc.get("authority")
    if (not isinstance(scope,str) or not scope
        or not isinstance(constraints,list) or any(not isinstance(item,str) or not item for item in constraints)
        or not ((isinstance(authority,str) and authority) or (isinstance(authority,dict) and authority))):
        raise QSError("core_blind_vector_boundary")

    map_path=run/"input/perspective-map.yaml"
    if not map_path.is_file(): raise QSError("core_blind_perspective_map_binding")
    perspective_map=load_yaml(map_path); map_hash=sha(map_path.read_bytes())
    map_binding=admission.get("perspective_map_binding")
    if (not isinstance(perspective_map,dict) or not isinstance(map_binding,dict)
        or map_binding.get("id")!=perspective_map.get("id")
        or map_binding.get("sha256",map_binding.get("content_hash"))!=map_hash):
        raise QSError("core_blind_perspective_map_binding")
    profiles=_validate_perspective_map_for_publish(run,state,current,perspective_map)
    profile=profiles.get(assignment.get("role_id"))
    if not isinstance(profile,dict) or profile.get("agent_id")!=assignment.get("agent_id"):
        raise QSError("core_blind_profile_binding")
    profile_hash=profile.get("profile_sha256")
    own_profile={field:profile.get(field) for field in PERSPECTIVE_PROFILE_FIELDS}
    if not isinstance(profile_hash,str) or _perspective_profile_hash(own_profile)!=profile_hash:
        raise QSError("core_blind_profile_binding")

    approval_hash=perspective_map.get("approval_sha256")
    approval_binding=admission.get("approval_binding")
    if (perspective_map.get("vector_result_id")!=goal_id or perspective_map.get("vector_sha256")!=vector_hash
        or not isinstance(approval_hash,str) or not isinstance(approval_binding,dict)
        or approval_binding.get("sha256",approval_binding.get("content_hash"))!=approval_hash):
        raise QSError("core_blind_approval_binding")
    return {
        "goal":{"requirements":requirements,"requirement_definitions":projected_definitions,"scope":scope,"hard_constraints":constraints,"authority":authority},
        "perspective_profile":own_profile,"perspective_profile_hash":profile_hash,
        "vector_sha256":vector_hash,"approval_sha256":approval_hash,
    }


def _blind_requirement_definitions(definitions):
    if not isinstance(definitions,list):
        raise QSError("core_blind_vector_requirements")
    projected_definitions=[]; definition_ids=[]
    for definition in definitions:
        if not isinstance(definition,dict): raise QSError("core_blind_vector_requirements")
        projected={field:definition[field] for field in CORE_BLIND_REQUIREMENT_DEFINITION_FIELDS if field in definition}
        requirement_id=projected.get("id")
        if (not isinstance(requirement_id,str) or not requirement_id
            or not any(isinstance(projected.get(field),str) and projected[field] for field in ("text","definition"))):
            raise QSError("core_blind_vector_requirements")
        definition_ids.append(requirement_id); projected_definitions.append(projected)
    return projected_definitions,definition_ids


def publish_perspective_map(args: argparse.Namespace) -> dict[str,Any]:
    return locked_command(args, _publish_perspective_map_locked)


def _publish_perspective_map_locked(args: argparse.Namespace, run: Path) -> dict[str,Any]:
    """Copy Root's exact, one-way P assignment map before PREP-2 starts."""
    state,current,bank=accepted(run); snap=load_yaml(run/"input/snapshot.yaml")
    if not isinstance(snap,dict) or snap.get("input_mode") not in NEW_MODES or current.get("status")!="PREP_OPEN": raise QSError("perspective_map_phase")
    source=captured_argument(Path(args.perspective_map)); raw=source.raw; value=load_yaml(source)
    _validate_perspective_map_for_publish(run,state,current,value)
    target=run/"input/perspective-map.yaml"
    if target.exists():
        if target.read_bytes()!=raw: raise QSError("perspective_map_rewrite_forbidden")
        return {"status":"IDEMPOTENT","perspective_map_sha256":sha(raw),"scope":"STRUCTURAL_ONLY"}
    atomic(target,raw)
    events=load_state(run,"events"); event={"event":"PERSPECTIVE_MAP_PUBLISHED","perspective_map_sha256":sha(raw)}; event["event_hash"]=sha(canon_yaml(event)); events["events"].append(event); save_yaml(state_file(run,"events"),events)
    return {"status":"PERSPECTIVE_MAP_PUBLISHED","perspective_map_sha256":sha(raw),"scope":"STRUCTURAL_ONLY"}


def _replay_blob(path: str, raw: bytes, policy: str) -> dict[str,str]:
    return {"path":path,"install_policy":policy,"sha256":sha(raw),"bytes_b64":base64.b64encode(raw).decode("ascii")}


def _admission_candidate(run: Path, args: argparse.Namespace, state: dict[str,Any], current: dict[str,Any], snap: dict[str,Any], bank_raw: bytes, admission_raw: bytes) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    bank=load_yaml(CapturedInput(str(args.bank),bank_raw)); admission=load_yaml(CapturedInput(str(args.admission),admission_raw))
    if not isinstance(bank,dict) or not isinstance(admission,dict): raise QSError("admission_inputs_not_mapping")
    admission_hash=sha(admission_raw); txid="TX-"+ident(admission.get("id",""),"admission_id")
    before=(run/"state/current.yaml").read_bytes()
    next_current={"schema_version":SCHEMA,"run_id":args.run_id,"input_snapshot":current["input_snapshot"],"input_mode":snap["input_mode"],"admission_transaction_id":txid,"bank_sha256":sha(bank_raw),"prep_admission_sha256":admission_hash,"revision":"1","status":"CORE_ADMITTED"}
    after=canon_yaml(next_current)
    tx={"schema_version":SCHEMA,"transaction_type":"PREP_ADMISSION","transaction_id":txid,"admission_id":admission["id"],"run_id":args.run_id,"input_snapshot":current["input_snapshot"],"input_mode":snap["input_mode"],"status":"COMMITTED","bank":_replay_blob("input/bank.yaml",bank_raw,"CREATE_IF_ABSENT" if snap["input_mode"]=="GOAL_TO_PLAN" else "VERIFY_EXISTING"),"admission":_replay_blob("input/prep-admission.yaml",admission_raw,"CREATE_IF_ABSENT"),"expected_current":_replay_blob("state/current.yaml",before,"VERIFY_EXISTING"),"admission_next_current":_replay_blob("state/current.yaml",after,"CREATE_IF_ABSENT")}
    if snap["input_mode"]=="GOAL_TO_PLAN" or snap.get("bank_entry_kind")=="EXTERNAL_READY_BANK":
        approval=load_yaml(run/"input/vector-approval.yaml")
        vector_id=approval.get("vector_result_id") if isinstance(approval,dict) else None
        vector=state.get("results",{}).get(vector_id) if isinstance(vector_id,str) else None
        tx.update({"current_vector_result_id":vector_id,"current_vector_sha256":vector.get("content_hash") if isinstance(vector,dict) else None,"current_approval_sha256":sha((run/"input/vector-approval.yaml").read_bytes())})
    if snap.get("input_mode")=="BANK_TO_PLAN":
        binding=snap.get("external_binding") if snap.get("bank_entry_kind")=="EXTERNAL_READY_BANK" else snap.get("import_binding",{})
        app_candidates=[]
        for entry in state.get("results",{}).values():
            if not isinstance(entry,dict) or entry.get("artifact_type")!="OPERATOR" or entry.get("role_id")!="OP-VECTOR": continue
            try: doc=load_yaml(child(run,entry.get("path","")))
            except QSError: continue
            if isinstance(doc,dict) and doc.get("operator_contract")=="BANK_APPLICABILITY": app_candidates.append(entry)
        if len(app_candidates)!=1: raise QSError("bank_applicability_current_cardinality")
        app=app_candidates[0]
        if snap.get("bank_entry_kind")=="EXTERNAL_READY_BANK":
            tx["external_payload_sha256"]=binding.get("external_payload_sha256") if isinstance(binding,dict) else None
        else:
            tx["import_payload_sha256"]=binding.get("import_payload_sha256") if isinstance(binding,dict) else None
        tx.update({"applicability_result_id":app.get("result_id"),"applicability_sha256":app.get("content_hash")})
    payload={key:value for key,value in tx.items() if key!="transaction_payload_sha256"}; tx["transaction_payload_sha256"]=sha(canon_yaml(payload))
    return bank,admission,{"transaction":tx,"next_current":next_current}


def admit_bank(args: argparse.Namespace) -> dict[str,Any]:
    return locked_command(args, _admit_bank_locked)


def _admit_bank_locked(args: argparse.Namespace, run: Path) -> dict[str,Any]:
    state,current,_=accepted(run); snap=load_yaml(run/"input/snapshot.yaml")
    if not isinstance(snap,dict) or snap.get("input_mode") not in NEW_MODES: raise QSError("admission_new_mode_only")
    if getattr(args,"test_fixture",False) and snap.get("fixture_only")!="YES": raise QSError("fixture_not_bound_at_init")
    bank_raw=captured_argument(Path(args.bank)).raw; admission_raw=captured_argument(Path(args.admission)).raw
    existing_bank=run/"input/bank.yaml"; existing_admission=run/"input/prep-admission.yaml"
    if existing_bank.exists() and existing_bank.read_bytes()!=bank_raw: raise QSError("bank_rewrite_forbidden")
    if existing_admission.exists() and existing_admission.read_bytes()!=admission_raw: raise QSError("admission_rewrite_forbidden")
    bank,admission,candidate=_admission_candidate(run,args,state,current,snap,bank_raw,admission_raw)
    if snap.get("question_arm") is not None:
        journal=command_journal(run)
        ledger=_question_projection(journal).ledger
        originals={qid for qid,question in ledger.questions.items() if question.parent is None} if ledger else set()
        if ledger is None or originals|ledger.aliases.keys()!=_question_inventory_ids(bank):
            raise QSError("question_inventory_bank_mismatch")
        advice_errors=_adviser_freeze_blockers(run,(state,current,bank,journal),"PREP")
        if advice_errors: raise QSError("admission_adviser_hold:"+"|".join(advice_errors))
    inputs=_prep_exact_inputs(run); inputs["input/bank.yaml"]={"sha256":sha(bank_raw),"bytes_b64":base64.b64encode(bank_raw).decode("ascii"),"document":bank}; inputs["input/prep-admission.yaml"]={"sha256":sha(admission_raw),"bytes_b64":base64.b64encode(admission_raw).decode("ascii"),"document":admission}
    docs=_prep_documents(run,state); transactions=_prep_transactions(run); txpath="transactions/admissions/"+candidate["transaction"]["transaction_id"]+".yaml"; transactions[txpath]=candidate["transaction"]
    current_for_pure={**current,"sha256":sha((run/"state/current.yaml").read_bytes())}
    approval=load_yaml(run/"input/vector-approval.yaml") if (run/"input/vector-approval.yaml").is_file() else None
    perspective=load_yaml(run/"input/perspective-map.yaml") if (run/"input/perspective-map.yaml").is_file() else None
    context_errors=prep_framing_context_errors(run,state)
    errors=validate_prep(snap,current_for_pure,state.get("results",{}),docs,inputs,bank,approval,perspective,admission,transactions,"admit")
    errors=sorted(set(errors+context_errors))
    if errors: raise QSError("admission_invalid:"+"|".join(errors))
    tx_path=run/txpath
    latest=load_state(run,"current")
    if latest!=current: raise QSError("admission_current_changed")
    if tx_path.exists() and tx_path.read_bytes()!=canon_yaml(candidate["transaction"]): raise QSError("admission_transaction_conflict")
    if not tx_path.exists(): save_yaml(tx_path,candidate["transaction"])
    if not existing_bank.exists(): atomic(existing_bank,bank_raw)
    if not existing_admission.exists(): atomic(existing_admission,admission_raw)
    save_yaml(state_file(run,"current"),candidate["next_current"])
    events=load_state(run,"events"); event={"event":"PREP_ADMISSION_COMMITTED","transaction_id":candidate["transaction"]["transaction_id"],"admission_sha256":sha(admission_raw)}; event["event_hash"]=sha(canon_yaml(event)); events["events"].append(event); save_yaml(state_file(run,"events"),events)
    return {"status":"CORE_ADMITTED","transaction_id":candidate["transaction"]["transaction_id"],"prep_admission_sha256":sha(admission_raw),"scope":"STRUCTURAL_ONLY"}

def commit(args: argparse.Namespace) -> dict[str,Any]:
    """Public name for the same immutable, idempotent acceptance operation."""
    return accept(args)

BLIND_REQUIRED_INSTRUCTION_PATHS={"references/roles/perspectives.md","references/phases/01-intake.md"}
BLIND_OPTIONAL_INSTRUCTION_PATHS={"references/invariants.md"}
BLIND_INSTRUCTION_PATHS=BLIND_REQUIRED_INSTRUCTION_PATHS|BLIND_OPTIONAL_INSTRUCTION_PATHS

def prep_framing_material_paths(run: Path, snapshot: dict[str,Any], allowed_reads: list[str]) -> list[str]:
    """Resolve only explicitly assigned immutable briefs for a blind P packet."""
    materials=snapshot.get("materials",[])
    if not isinstance(materials,list): raise QSError("prep_framing_materials_contract")
    by_path:dict[str,str]={}
    for item in materials:
        if not isinstance(item,dict) or not isinstance(item.get("path"),str) or not isinstance(item.get("sha256"),str):
            raise QSError("prep_framing_materials_contract")
        path=item["path"]
        if path in by_path: raise QSError("prep_framing_materials_contract")
        material_path=child(run,path)
        if not material_path.is_file() or sha(material_path.read_bytes())!=item["sha256"]:
            raise QSError("immutable_material_snapshot_drift")
        by_path[path]=item["sha256"]
    selected=[path for path in allowed_reads if path in by_path]
    sensitive_paths={value for key,value in snapshot.items() if key in {"bank_path"} and isinstance(value,str)}
    sensitive_hashes={value for key,value in snapshot.items() if key in {"bank_sha256"} and isinstance(value,str)}
    original=snapshot.get("original_bank")
    if isinstance(original,dict):
        sensitive_paths.update(value for key,value in original.items() if key=="path" and isinstance(value,str))
        sensitive_hashes.update(value for key,value in original.items() if key=="sha256" and isinstance(value,str))
    for binding_name in ("import_binding","external_binding"):
        binding=snapshot.get(binding_name)
        if not isinstance(binding,dict): continue
        sensitive_paths.update(value for key,value in binding.items() if key.endswith("_path") and isinstance(value,str))
        sensitive_hashes.update(value for key,value in binding.items() if key.endswith("_sha256") and isinstance(value,str))
    if any(path in sensitive_paths or by_path[path] in sensitive_hashes for path in selected):
        raise QSError("prep_framing_material_forbidden")
    return selected

def blind_handoff(assignment: dict[str,Any], bank: dict[str,Any]) -> dict[str,Any]:
    """Validate the only disclosure permitted before a P framing result.

    The handoff lives in the immutable assignment bytes and contains no bank
    locator, Q/R identifier, dependency, result path, or free-form nested data.
    It is therefore addressable without giving a blind role filesystem access.
    """
    handoff=assignment.get("blind_handoff")
    required={"handoff_id","assignment_id","attempt_id","role_id","input_snapshot","phase","objective","constraints"}
    if not isinstance(handoff,dict) or set(handoff)!=required: raise QSError("blind_handoff_contract")
    for key in ("handoff_id","assignment_id","attempt_id","role_id","input_snapshot","phase","objective"):
        if not isinstance(handoff.get(key),str) or not handoff[key]: raise QSError("blind_handoff_contract")
    if handoff["assignment_id"]!=assignment.get("assignment_id") or handoff["attempt_id"]!=assignment.get("attempt_id") or handoff["role_id"]!=assignment.get("role_id") or handoff["input_snapshot"]!=assignment.get("input_snapshot") or handoff["phase"]!="FRAMING": raise QSError("blind_handoff_binding")
    if not isinstance(handoff["constraints"],list) or not handoff["constraints"] or any(not isinstance(item,str) or not item for item in handoff["constraints"]): raise QSError("blind_handoff_contract")
    # Reject exact identifiers and source text from the frozen bank; an opaque
    # objective may describe G but cannot smuggle a source question or R id.
    encoded=canon_yaml(handoff).decode("utf-8")
    forbidden=[]
    for question in bank.get("questions",[]) if isinstance(bank.get("questions"),list) else []:
        if isinstance(question,dict): forbidden.extend(value for value in (question.get("id"),question.get("text")) if isinstance(value,str) and value)
    forbidden.extend(value for value in bank.get("requirements",[]) if isinstance(value,str) and value)
    if any(value in encoded for value in forbidden): raise QSError("blind_handoff_bank_disclosure")
    return handoff


def _approved_framing_import(run,snapshot):
    binding=snapshot.get("import_binding",{})
    inputs={}
    for field in ("vector_path","approval_path","statement_path"):
        relative=binding.get(field)
        if not isinstance(relative,str):
            raise QSError("prep_import_framing_locator")
        raw=captured_argument(child(run,relative)).raw
        inputs[relative]={"sha256":sha(raw)}
        if field!="statement_path":
            inputs[relative]["document"]=load_yaml(CapturedInput(relative,raw))
    vector,binding,errors=approved_import_for_framing(snapshot,inputs)
    if errors:
        raise QSError("prep_import_framing:"+"|".join(errors))
    return vector,binding


def validate_prep_framing_assignment(assignment: dict[str,Any], run: Path | None=None, state: dict[str,Any] | None=None, current: dict[str,Any] | None=None) -> dict[str,Any]:
    if assignment.get("lifecycle_scope")!="PREP" or assignment.get("prep_stage")!="PREP-2_PERSPECTIVES": raise QSError("prep_framing_stage")
    if assignment.get("artifact_type")!="FRAMING" or assignment.get("role_id") not in {"P-%02d" % n for n in range(1,11)}: raise QSError("prep_framing_role")
    if assignment.get("dependency_hashes")!=[] or assignment.get("dependency_result_ids",[]) not in ([],None) or assignment.get("contradiction_result_ids",[]) not in ([],None): raise QSError("prep_framing_dependencies")
    if assignment.get("assigned_ids",{}) not in ({},None): raise QSError("prep_framing_assigned_ids")
    profile=assignment.get("perspective_profile")
    if not isinstance(profile,dict) or profile.get("role_id")!=assignment.get("role_id") or profile.get("agent_id")!=assignment.get("agent_id"):
        raise QSError("prep_perspective_profile")
    profile_hash=_perspective_profile_hash(profile)
    if assignment.get("perspective_profile_hash")!=profile_hash: raise QSError("prep_perspective_profile_hash")
    if not isinstance(assignment.get("goal_result_id"),str) or not isinstance(assignment.get("vector_sha256"),str) or not isinstance(assignment.get("approval_sha256"),str):
        raise QSError("prep_framing_vector_binding")
    paths=assignment.get("role_instruction_paths",[]); reads=assignment.get("allowed_reads",[])
    allowed={"references/invariants.md","references/preparation.md","references/roles/perspectives.md"}
    if not isinstance(paths,list) or not isinstance(reads,list): raise QSError("prep_framing_readset")
    combined=set(paths)|set(reads)
    if not combined or not set(paths) <= allowed or len(paths)+len(reads)!=len(combined): raise QSError("prep_framing_readset")
    if run is not None:
        if not isinstance(state,dict) or not isinstance(current,dict): raise QSError("prep_framing_map_state")
        snapshot=load_yaml(run/"input/snapshot.yaml")
        if not isinstance(snapshot,dict): raise QSError("prep_framing_materials_contract")
        material_paths=set(prep_framing_material_paths(run,snapshot,reads))
        if not set(reads) <= allowed|material_paths: raise QSError("prep_framing_readset")
        map_path=run/"input/perspective-map.yaml"
        if not map_path.is_file(): raise QSError("prep_framing_map_missing")
        rows=_validate_perspective_map_for_publish(run,state,current,load_yaml(map_path))
        mapped=rows.get(assignment["role_id"])
        if mapped is None or {key:profile[key] for key in PERSPECTIVE_PROFILE_FIELDS} != {key:mapped[key] for key in PERSPECTIVE_PROFILE_FIELDS} or mapped["profile_sha256"]!=profile_hash:
            raise QSError("prep_framing_map_profile_mismatch")
        if snapshot.get("question_arm") is not None and snapshot.get("bank_entry_kind")=="APPROVED_RUN_IMPORT":
            _,binding=_approved_framing_import(run,snapshot)
            approved=(binding["source_vector_result_id"],binding["vector_sha256"],binding["approval_sha256"])
        else:
            source=captured_argument(child(run,"input/vector-approval.yaml"))
            approval=load_yaml(source)
            if not isinstance(approval,dict):
                raise QSError("prep_framing_approved_vector_mismatch")
            approved=(approval.get("vector_result_id"),approval.get("vector_sha256"),sha(source.raw))
        if (assignment["goal_result_id"],assignment["vector_sha256"],assignment["approval_sha256"])!=approved:
            raise QSError("prep_framing_approved_vector_mismatch")
    return profile

def validate_blind_assignment(assignment: dict[str,Any], bank: dict[str,Any]) -> dict[str,Any]:
    if assignment.get("artifact_type")!="FRAMING": raise QSError("blind_artifact_type")
    role=assignment.get("role_id")
    if not isinstance(role,str) or not role.startswith("P-"): raise QSError("blind_role")
    paths=assignment.get("role_instruction_paths",[]); reads=assignment.get("allowed_reads",[])
    readset=set(paths)|set(reads) if isinstance(paths,list) and isinstance(reads,list) else set()
    if (not isinstance(paths,list) or not isinstance(reads,list) or not BLIND_REQUIRED_INSTRUCTION_PATHS.issubset(readset)
        or not readset.issubset(BLIND_INSTRUCTION_PATHS) or len(paths)+len(reads)!=len(readset)):
        raise QSError("blind_readset_allowlist")
    if assignment.get("dependency_hashes") != [] or assignment.get("dependency_result_ids",[]) not in ([],None) or assignment.get("contradiction_result_ids",[]) not in ([],None): raise QSError("blind_dependencies_forbidden")
    assigned=assignment.get("assigned_ids",{})
    if assigned not in ({},None) and (not isinstance(assigned,dict) or any(assigned.get(key,[]) not in ([],None) for key in ("questions","requirements","decisions","evidence"))): raise QSError("blind_assigned_obligations_forbidden")
    return blind_handoff(assignment,bank)

def require_current_assignment(run: Path, state: dict[str,Any], current: dict[str,Any], assignment: dict[str,Any]) -> None:
    assignment_id=assignment["assignment_id"]
    attempt_id=assignment["attempt_id"]
    if state.get("current_attempts",{}).get(assignment_id,attempt_id)!=attempt_id:
        raise QSError("assignment_attempt_superseded")
    if terminal_attempt(run,assignment_id,attempt_id): raise QSError("assignment_attempt_terminal")
    stale=current.get("stale_result_ids",[])
    if not isinstance(stale,list) or any(not isinstance(item,str) for item in stale):
        raise QSError("stale_result_ids_invalid")
    pending,withdrawn=intake_affected_results(run,state)
    result_ids=assignment.get("dependency_result_ids") or []
    if not isinstance(result_ids,list) or any(not isinstance(item,str) for item in result_ids):
        raise QSError("assignment_dependency_ids_invalid")
    dependencies=set(assignment["dependency_hashes"])
    contradictions=assignment.get("contradiction_result_ids") or []
    if not isinstance(contradictions,list) or any(not isinstance(item,str) for item in contradictions):
        raise QSError("assignment_contradiction_ids_invalid")
    references={assignment.get("result_id"),*result_ids,*contradictions}
    assigned=assignment.get("assigned_ids")
    if isinstance(assigned,dict):
        for ids in assigned.values():
            if isinstance(ids,list): references.update(value for value in ids if isinstance(value,str))
    for code, affected in (("assignment_evidence_pending",pending),("assignment_evidence_stale",set(stale)|withdrawn)):
        hashes={entry["content_hash"] for key,entry in state.get("results",{}).items()
                if key in affected and isinstance(entry,dict) and "content_hash" in entry}
        if affected & references or hashes & dependencies:
            raise QSError(code)


def _registered_assignment(run: Path, assignment_path: Path, *, historical=False) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    captured = captured_argument(assignment_path)
    assignment = load_yaml(captured)
    if not isinstance(assignment,dict): raise QSError("invalid_assignment")
    state,current,bank=accepted(run)
    key=str(assignment.get("assignment_id",""))+"\0"+str(assignment.get("attempt_id",""))
    registered=state.get("registered",{}).get(key)
    raw = captured.raw
    if not isinstance(registered,dict) or registered.get("assignment")!=assignment or registered.get("assignment_sha256")!=sha(raw):
        raise QSError("assignment_not_registered_or_changed")
    if not historical:
        _new_mode_assignment_guard(run,current,assignment)
        require_current_assignment(run,state,current,assignment)
    return assignment,state,bank

def context_packet_path(run: Path, assignment: dict[str,Any]) -> Path:
    return child(run,"state/contexts/"+assignment["assignment_id"]+"--"+assignment["attempt_id"]+".yaml")


def validate_context_output(run: Path, stored: Path, output: Path) -> None:
    if output==stored: return
    if (output.is_relative_to(run.parent) or output.resolve()!=output
        or output.is_relative_to(Path(__file__).resolve().parent.parent)
        or output.exists() and (not output.is_file() or output.stat().st_nlink!=1)):
        raise QSError("context_output_protected")


def context_proof(run: Path, assignment: dict[str,Any], assignment_sha256: str, raw: bytes):
    packet=context_packet_path(run,assignment)
    proof_path=child(run,"transactions/contexts/"+packet.name)
    proof={"schema_version":SCHEMA,"run_id":run.name,"input_snapshot":assignment["input_snapshot"],
           "assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],
           "assignment_sha256":assignment_sha256,"packet_sha256":sha(raw)}
    return proof_path,canon_yaml(proof)


def check_historical_context_proof(run, assignment, assignment_sha256, raw, state):
    path,expected=context_proof(run,assignment,assignment_sha256,raw)
    if path.exists() or path.is_symlink():
        recorded=captured_argument(path,64 * 1024).raw
        if recorded!=expected:
            raise QSError("historical_context_proof_mismatch")
        return sha(recorded)
    pair=assignment["assignment_id"]+"\0"+assignment["attempt_id"]
    attempt=state.get("attempts",{}).get(pair)
    if isinstance(attempt,dict):
        result_id=ident(attempt.get("result_id"),"result_id")
        entry=state["results"].get(result_id)
        if isinstance(entry,dict) and entry.get("content_hash")==attempt.get("content_hash"):
            _entry,doc=_admitted_document(run,state,result_id,entry.get("artifact_type"))
            tx=load_yaml(captured_argument(child(run,"transactions/"+result_id+".yaml"),48 * 1024**2))
            if (isinstance(tx,dict) and tx.get("status")=="COMMITTED" and tx.get("entry")==entry
                and tx.get("assignment_sha256")==assignment_sha256 and tx.get("result_sha256")==entry["content_hash"]
                and doc.get("context_packet_sha256")==sha(raw)):
                return
    raise QSError("historical_context_proof_missing")


class ContextRevision(NamedTuple):
    path: Path
    raw: bytes
    packet: dict[str,Any]
    proof_sha256: str | None = None


def _context_revision_paths(run,assignment):
    pair=assignment["assignment_id"]+"\0"+assignment["attempt_id"]
    suffix="contexts/revisions/"+sha(pair.encode())
    return child(run,"state/"+suffix),child(run,"transactions/"+suffix)


def _context_revision_entries(directory):
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise QSError("context_revision_path")
    if not directory.is_dir():
        return set()
    return {path.name for path in directory.iterdir() if not path.name.startswith(".tmp-")}


def _context_dependency_rows(packet):
    rows=packet.get("accepted_dependencies",[])
    if not isinstance(rows,list):
        raise QSError("context_revision_dependencies")
    indexed={}
    for row in rows:
        source=row.get("source") if isinstance(row,dict) else None
        projection=row.get("projection") if isinstance(row,dict) else None
        view=projection.get("view") if isinstance(projection,dict) else None
        if (not isinstance(source,dict) or not isinstance(source.get("result_id"),str)
            or source["result_id"] in indexed or not isinstance(source.get("sha256"),str)
            or not isinstance(view,dict) or not isinstance(view.get("kind"),str)
            or projection.get("source")!=source or projection.get("version")!=CONTEXT_PROJECTION_VERSION
            or projection.get("sha256")!=sha(canon_yaml({key:value for key,value in projection.items() if key!="sha256"}))):
            raise QSError("context_revision_dependencies")
        indexed[source["result_id"]]=row
    return indexed


def _full_context_sources(rows):
    return {rid:row["source"]["sha256"] for rid,row in rows.items()
            if row["projection"]["view"]["kind"]=="FULL_LINKED_EXPANSION"}


def _context_expansion(previous, packet, assignment, state):
    """Validate that this revision only adds exact, assignment-linked full views."""
    before=_context_dependency_rows(previous)
    after=_context_dependency_rows(packet)
    full=_full_context_sources(after)
    prior_full=_full_context_sources(before)
    hashes=set(assignment["dependency_hashes"])
    contradictions=set(assignment.get("contradiction_result_ids") or [])
    if (assignment["artifact_type"]=="FRAMING" or assignment.get("adviser") or packet.get("blind")!="false"
        or {key:value for key,value in previous.items() if key!="accepted_dependencies"}
           !={key:value for key,value in packet.items() if key!="accepted_dependencies"}
        or not before.keys()<=after.keys() or not prior_full.items()<=full.items()):
        raise QSError("context_revision_projection_change")
    changed={rid for rid,row in after.items() if before.get(rid)!=row}
    if not changed or changed!=full.keys()-prior_full.keys():
        raise QSError("context_revision_projection_change")
    for rid,digest in full.items():
        row=after[rid]
        accepted_entry=state["results"].get(rid,{})
        if (digest!=accepted_entry.get("content_hash") or row["source"].get("path")!=accepted_entry.get("path")
            or (digest not in hashes and rid not in contradictions)
            or (rid in before and row["source"]!=before[rid]["source"])):
            raise QSError("context_revision_source_binding")
    return [{"result_id":rid,"sha256":digest} for rid,digest in sorted(full.items())]


def _context_revision_proof(run, assignment, assignment_sha256, previous, previous_proof, revision, number, expanded):
    return {"schema_version":SCHEMA,"run_id":run.name,"input_snapshot":assignment["input_snapshot"],
            "assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],
            "assignment_sha256":assignment_sha256,"revision":str(number),
            "previous_packet_path":previous.path.relative_to(run).as_posix(),"previous_packet_sha256":sha(previous.raw),
            "previous_proof_path":previous_proof.relative_to(run).as_posix(),
            "previous_proof_sha256":previous.proof_sha256,
            "packet_path":revision.path.relative_to(run).as_posix(),"packet_sha256":sha(revision.raw),
            "packet_bytes_b64":base64.b64encode(revision.raw).decode("ascii"),"expanded_sources":expanded}


def _context_result_witness(run, assignment, assignment_sha256, state):
    """Read a committed packet digest from the exact result or its durable pending projection."""
    pair=assignment["assignment_id"]+"\0"+assignment["attempt_id"]
    attempts=state.get("attempts",{})
    if not isinstance(attempts,dict):
        raise QSError("context_revision_result_binding")
    attempt=attempts.get(pair)
    pending={}
    if attempt is None:
        result_id=assignment.get("result_id")
        if not isinstance(result_id,str):
            return None
        transaction=child(run,"transactions/"+result_id+".yaml")
        if not transaction.is_file():
            return None
        state,pending=prospective_result_projection(run,state,transaction=transaction,
                                                     assignment_sha256=assignment_sha256)
        attempt=state["attempts"].get(pair)
        if attempt is None:
            return None
    result_id=attempt["result_id"]
    entry=state["results"][result_id]
    raw=pending.get(result_id)
    if raw is None:
        _,document=_admitted_document(run,state,result_id,assignment["artifact_type"])
    else:
        document=load_yaml(CapturedInput(entry["path"],raw))
    if (entry.get("content_hash")!=attempt.get("content_hash") or not isinstance(document,dict)
        or document.get("id")!=result_id or document.get("assignment_id")!=assignment["assignment_id"]
        or document.get("attempt_id")!=assignment["attempt_id"]):
        raise QSError("context_revision_result_binding")
    if "context_packet_sha256" not in document:
        return None
    digest=document["context_packet_sha256"]
    if not isinstance(digest,str) or not re.fullmatch(r"[0-9a-f]{64}",digest):
        raise QSError("context_revision_result_binding")
    return digest


def context_revision_chain(run,assignment,assignment_sha256,state,*,reissue=False,journal=(),result_witness=None):
    claimed_hash=next((receipt["context_packet_sha256"] for row in reversed(journal)
                       if (receipt:=row["receipt"]).get("status")=="EFFECT_CLAIMED"
                       and receipt.get("assignment_sha256")==assignment_sha256
                       and receipt.get("attempt_id")==assignment["attempt_id"]
                       and "context_packet_sha256" in receipt),None)
    base=context_packet_path(run,assignment)
    base_proof,_=context_proof(run,assignment,assignment_sha256,b"")
    packets,proofs=_context_revision_paths(run,assignment)
    packet_names=_context_revision_entries(packets)
    proof_names=_context_revision_entries(proofs)
    names=packet_names|proof_names
    if not base.exists() and not base.is_symlink():
        if names or claimed_hash is not None or ((base_proof.exists() or base_proof.is_symlink()) and not reissue):
            raise QSError("context_revision_incomplete")
        if result_witness is not None:
            raise QSError("context_revision_result_binding")
        return []
    raw=captured_argument(base,48 * 1024**2).raw
    proof_sha256=check_historical_context_proof(run,assignment,assignment_sha256,raw,state)
    packet=load_yaml(CapturedInput(str(base),raw))
    binding={"assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],
             "run_id":run.name,"input_snapshot":assignment["input_snapshot"]}
    if not isinstance(packet,dict) or any(packet.get(key)!=value for key,value in binding.items()):
        raise QSError("historical_context_binding")
    chain=[ContextRevision(base,raw,packet,proof_sha256)]
    previous_proof=base_proof
    for number,name in enumerate(sorted(names),2):
        if name!=f"{number:08d}.yaml":
            raise QSError("context_revision_sequence")
        if name not in proof_names or (name not in packet_names and (not reissue or number!=len(names)+1)):
            raise QSError("context_revision_incomplete")
        if chain[-1].proof_sha256 is None:
            raise QSError("context_revision_predecessor_proof_missing")
        proof=proofs/name
        source=captured_argument(proof,66 * 1024**2)
        record=load_yaml(source)
        if not isinstance(record,dict):
            raise QSError("context_revision_proof")
        try:
            raw=base64.b64decode(record.get("packet_bytes_b64"),validate=True)
        except (ValueError,TypeError,binascii.Error) as exc:
            raise QSError("context_revision_proof") from exc
        if len(raw)>48 * 1024**2:
            raise QSError("context_revision_packet_size")
        path=packets/name
        if name in packet_names and captured_argument(path,48 * 1024**2).raw!=raw:
            raise QSError("context_revision_packet_drift")
        packet=load_yaml(CapturedInput(str(path),raw))
        if not isinstance(packet,dict):
            raise QSError("context_revision_packet")
        revision=ContextRevision(path,raw,packet,sha(source.raw))
        expanded=_context_expansion(chain[-1].packet,packet,assignment,state)
        expected=_context_revision_proof(run,assignment,assignment_sha256,chain[-1],previous_proof,revision,number,expanded)
        if source.raw!=canon_yaml(expected):
            raise QSError("context_revision_proof")
        chain.append(revision)
        previous_proof=proof
    if claimed_hash is not None and sha(chain[-1].raw)!=claimed_hash:
        raise QSError("context_revision_claim_binding")
    if result_witness is not None and sha(chain[-1].raw)!=result_witness:
        raise QSError("context_revision_result_binding")
    return chain


def resolve_context_revision(run,assignment,assignment_sha256,state,packet_sha256=None,*,journal=(),result_witness=None):
    chain=context_revision_chain(run,assignment,assignment_sha256,state,journal=journal,result_witness=result_witness)
    if not chain:
        raise QSError("context_packet_missing")
    if packet_sha256 is None:
        return chain[-1]
    for revision in chain:
        if sha(revision.raw)==packet_sha256:
            return revision
    raise QSError("context_packet_hash_unknown")


def _context_output_pending(run,stored,output,raw):
    validate_context_output(run,stored,output)
    if output==stored:
        return False
    if not output.exists():
        return True
    if captured_argument(output,48 * 1024**2).raw!=raw:
        raise QSError("context_output_conflict")
    return False


def _deliver_context_output(output,raw):
    try:
        atomic(output,raw,replace=False)
    except FileExistsError:
        if captured_argument(output,48 * 1024**2).raw!=raw:
            raise QSError("context_output_conflict")


def _guard_context_revision(run, assignment, registered, state, journal):
    pair=assignment["assignment_id"]+"\0"+assignment["attempt_id"]
    result_id=assignment.get("result_id")
    if (registered.get("status")!="ASSIGNED" or pair in state["attempts"]
        or (result_id and child(run,"transactions/"+result_id+".yaml").is_file())):
        raise QSError("context_revision_attempt_committed")
    record=load_launch_intents(run,assignment["input_snapshot"])["records"].get(pair)
    launch=checked_launch_chain(run,assignment["input_snapshot"],pair,registered,record)
    if (registered.get("launch_state")=="DISPATCH_UNKNOWN"
        or isinstance(record,dict) and record.get("state")=="DISPATCH_UNKNOWN"
        or launch and (record!=launch[-1] or launch[-1]["state"]=="DISPATCH_UNKNOWN")):
        raise QSError("context_revision_dispatch_fenced")
    effects=[row["receipt"] for row in journal if row["receipt"].get("status") in {"EFFECT_CLAIMED","EFFECT_RECONCILED"}]
    occurrence=assignment.get("logical_occurrence_id")
    latest=next((effect for effect in reversed(effects) if occurrence and effect.get("logical_occurrence_id")==occurrence),None)
    if (any(effect.get("assignment_id")==assignment["assignment_id"] and effect.get("attempt_id")==assignment["attempt_id"] for effect in effects)
        or latest is not None and latest.get("effect_state")!="NOT_SENT"):
        raise QSError("context_revision_dispatch_fenced")


def write_context_packet(run, assignment, output, packet, registered, state, chain, requested, journal):
    """Write the durable context proof before projecting its exact packet."""
    assignment_sha256=registered["assignment_sha256"]
    if assignment.get("lifecycle_scope")=="CORE":
        metadata=core_metadata(assignment)
        if any(packet.get(key)!=value for key,value in metadata.items()):
            raise QSError("core_context_propagation")
    raw=canon_yaml(packet)
    if len(raw)>48 * 1024**2:
        raise QSError("context_revision_packet_size")
    if requested and not requested<=_full_context_sources(_context_dependency_rows(packet)).keys():
        raise QSError("context_expansion_unsupported")
    marker=None
    if not chain:
        stored=context_packet_path(run,assignment)
        proof_path,proof_raw=context_proof(run,assignment,assignment_sha256,raw)
        if proof_path.exists() or proof_path.is_symlink():
            if captured_argument(proof_path,64 * 1024).raw!=proof_raw:
                raise QSError("context_packet_proof_conflict")
        else:
            marker=(proof_path,proof_raw)
    else:
        prior=chain[-1]
        stored=prior.path
        if raw!=prior.raw:
            if not requested:
                raise QSError("context_packet_conflict")
            if not prior.path.is_file():
                raise QSError("context_revision_incomplete")
            if prior.proof_sha256 is None:
                raise QSError("context_revision_predecessor_proof_missing")
            expanded=_context_expansion(prior.packet,packet,assignment,state)
            _guard_context_revision(run,assignment,registered,state,journal)
            packets,proofs=_context_revision_paths(run,assignment)
            number=len(chain)+1
            name=f"{number:08d}.yaml"
            stored=packets/name
            prior_proof=(context_proof(run,assignment,assignment_sha256,prior.raw)[0]
                         if number==2 else proofs/prior.path.name)
            proof=_context_revision_proof(run,assignment,assignment_sha256,prior,prior_proof,
                                          ContextRevision(stored,raw,packet),number,expanded)
            marker=(proofs/name,canon_yaml(proof))
        elif not stored.is_file():
            # Only an explicit exact reissue can complete a journal-first tail.
            before=_full_context_sources(_context_dependency_rows(chain[-2].packet))
            after=_full_context_sources(_context_dependency_rows(prior.packet))
            if not (after.keys()-before.keys())<=requested:
                raise QSError("context_revision_incomplete")
            _guard_context_revision(run,assignment,registered,state,journal)
    deliver=_context_output_pending(run,stored,output,raw)
    publish_locked(marker,[] if stored.is_file() else [(stored,raw)],atomic)
    if deliver:
        _deliver_context_output(output,raw)
    return context_receipt(output,raw,packet)


def context_receipt(output, raw, packet, *, historical=False):
    return {"status":"CONTEXT_HISTORICAL" if historical else "CONTEXT_READY","output":str(output),
            "sha256":sha(raw),"bytes":len(raw),"blind":packet["blind"],"tokens":"UNAVAILABLE",
            "cost":"UNAVAILABLE","scope":"STRUCTURAL_ONLY"}


def prep_framing_context_errors(run: Path, state: dict[str,Any]) -> list[str]:
    """Check every accepted PREP-2 result against its immutable context packet."""
    errors=[]
    registered=state.get("registered",{})
    results=state.get("results",{})
    if not isinstance(registered,dict) or not isinstance(results,dict):
        return ["prep:framing_context:registry:invalid"]
    for key, registered_entry in sorted(registered.items()):
        assignment=registered_entry.get("assignment") if isinstance(registered_entry,dict) else None
        if not isinstance(assignment,dict) or assignment.get("lifecycle_scope")!="PREP" or assignment.get("prep_stage")!="PREP-2_PERSPECTIVES":
            continue
        result_id=assignment.get("result_id")
        entry=results.get(result_id) if isinstance(result_id,str) else None
        if not isinstance(entry,dict):
            continue
        prefix="prep:framing_context:"+str(result_id)
        if not context_packet_path(run,assignment).is_file():
            errors.append(prefix+":missing")
            continue
        try:
            document=load_yaml(child(run,entry.get("path", "")))
        except QSError:
            errors.append(prefix+":result_unreadable")
            continue
        try:
            digest=document.get("context_packet_sha256") if isinstance(document,dict) else None
            if not isinstance(digest,str):
                raise QSError("result_prep_context_binding")
            resolve_context_revision(run,assignment,registered_entry["assignment_sha256"],state,digest)
        except (QSError,OSError,ValueError):
            errors.append(prefix+":hash_mismatch")
    return errors

def prep_initial_question_sources(run: Path, state: dict[str,Any]) -> dict[str,dict[str,Any]]:
    """Resolve initial PREP-2 questions from accepted immutable framing bytes.

    Before GOAL admission there is intentionally no ``input/bank.yaml``.  A
    PREP-3 worker can therefore receive only the initial questions explicitly
    assigned to it, each with its accepted framing result and hash.  This is a
    provenance projection, not a synthesized bank or a disclosure shortcut.
    """
    results=state.get("results",{})
    if not isinstance(results,dict): raise QSError("prep_initial_sources_registry")
    sources: dict[str,dict[str,Any]]={}
    for result_id,entry in sorted(results.items()):
        if not isinstance(result_id,str) or not isinstance(entry,dict): continue
        if entry.get("artifact_type")!="FRAMING" or entry.get("lifecycle_scope")!="PREP": continue
        document=load_bound_yaml(run,entry.get("path"),entry.get("content_hash"),"prep_initial_source_drift")
        if not isinstance(document,dict) or document.get("id")!=result_id:
            raise QSError("prep_initial_source_unreadable")
        if document.get("prep_stage")!="PREP-2_PERSPECTIVES": continue
        questions=document.get("initial_questions")
        if not isinstance(questions,list): raise QSError("prep_initial_questions_contract")
        for question in questions:
            if not isinstance(question,dict) or not isinstance(question.get("id"),str) or not question["id"]:
                raise QSError("prep_initial_questions_contract")
            qid=question["id"]
            if qid in sources: raise QSError("prep_initial_question_duplicate")
            sources[qid]={"result_id":result_id,"sha256":entry.get("content_hash"),"role_id":entry.get("role_id"),"question":question}
    return sources

CONTEXT_PROJECTION_VERSION="CONTEXT_DEPENDENCY_PROJECTION_V1"

def dependency_view(run: Path, entry: dict[str,Any], assigned_question_ids: set[str], expanded: bool) -> dict[str,Any]:
    """Expose a hash-bound minimum dependency view, never an implicit full body."""
    result_id=entry.get("result_id"); path=entry.get("path"); artifact_type=entry.get("artifact_type"); content_hash=entry.get("content_hash")
    if not all(isinstance(value,str) and value for value in (result_id,path,artifact_type,content_hash)):
        raise QSError("context_dependency_contract")
    document=load_bound_yaml(run,path,content_hash,"context_dependency_source_drift")
    if not isinstance(document,dict) or document.get("id")!=result_id:
        raise QSError("context_dependency_document")
    if artifact_type=="EVIDENCE":
        cache_errors=external_cache_errors(run,document,f"external_evidence:{result_id}")
        if cache_errors:
            raise QSError(cache_errors[0])
    source={"result_id":result_id,"artifact_type":artifact_type,"path":path,"sha256":content_hash,"role_id":entry.get("role_id")}
    if expanded:
        view={"kind":"FULL_LINKED_EXPANSION","document":document}
    elif artifact_type=="FRAMING":
        rows=document.get("initial_questions",[])
        if not isinstance(rows,list): raise QSError("context_dependency_framing")
        subset=[row for row in rows if isinstance(row,dict) and row.get("id") in assigned_question_ids]
        view={"kind":"ASSIGNED_INITIAL_QUESTION_SUBSET","initial_questions":subset}
    elif artifact_type in {"PRIMARY","ANSWER"}:
        addressed=document.get("addressed_q_ids",[])
        outcomes=document.get("per_question_outcomes",[])
        view={"kind":"ASSIGNED_QUESTION_CLAIMS","addressed_q_ids":[qid for qid in addressed if isinstance(qid,str) and qid in assigned_question_ids] if isinstance(addressed,list) else [],"per_question_outcomes":[row for row in outcomes if isinstance(row,dict) and row.get("q_id") in assigned_question_ids] if isinstance(outcomes,list) else []}
    else:
        view={"kind":"PROVENANCE_ONLY"}
    projection={"version":CONTEXT_PROJECTION_VERSION,"source":source,"view":view}
    return {"source":source,"projection":{**projection,"sha256":sha(canon_yaml(projection))}}


def context(args: argparse.Namespace) -> dict[str,Any]:
    """Write a bounded, hash-addressed readset; this never launches a role."""
    return locked_command(args, _context_locked)


def current_question_index(run,state,current,bank):
    """Current source and discovered question identities from the ordinary context seam."""
    questions={row["id"]:row for row in bank.get("questions",[])
               if isinstance(row,dict) and isinstance(row.get("id"),str)}
    entry,document,_=resolve_current_selection(run,state,current,"SOURCE_OUTCOMES",allow_stale=True)
    records=document.get("records",[]) if isinstance(document,dict) else []
    if not isinstance(records,list):
        raise QSError("source_outcomes_records")
    for record in records:
        if not isinstance(record,dict) or record.get("record_kind")!="DERIVED_Q" or not isinstance(record.get("q_id"),str):
            continue
        identifier=record["q_id"]
        questions[identifier]={"id":identifier,"text":record.get("materiality","derived question"),
                               "record_kind":"DERIVED_Q","parent_source_ids":record.get("parent_source_ids",[])}
    if _question_arm(run,current) is not None:
        projection=_question_projection(command_journal(run))
        if projection.ledger is not None:
            for identifier,question in projection.ledger.questions.items():
                if question.parent is None:
                    continue
                source,authority=_admitted_document(run,state,question.source.id,"EVIDENCE")
                derived=authority.get("derived_question",{})
                if (source["content_hash"]!=question.source.sha256 or derived.get("id")!=identifier
                    or derived.get("parent")!=question.parent):
                    raise QSError("question_derived_source_binding")
                questions[identifier]={"id":identifier,"text":derived["text"],"record_kind":"DERIVED_Q",
                                       "parent_source_ids":[question.parent],"source_result_id":question.source.id,
                                       "source_sha256":question.source.sha256}
    return questions,(entry,records)


def _context_locked(args: argparse.Namespace, run: Path) -> dict[str, Any]:
    assignment_path=Path(args.assignment).absolute()
    output=Path(args.output).absolute()
    if output.resolve()==assignment_path.resolve(): raise QSError("context_output_assignment_collision")
    historical=getattr(args,"historical",False)
    assignment,state,bank=_registered_assignment(run,assignment_path,historical=historical)
    if historical and assignment["role_id"]==ADVISER_ROLE:
        raise QSError("adviser_historical_context_not_exposed")
    pair=assignment["assignment_id"]+"\0"+assignment["attempt_id"]
    assignment_sha256=state["registered"][pair]["assignment_sha256"]
    registered=state["registered"][pair]
    requested=set(getattr(args,"expand",[]))
    requested_hash=getattr(args,"packet_sha256",None)
    journal=command_journal(run)
    if requested_hash is not None and (not historical or not re.fullmatch(r"[0-9a-f]{64}",requested_hash)):
        raise QSError("historical_context_hash_contract")
    if historical:
        if requested: raise QSError("historical_context_expand_forbidden")
        witness=_context_result_witness(run,assignment,assignment_sha256,state)
        revision=resolve_context_revision(run,assignment,assignment_sha256,state,requested_hash,
                                          journal=journal,result_witness=witness)
        if _context_output_pending(run,revision.path,output,revision.raw):
            _deliver_context_output(output,revision.raw)
        return context_receipt(output,revision.raw,revision.packet,historical=True)
    current=load_state(run,"current")
    arm=_question_arm(run,current)
    is_blind=assignment["artifact_type"]=="FRAMING"
    if requested and is_blind:
        raise QSError("blind_expand_forbidden")
    witness=_context_result_witness(run,assignment,assignment_sha256,state)
    chain=context_revision_chain(run,assignment,assignment_sha256,state,reissue=True,journal=journal,
                                 result_witness=witness)
    if chain and not chain[-1].path.is_file() and not requested:
        raise QSError("context_revision_incomplete")
    expanded=set(requested)
    if chain:
        expanded.update(_full_context_sources(_context_dependency_rows(chain[-1].packet)))
    def publish(packet):
        return write_context_packet(run,assignment,output,packet,registered,state,chain,requested,journal)
    armed_bank=arm is not None and arm.procedure.input_mode=="BANK_TO_PLAN"
    reviewable_plans=set()
    if arm is not None:
        if armed_bank:
            if is_blind and not chain and _bank_framing_exposed(run,state,assignment["agent_id"]):
                raise QSError("bank_framing_actor_already_exposed")
            if assignment["artifact_type"]=="REVIEW":
                _require_bank_framing(run,state,current,assignment)
        view=(state,current,bank,journal)
        packet=_question_context(run,view,arm,registered,expanded)
        if packet is not None:
            return publish(packet)
        reviewable_plans=_adviser_dependency_guard(run,view,arm,assignment,expanded)
    results=state.get("results",{})
    instruction_paths=assignment.get("role_instruction_paths",[])
    allowed_reads=assignment.get("allowed_reads",[])
    if not isinstance(instruction_paths,list) or not isinstance(allowed_reads,list) or any(not isinstance(value,str) or Path(value).is_absolute() or ".." in Path(value).parts for value in instruction_paths+allowed_reads): raise QSError("invalid_role_instruction_paths")
    is_prep_framing=is_blind and assignment.get("lifecycle_scope")=="PREP"
    handoff=(None if is_prep_framing else validate_blind_assignment(assignment,bank)) if is_blind else None
    profile=validate_prep_framing_assignment(assignment,run,state,load_state(run,"current")) if is_prep_framing else None
    workspace=root(args.root); package=Path(__file__).resolve().parent.parent
    def readset_entry(relative: str) -> dict[str,str]:
        if relative.startswith("runs/"): path=child(workspace,relative)
        elif relative.startswith("input/"): path=child(run,relative)
        elif relative.startswith("references/"): path=child(package,relative)
        else: raise QSError("readset_path_not_addressable")
        if not path.is_file(): raise QSError("readset_missing")
        raw=captured_argument(path).raw
        try: content=raw.decode("utf-8")
        except UnicodeDecodeError: raise QSError("readset_not_utf8")
        return {"path":relative,"sha256":sha(raw),"content":content}
    readset=[readset_entry(path) for path in sorted(set(instruction_paths+allowed_reads))]
    if armed_bank and not is_blind and re.fullmatch(r"P-(0[1-9]|10)",assignment["role_id"]):
        if _bank_readset_exposed(run,readset):
            _require_bank_framing(run,state,current,assignment)
    snapshot_doc=load_bound_yaml(run,"input/snapshot.yaml",state["input_snapshot"],"immutable_input_snapshot_drift")
    if assignment.get("lifecycle_scope")=="PREP" and assignment.get("prep_stage")=="PREP-1_VECTOR":
        external_bank=isinstance(snapshot_doc,dict) and snapshot_doc.get("input_mode")=="BANK_TO_PLAN" and snapshot_doc.get("bank_entry_kind")=="EXTERNAL_READY_BANK"
        if not isinstance(snapshot_doc,dict) or (snapshot_doc.get("input_mode")!="GOAL_TO_PLAN" and not external_bank): raise QSError("prep_vector_context_mode")
        goal_path=snapshot_doc.get("goal_path")
        if not isinstance(goal_path,str): raise QSError("prep_vector_context_goal")
        goal=readset_entry(goal_path)
        materials=[readset_entry(item["path"]) for item in snapshot_doc.get("materials",[]) if isinstance(item,dict) and isinstance(item.get("path"),str)]
        bank_context:Any="ABSENT"
        if external_bank:
            binding=snapshot_doc.get("external_binding")
            if not isinstance(binding,dict): raise QSError("context_external_bank_binding")
            bank_context={"entry_kind":"EXTERNAL_READY_BANK","binding":binding,"normalized_bank":bank}
        packet={"schema_version":SCHEMA,"packet_type":"PREP_VECTOR_CONTEXT","run_id":args.run_id,"input_snapshot":state["input_snapshot"],"assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],"role_id":"OP-VECTOR","artifact_type":"OPERATOR","lifecycle_scope":"PREP","prep_stage":"PREP-1_VECTOR","readset":readset,"goal_input":goal,"materials":materials,"bank":bank_context,"blind":"false"}
        return publish(packet)
    goal_id=assignment.get("goal_result_id")
    goal_doc=None
    if not isinstance(goal_id,str):
        goal_id=None
        # An OP-VECTOR-labelled applicability/administrative result is not a
        # goal handoff.  Only an accepted document with both normative bounds
        # may supply the generic Core goal packet.
        candidates=[]
        for result_id,entry in results.items():
            if not (isinstance(entry,dict) and entry.get("role_id")=="OP-VECTOR" and entry.get("artifact_type")=="OPERATOR"):
                continue
            try:
                candidate_doc=load_bound_yaml(run,entry.get("path"),entry.get("content_hash"),"context_goal_source_drift")
            except QSError:
                continue
            if isinstance(candidate_doc,dict) and candidate_doc.get("id")==result_id and "G" in candidate_doc and "NOT_G" in candidate_doc:
                candidates.append((result_id,candidate_doc))
        if len(candidates)==1:
            goal_id,goal_doc=candidates[0]
    goal_entry=results.get(goal_id)
    # BANK_TO_PLAN begins with a hash-bound approved foreign Vector rather than
    # an accepted result in this run.  Core workers still need its G/NOT_G;
    # expose it only through the immutable import binding, never a mutable
    # applicability record that happens to carry OP-VECTOR's role label.
    if not isinstance(goal_entry,dict) and isinstance(snapshot_doc,dict) and snapshot_doc.get("input_mode")=="BANK_TO_PLAN" and snapshot_doc.get("bank_entry_kind")=="APPROVED_RUN_IMPORT":
        binding=snapshot_doc.get("import_binding")
        if (goal_id is not None and (arm is None or not is_prep_framing
            or goal_id!=binding.get("source_vector_result_id"))):
            raise QSError("context_goal_missing")
        if not isinstance(binding,dict): raise QSError("context_import_goal_binding")
        vector_path=binding.get("vector_path"); vector_hash=binding.get("vector_sha256")
        if not isinstance(vector_path,str) or not isinstance(vector_hash,str): raise QSError("context_import_goal_binding")
        goal_doc=load_bound_yaml(run,vector_path,vector_hash,"context_import_goal_binding")
        if (not isinstance(goal_doc,dict) or goal_doc.get("id")!=binding.get("source_vector_result_id")
            or goal_doc.get("input_snapshot")!=binding.get("source_input_snapshot")
            or goal_doc.get("run_id")!=binding.get("source_run_id")):
            raise QSError("context_import_goal_binding")
        goal_id=goal_doc["id"]
        goal_entry={"result_id":goal_id,"path":vector_path,"content_hash":vector_hash,"role_id":"OP-VECTOR","artifact_type":"OPERATOR"}
    if not isinstance(goal_entry,dict): raise QSError("context_goal_missing")
    if is_blind and (goal_entry.get("role_id")!="OP-VECTOR" or goal_entry.get("artifact_type")!="OPERATOR"):
        raise QSError("blind_goal_not_vector")
    if goal_doc is None:
        goal_doc=load_bound_yaml(run,goal_entry.get("path"),goal_entry.get("content_hash"),"context_goal_source_drift")
    if not isinstance(goal_doc,dict) or goal_doc.get("id")!=goal_id or "G" not in goal_doc or "NOT_G" not in goal_doc:
        raise QSError("context_goal_contract")
    if is_prep_framing:
        if not isinstance(snapshot_doc,dict): raise QSError("prep_framing_materials_contract")
        selected_materials=set(prep_framing_material_paths(run,snapshot_doc,allowed_reads))
        material_rows=[item for item in readset if item["path"] in selected_materials]
        readset=[item for item in readset if item["path"] not in selected_materials]
        allowed_goal_fields=("value_recipient","scope","success_criteria","requirement_definitions","hard_constraints","changeable_assumptions","current_state","resources","authority","owner_decisions","open_research_questions")
        if armed_bank:
            allowed_goal_fields=("scope","requirement_definitions","hard_constraints","authority")
        bounded_goal={field:goal_doc[field] for field in allowed_goal_fields if field in goal_doc}
        if armed_bank and "requirement_definitions" in bounded_goal:
            bounded_goal["requirement_definitions"]=_blind_requirement_definitions(bounded_goal["requirement_definitions"])[0]
        packet={"schema_version":SCHEMA,"packet_type":"PREP_FRAMING_CONTEXT","run_id":args.run_id,"input_snapshot":state["input_snapshot"],"assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],"role_id":assignment["role_id"],"artifact_type":"FRAMING","lifecycle_scope":"PREP","prep_stage":"PREP-2_PERSPECTIVES","readset":readset,"goal":{"result_id":goal_id,"sha256":goal_entry["content_hash"],"G":goal_doc["G"],"NOT_G":goal_doc["NOT_G"],"requirements":goal_doc.get("requirements",[]),**bounded_goal},"materials":material_rows,"perspective_profile":profile,"perspective_profile_hash":assignment["perspective_profile_hash"],"vector_sha256":assignment["vector_sha256"],"approval_sha256":assignment["approval_sha256"],"bank":"ABSENT","blind":"true"}
        if armed_bank:
            _bank_blind_question_guard(bank,packet["goal"],material_rows)
        return publish(packet)
    is_prep_expansion=(assignment.get("lifecycle_scope")=="PREP" and assignment.get("prep_stage")=="PREP-3_EXPAND_NORMALIZE")
    if is_prep_expansion and not (bank.get("questions") if isinstance(bank,dict) else []):
        # GOAL_TO_PLAN has no bank until PREP-4 admission.  Source questions
        # remain in the accepted PREP-2 framing records, so project just the
        # explicitly assigned subset with source-result bindings.
        assigned=assignment.get("assigned_ids",{})
        if not isinstance(assigned,dict): raise QSError("prep_expansion_assigned_obligations")
        qids=assigned.get("questions",[]); rids=assigned.get("requirements",[])
        if (not isinstance(qids,list) or not qids or len(qids)!=len(set(qids))
            or not isinstance(rids,list) or not rids or len(rids)!=len(set(rids))):
            raise QSError("prep_expansion_assigned_obligations")
        source_questions=prep_initial_question_sources(run,state)
        if any(not isinstance(qid,str) or qid not in source_questions for qid in qids):
            raise QSError("prep_expansion_unknown_initial_question")
        definitions=goal_doc.get("requirement_definitions",[])
        known_requirements={item.get("id"):item for item in definitions if isinstance(item,dict) and isinstance(item.get("id"),str)}
        if any(not isinstance(rid,str) or rid not in known_requirements for rid in rids):
            raise QSError("prep_expansion_unknown_requirement")
        dependency_ids=assignment.get("dependency_result_ids",[])
        if not isinstance(dependency_ids,list) or any(not isinstance(item,str) for item in dependency_ids):
            raise QSError("prep_expansion_dependencies")
        required_sources={source_questions[qid]["result_id"] for qid in qids}
        if not required_sources.issubset(set(dependency_ids)):
            raise QSError("prep_expansion_source_dependency_missing")
        wanted=set(dependency_ids) | expanded
        if not all(isinstance(result_id,str) and result_id in results for result_id in wanted):
            raise QSError("context_unknown_dependency")
        by_hash={entry.get("content_hash"):result_id for result_id,entry in results.items() if isinstance(entry,dict)}
        hashed={by_hash.get(value) for value in assignment.get("dependency_hashes",[]) if isinstance(value,str)}
        contradiction_ids=set(assignment.get("contradiction_result_ids",[])) if isinstance(assignment.get("contradiction_result_ids",[]),list) else set()
        if not wanted.issubset({item for item in hashed if isinstance(item,str)}|contradiction_ids):
            raise QSError("context_expansion_not_linked")
        allowed_goal_fields=("value_recipient","scope","success_criteria","requirements","requirement_definitions","hard_constraints","changeable_assumptions","current_state","resources","authority","owner_decisions","open_research_questions")
        bounded_goal={field:goal_doc[field] for field in allowed_goal_fields if field in goal_doc}
        packet={"schema_version":SCHEMA,"packet_type":"PREP_EXPANSION_CONTEXT","run_id":args.run_id,"input_snapshot":state["input_snapshot"],"assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],"role_id":assignment["role_id"],"artifact_type":assignment["artifact_type"],"lifecycle_scope":"PREP","prep_stage":"PREP-3_EXPAND_NORMALIZE","output_path":assignment["output_path"],"readset":readset,"goal":{"result_id":goal_id,"sha256":goal_entry["content_hash"],"G":goal_doc["G"],"NOT_G":goal_doc["NOT_G"],**bounded_goal},"questions":[source_questions[qid]["question"] for qid in qids],"requirements":[known_requirements[rid] for rid in rids],"initial_question_sources":[{"question_id":qid,"source_result_id":source_questions[qid]["result_id"],"source_sha256":source_questions[qid]["sha256"],"source_role_id":source_questions[qid]["role_id"]} for qid in qids],"dependency_projection_version":CONTEXT_PROJECTION_VERSION,"accepted_dependencies":[dependency_view(run,results[result_id],set(qids),result_id in expanded) for result_id in sorted(wanted)],"bank":"ABSENT","blind":"false"}
        return publish(packet)
    if is_blind:
        # Even the goal document is a bounded handoff.  It cannot turn a
        # foreign primary result into an indirect source-bank disclosure.
        goal_text=canon_yaml({"G":goal_doc["G"],"NOT_G":goal_doc["NOT_G"]}).decode("utf-8")
        forbidden=[]
        for question in bank.get("questions",[]) if isinstance(bank.get("questions"),list) else []:
            if isinstance(question,dict): forbidden.extend(value for value in (question.get("id"),question.get("text")) if isinstance(value,str) and value)
        forbidden.extend(value for value in bank.get("requirements",[]) if isinstance(value,str) and value)
        if any(value in goal_text for value in forbidden): raise QSError("blind_goal_bank_disclosure")
    core_blind=(core_blind_vector_projection(run,state,load_state(run,"current"),snapshot_doc,assignment,goal_id,goal_entry,goal_doc)
                if is_blind and assignment.get("lifecycle_scope")=="CORE" and isinstance(snapshot_doc,dict) and snapshot_doc.get("input_mode") in NEW_MODES else None)
    goal={"result_id":goal_id,"sha256":goal_entry["content_hash"],"G":goal_doc["G"],"NOT_G":goal_doc["NOT_G"]}
    if isinstance(core_blind,dict): goal.update(core_blind["goal"])
    packet={"schema_version":SCHEMA,"packet_type":"ROLE_CONTEXT","run_id":args.run_id,"input_snapshot":state["input_snapshot"],"assignment_id":assignment["assignment_id"],"attempt_id":assignment["attempt_id"],"role_id":assignment["role_id"],"artifact_type":assignment["artifact_type"],"output_path":assignment["output_path"],**core_metadata(assignment),"readset":readset,"goal":goal,"blind":"true" if is_blind else "false"}
    if is_blind:
        packet["blind_handoff"]={"handoff_id":handoff["handoff_id"],"sha256":sha(canon_yaml(handoff)),"document":handoff}
        if isinstance(core_blind,dict): packet.update({key:value for key,value in core_blind.items() if key!="goal"})
    else:
        assigned=assignment.get("assigned_ids",{})
        if not isinstance(assigned,dict): raise QSError("context_assigned_obligations")
        qids=assigned.get("questions",[]); rids=assigned.get("requirements",[])
        try:
            known_q,_outcomes=current_question_index(run,state,load_state(run,"current"),bank)
        except QSError as exc:
            raise QSError("context_source_outcomes:"+str(exc))
        known_r={r for r in bank.get("requirements",[]) if isinstance(r,str)}
        if not isinstance(qids,list) or any(q not in known_q for q in qids) or not isinstance(rids,list) or any(r not in known_r for r in rids): raise QSError("context_assigned_obligations")
        packet["questions"]=[known_q[q] for q in qids]
        packet["requirements"]=[{"id":item} for item in rids]
        wanted=set(assignment.get("dependency_result_ids",[])) | expanded
        if not all(isinstance(result_id,str) and result_id in results for result_id in wanted): raise QSError("context_unknown_dependency")
        # PLAN history remains addressable for audit but cannot be handed to a
        # current worker as a dependency.  The projection is the sole current
        # graph selector, including after a retained repair revision.
        selected_plans=set()
        if any(results[result_id].get("artifact_type")=="PLAN" for result_id in wanted):
            try:
                selected_plans,_=plan_projection_for_run(run,state,load_state(run,"current"))
            except QSError as exc:
                features=snapshot_doc.get("methodology_features",[]) if isinstance(snapshot_doc,dict) else []
                plan_dependencies={rid for rid in wanted if results[rid].get("artifact_type")=="PLAN"}
                replacements=(_plan_revision_context_dependencies(run,state,load_state(run,"current"),assignment)
                              if (isinstance(features,list) and ARCHITECTURE_FEATURE in features
                                  and str(exc) in {"plan_projection_current_attempt","plan_projection_ambiguous_history"}) else set())
                if reviewable_plans and str(exc) in {"plan_projection_current_attempt","plan_projection_ambiguous_history"}:
                    replacements|=reviewable_plans & _plan_revision_review_candidates(run,state,current)
                if not replacements or not plan_dependencies.issubset(replacements): raise
                if any(results[rid].get("content_hash") not in assignment.get("dependency_hashes",[]) for rid in plan_dependencies):
                    raise QSError("context_revision_plan_dependency_requires_hash")
                selected_plans=replacements
                packet["plan_dependency_selection_state"]="REVISION_IN_PROGRESS"
        if any(results[result_id].get("artifact_type")=="PLAN" and result_id not in selected_plans for result_id in wanted):
            raise QSError("context_historical_plan_dependency")
        # Hash dependencies are authoritative. Named additions must be accepted
        # explicit dependencies or contradiction records; they never load history.
        by_hash={entry.get("content_hash"):result_id for result_id,entry in results.items() if isinstance(entry,dict)}
        hashed={by_hash.get(value) for value in assignment.get("dependency_hashes",[]) if isinstance(value,str)}
        contradiction_ids=set(assignment.get("contradiction_result_ids",[])) if isinstance(assignment.get("contradiction_result_ids",[]),list) else set()
        if not wanted.issubset({item for item in hashed if isinstance(item,str)}|contradiction_ids): raise QSError("context_expansion_not_linked")
        packet["dependency_projection_version"]=CONTEXT_PROJECTION_VERSION
        packet["accepted_dependencies"]=[dependency_view(run,results[result_id],set(qids),result_id in expanded) for result_id in sorted(wanted)]
    current=load_state(run,"current")
    if not is_blind and double_iceberg_enabled(snapshot_doc):
        iceberg_projection,_iceberg_history=iceberg_projection_for_run(run,state,current,allow_partial=True)
        packet["current_iceberg_views"]=iceberg_projection
        packet["current_iceberg_views_sha256"]=sha(canon_yaml(iceberg_projection))
    architecture_features=snapshot_doc.get("methodology_features",[]) if isinstance(snapshot_doc,dict) else []
    if not is_blind and isinstance(architecture_features,list) and ARCHITECTURE_FEATURE in architecture_features:
        architecture=architecture_projection_for_run(run,state,current,bank,allow_unselected=True,allow_stale=True,allow_incomplete=True)
        packet.update(architecture_checkpoint_fields(architecture))
    selections={
        "source_outcomes":resolve_current_selection(run,state,current,"SOURCE_OUTCOMES",allow_stale=True)[2],
        "final_audit":resolve_current_selection(run,state,current,"FINAL_AUDIT",allow_stale=True)[2],
    }
    # Strict YAML deliberately rejects implicit null scalars.  Before either
    # revision family exists, omit its selector instead of serializing a fake
    # current value or an unreadable ``null`` into the immutable context.
    packet["current_selections"]={key:value for key,value in selections.items() if isinstance(value,dict)}
    return publish(packet)

def resume(args: argparse.Namespace) -> dict[str,Any]:
    return locked_command(args, _resume_locked)


def _same_approved_plan(current, map_hash, projection):
    return (map_hash==current.get("approved_map_sha256")
            and projection==current.get("current_plan_projection"))


def _resume_locked(args: argparse.Namespace, run: Path) -> dict[str,Any]:
    """Recover and classify known assignments without dispatching or guessing routes."""
    # A first current-plan selection is admitted atomically with the Root map.
    # Recovery validates that supplied immutable selection before it rebuilds the
    # mutable index; without it, retained PLAN revisions fail closed.
    projection_override=None
    approved_path=getattr(args,"approved_map",None)
    if not approved_path:
        try:
            _,before_recovery,_=accepted(run,recovering=True)
            snapshot=load_yaml(run/"input/snapshot.yaml")
            before_recovery=replay_current_transitions_if_needed(run,before_recovery,snapshot)
        except QSError as exc:
            return {"status":"FAIL","errors":["index_or_input_corrupt:"+str(exc)],"scope":"STRUCTURAL_ONLY"}
        stored_hash=before_recovery.get("approved_map_sha256")
        if stored_hash is not None:
            stored=state_file(run,"approved-map")
            if not isinstance(stored_hash,str) or not stored.is_file() or sha(stored.read_bytes())!=stored_hash:
                raise QSError("approved_map_drift")
            approved_path=str(stored)
    if approved_path:
        approved_source=captured_argument(Path(approved_path))
        proposed=load_yaml(approved_source)
        if not isinstance(proposed,dict): raise QSError("approved_map_not_mapping")
        map_hash=sha(approved_source.raw)
        if getattr(args,"approved_map",None):
            before_recovery=load_state(run,"current")
            prior=before_recovery.get("approved_map_sha256")
            if prior is not None and prior!=map_hash:
                lineage=before_recovery.get("approved_map_lineage",[])
                if not isinstance(lineage,list) or any(not isinstance(item,str) for item in lineage):
                    raise QSError("approved_map_lineage_corrupt")
        projection_override=proposed.get("current_plan_graph")
        if _same_approved_plan(before_recovery,map_hash,projection_override):
            projection_override=None
    started=time.monotonic(); recovered=_recover_locked(args,run,projection_override)
    if recovered.get("status")!="RECOVERED": return {**recovered,"status":"FAIL"}
    state,current,bank=accepted(run); results=state.get("results",{})
    changed=list(getattr(args,"changed",[]) or [])
    if len(changed)!=len(set(changed)): raise QSError("duplicate_changed_result")
    for result_id in changed:
        entry=results.get(result_id)
        if not isinstance(entry,dict) or entry.get("artifact_type")!="EVIDENCE": raise QSError("changed_not_accepted_evidence")
    docs: dict[str,dict[str,Any]]={}
    for result_id,entry in results.items():
        try:
            doc=load_yaml(child(run,entry["path"]))
            if isinstance(doc,dict): docs[result_id]=doc
        except (QSError,KeyError): continue
    persisted_stale={item for item in current.get("stale_result_ids",[]) if isinstance(item,str)}
    pending_intake,withdrawn=intake_affected_results(run,state)
    stale=stale_result_closure(results,docs,sorted(persisted_stale|set(changed)|withdrawn))
    orchestration: dict[str,Any] | None=None
    if approved_path:
        approved_raw=approved_source.raw; approved_map=proposed
        completed_unknown,current_question_claims=_orchestration_proofs(run,state,current)
        gap_proofs,primary_owners,retired=_question_route_proofs(run,(state,current,bank,command_journal(run)))
        orchestration=validate_orchestration(
            bank,approved_map,{"registered":state.get("registered",{}),"current_attempts":state.get("current_attempts",{})},results,docs,changed,
            completed_unknown,current_question_claims,sorted(stale),gap_proofs,primary_owners,retired
        )
        if orchestration.get("errors"): raise QSError("orchestration_invalid:"+"|".join(str(item) for item in orchestration["errors"]))
        stale.update(item for item in orchestration.get("stale_ids",[]) if isinstance(item,str))
        latest=load_state(run,"current"); stored=state_file(run,"approved-map")
        prior=latest.get("approved_map_sha256")
        if prior is not None and (not stored.is_file() or sha(stored.read_bytes())!=prior): raise QSError("approved_map_drift")
        projection=None
        if "current_plan_graph" in approved_map:
            supplied=approved_map["current_plan_graph"]
            if _same_approved_plan(latest,map_hash,supplied):
                _,projection=plan_projection_for_run(run,state,latest,allow_stale=True)
            else:
                prospective={**latest,"stale_result_ids":sorted(stale)}
                _,projection=plan_projection_for_run(run,state,prospective,supplied)
        if prior!=map_hash:
            after={**latest,"approved_map_sha256":map_hash,**({"current_plan_projection":projection,"current_plan_projection_sha256":sha(canon_yaml(projection))} if projection is not None else {})}
            _require_plan_advice_resolution(run,state,latest,{**after,"stale_result_ids":sorted(stale)},bank,projection)
            archives={map_hash:approved_raw}
            mutation="APPROVED_MAP_SET"
            if prior is not None:
                if approved_map.get("supersedes_sha256")!=prior or not isinstance(approved_map.get("version"),str) or not approved_map["version"]:
                    raise QSError("approved_map_revision_lineage")
                lineage=latest.get("approved_map_lineage",[])
                if not isinstance(lineage,list) or any(not isinstance(item,str) for item in lineage):
                    raise QSError("approved_map_lineage_corrupt")
                archives[prior]=captured_argument(stored).raw
                after["approved_map_lineage"]=lineage+[prior]
                mutation="APPROVED_MAP_REVISE"
            staged={}
            for digest,raw in archives.items():
                archive=child(run,"state/approved-maps/"+digest+".yaml")
                if sha(raw)!=digest or archive.exists() and captured_argument(archive).raw!=raw:
                    raise QSError("approved_map_archive_drift")
                staged[archive]=(digest,raw)
            for archive,(_digest,raw) in staged.items():
                if not archive.exists(): atomic(archive,raw)
            records=[{"kind":"APPROVED_MAP_ARCHIVE","path":str(path.relative_to(run)),"sha256":digest}
                     for path,(digest,_raw) in staged.items()]
            current=commit_current_transition(run,latest,after,mutation,records,approved_map_raw=approved_raw)
    by_hash={entry.get("content_hash"):result_id for result_id,entry in results.items() if isinstance(entry,dict)}
    tasks=[]; reused:dict[str,int]={}
    for registered in state.get("registered",{}).values():
        if not isinstance(registered,dict) or not isinstance(registered.get("assignment"),dict): continue
        assignment=registered["assignment"]; result_id=assignment.get("result_id")
        dependencies=assignment.get("dependency_hashes",[]) if isinstance(assignment.get("dependency_hashes"),list) else []
        for digest in dependencies: reused[str(digest)]=reused.get(str(digest),0)+1
        launch_state=registered.get("launch_state",assignment.get("launch_state","NOT_LAUNCHED"))
        occurrence=assignment.get("logical_occurrence_id")
        if isinstance(occurrence,str) and occurrence_effect(run,occurrence)=="UNKNOWN":
            launch_state="DISPATCH_UNKNOWN"
        dependency_ids=set(assignment.get("dependency_result_ids") or [])
        references={result_id,*dependency_ids,*(by_hash.get(digest) for digest in dependencies)}
        if result_id in stale: status="STALE"
        elif terminal_attempt(run,assignment.get("assignment_id"),assignment.get("attempt_id")): status="REJECTED"
        elif registered.get("status")=="ACCEPTED": status="ACCEPTED"
        elif not set(dependencies).issubset(set(by_hash)): status="WAITING"
        elif launch_state=="DISPATCH_UNKNOWN": status="DISPATCH_UNKNOWN"
        elif references & pending_intake: status="WAITING"
        elif launch_state=="READY_TO_DISPATCH" and references & stale: status="WAITING"
        elif launch_state=="READY_TO_DISPATCH": status="READY"
        else: status="NOT_LAUNCHED"
        tasks.append({"assignment_id":assignment.get("assignment_id"),"result_id":result_id,"role_id":assignment.get("role_id"),"group_id":assignment.get("group_id","1"),"status":status})
    groups={group:[] for group in ("1","2","3")}
    for task in tasks:
        if task["status"]=="READY" and task["group_id"] in groups: groups[task["group_id"]].append(task["assignment_id"])
    if orchestration is not None:
        states={item.get("assignment_id"):item for item in orchestration.get("task_states",[]) if isinstance(item,dict)}
        for task in tasks:
            replacement=states.get(task["assignment_id"])
            if isinstance(replacement,dict) and isinstance(replacement.get("state"),str): task["status"]=replacement["state"]
        groups={str(item.get("group_id")):list(item.get("assignment_ids",[])) for item in orchestration.get("ready_groups",[]) if isinstance(item,dict)}
    if changed:
        latest=load_state(run,"current")
        prior={item for item in latest.get("stale_result_ids",[]) if isinstance(item,str)}
        after={**latest,"stale_result_ids":sorted(prior|{item for item in stale if item in results}),"status":"STALE"}
        current=commit_current_transition(run,latest,after,"STALE_SET_EXTEND",[{"kind":"STALE_DERIVATION","path":"state/assignments.yaml","sha256":sha((run/"state/assignments.yaml").read_bytes())}])
    # Before admission this is a preparation checkpoint, not a replay queue:
    # consumers receive only unfinished work. Accepted Vector/P foundations are
    # preserved in immutable history and reported separately above.
    snapshot_doc=load_yaml(run/"input/snapshot.yaml")
    if isinstance(snapshot_doc,dict) and snapshot_doc.get("input_mode") in NEW_MODES and current.get("status")=="PREP_OPEN":
        tasks=[task for task in tasks if task.get("status")!="ACCEPTED"]
    source_selection=resolve_current_selection(run,state,current,"SOURCE_OUTCOMES",allow_stale=True)[2]
    audit_selection=resolve_current_selection(run,state,current,"FINAL_AUDIT",allow_stale=True)[2]
    selected_ids={item.get("result_id") for item in (source_selection,audit_selection) if isinstance(item,dict)}
    selection_state={item:"NEEDS_RECHECK" if item in set(current.get("stale_result_ids",[])) else "CURRENT" for item in selected_ids if isinstance(item,str)}
    iceberg_projection,_iceberg_history=iceberg_projection_for_run(
        run,state,current,allow_partial=True,allow_stale_heads=True
    )
    current_stale={item for item in current.get("stale_result_ids",[]) if isinstance(item,str)}
    iceberg_state={kind:("NEEDS_RECHECK" if binding.get("result_id") in current_stale else "CURRENT") for kind,binding in iceberg_projection.items()}
    architecture=architecture_projection_for_run(run,state,current,bank,allow_unselected=True,allow_stale=True,allow_incomplete=True)
    continuation={}
    projection=current.get("current_plan_projection")
    if isinstance(projection,dict):
        continuation["current_plan"]={"version":projection["plan_version"],
            "sha256":current["current_plan_projection_sha256"],"current_revision":current["revision"]}
    marker=run/"transactions/current"/f"TX-CURRENT-{current['revision'].zfill(8)}.yaml"
    if marker.is_file():
        source=captured_argument(marker,48 * 1024**2)
        confirmed=load_yaml(source)
        if (not isinstance(confirmed,dict) or confirmed.get("status")!="COMMITTED"
            or confirmed.get("to_revision")!=current["revision"]
            or confirmed.get("after_current_sha256")!=sha(captured_argument(state_file(run,"current")).raw)):
            raise QSError("resume_confirmation_binding")
        continuation["last_confirmed_transition"]={"operation":confirmed["mutation_type"],
            "revision":confirmed["to_revision"],"path":str(marker.relative_to(run)),"sha256":sha(source.raw)}
    return {**continuation,"status":"RESUMED","input_snapshot":current["input_snapshot"],"accepted_result_ids":recovered["accepted_result_ids"],"pending_assignments":recovered["pending_assignments"],"tasks":sorted(tasks,key=lambda task:str(task["assignment_id"])),"ready_groups":[{"group_id":group,"assignment_ids":sorted(items)} for group,items in sorted(groups.items()) if items],"stale_result_ids":sorted(stale),"current_source_outcomes":source_selection,"current_final_audit":audit_selection,"selection_state":selection_state,"current_iceberg_views":iceberg_projection,"current_iceberg_views_sha256":sha(canon_yaml(iceberg_projection)),"iceberg_selection_state":iceberg_state,**architecture_checkpoint_fields(architecture),"routing_status":orchestration.get("routing_status","INVALID") if orchestration is not None else "NOT_CONFIGURED","orchestration":orchestration,"metrics":{"elapsed_ms":str(int((time.monotonic()-started)*1000)),"accepted_reused_results":str(sum(1 for count in reused.values() if count>1)),"tokens":"UNAVAILABLE","cost":"UNAVAILABLE"},"scope":"STRUCTURAL_ONLY"}

def _prep_exact_inputs(run: Path) -> dict[str,dict[str,Any]]:
    """Exact local bytes for the pure preparation validator; never external paths."""
    records: dict[str,dict[str,Any]]={}
    for path in sorted((run/"input").rglob("*")):
        if not path.is_file(): continue
        rel=str(path.relative_to(run)); source=captured_argument(path,48 * 1024**2); raw=source.raw
        row={"sha256":sha(raw),"bytes_b64":base64.b64encode(raw).decode("ascii")}
        if path.suffix in {".yaml",".yml"}:
            try:
                doc=load_yaml(source)
                if isinstance(doc,dict): row["document"]=doc
            except QSError: pass
        records[rel]=row
    return records


def stale_result_closure(
    results: dict[str,Any],
    docs: dict[str,dict[str,Any]],
    changed: list[str],
) -> set[str]:
    """Close exact execution dependencies and Iceberg source bindings."""
    graph:dict[str,set[str]]={result_id:set() for result_id in results}
    by_hash:dict[str,set[str]]={}
    for result_id,entry in results.items():
        digest=entry.get("content_hash") if isinstance(entry,dict) else None
        if isinstance(digest,str): by_hash.setdefault(digest,set()).add(result_id)
    for result_id,entry in results.items():
        dependencies=entry.get("dependency_hashes",[]) if isinstance(entry,dict) else []
        for digest in dependencies if isinstance(dependencies,list) else []:
            if isinstance(digest,str):
                for producer_id in by_hash.get(digest,set()): graph[producer_id].add(result_id)
    for result_id,doc in docs.items():
        # Only execution/provenance links invalidate their consumers.  The
        # semantic graph records relationships without creating dependencies.
        for field in ("dependency_edges","evidence_provenance_edges","execution_edges"):
            for edge in doc.get(field,[]) if isinstance(doc,dict) and isinstance(doc.get(field,[]),list) else []:
                if isinstance(edge,dict) and isinstance(edge.get("from_id"),str) and isinstance(edge.get("to_id"),str):
                    graph.setdefault(edge["from_id"],set()).add(edge["to_id"])
        if isinstance(doc,dict) and doc.get("artifact_type")=="SOURCE_OUTCOMES":
            records=doc.get("records",[])
            for record in records if isinstance(records,list) else []:
                source=record.get("answer_result_id") if isinstance(record,dict) else None
                if isinstance(source,str) and source in results:
                    graph[source].add(result_id)
        view=doc.get("iceberg_view") if isinstance(doc,dict) else None
        for binding in view.get("source_bindings",[]) if isinstance(view,dict) else []:
            if isinstance(binding,dict) and binding.get("source_kind")=="ACCEPTED_RESULT" and isinstance(binding.get("source_id"),str):
                graph.setdefault(binding["source_id"],set()).add(result_id)
    stale=set(changed); queue=list(changed)
    while queue:
        source=queue.pop(0)
        for target in sorted(graph.get(source,set())):
            if target not in stale: stale.add(target); queue.append(target)
    return stale


def _prep_documents(run: Path, state: dict[str,Any], *, strict=False) -> dict[str,dict[str,Any]]:
    docs={}
    for result_id,entry in state.get("results",{}).items():
        if isinstance(entry,dict):
            try:
                doc=load_bound_yaml(run,entry.get("path"),entry.get("content_hash"),"accepted_document_drift:"+result_id)
                if isinstance(doc,dict) and doc.get("id")==result_id:
                    docs[result_id]=doc
                elif strict:
                    raise QSError("accepted_document_binding:"+result_id)
            except QSError:
                if strict: raise
    return docs


def iceberg_projection_for_run(
    run: Path,
    state: dict[str,Any],
    current: dict[str,Any],
    *,
    allow_partial: bool,
    allow_stale_heads: bool = False,
) -> tuple[dict[str,dict[str,Any]],set[str]]:
    """Derive the two current view heads from immutable accepted history."""
    snapshot_doc=load_yaml(run/"input/snapshot.yaml")
    if not double_iceberg_enabled(snapshot_doc): return {},set()
    results=state.get("results",{})
    docs=_prep_documents(run,state)
    projection,historical,errors=current_iceberg_view_projection(
        results,docs,input_snapshot=str(current.get("input_snapshot",""))
    )
    missing={f"iceberg_view:{kind}:missing" for kind in ICEBERG_VIEW_OWNERS}
    blocking=[error for error in errors if not (allow_partial and error in missing)]
    stale={item for item in current.get("stale_result_ids",[]) if isinstance(item,str)}
    stale_heads=sorted(
        binding["result_id"] for binding in projection.values()
        if isinstance(binding,dict) and binding.get("result_id") in stale
    )
    if stale_heads and not allow_stale_heads: blocking.append("iceberg_view:stale_current:"+",".join(stale_heads))
    if not allow_partial and set(projection)!=set(ICEBERG_VIEW_OWNERS):
        blocking.append("iceberg_view:current_heads_incomplete")
    if blocking: raise QSError("iceberg_projection_invalid:"+"|".join(sorted(set(blocking))))
    return projection,historical


def _iceberg_vector_binding(run: Path, state: dict[str,Any], snapshot_doc: dict[str,Any]) -> tuple[str,str]:
    if snapshot_doc.get("input_mode")=="BANK_TO_PLAN" and snapshot_doc.get("bank_entry_kind")=="APPROVED_RUN_IMPORT":
        binding=snapshot_doc.get("import_binding")
        vector_id=binding.get("source_vector_result_id") if isinstance(binding,dict) else None
        vector_hash=binding.get("vector_sha256") if isinstance(binding,dict) else None
    else:
        approval_path=run/"input/vector-approval.yaml"
        approval=load_yaml(approval_path) if approval_path.is_file() else None
        vector_id=approval.get("vector_result_id") if isinstance(approval,dict) else None
        vector_hash=approval.get("vector_sha256") if isinstance(approval,dict) else None
        entry=state.get("results",{}).get(vector_id) if isinstance(vector_id,str) else None
        if not isinstance(entry,dict) or entry.get("content_hash")!=vector_hash:
            raise QSError("iceberg_vector_binding")
    if not isinstance(vector_id,str) or not vector_id or not isinstance(vector_hash,str) or not vector_hash:
        raise QSError("iceberg_vector_binding")
    return vector_id,vector_hash


def _validate_iceberg_candidate(
    run: Path,
    state: dict[str,Any],
    current: dict[str,Any],
    result: dict[str,Any],
    entry: dict[str,Any],
) -> None:
    snapshot_doc=load_yaml(run/"input/snapshot.yaml")
    if not double_iceberg_enabled(snapshot_doc): return
    if entry.get("artifact_type")=="FRAMING" and entry.get("role_id") in {f"P-{number:02d}" for number in range(1,11)}:
        vector_id,vector_hash=_iceberg_vector_binding(run,state,snapshot_doc)
        position_errors=validate_blind_iceberg_position(
            str(entry.get("result_id")),result,input_snapshot=str(current.get("input_snapshot")),
            vector_id=vector_id,vector_sha256=vector_hash,
        )
        if position_errors: raise QSError("iceberg_position_invalid:"+"|".join(sorted(set(position_errors))))
    owner=entry.get("role_id") in set(ICEBERG_VIEW_OWNERS.values()) and entry.get("artifact_type")=="OPERATOR"
    if "iceberg_view" in result and not owner: raise QSError("iceberg_view_owner")
    if not owner: return
    view=result.get("iceberg_view")
    expected_kind=next(kind for kind,role in ICEBERG_VIEW_OWNERS.items() if role==entry.get("role_id"))
    if not isinstance(view,dict) or view.get("view_kind")!=expected_kind:
        raise QSError("iceberg_view_required_for_owner")
    candidate={**state,"results":{**state.get("results",{}),str(entry["result_id"]):entry}}
    docs=_prep_documents(run,state); docs[str(entry["result_id"])]=result
    projection,_historical,errors=current_iceberg_view_projection(
        candidate["results"],docs,input_snapshot=str(current.get("input_snapshot",""))
    )
    allowed_missing={f"iceberg_view:{kind}:missing" for kind in ICEBERG_VIEW_OWNERS if kind!=expected_kind}
    blocking=[error for error in errors if error not in allowed_missing]
    binding=projection.get(expected_kind)
    if not isinstance(binding,dict) or binding.get("result_id")!=entry.get("result_id"):
        blocking.append("iceberg_view:candidate_not_current_head")
    if blocking: raise QSError("iceberg_view_invalid:"+"|".join(sorted(set(blocking))))


def _prep_transactions(run: Path) -> dict[str,dict[str,Any]]:
    rows={}
    for path in sorted((run/"transactions").rglob("*.yaml")):
        try:
            doc=load_yaml(path)
            if isinstance(doc,dict): rows[str(path.relative_to(run))]=doc
        except QSError: continue
    # The pure validator receives parsed before/after images as caller-owned
    # evidence, while on-disk transactions retain only the required exact
    # after bytes. Reconstruct each predecessor from admission/current lineage.
    admission_rows=[row for path,row in rows.items() if path.startswith("transactions/admissions/") and row.get("transaction_type")=="PREP_ADMISSION"]
    if len(admission_rows)==1:
        try:
            previous_raw=base64.b64decode(admission_rows[0].get("admission_next_current",{}).get("bytes_b64",""),validate=True)
            previous=yaml.load(previous_raw.decode("utf-8"),Loader=StrictLoader)
            for path in sorted(key for key,row in rows.items() if key.startswith("transactions/current/") and row.get("transaction_type")=="CURRENT_TRANSITION"):
                row=rows[path]
                after_raw=base64.b64decode(row.get("after_current_bytes_b64",""),validate=True)
                after=yaml.load(after_raw.decode("utf-8"),Loader=StrictLoader)
                if isinstance(previous,dict) and isinstance(after,dict):
                    row["__before_current"]=previous; row["__after_current"]=after
                    previous=after
        except (ValueError,UnicodeDecodeError,yaml.YAMLError,QSError,binascii.Error):
            pass
    return rows


def commit_current_transition(run: Path, current: dict[str,Any], after: dict[str,Any], mutation: str, backing_records: list[dict[str,str]], *, approved_map_raw: bytes | None = None, release_raw: bytes | None = None, frontier_command: dict[str,str] | None = None, plan_selection_request: dict[str,str] | None = None) -> dict[str,Any]:
    """Commit one finite current-image transition before replacing current.yaml."""
    if mutation not in {"APPROVED_MAP_SET","APPROVED_MAP_REVISE","PLAN_PROJECTION_SELECT","APPLICABILITY_FRONTIER_ADVANCE","STALE_SET_EXTEND","SOURCE_OUTCOMES_SELECT","FINAL_AUDIT_SELECT","RELEASE_SEAL"}: raise QSError("current_transition_type")
    before_raw=(run/"state/current.yaml").read_bytes(); before_hash=sha(before_raw)
    from_revision=current.get("revision")
    if not isinstance(from_revision,str) or not from_revision.isdigit(): raise QSError("current_transition_revision")
    to_revision=str(int(from_revision)+1); after={**after,"revision":to_revision}; after_raw=canon_yaml(after)
    tx={"schema_version":SCHEMA,"transaction_type":"CURRENT_TRANSITION","transaction_id":"TX-CURRENT-"+to_revision.zfill(8),"current_path":"state/current.yaml","run_id":current.get("run_id"),"input_snapshot":current.get("input_snapshot"),"mutation_type":mutation,"from_revision":from_revision,"to_revision":to_revision,"before_current_sha256":before_hash,"after_current_sha256":sha(after_raw),"after_current_bytes_b64":base64.b64encode(after_raw).decode("ascii"),"backing_records":backing_records,"status":"COMMITTED"}
    if isinstance(current.get("admission_transaction_id"),str): tx["admission_transaction_id"]=current["admission_transaction_id"]
    if isinstance(current.get("prep_admission_sha256"),str): tx["prep_admission_sha256"]=current["prep_admission_sha256"]
    if approved_map_raw is not None:
        if sha(approved_map_raw)!=after.get("approved_map_sha256"): raise QSError("approved_map_projection_hash")
        tx["approved_map_projection"]="STAGED_ARCHIVE"
    if release_raw is not None:
        if sha(release_raw)!=after.get("release_sha256"): raise QSError("release_projection_hash")
        tx["release_projection"]="STAGED_ARCHIVE"
    if mutation=="APPLICABILITY_FRONTIER_ADVANCE":
        if not frontier_transition_valid(current,after,frontier_command):
            raise QSError("applicability_frontier_transition")
        tx["frontier_command"]=frontier_command
    elif frontier_command is not None:
        raise QSError("applicability_frontier_transition_type")
    if mutation=="PLAN_PROJECTION_SELECT":
        if not isinstance(plan_selection_request,dict):
            raise QSError("plan_selection_request_binding")
        tx["plan_selection_request"]=plan_selection_request
    elif plan_selection_request is not None:
        raise QSError("plan_selection_transition_type")
    tx["transaction_payload_sha256"]=sha(canon_yaml(tx))
    path=run/"transactions/current"/(tx["transaction_id"]+".yaml")
    if path.exists() and path.read_bytes()!=canon_yaml(tx): raise QSError("current_transition_conflict")
    projections=[]
    if frontier_command is not None:
        projections.append((child(run,frontier_command["path"]),base64.b64decode(frontier_command["bytes_b64"],validate=True)))
    if approved_map_raw is not None: projections.append((state_file(run,"approved-map"),approved_map_raw))
    if release_raw is not None: projections.append((child(run,"release.yaml"),release_raw))
    projections.append((state_file(run,"current"),after_raw))
    publish_locked(None if path.exists() else (path,canon_yaml(tx)),projections,atomic)
    return after


def prep_state(run: Path, state: dict[str,Any] | None=None, current: dict[str,Any] | None=None, *, approval_candidate: tuple[bytes,dict[str,Any]] | None=None) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any],dict[str,Any],Any,Any,Any,list[str]]:
    state=state or load_state(run,"assignments"); current=current or load_state(run,"current")
    current={**current,"sha256":sha((run/"state/current.yaml").read_bytes())}
    snapshot=load_yaml(run/"input/snapshot.yaml")
    if not isinstance(snapshot,dict): raise QSError("prep_snapshot_not_mapping")
    inputs=_prep_exact_inputs(run); bank=load_yaml(run/"input/bank.yaml") if (run/"input/bank.yaml").is_file() else None
    approval=load_yaml(run/"input/vector-approval.yaml") if (run/"input/vector-approval.yaml").is_file() else None
    if approval_candidate is not None:
        approval_raw,approval=approval_candidate
        inputs["input/vector-approval.yaml"]={"sha256":sha(approval_raw),"bytes_b64":base64.b64encode(approval_raw).decode("ascii"),"document":approval}
    perspective=load_yaml(run/"input/perspective-map.yaml") if (run/"input/perspective-map.yaml").is_file() else None
    admission=load_yaml(run/"input/prep-admission.yaml") if (run/"input/prep-admission.yaml").is_file() else None
    docs=_prep_documents(run,state); transactions=_prep_transactions(run)
    errors=validate_prep(snapshot,current,state.get("results",{}),docs,inputs,bank,approval,perspective,admission,transactions,"recover")
    errors=sorted(set(errors+prep_framing_context_errors(run,state)))
    return snapshot,state,current,inputs,bank,approval,admission,errors


def prospective_result_projection(run,state,*,transaction=None,assignment_sha256=None):
    """Read one missing journal projection against its exact index predecessor."""
    raw_index=captured_argument(state_file(run,"assignments"),64 * 1024**2).raw
    digest=sha(raw_index)
    candidates=[]
    token=('"before_assignments_sha256": "'+digest+'"').encode("ascii")
    paths=(transaction,) if transaction is not None else sorted(child(run,"transactions").glob("*.yaml"))
    for path in paths:
        captured=captured_argument(path,48 * 1024**2)
        if token not in captured.raw: continue
        tx=load_yaml(captured)
        if (not isinstance(tx,dict) or tx.get("before_assignments_sha256")!=digest
            or tx.get("result_publication")!="JOURNAL_FIRST" or tx.get("status")!="COMMITTED"):
            continue
        if assignment_sha256 is not None:
            if tx.get("assignment_sha256")!=assignment_sha256:
                continue
            if sha(base64.b64decode(tx.get("assignment_bytes_b64",""),validate=True))!=assignment_sha256:
                raise QSError("result_projection_assignment_hash")
        entry=tx.get("entry")
        if not isinstance(entry,dict): raise QSError("result_projection_entry")
        result_id=ident(entry.get("result_id"),"result_id")
        pair=ident(entry.get("assignment_id"),"assignment_id")+"\0"+ident(entry.get("attempt_id"),"attempt_id")
        if result_id in state["results"] or pair in state["attempts"]: continue
        raw=base64.b64decode(tx.get("result_bytes_b64",""),validate=True)
        if sha(raw)!=entry.get("content_hash") or sha(raw)!=tx.get("result_sha256"):
            raise QSError("result_projection_hash")
        candidates.append((result_id,pair,entry,raw))
    if len(candidates)>1: raise QSError("result_projection_predecessor_conflict")
    if not candidates: return state,{}
    result_id,pair,entry,raw=candidates[0]
    projected={**state,"results":{**state["results"],result_id:entry},
               "attempts":{**state["attempts"],pair:{"result_id":result_id,"content_hash":entry["content_hash"]}}}
    return projected,{result_id:raw}


def accepted(run: Path, *, recovering=False) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any]]:
    state=load_state(run,"assignments"); current=load_state(run,"current"); snapshot=current["input_snapshot"]
    snap=load_bound_yaml(run,"input/snapshot.yaml",snapshot,"immutable_input_snapshot_drift")
    if not isinstance(snap,dict): raise QSError("immutable_input_snapshot_drift")
    mode=snap.get("input_mode")
    if mode in NEW_MODES:
        features=snap.get("methodology_features")
        if features is not None and features not in ([ICEBERG_FEATURE],[ICEBERG_FEATURE,ARCHITECTURE_FEATURE]): raise QSError("immutable_methodology_features_drift")
        goal=child(run,snap.get("goal_path","") if isinstance(snap.get("goal_path"),str) else "")
        if not goal.is_file() or sha(goal.read_bytes())!=snap.get("goal_sha256"): raise QSError("immutable_goal_snapshot_drift")
        for item in snap.get("materials",[]) if isinstance(snap.get("materials"),list) else []:
            if not isinstance(item,dict): raise QSError("immutable_material_snapshot_drift")
            path=child(run,item.get("path","") if isinstance(item.get("path"),str) else "")
            if not path.is_file() or sha(path.read_bytes())!=item.get("sha256"): raise QSError("immutable_material_snapshot_drift")
        if mode=="BANK_TO_PLAN":
            bank=load_bound_yaml(run,"input/bank.yaml",snap.get("bank_sha256"),"immutable_bank_snapshot_drift")
            if not isinstance(bank,dict) or snap.get("requirements_sha256")!=sha(canon_yaml(bank.get("requirements"))):
                raise QSError("immutable_bank_snapshot_drift")
            if snap.get("bank_entry_kind")=="EXTERNAL_READY_BANK":
                binding=snap.get("external_binding")
                if not isinstance(binding,dict) or binding.get("external_payload_sha256")!=sha(canon_yaml({key:value for key,value in binding.items() if key!="external_payload_sha256"})):
                    raise QSError("immutable_external_bank_binding_drift")
                for path_field,hash_field in (("original_path","original_sha256"),("normalized_path","normalized_sha256")):
                    path=child(run,binding.get(path_field,"") if isinstance(binding.get(path_field),str) else "")
                    if not path.is_file() or sha(path.read_bytes())!=binding.get(hash_field): raise QSError("immutable_external_bank_bytes_drift")
            else:
                binding=snap.get("import_binding")
                if not isinstance(binding,dict) or binding.get("import_payload_sha256")!=sha(canon_yaml({key:value for key,value in binding.items() if key!="import_payload_sha256"})):
                    raise QSError("immutable_import_binding_drift")
                for path_field,hash_field in (("vector_path","vector_sha256"),("approval_path","approval_sha256"),("statement_path","statement_sha256")):
                    path=child(run,binding.get(path_field,"") if isinstance(binding.get(path_field),str) else "")
                    if not path.is_file() or sha(path.read_bytes())!=binding.get(hash_field): raise QSError("immutable_import_bytes_drift")
        elif current.get("status") in {"CORE_ADMITTED","STALE","SEALED"}:
            bank=load_bound_yaml(run,"input/bank.yaml",current.get("bank_sha256"),"immutable_admitted_bank_drift")
            if not isinstance(bank,dict): raise QSError("immutable_admitted_bank_drift")
            admission_path=run/"input/prep-admission.yaml"
            if not admission_path.is_file() or sha(admission_path.read_bytes())!=current.get("prep_admission_sha256"):
                raise QSError("immutable_admission_drift")
        else:
            bank={"questions":[],"requirements":[]}
    else:
        bank=load_bound_yaml(run,"input/bank.yaml",snap.get("bank_sha256"),"immutable_bank_snapshot_drift")
        requirements=bank.get("requirements") if isinstance(bank,dict) else None
        if snap.get("requirements_sha256") != sha(canon_yaml(requirements)):
            raise QSError("immutable_requirements_snapshot_drift")
        raw=snap.get("original_bank",{})
        if isinstance(raw,dict) and raw.get("provided")=="true":
            raw_path=child(run,raw.get("path", ""))
            if not raw_path.is_file() or sha(raw_path.read_bytes()) != raw.get("sha256"):
                raise QSError("immutable_original_snapshot_drift")
    for result_id,entry in state.get("results",{}).items():
        if not isinstance(entry,dict): raise QSError("invalid_registry")
        if entry.get("input_snapshot") != snapshot: raise QSError("result_snapshot_binding_drift:"+str(result_id))
        data=child(run,entry.get("path",""))
        if not data.is_file() or sha(data.read_bytes())!=entry.get("content_hash"): raise QSError("immutable_result_tamper:"+str(result_id))
    if isinstance(snap,dict):
        projection,overrides=prospective_result_projection(run,state) if recovering and mode in NEW_MODES else (state,{})
        propagation=core_propagation_errors(run,projection,current,snap,overrides)
        if propagation: raise QSError("|".join(propagation))
    return state,current,bank if isinstance(bank,dict) else {"questions":[],"requirements":[]}

def resolve_current_plan_projection(state: dict[str,Any], current: dict[str,Any], docs: dict[str,dict[str,Any]], supplied: Any = None, *, allow_stale=False) -> tuple[dict[str,str],dict[str,Any]]:
    """Resolve immutable PLAN revisions into one hash-bound logical graph."""
    results=state.get("results",{})
    plans={rid:entry for rid,entry in results.items() if isinstance(entry,dict) and entry.get("artifact_type")=="PLAN"}
    if not plans: return {},{"plan_version":"","stages":[]}
    raw=current.get("current_plan_projection") if supplied is None else supplied
    if raw is None:
        grouped:dict[str,list[str]]={}
        for rid in plans:
            identity=docs.get(rid,{}).get("identity_result",{}) if isinstance(docs.get(rid),dict) else {}
            logical=identity.get("stage_id") if isinstance(identity,dict) else None
            if not isinstance(logical,str) or not logical: raise QSError("plan_projection_logical_stage")
            grouped.setdefault(logical,[]).append(rid)
        if any(len(rows)!=1 for rows in grouped.values()): raise QSError("plan_projection_required")
        mapping={rows[0]:logical for logical,rows in grouped.items()}
        versions={docs[rid].get("plan_version") for rid in mapping}
        if len(versions)!=1 or not all(isinstance(version,str) and version for version in versions): raise QSError("plan_projection_version")
        projection={"plan_version":next(iter(versions)),"stages":[{"logical_stage_id":logical,"result_id":rid,"sha256":plans[rid].get("content_hash"),"assignment_id":plans[rid].get("assignment_id"),"attempt_id":plans[rid].get("attempt_id")} for rid,logical in sorted(mapping.items())]}
        return mapping,projection
    if not isinstance(raw,dict) or set(raw)!={"plan_version","stages"} or not isinstance(raw.get("plan_version"),str) or not raw["plan_version"] or not isinstance(raw.get("stages"),list) or not raw["stages"]: raise QSError("plan_projection_contract")
    # Recovery and scoped queries may identify stale stages in the durable
    # selection. Explicit proposals still require a current proof cut.
    allow_stale_identity=allow_stale and supplied is None
    mapping:dict[str,str]={}; selected_logical:set[str]=set()
    for row in raw["stages"]:
        required={"logical_stage_id","result_id","sha256","assignment_id","attempt_id"}
        if not isinstance(row,dict) or set(row)!=required or any(not isinstance(row.get(key),str) or not row[key] for key in required): raise QSError("plan_projection_stage_contract")
        rid=row["result_id"]; entry=plans.get(rid); doc=docs.get(rid)
        if not isinstance(entry,dict) or not isinstance(doc,dict) or entry.get("content_hash")!=row["sha256"] or entry.get("assignment_id")!=row["assignment_id"] or entry.get("attempt_id")!=row["attempt_id"] or doc.get("id")!=rid or doc.get("plan_version")!=raw["plan_version"] or doc.get("identity_result",{}).get("stage_id")!=row["logical_stage_id"]: raise QSError("plan_projection_binding")
        pair=row["assignment_id"]+"\0"+row["attempt_id"]; registered=state.get("registered",{}).get(pair)
        if not isinstance(registered,dict) or registered.get("assignment",{}).get("result_id")!=rid or state.get("current_attempts",{}).get(row["assignment_id"])!=row["attempt_id"]: raise QSError("plan_projection_current_attempt")
        if rid in mapping or row["logical_stage_id"] in selected_logical: raise QSError("plan_projection_duplicate")
        stale=current.get("stale_result_ids",[])
        if isinstance(stale,list) and rid in stale and not allow_stale_identity:
            raise QSError("plan_projection_stale")
        mapping[rid]=row["logical_stage_id"]; selected_logical.add(row["logical_stage_id"])
    # All retained revisions of a selected logical stage must form one exact
    # predecessor chain ending at the selected immutable result; alternatives
    # cannot be hidden by choosing an arbitrary branch.
    for logical in selected_logical:
        candidates={rid for rid in plans if isinstance(docs.get(rid),dict) and docs[rid].get("identity_result",{}).get("stage_id")==logical}
        selected=next(rid for rid,value in mapping.items() if value==logical); chain=set(); cursor=selected
        while cursor is not None:
            if cursor in chain: raise QSError("plan_projection_supersession_cycle")
            chain.add(cursor); previous=docs[cursor].get("supersedes")
            if previous is None: cursor=None
            elif not isinstance(previous,str) or previous not in candidates: raise QSError("plan_projection_supersedes")
            else: cursor=previous
        if candidates!=chain: raise QSError("plan_projection_ambiguous_history")
    return mapping,raw

def plan_projection_for_run(run: Path, state: dict[str,Any], current: dict[str,Any], supplied: Any = None, *, documents=None, allow_stale=False) -> tuple[dict[str,str],dict[str,Any]]:
    if documents is None:
        documents={}
        for rid,entry in state.get("results",{}).items():
            if isinstance(entry,dict) and entry.get("artifact_type")=="PLAN":
                doc=load_bound_yaml(run,entry.get("path"),entry.get("content_hash"),"plan_projection_source_drift:"+rid)
                if isinstance(doc,dict): documents[rid]=doc
    mapping,projection=resolve_current_plan_projection(state,current,documents,supplied,allow_stale=allow_stale)
    expected=current.get("current_plan_projection_sha256")
    if supplied is None and current.get("current_plan_projection") is not None and (not isinstance(expected,str) or expected!=sha(canon_yaml(projection))): raise QSError("plan_projection_hash_drift")
    return mapping,projection


def plan_selection_proof(run, state, current, bank, supplied):
    if current.get("status") not in {"INITIALIZED","CORE_ADMITTED","STALE"}:
        raise QSError("plan_selection_current_status")
    docs=_prep_documents(run,state,strict=True)
    mapping,projection=plan_projection_for_run(run,state,current,supplied,documents=docs)
    roots=[rid for rid,logical in mapping.items() if logical=="S-000"]
    if len(roots)!=1:
        raise QSError("plan_selection_root")
    root_id=roots[0]
    root_doc=docs[root_id]
    dag=root_doc.get("local_execution_graph",{}).get("stage_dag")
    if (not isinstance(dag,list) or any(not isinstance(row,dict) or not isinstance(row.get("stage_id"),str) for row in dag)
        or len(dag)!=len(mapping) or {row["stage_id"] for row in dag}!=set(mapping.values())):
        raise QSError("plan_selection_stage_membership")
    logical_results={logical:rid for rid,logical in mapping.items()}
    for row in dag:
        rid=logical_results[row["stage_id"]]
        graph=docs[rid].get("local_execution_graph",{})
        if row.get("result_id")!=rid or row.get("path")!=state["results"][rid].get("path"):
            raise QSError("plan_selection_stage_result_binding:"+row["stage_id"])
        for field,collection in (("task_refs","tasks"),("check_refs","acceptance_exit")):
            selected=row.get(field)
            owned=docs[rid].get(collection)
            if (not isinstance(selected,list) or not isinstance(owned,list)
                or not all(isinstance(ref,str) for ref in selected)
                or not all(isinstance(item,dict) and isinstance(item.get("id"),str) for item in owned)
                or len(selected)!=len(set(selected)) or set(selected)!={item["id"] for item in owned}):
                raise QSError("plan_selection_stage_dag_binding:"+row["stage_id"]+":"+field)
        for field in ("entry","exit","predecessors","next_stages"):
            if row.get(field)!=graph.get(field):
                raise QSError("plan_selection_stage_dag_binding:"+row["stage_id"]+":"+field)
    result=architecture_projection_for_run(run,state,current,bank,supplied_plan_projection=projection,documents=docs)
    reference=result.get("architecture_ref")
    if not isinstance(reference,dict) or reference!=root_doc.get("architecture_ref"):
        raise QSError("plan_selection_architecture_reference")
    errors=[]
    _validate_stages(run,bank,state["results"],docs,_immutable_input_hashes(run,bank,sha),errors,mapping)
    if errors:
        raise QSError("plan_selection_global:"+"|".join(sorted(set(errors))))
    backing=[]
    for rid in sorted({reference["synthesis_id"],*mapping}):
        entry=state["results"][rid]
        assignment_id,attempt_id=entry["assignment_id"],entry["attempt_id"]
        if (state.get("current_attempts",{}).get(assignment_id)!=attempt_id
            or _immutable_accepted_attempt_result(run,state,assignment_id,attempt_id)!=rid):
            raise QSError("plan_selection_accepted_attempt:"+rid)
        if sha(captured_argument(child(run,entry["path"])).raw)!=entry["content_hash"]:
            raise QSError("plan_selection_immutable_result:"+rid)
        backing.append({"kind":entry["artifact_type"],"path":entry["path"],"sha256":entry["content_hash"]})
    root_entry=state["results"][root_id]
    assignment=state["registered"][root_entry["assignment_id"]+"\0"+root_entry["attempt_id"]]
    if assignment["assignment"].get("selection_authority")!="ROOT":
        raise QSError("plan_selection_root_authority")
    authority={"result_id":root_id,"assignment_sha256":assignment["assignment_sha256"]}
    return projection,reference,backing,authority


def select_plan(args):
    return locked_command(args,_select_plan_locked)


def plan_selection_receipt(current):
    return {"status":"PLAN_SELECTED","revision":current["revision"],
            "current_plan_projection_sha256":current["current_plan_projection_sha256"],"scope":"STRUCTURAL_ONLY"}


def plan_selection_replay_receipt(run,state,request,raw):
    request_sha=sha(raw)
    matches=[]
    for path in sorted(child(run,"transactions/current").glob("*.yaml")):
        tx=load_yaml(path)
        marker=tx.get("plan_selection_request") if isinstance(tx,dict) else None
        if isinstance(marker,dict) and marker.get("request_sha256")==request_sha:
            matches.append((tx,marker))
    if not matches:
        return None
    if len(matches)!=1:
        raise QSError("plan_selection_replay_cardinality")
    tx,marker=matches[0]
    authority=marker.get("authority")
    result_id=authority.get("result_id") if isinstance(authority,dict) else None
    entry=state["results"].get(result_id)
    if not isinstance(entry,dict) or entry.get("artifact_type")!="PLAN":
        raise QSError("plan_selection_replay_authority")
    assignment_id,attempt_id=entry["assignment_id"],entry["attempt_id"]
    registered=state["registered"].get(assignment_id+"\0"+attempt_id)
    if (not isinstance(registered,dict) or registered.get("assignment_sha256")!=request["authority_sha256"]
        or authority.get("assignment_sha256")!=request["authority_sha256"]
        or registered.get("assignment",{}).get("selection_authority")!="ROOT"
        or _immutable_accepted_attempt_result(run,state,assignment_id,attempt_id)!=result_id
        or tx.get("mutation_type")!="PLAN_PROJECTION_SELECT"
        or tx.get("from_revision")!=request["expected_revision"]
        or tx.get("before_current_sha256")!=request["expected_head_sha256"]):
        raise QSError("plan_selection_replay_authority")
    after=base64.b64decode(tx["after_current_bytes_b64"],validate=True)
    image=load_yaml(CapturedInput("plan selection committed image",after))
    projection=image.get("current_plan_projection") if isinstance(image,dict) else None
    supplied=request["current_plan_graph"]
    if (not isinstance(projection,dict) or not isinstance(supplied,dict)
        or set(supplied)!={"plan_version","stages"} or not isinstance(supplied["stages"],list)
        or not isinstance(projection.get("stages"),list)
        or projection.get("plan_version")!=request["plan_version"]
        or supplied["plan_version"]!=request["plan_version"]
        or sorted(canon_yaml(row) for row in projection["stages"])!=sorted(canon_yaml(row) for row in supplied["stages"])
        or image.get("current_plan_projection_sha256")!=sha(canon_yaml(projection))):
        raise QSError("plan_selection_replay_subject")
    _,root_stage=_admitted_document(run,state,result_id,"PLAN")
    if root_stage.get("architecture_ref")!=request["architecture_ref"]:
        raise QSError("plan_selection_replay_subject")
    return plan_selection_receipt(image)


def _select_plan_locked(args,run):
    captured=captured_argument(Path(args.selection),256 * 1024)
    request=load_yaml(captured)
    required={"run_id","input_snapshot","expected_revision","expected_head_sha256",
              "architecture_ref","plan_version","current_plan_graph","actor","authority_sha256"}
    if not isinstance(request,dict) or set(request)!=required:
        raise QSError("plan_selection_envelope")
    state,current,bank=accepted(run,recovering=True)
    snapshot=load_bound_yaml(run,"input/snapshot.yaml",current["input_snapshot"],"immutable_input_snapshot_drift")
    current=replay_current_transitions_if_needed(run,current,snapshot)
    if request["run_id"]!=run.name or request["input_snapshot"]!=current["input_snapshot"]:
        raise QSError("plan_selection_run_snapshot")
    if request["actor"]!="ROOT":
        raise QSError("plan_selection_actor")
    replay=plan_selection_replay_receipt(run,state,request,captured.raw)
    if replay is not None:
        return replay
    projection,reference,backing,authority=plan_selection_proof(run,state,current,bank,request["current_plan_graph"])
    if request["authority_sha256"]!=authority["assignment_sha256"]:
        raise QSError("plan_selection_authority_hash")
    if request["architecture_ref"]!=reference or request["plan_version"]!=projection["plan_version"]:
        raise QSError("plan_selection_expected_subject")
    digest=sha(canon_yaml(projection))
    if (request["expected_revision"]!=current.get("revision")
        or request["expected_head_sha256"]!=sha(captured_argument(state_file(run,"current")).raw)):
        raise QSError("plan_selection_revision_conflict")
    if current.get("current_plan_projection")!=projection:
        after={**current,"current_plan_projection":projection,"current_plan_projection_sha256":digest}
        _require_plan_advice_resolution(run,state,current,after,bank,projection)
        current=commit_current_transition(run,current,after,"PLAN_PROJECTION_SELECT",backing,
                                          plan_selection_request={"request_sha256":sha(captured.raw),"authority":authority})
    elif current.get("current_plan_projection_sha256")!=digest:
        raise QSError("plan_projection_hash_drift")
    return plan_selection_receipt(current)


def _plan_revision_context_gap(run: Path, state: dict[str,Any], current: dict[str,Any]) -> bool:
    """Prove that a valid selected history is only awaiting a replacement map.

    This proof supplies no current graph.  It is used only for a checkpoint
    after the strict resolver rejects a superseded selection.
    """
    raw=current.get("current_plan_projection")
    if not isinstance(raw,dict) or current.get("current_plan_projection_sha256")!=sha(canon_yaml(raw)):
        return False
    rows=raw.get("stages"); results=state.get("results",{}); registered=state.get("registered",{}); attempts=state.get("current_attempts",{})
    if (not isinstance(rows,list) or not rows or not isinstance(results,dict)
        or not isinstance(registered,dict) or not isinstance(attempts,dict)):
        return False
    plans={rid:entry for rid,entry in results.items() if isinstance(entry,dict) and entry.get("artifact_type")=="PLAN"}
    docs={rid:load_yaml(child(run,entry.get("path",""))) for rid,entry in plans.items()}

    def bound_result(rid: str) -> bool:
        entry=plans.get(rid); doc=docs.get(rid)
        if not isinstance(entry,dict) or not isinstance(doc,dict): return False
        aid=entry.get("assignment_id"); attempt=entry.get("attempt_id")
        if not isinstance(aid,str) or not isinstance(attempt,str): return False
        record=registered.get(aid+"\0"+attempt)
        assignment=record.get("assignment") if isinstance(record,dict) else None
        if not isinstance(assignment,dict) or record.get("status")!="ACCEPTED": return False
        pairs=(("assignment_id","assignment_id"),("attempt_id","attempt_id"),("result_id","result_id"),
               ("artifact_type","artifact_type"),("role_id","role_id"),("agent_id","agent_id"),
               ("input_snapshot","input_snapshot"),("output_path","path"))
        return (all(assignment.get(left)==entry.get(right) for left,right in pairs)
                and entry.get("result_id")==rid and entry.get("role_id")=="PLAN-AUTHOR"
                and doc.get("id")==rid and doc.get("artifact_type")=="PLAN"
                and doc.get("author_role")==entry.get("role_id")
                and all(doc.get(key)==entry.get(key) for key in ("assignment_id","attempt_id","agent_id","input_snapshot"))
                and sha(child(run,entry["path"]).read_bytes())==entry.get("content_hash"))

    def retry_reaches(entry: dict[str,Any], candidates: set[str]) -> bool:
        aid=entry["assignment_id"]; base=entry["attempt_id"]; cursor=attempts.get(aid); seen=set()
        while isinstance(cursor,str) and cursor and cursor not in seen:
            seen.add(cursor); record=registered.get(aid+"\0"+cursor)
            assignment=record.get("assignment") if isinstance(record,dict) else None
            if (not isinstance(assignment,dict) or assignment.get("assignment_id")!=aid
                or assignment.get("attempt_id")!=cursor or assignment.get("artifact_type")!="PLAN"
                or assignment.get("role_id")!="PLAN-AUTHOR" or assignment.get("input_snapshot")!=entry.get("input_snapshot")):
                return False
            rid=assignment.get("result_id")
            if record.get("status")=="ACCEPTED":
                if not isinstance(rid,str) or rid not in candidates or not bound_result(rid): return False
            elif record.get("status")=="ASSIGNED" and cursor==attempts.get(aid):
                path=assignment.get("output_path")
                if (not isinstance(rid,str) or not rid or rid in results or not isinstance(path,str)
                    or not path.startswith("plan/") or any(path==item.get("path") for item in results.values() if isinstance(item,dict))):
                    return False
                child(run,path)  # retain the ordinary traversal/absolute-path guard
            else:
                return False
            if cursor==base: return record.get("status")=="ACCEPTED" and rid==entry["result_id"]
            cursor=assignment.get("supersedes_attempt_id")
        return False

    removed=set(); historical_attempts=dict(attempts); changed=False
    for row in rows:
        if not isinstance(row,dict): return False
        selected=row.get("result_id"); logical=row.get("logical_stage_id")
        if not isinstance(selected,str) or not isinstance(logical,str) or selected not in plans: return False
        candidates={rid for rid,doc in docs.items() if isinstance(doc,dict) and isinstance(doc.get("identity_result"),dict) and doc["identity_result"].get("stage_id")==logical}
        if selected not in candidates or any(not bound_result(rid) for rid in candidates): return False
        history=set(); cursor=selected
        while cursor is not None:
            if not isinstance(cursor,str) or cursor not in candidates or cursor in history: return False
            history.add(cursor); cursor=docs[cursor].get("supersedes")
        descendants=candidates-history; children={}
        for rid in descendants:
            previous=docs[rid].get("supersedes")
            if not isinstance(previous,str) or previous not in descendants|{selected} or previous in children: return False
            children[previous]=rid
        reached=set(); cursor=selected
        while cursor in children:
            cursor=children[cursor]
            if cursor in reached: return False
            reached.add(cursor)
        if reached!=descendants or any(not retry_reaches(plans[rid],candidates) for rid in candidates): return False
        entry=plans[selected]; aid=entry["assignment_id"]
        changed=changed or bool(descendants) or attempts.get(aid)!=entry["attempt_id"]
        historical_attempts[aid]=entry["attempt_id"]; removed.update(descendants)
    if not changed: return False
    historical={**state,"results":{rid:entry for rid,entry in results.items() if rid not in removed},"current_attempts":historical_attempts}
    # Reuse every strict shape/hash/binding/staleness/history check for the
    # original selection; only proved newer revisions and retries are projected.
    plan_projection_for_run(run,historical,current)
    return True


def _plan_revision_context_dependencies(run: Path, state: dict[str,Any], current: dict[str,Any], assignment: dict[str,Any]) -> set[str]:
    """Permit an anchored planner retry to inspect exact accepted repair heads."""
    if assignment.get("artifact_type")!="PLAN" or assignment.get("role_id")!="PLAN-AUTHOR": return set()
    raw=current.get("current_plan_projection")
    rows=raw.get("stages",[]) if isinstance(raw,dict) else []
    selected=[row for row in rows if isinstance(row,dict)] if isinstance(rows,list) else []
    own=[row for row in selected if row.get("assignment_id")==assignment.get("assignment_id")]
    if (len(own)!=1 or assignment.get("attempt_id")==own[0].get("attempt_id")
        or state.get("current_attempts",{}).get(assignment.get("assignment_id"))!=assignment.get("attempt_id")
        or not _plan_revision_context_gap(run,state,current)):
        return set()
    return _plan_revision_context_candidates(run,state,current,selected)


def _plan_revision_review_candidates(run,state,current):
    if not _plan_revision_context_gap(run,state,current):
        return set()
    return _plan_revision_context_candidates(run,state,current,current["current_plan_projection"]["stages"])


def _plan_revision_context_candidates(run,state,current,selected):
    plans={rid:load_yaml(child(run,entry.get("path",""))) for rid,entry in state.get("results",{}).items()
           if isinstance(entry,dict) and entry.get("artifact_type")=="PLAN"}
    logical={row.get("logical_stage_id") for row in selected}; old={row.get("result_id") for row in selected}
    superseded={doc.get("supersedes") for doc in plans.values() if isinstance(doc,dict) and isinstance(doc.get("supersedes"),str)}
    stale=current.get("stale_result_ids",[])
    return {rid for rid,doc in plans.items() if (isinstance(doc,dict) and isinstance(doc.get("identity_result"),dict)
            and doc["identity_result"].get("stage_id") in logical and rid not in old|superseded
            and (not isinstance(stale,list) or rid not in stale))}


def capture_selected_branch_proofs(run,state,stage_ids,documents):
    for stage_id in stage_ids:
        stage=documents.get(stage_id,{})
        contracts=stage.get("inputs_contracts",{}) if isinstance(stage,dict) else {}
        outputs=contracts.get("outputs",[]) if isinstance(contracts,dict) else []
        for output in outputs if isinstance(outputs,list) else []:
            if not isinstance(output,dict) or output.get("availability")!="SELECTED_BRANCH":
                continue
            evidence_id=output.get("branch_selection_evidence_id")
            if not isinstance(evidence_id,str) or evidence_id not in state["results"]:
                continue
            _,evidence=_admitted_document(run,state,evidence_id,"EVIDENCE",verify_cache=True)
            documents[evidence_id]=evidence
            review_id=evidence.get("branch_selection_review_id")
            if isinstance(review_id,str) and review_id in state["results"]:
                _,review=_admitted_document(run,state,review_id,"REVIEW")
                documents[review_id]=review


def architecture_projection_for_run(
    run: Path,
    state: dict[str,Any],
    current: dict[str,Any],
    bank: dict[str,Any],
    *,
    supplied_plan_projection: Any = None,
    allow_unselected: bool = False,
    allow_stale: bool = False,
    allow_incomplete: bool = False,
    documents: dict[str,dict[str,Any]] | None = None,
) -> dict[str,Any]:
    """Resolve the graph through the current plan, without a second head index."""
    snapshot=load_bound_yaml(run,"input/snapshot.yaml",current["input_snapshot"],"immutable_input_snapshot_drift")
    features=snapshot.get("methodology_features",[]) if isinstance(snapshot,dict) else []
    if not isinstance(features,list) or ARCHITECTURE_FEATURE not in features:
        return {"selection_status":"LEGACY_QUALIFIED","errors":[]}
    if documents is None:
        documents=_prep_documents(run,state,strict=True)
    try:
        mapping,projection=plan_projection_for_run(run,state,current,supplied_plan_projection,documents=documents,allow_stale=allow_stale)
    except QSError as exc:
        # A retained revision can be accepted before the remaining stages of
        # its explicit current map exist.  Planner contexts may report that
        # missing selection, but must not infer a graph from either revision.
        if allow_incomplete and str(exc)=="plan_projection_required":
            return {"selection_status":"INCOMPLETE","errors":["architecture:plan_projection_required"]}
        if (allow_incomplete and supplied_plan_projection is None
            and str(exc) in {"plan_projection_current_attempt","plan_projection_ambiguous_history"}
            and _plan_revision_context_gap(run,state,current)):
            return {"selection_status":"INCOMPLETE","errors":["architecture:"+str(exc)]}
        raise
    pending,withdrawn=intake_affected_results(run,state,documents=documents)
    stale=set(current.get("stale_result_ids",[]))|pending|withdrawn
    capture_selected_branch_proofs(run,state,mapping,documents)
    resolved=resolve_architecture_model(
        run,bank,state.get("results",{}),documents,mapping,
        current={**current,"stale_result_ids":sorted(stale)},allow_unselected=allow_unselected,allow_stale=allow_stale,
        load_yaml=load_yaml,sha=sha,snapshot=snapshot,
    )
    errors=list(resolved.get("errors",[]))
    reference=resolved.get("architecture_ref")
    if isinstance(reference,dict):
        result_id=reference.get("synthesis_id")
        entry=state.get("results",{}).get(result_id)
        if isinstance(entry,dict):
            assignment_id=entry.get("assignment_id"); attempt_id=entry.get("attempt_id")
            pair=str(assignment_id)+"\0"+str(attempt_id)
            registered=state.get("registered",{}).get(pair)
            assignment=registered.get("assignment") if isinstance(registered,dict) else None
            if (state.get("current_attempts",{}).get(assignment_id)!=attempt_id
                or not isinstance(assignment,dict)
                or assignment.get("result_id")!=result_id):
                errors.append("architecture:current_synthesis_attempt")
        else:
            errors.append("architecture:synthesis_not_accepted")
    errors=sorted(set(errors))
    if errors and not allow_incomplete:
        raise QSError("architecture_projection_invalid:"+"|".join(errors))
    # A planner may need a context while its future S/T/C bindings are still
    # being committed.  Checkpoints expose those addressed gaps and retain the
    # selected immutable reference; they never promote the partial graph to a
    # valid model or derive dispatch decisions from it.
    return {**resolved,"errors":errors,
            **({"selection_status":"INCOMPLETE"} if errors else {}),
            "current_plan_projection_sha256":sha(canon_yaml(projection))}


# The accepted run supplies the typed objects. The query vocabulary is a
# controller contract and does not depend on the author declaring eight rows.
GRAPH_DUTIES={
    "KNOWLEDGE": ({"E","Q","H"}, {"semantic_edges","evidence_provenance_edges"}),
    "DECISIONS": ({"D"}, {"semantic_edges","evidence_provenance_edges"}),
    "PROBLEMS": ({"R","H"}, {"semantic_edges","execution_edges"}),
    "COVERAGE": ({"R","Q","G"}, {"semantic_edges","execution_edges"}),
    "EVIDENCE": ({"E","D","C","REVIEW"}, {"evidence_provenance_edges","semantic_edges"}),
    "ALTERNATIVES": ({"D","H","Q"}, {"semantic_edges","evidence_provenance_edges"}),
    "EXECUTION": ({"G","R","T","C","DATA","S"}, {"execution_edges","architecture_edges"}),
    "EVOLUTION": ({"E","D","R","T","C"}, {"semantic_edges","execution_edges","architecture_edges"}),
}


def graph_source(entry, pointer):
    return {"path":entry["path"],"sha256":entry["content_hash"],"pointer":pointer}


def graph_corrective_paths(address, discovered, edges):
    """Find authorized constructive continuations from the duty's own anchors."""
    paths={address:[]}
    queue=[address]
    linked={}
    for edge in discovered:
        linked.setdefault(edge["from_id"],[]).append((edge["to_id"],edge))
        linked.setdefault(edge["to_id"],[]).append((edge["from_id"],edge))
    for node in queue:
        for other,edge in linked.get(node,[]):
            if other not in paths:
                paths[other]=[*paths[node],edge]
                queue.append(other)
    forward={}
    for edge in edges:
        forward.setdefault(edge["from_id"],[]).append(edge)
    for node in queue:
        for edge in forward.get(node,[]):
            other=edge["to_id"]
            if other not in paths:
                paths[other]=[*paths[node],edge]
                queue.append(other)
    return paths


def _graph_query_locked(args,run):
    assignment,state,bank=_registered_assignment(run,Path(args.assignment))
    if assignment["role_id"]==ADVISER_ROLE:
        raise QSError("adviser_graph_requires_current_context")
    current=load_state(run,"current")
    if assignment["artifact_type"]!="FRAMING" and _question_arm(run,current) is not None:
        _require_bank_framing(run,state,current,assignment)
    response=_graph_query_projection(run,assignment,state,bank,args.duty,args.ref)
    journal=command_journal(run)
    projection=_question_projection(journal)
    if projection.ledger is not None:
        visible={args.ref}|{endpoint for edge in response["edges"] for endpoint in (edge["from_id"],edge["to_id"])}
        response["question_coverage"]=_scoped_question_coverage(projection,visible)
        response["adviser_blockers"]=list(_adviser_freeze_blockers(
            run,(state,current,bank,journal),"CORE",visible))
    return response


def _graph_query_projection(run,assignment,state,bank,duty,query_reference,*,current=None):
    if assignment.get("artifact_type")=="FRAMING":
        raise QSError("graph_query_blind_exposure")
    current=load_state(run,"current") if current is None else current
    selected=architecture_projection_for_run(run,state,current,bank,allow_stale=True,allow_incomplete=True)
    report=selected.get("report",{})
    withdrawn_branches=report.get("withdrawn_branch_errors",{})
    errors=selected.get("errors",[])
    if set(errors)-set(withdrawn_branches.values()):
        raise QSError("architecture_projection_invalid:"+"|".join(errors))
    reference=selected.get("architecture_ref")
    graph=selected.get("graph")
    if not isinstance(reference,dict) or not isinstance(graph,dict):
        raise QSError("graph_query_current_selection")
    synthesis_entry,synthesis=_admitted_document(run,state,reference["synthesis_id"],"SYNTHESIS")
    address=ident(query_reference,"graph_ref")
    exposed=assignment.get("assigned_ids",{})
    if not isinstance(exposed,dict):
        raise QSError("graph_query_assignment_scope")
    scopes={"requirements":"R","questions":"Q","decisions":"D","evidence":"E"}
    if any(not isinstance(exposed.get(field,[]),list) for field in scopes):
        raise QSError("graph_query_assignment_scope")
    visible={item:kind for field,kind in scopes.items()
             for item in exposed.get(field,[]) if isinstance(item,str)}
    questions,(outcome_entry,outcomes)=current_question_index(run,state,current,bank)
    requirements={item if isinstance(item,str) else item.get("id")
                  for item in bank.get("requirements",[]) if isinstance(item,(str,dict))}
    visible={identifier:kind for identifier,kind in visible.items()
             if ((kind=="Q" and identifier in questions)
                 or (kind=="R" and identifier in requirements)
                 or (kind=="E" and state["results"].get(identifier,{}).get("artifact_type")=="EVIDENCE")
                 or (kind=="D" and state["results"].get(identifier,{}).get("artifact_type")=="DECISION"))}
    artifact_kinds={"EVIDENCE":"E","DECISION":"D","HYPOTHESIS":"H","REVIEW":"REVIEW","PLAN":"S"}
    goal=assignment.get("goal_result_id")
    if isinstance(goal,str) and state["results"].get(goal,{}).get("role_id")=="OP-VECTOR":
        _admitted_document(run,state,goal,"OPERATOR")
        visible[goal]="G"
    selected_stages,_=plan_projection_for_run(run,state,current,allow_stale=True)
    plan_owners={}
    plan_nodes={}
    exposed_reviews={}
    for identifier in assignment.get("dependency_result_ids") or []:
        entry=state["results"].get(identifier)
        if isinstance(entry,dict) and entry.get("artifact_type") in artifact_kinds:
            if entry["artifact_type"]!="PLAN":
                visible[identifier]=artifact_kinds[entry["artifact_type"]]
                if entry["artifact_type"]=="REVIEW":
                    _,exposed_reviews[identifier]=_admitted_document(run,state,identifier,"REVIEW")
            elif identifier in selected_stages:
                stage_id=selected_stages[identifier]
                visible[stage_id]="S"
                plan_owners[stage_id]=identifier
                _,stage=_admitted_document(run,state,identifier,"PLAN")
                for field,kind in (("tasks","T"),("acceptance_exit","C")):
                    for index,row in enumerate(stage.get(field,[])):
                        if isinstance(row,dict) and isinstance(row.get("id"),str):
                            visible[row["id"]]=kind
                            plan_owners[row["id"]]=identifier
                            plan_nodes[row["id"]]=(row,entry,f"/{field}/{index}")
    kinds,relations=GRAPH_DUTIES[duty]
    if "architecture_edges" in relations or withdrawn_branches:
        implementation=derive_architecture_views(graph)["implementation"]["detail"]
        nodes={node["id"]:(node.get("existing_id",node["id"]),node["kind"])
               for node in implementation["nodes"]}
        for edge in implementation["edges"] if "architecture_edges" in relations else []:
            left,left_kind=nodes[edge["from_id"]]
            right,right_kind=nodes[edge["to_id"]]
            if edge["kind"]=="PRODUCES" and right_kind=="DATA" and visible.get(left)=="T":
                visible.setdefault(right,"DATA")
            if edge["kind"]=="CONSUMES" and left_kind=="DATA" and right_kind in {"T","C"} and visible.get(right)==right_kind:
                visible.setdefault(left,"DATA")
    if visible.get(address) not in kinds:
        raise QSError("graph_query_address_not_assigned_or_wrong_kind")
    pending,withdrawn=intake_affected_results(run,state)
    stale=set(current.get("stale_result_ids",[]))|withdrawn
    indexed={}
    corrective_edges=[]
    for field in sorted((relations|{"execution_edges"})-{"architecture_edges"}):
        edges=synthesis.get(field,[])
        if not isinstance(edges,list):
            raise QSError("graph_query_edge_contract")
        for index,edge in enumerate(edges):
            if not isinstance(edge,dict):
                raise QSError("graph_query_edge_contract")
            left,right=edge.get("from_id"),edge.get("to_id")
            if not isinstance(left,str) or not isinstance(right,str):
                raise QSError("graph_query_endpoint_contract")
            if (visible.get(left)!=edge.get("from_type") or visible.get(right)!=edge.get("to_type")
                or left not in visible or right not in visible):
                continue
            key=(field,str(index))
            record={"relation":field,"edge_id":edge.get("id"),"index":str(index),
                    "from_id":left,"to_id":right,
                    "source":graph_source(synthesis_entry,f"/{field}/{index}")}
            if field=="execution_edges":
                corrective_edges.append(record)
            if field not in relations:
                continue
            if field=="semantic_edges":
                record["semantic_relation"]=edge.get("relation")
                record["knowledge_status"]=edge.get("knowledge_status")
                discriminator=edge.get("distinguishing_check_id")
                if isinstance(discriminator,str) and discriminator in visible:
                    record["distinguishing_check_id"]=discriminator
                    if visible[discriminator]=="C":
                        corrective_edges.append({"relation":"DISTINGUISHES","from_id":left,"to_id":discriminator,
                            "source":graph_source(synthesis_entry,f"/{field}/{index}/distinguishing_check_id")})
                evidence=edge.get("evidence_ids",[])
                if isinstance(evidence,list):
                    record["evidence_ids"]=[item for item in evidence
                                            if isinstance(item,str) and visible.get(item)=="E"]
            indexed.setdefault(left,{})[key]=record
            indexed.setdefault(right,{})[key]=record
    if "architecture_edges" in relations:
        source_positions={edge["id"]:index for index,edge in enumerate(graph["edges"])}
        for index,edge in enumerate(implementation["edges"]):
            left,left_kind=nodes[edge["from_id"]]
            right,right_kind=nodes[edge["to_id"]]
            if visible.get(left)!=left_kind or visible.get(right)!=right_kind:
                continue
            key=("architecture_edges",str(index))
            source_index=source_positions[edge["id"]]
            record={"relation":edge["kind"],"edge_id":edge["id"],"index":str(index),
                    "from_id":left,"to_id":right,
                    "source":graph_source(synthesis_entry,f"/architecture_graph/edges/{source_index}")}
            if isinstance(edge.get("contract_id"),str):
                record["contract_id"]=edge["contract_id"]
            indexed.setdefault(left,{})[key]=record
            indexed.setdefault(right,{})[key]=record
    connected={address}
    stack=[address]
    traversed={}
    while stack:
        for key,edge in indexed.get(stack.pop(),{}).items():
            if key in traversed:
                continue
            traversed[key]=edge
            for endpoint in (edge["from_id"],edge["to_id"]):
                if endpoint not in connected:
                    connected.add(endpoint)
                    stack.append(endpoint)
    for node,(row,entry,pointer) in plan_nodes.items():
        if visible[node]!="T":
            continue
        for field,kind in (("r_refs","R"),("d_refs","D"),("e_refs","E"),("checks","C")):
            for index,reference_id in enumerate(row.get(field,[])):
                if isinstance(reference_id,str) and visible.get(reference_id)==kind:
                    source,target=(node,reference_id) if kind=="C" else (reference_id,node)
                    corrective_edges.append({"relation":field,"from_id":source,"to_id":target,
                        "source":graph_source(entry,f"{pointer}/{field}/{index}")})
    for review_id,review in exposed_reviews.items():
        target=review.get("target_result_id")
        if not isinstance(target,str) or target not in visible:
            continue
        entry=state["results"].get(target)
        if not isinstance(entry,dict) or review.get("target_hash")!=entry["content_hash"]:
            continue
        edge={"relation":"EXACT_REVIEW_TARGET","from_id":target,"to_id":review_id,
              "source":graph_source(state["results"][review_id],"/target_result_id")}
        if target in connected or review_id in connected:
            traversed[("review_target",review_id)]=edge
            connected.update((target,review_id))
    paths=graph_corrective_paths(address,[traversed[key] for key in sorted(traversed)],corrective_edges)
    connected.update(paths)
    artifacts={}
    operative_fields={
        "KNOWLEDGE":{"EVIDENCE":("source_refs","applicability_scope_version","knowledge_status")},
        "DECISIONS":{"DECISION":("grounds","mechanism","alternatives","scope","risk_of_error","review_requirements","reconsideration_trigger"),
                     "REVIEW":("target_result_id","outcome","objections","findings")},
        "PROBLEMS":{"HYPOTHESIS":("claim","knowledge_status","scope","finite_check","strongest_competing_cause","falsifier"),
                    "REVIEW":("target_result_id","outcome","procedure"),
                    "DECISION":("grounds","risk_of_error")},
        "EVIDENCE":{"EVIDENCE":("source_refs","observation","scope","procedure","knowledge_status")},
        "ALTERNATIVES":{"DECISION":("alternatives","grounds","risk_of_error","reconsideration_trigger"),
                        "REVIEW":("objections","findings","outcome","procedure")},
        "EVOLUTION":{"EVIDENCE":("applicability_scope_version","command_intake_scope")},
        "COVERAGE":{"OPERATOR":("G","NOT_G")},
    }.get(duty,{})
    execution_bindings={}
    documents={}
    for node in sorted(connected & plan_owners.keys()):
        owner=plan_owners[node]
        execution_bindings[node]={"kind":visible[node],"plan_result_id":owner}
    related_results=(connected & set(state["results"]))|{plan_owners[node] for node in execution_bindings}
    for rid in sorted(related_results):
        entry=state["results"][rid]
        checked,document=_admitted_document(run,state,rid,entry["artifact_type"],verify_cache=True)
        documents[rid]=document
        artifacts[rid]={"path":checked["path"],"sha256":checked["content_hash"],
                        "state":"STALE" if rid in stale else "PENDING" if rid in pending else "CURRENT_REQUIRED",
                        "field_paths":["/"+field for field in operative_fields.get(entry["artifact_type"],())
                                       if field in document]}
    coverage=[]
    if duty in {"KNOWLEDGE","COVERAGE","EVIDENCE"} and isinstance(outcome_entry,dict):
        for index,row in enumerate(outcomes):
            if not isinstance(row,dict):
                continue
            addressed=row.get("original_q_ids",[]) if row.get("record_kind")=="SOURCE_OUTCOME" else [row.get("q_id")]
            if isinstance(addressed,list):
                for question in sorted({item for item in addressed if isinstance(item,str)} & connected & questions.keys()):
                    coverage.append({"q_id":question,"record_index":str(index),
                                     "record_kind":row.get("record_kind"),"answer_status":row.get("answer_status"),
                                     "path":outcome_entry["path"],"sha256":outcome_entry["content_hash"]})
    response={"status":"GRAPH_QUERY","duty":duty,"ref":address,"ref_kind":visible[address],
              "scope":"STRUCTURAL_ONLY","architecture_ref":reference,
              "edges":[traversed[key] for key in sorted(traversed)],"coverage":coverage,
              "artifacts":artifacts,"blockers":sorted(related_results & (pending|stale))}
    if execution_bindings:
        response["execution_bindings"]=execution_bindings
    corrective=[]
    for node,(row,entry,pointer) in sorted(plan_nodes.items()):
        if visible[node]!="T" or node not in paths:
            continue
        for check in row.get("checks",[]):
            if not isinstance(check,str) or check not in plan_nodes or visible.get(check)!="C":
                continue
            check_row,check_entry,check_pointer=plan_nodes[check]
            if check_row.get("task_id")!=node:
                continue
            corrective.append({"task_id":node,"check_id":check,"path":paths[node],
                "task":graph_source(entry,pointer),"check":graph_source(check_entry,check_pointer),
                "applicability":artifacts[entry["result_id"]]["state"]})
    reuse=[]
    for rid in sorted(paths.keys() & documents.keys()):
        evidence=documents[rid]
        if (visible.get(rid)!="E" or rid in pending|stale or not evidence.get("scope")
            or not evidence.get("source_refs")):
            continue
        for review_id,review in exposed_reviews.items():
            if (review_id not in pending|stale and review.get("outcome")=="PASS"
                and review.get("target_result_id")==rid and review.get("target_hash")==state["results"][rid]["content_hash"]
                and state["results"][review_id].get("agent_id")!=state["results"][rid].get("agent_id")):
                reuse.append({"evidence_id":rid,"path":paths[rid],"scope":graph_source(state["results"][rid],"/scope"),
                              "provenance":graph_source(state["results"][rid],"/source_refs"),
                              "review":graph_source(state["results"][review_id],"/outcome")})
    branch_holds=[]
    if withdrawn_branches:
        for blocker in report.get("blockers",[]):
            canonical_id=blocker["node_id"]
            node=nodes.get(canonical_id,(canonical_id,None))[0]
            if node in connected and node in visible and any(root in withdrawn_branches for root in blocker["blocked_by"]):
                branch_holds.append(node)
        if branch_holds:
            response["branch_holds"]=sorted(set(branch_holds))
    response["actionable"]={
        "status":"HELD" if response["blockers"] or branch_holds else "ADDRESSED" if corrective or reuse else "UNRESOLVED",
        "corrective":corrective,"qualified_reuse":reuse,
    }
    if duty=="COVERAGE":
        lineage=[]
        anchors=current.get("applicability_frontier",{})
        for row in command_journal(run):
            receipt=row["receipt"]
            if row["operation"]!="evidence-disposition" or receipt.get("disposition")!="NEW":
                continue
            evidence_id=receipt["evidence_result_id"]
            review_id=receipt["review_result_id"]
            proof=receipt.get("frontier",{}).get("proof",{}).get("lineage",{})
            qid=proof.get("question_or_alternative_id")
            anchor=anchors.get(evidence_id,{})
            if (visible.get(evidence_id)!="E" or visible.get(review_id)!="REVIEW"
                or visible.get(qid)!="Q" or qid not in connected or anchor.get("sha256")!=row["raw_sha256"]):
                continue
            review_entry,review=_admitted_document(run,state,review_id,"REVIEW")
            evidence_entry,_=_admitted_document(run,state,evidence_id,"EVIDENCE",verify_cache=True)
            if (receipt["review_sha256"]!=review_entry["content_hash"]
                or receipt["evidence_sha256"]!=evidence_entry["content_hash"]
                or review.get("applicability_frontier",{}).get("lineage")!=proof):
                raise QSError("graph_query_frontier_lineage_binding")
            lineage.append({"question_id":qid,"evidence_id":evidence_id,"review_id":review_id,
                            "source":graph_source(review_entry,"/applicability_frontier/lineage")})
        if lineage:
            response["new_lineage"]=lineage
    if duty=="EVOLUTION":
        journal=command_journal(run)
        frontier=current.get("applicability_frontier",{})
        response["frontier"]={
            rid:{"position":anchor["position"],"sha256":anchor["sha256"]}
            for rid,anchor in frontier.items() if rid in connected
        }
        response["command_cut"]=command_revision(run,journal)
    return response


def architecture_checkpoint_fields(resolved: dict[str,Any]) -> dict[str,Any]:
    status=resolved.get("selection_status","UNSELECTED")
    fields={"architecture_selection_state":"CURRENT" if status=="SELECTED" else status}
    if isinstance(resolved.get("architecture_ref"),dict):
        fields["architecture_ref"]=resolved["architecture_ref"]
    if resolved.get("errors"):
        fields["architecture_errors"]=resolved["errors"]
    return fields

def _accepted_current_attempt_chain(state: dict[str,Any], assignment_id: str) -> list[str]:
    """Resolve one complete accepted retry chain, newest result first."""
    results=state.get("results",{}); registered=state.get("registered",{}); attempts=state.get("attempts",{}); current_attempts=state.get("current_attempts",{})
    if not all(isinstance(value,dict) for value in (results,registered,attempts,current_attempts)): return []
    current_attempt=current_attempts.get(assignment_id)
    if not isinstance(current_attempt,str): return []
    accepted_attempts:set[str]=set()
    for row in registered.values():
        assignment=row.get("assignment") if isinstance(row,dict) else None
        if isinstance(assignment,dict) and assignment.get("assignment_id")==assignment_id and row.get("status")=="ACCEPTED":
            attempt_id=assignment.get("attempt_id")
            if not isinstance(attempt_id,str): return []
            accepted_attempts.add(attempt_id)
    chain_results:list[str]=[]; chain_attempts:list[str]=[]; seen:set[str]=set(); attempt_id=current_attempt
    while attempt_id not in seen:
        seen.add(attempt_id); pair=assignment_id+"\0"+attempt_id; row=registered.get(pair)
        assignment=row.get("assignment") if isinstance(row,dict) else None
        result_id=assignment.get("result_id") if isinstance(assignment,dict) else None
        entry=results.get(result_id) if isinstance(result_id,str) else None
        attempt=attempts.get(pair)
        if (not isinstance(row,dict) or row.get("status")!="ACCEPTED" or not isinstance(assignment,dict)
            or assignment.get("assignment_id")!=assignment_id or assignment.get("attempt_id")!=attempt_id
            or not isinstance(entry,dict) or entry.get("result_id")!=result_id
            or entry.get("assignment_id")!=assignment_id or entry.get("attempt_id")!=attempt_id
            or entry.get("role_id")!=assignment.get("role_id") or entry.get("agent_id")!=assignment.get("agent_id")
            or entry.get("artifact_type")!=assignment.get("artifact_type")
            or not isinstance(attempt,dict) or attempt.get("result_id")!=result_id
            or attempt.get("content_hash")!=entry.get("content_hash")):
            return []
        chain_results.append(result_id); chain_attempts.append(attempt_id)
        previous=assignment.get("supersedes_attempt_id")
        if previous is None: break
        if not isinstance(previous,str): return []
        attempt_id=previous
    else:
        return []
    if set(chain_attempts)!=accepted_attempts: return []
    return chain_results


def superseded_attempt_result_ids(state: dict[str,Any]) -> set[str]:
    """Return accepted results behind one exact current-attempt chain.

    A stale result does not become historical merely because another attempt
    exists.  The current attempt and every predecessor must have an accepted
    result in the registry, and every immutable assignment must name the prior
    attempt through ``supersedes_attempt_id``.
    """
    current_attempts=state.get("current_attempts",{})
    if not isinstance(current_attempts,dict): return set()
    historical:set[str]=set()
    for assignment_id in current_attempts:
        if not isinstance(assignment_id,str): continue
        chain=_accepted_current_attempt_chain(state,assignment_id)
        historical.update(chain[1:])
    return historical


def active_critical_decision_result_ids(state: dict[str,Any], docs: dict[str,dict[str,Any]]) -> tuple[set[str],list[str]]:
    """Project active critical decisions without deleting rejected history."""
    results=state.get("results",{}); registered=state.get("registered",{}); current_attempts=state.get("current_attempts",{})
    if not isinstance(results,dict) or not isinstance(registered,dict) or not isinstance(current_attempts,dict):
        return set(),["decision_projection:state_contract"]
    critical={result_id for result_id,doc in docs.items() if isinstance(doc,dict) and doc.get("criticality")=="CRITICAL" and isinstance(results.get(result_id),dict) and results[result_id].get("artifact_type")=="DECISION"}
    errors:list[str]=[]; historical:set[str]=set()
    assignment_ids={results[result_id].get("assignment_id") for result_id in critical if isinstance(results[result_id].get("assignment_id"),str)}
    for assignment_id in sorted(assignment_ids):
        if assignment_id not in current_attempts: continue
        chain=_accepted_current_attempt_chain(state,assignment_id)
        if not chain:
            errors.append("decision_projection:"+assignment_id+":accepted_current_chain"); continue
        if len(chain)<2: continue
        valid=True; identity:tuple[Any,Any]|None=None
        for index,result_id in enumerate(chain):
            entry=results.get(result_id); doc=docs.get(result_id)
            if (not isinstance(entry,dict) or entry.get("artifact_type")!="DECISION" or not isinstance(doc,dict)
                or doc.get("id")!=result_id or doc.get("assignment_id")!=assignment_id
                or doc.get("attempt_id")!=entry.get("attempt_id") or doc.get("author_role")!=entry.get("role_id")
                or doc.get("agent_id")!=entry.get("agent_id") or doc.get("workflow_status")!="ACCEPTED"):
                errors.append("decision_projection:"+assignment_id+":accepted_binding"); valid=False; break
            current_identity=(entry.get("role_id"),entry.get("agent_id"))
            if identity is None: identity=current_identity
            elif current_identity!=identity:
                errors.append("decision_projection:"+assignment_id+":cross_identity"); valid=False; break
            if index+1<len(chain):
                predecessor=chain[index+1]; related=doc.get("related_ids")
                if doc.get("supersedes")!=predecessor or not isinstance(related,list) or predecessor not in related:
                    errors.append("decision_projection:"+assignment_id+":result_supersession"); valid=False; break
        if not valid: continue
        prior_critical={result_id for result_id in chain[1:] if result_id in critical}
        if prior_critical and chain[0] not in critical:
            errors.append("decision_projection:"+assignment_id+":criticality_downgrade"); continue
        historical.update(prior_critical)
    return critical-historical,errors


def validate(args: argparse.Namespace) -> dict[str, Any]:
    run=run_dir(root(args.root),args.run_id)
    if not (run/"state").is_dir():
        return {"status":"FAIL","errors":["command_run_state_missing"],"scope":"STRUCTURAL_ONLY"}
    return locked_command(args,_validate_locked)


def _validate_locked(args: argparse.Namespace,run: Path) -> dict[str, Any]:
    errors=[]
    try: state,current,bank=accepted(run)
    except QSError as exc: return {"status":"FAIL","errors":[str(exc)],"scope":"STRUCTURAL_ONLY"}
    snapshot_doc=load_yaml(run/"input/snapshot.yaml")
    if isinstance(snapshot_doc,dict) and snapshot_doc.get("input_mode") in NEW_MODES:
        try:
            _snapshot, _state, _current, inputs, prep_bank, approval, admission, prep_errors=prep_state(run,state,current)
            state_info=resolve_admission_state(snapshot_doc,_current,inputs,prep_bank,admission,_prep_transactions(run))
            prep_errors=sorted(set(prep_errors+state_info.get("errors",[])))
        except QSError as exc:
            return {"status":"FAIL","errors":["prep_guard:"+str(exc)],"scope":"STRUCTURAL_ONLY"}
        if prep_errors or state_info.get("state")!="CORE_ADMITTED":
            extras=prep_errors+([] if state_info.get("state")=="CORE_ADMITTED" else ["prep:admission_state:"+str(state_info.get("state"))+":phase_"+str(args.phase)+"_requires_core"])
            return {"status":"FAIL","errors":sorted(set(extras)),"scope":"STRUCTURAL_ONLY"}
    snapshot=current["input_snapshot"]; rid=args.run_id; results=state["results"]
    journal=command_journal(run)
    question_view=(state,current,bank,journal)
    armed_ledger=None
    try:
        gap_proofs,primary_owners,retired=_question_route_proofs(run,question_view)
        if primary_owners is not None:
            armed_ledger=_question_projection(journal).ledger
            count_blocker=armed_ledger.count_blocker() if armed_ledger else "INCOMPLETE"
            if count_blocker: errors.append("question_count:"+count_blocker)
            if armed_ledger:
                _question_current_closes(run,question_view,armed_ledger)
            errors.extend(_adviser_freeze_blockers(run,question_view,"CORE"))
    except QSError as exc:
        errors.append(str(exc))
        gap_proofs,primary_owners,retired={},None,set()
    # Current research outcomes are accepted evidence, never mutable bank input.
    outcome_entry=None; outcome_doc=None; outcome_selector=None
    try:
        outcome_entry,outcome_doc,outcome_selector=resolve_current_selection(run,state,current,"SOURCE_OUTCOMES")
    except QSError as exc:
        errors.append(str(exc))
    if not isinstance(outcome_entry,dict) or not isinstance(outcome_doc,dict):
        errors.append("source_outcomes_missing"); outcomes=[]
    elif common(outcome_doc,rid,snapshot) or not isinstance(outcome_doc.get("records"),list):
        errors.append("source_outcomes_contract"); outcomes=[]
    else: outcomes=outcome_doc["records"]
    seen={q["id"]:0 for q in bank.get("questions",[]) if isinstance(q,dict)}
    derived: dict[str,dict[str,Any]]={}; source_records: list[dict[str,Any]]=[]
    unclosed_q: set[str]=set()

    def addressed_ids(entry: dict[str,Any]) -> set[str]:
        """Read only explicit per-question answer claims; DONE is not coverage."""
        try: document=load_yaml(child(run,entry.get("path", "")))
        except QSError: return set()
        if not isinstance(document,dict): return set()
        claimed=document.get("addressed_q_ids", document.get("original_q_ids", []))
        values=set(claimed) if isinstance(claimed,list) and all(isinstance(q,str) for q in claimed) else set()
        rows=document.get("per_question_outcomes", [])
        if isinstance(rows,list):
            values.update(row.get("q_id") for row in rows if isinstance(row,dict) and isinstance(row.get("q_id"),str))
        return values

    registered=state.get("registered",{})
    registered_by_assignment={entry["assignment"].get("assignment_id"):entry["assignment"]
                              for entry in registered.values()
                              if isinstance(entry,dict) and isinstance(entry.get("assignment"),dict)}
    for assignment_id,attempt_id in state.get("current_attempts",{}).items():
        selected=registered.get(assignment_id+"\0"+attempt_id)
        if selected is not None:
            registered_by_assignment[assignment_id]=selected["assignment"]
    for item in outcomes if isinstance(outcomes,list) else []:
        if not isinstance(item,dict): errors.append("question_record_type"); continue
        if item.get("record_kind")=="SOURCE_OUTCOME":
            originals=item.get("original_q_ids"); disp=item.get("source_disposition")
            if not isinstance(originals,list) or not originals: errors.append("source_outcome_originals"); continue
            if len(originals)!=len(set(originals)) or any(not isinstance(qid,str) for qid in originals): errors.append("source_outcome_originals"); continue
            source_records.append(item)
            for qid in originals:
                if qid not in seen: errors.append("unknown_q:"+str(qid)); unclosed_q.add(qid)
                else: seen[qid]+=1
            if disp not in {"KEPT","REFINED","MERGED","CLOSED_BY_EVIDENCE","NOT_APPLICABLE","SPLIT"}: errors.append("source_disposition")
            if disp=="NOT_APPLICABLE" and item.get("answer_status")!="NOT_APPLICABLE": errors.append("not_applicable_status")
        elif item.get("record_kind")=="DERIVED_Q" and isinstance(item.get("q_id"),str) and isinstance(item.get("parent_source_ids"),list) and item.get("parent_source_ids"):
            qid=item["q_id"]; parents=item["parent_source_ids"]
            if qid in derived or len(parents)!=len(set(parents)) or any(parent not in seen for parent in parents):
                errors.append("derived_question_contract")
            else: derived[qid]=item
        else: errors.append("derived_question_contract")
        entry=results.get(item.get("answer_result_id")); self_ref=isinstance(outcome_entry,dict) and item.get("answer_result_id")==outcome_entry.get("result_id")
        assigned=registered_by_assignment.get(item.get("author_assignment_id"))
        targets=item.get("original_q_ids") if item.get("record_kind")=="SOURCE_OUTCOME" else [item.get("q_id")]
        if "question_gap" in item:
            if not ledger_gap_outcome(item,targets,gap_proofs):
                errors.append("question_gap_provenance")
            continue
        if armed_ledger is not None and isinstance(targets,list):
            for identifier in targets:
                canonical=armed_ledger._canonical(identifier)
                close=armed_ledger.closed.get(canonical)
                if close is None:
                    errors.append("question_outcome_without_work:"+str(identifier))
                elif (close.close_outcome=="OPEN")!=(item.get("answer_status")=="OPEN"):
                    errors.append("question_close_outcome_mismatch:"+str(identifier))
                else:
                    owner=close.operation.assignment
                    if (not isinstance(entry,dict) or entry.get("assignment_id")!=owner.assignment.id
                        or entry.get("attempt_id")!=owner.attempt):
                        errors.append("question_outcome_close_author_mismatch:"+str(identifier))
        if (not isinstance(entry,dict) or entry.get("assignment_id")!=item.get("author_assignment_id") or entry.get("artifact_type") not in {"ANSWER","PRIMARY"} or self_ref
            or not isinstance(targets,list) or not set(targets).issubset(addressed_ids(entry))
            or (isinstance(assigned,dict) and isinstance(assigned.get("assigned_ids"),dict) and isinstance(assigned["assigned_ids"].get("questions"),list) and not set(targets).issubset(set(assigned["assigned_ids"]["questions"])) )):
            errors.append("question_answer_provenance")
            unclosed_q.update(q for q in targets if isinstance(q,str))
    for qid,count in seen.items():
        if count!=1: unclosed_q.add(qid)
    if unclosed_q: errors.append("source_question_coverage")
    if armed_ledger is not None:
        missing_derived={qid for qid,row in armed_ledger.questions.items() if row.parent is not None}-derived.keys()
        if missing_derived: errors.append("question_derived_outcome_missing:"+",".join(sorted(missing_derived)))
    for item in source_records:
        if item.get("source_disposition") in {"SPLIT","MERGED"}:
            targets=item.get("disposition_target_ids")
            if not isinstance(targets,list) or not targets or len(targets)!=len(set(targets)) or not all(isinstance(target,str) and target in derived and set(derived[target].get("parent_source_ids",[]))==set(item["original_q_ids"]) for target in targets):
                errors.append("derived_trace")
    # Mandatory role cards are separate accepted result identities.
    role_rows=[e for e in results.values() if isinstance(e,dict) and e.get("role_id") in ROLES]
    required_roles=ROLES if args.phase=="final" else ROLES-{"FINAL-AUDITOR"}
    for role in required_roles:
        got=[e for e in role_rows if e.get("role_id")==role and e.get("artifact_type") in ROLE_KINDS[role]]
        if {e.get("artifact_type") for e in got} != set(ROLE_KINDS[role]) or len({e.get("agent_id") for e in got})!=1: errors.append("role_card:"+role)
    if len({next((e.get("agent_id") for e in role_rows if e.get("role_id")==role),None) for role in required_roles})!=len(required_roles): errors.append("role_agent_identity")
    docs: dict[str,dict[str,Any]] = {}
    for result_id, entry in results.items():
        try:
            doc=load_yaml(child(run,entry.get("path","")))
            if isinstance(doc,dict) and not common(doc,rid,snapshot) and doc.get("id")==result_id: docs[result_id]=doc
            else: errors.append("accepted_result_contract:"+str(result_id))
        except QSError: errors.append("accepted_result_contract:"+str(result_id))
    # A Root-approved routing map, once stored, is part of the sealed
    # operational contract.  Validation never lets an invalid cache disappear
    # merely because a later resume invocation omitted --approved-map.
    map_hash=current.get("approved_map_sha256")
    if map_hash is not None:
        map_path=state_file(run,"approved-map")
        try:
            if not isinstance(map_hash,str) or not map_path.is_file() or sha(map_path.read_bytes())!=map_hash: raise QSError("approved_map_drift")
            approved_map=load_yaml(map_path)
            if not isinstance(approved_map,dict): raise QSError("approved_map_not_mapping")
            completed_unknown,current_question_claims=_orchestration_proofs(run,state,current)
            route_check=validate_orchestration(
                bank,approved_map,{"registered":state.get("registered",{}),"current_attempts":state.get("current_attempts",{})},results,docs,[],
                completed_unknown,current_question_claims,question_gap_proofs=gap_proofs,
                question_primary_owners=primary_owners,question_retired_claims=retired,
            )
            for issue in route_check.get("errors",[]): errors.append("approved_map:"+str(issue))
            if route_check.get("routing_status")!="VALIDATED": errors.append("approved_map_invalid")
        except QSError as exc: errors.append("approved_map:"+str(exc))
    # A current projection is authoritative only when its persisted exact hash
    # resolves against the retained PLAN history.  A stale PLAN result outside
    # that projection is historical provenance; it cannot block the selected
    # v2 graph.  Every selected PLAN, non-PLAN, unknown, or malformed-projection
    # stale result still fails closed.
    try:
        plan_projection,_=plan_projection_for_run(run,state,current)
    except QSError as exc:
        errors.append(str(exc)); plan_projection=None
    try:
        architecture_projection_for_run(run,state,current,bank)
    except QSError as exc:
        errors.append(str(exc))
    try:
        _audit_history_entry,_audit_history_doc,audit_history_selector=resolve_current_selection(run,state,current,"FINAL_AUDIT",allow_stale=True)
    except QSError as exc:
        errors.append(str(exc)); audit_history_selector=None
    revision_scope=model_revision_scope_for_run(
        run,results,docs,plan_projection,
        audit_history_selector.get("result_id") if isinstance(audit_history_selector,dict) else None,
        load_yaml,sha,phase=args.phase,
    )
    iceberg_projection:dict[str,dict[str,Any]]={}; iceberg_historical:set[str]=set()
    try:
        iceberg_projection,iceberg_historical,iceberg_contract_errors=validate_double_iceberg(
            run,bank,results,docs,args.phase,load_yaml,sha,revision_scope=revision_scope,
        )
        if revision_scope["errors"]: iceberg_historical=set()
    except (QSError,TypeError,ValueError) as exc:
        errors.append("double_iceberg_projection:"+str(exc)); iceberg_historical=set()
    model_history=(set(revision_scope["historical_result_ids"])
                   if not revision_scope["errors"] else set())
    stale=current.get("stale_result_ids",[])
    if isinstance(stale,list) and stale:
        blocking=[]
        historical_attempts=superseded_attempt_result_ids(state)
        stale_set={item for item in stale if isinstance(item,str)}
        active_consumer_docs={rid:doc for rid,doc in docs.items() if rid not in model_history}
        iceberg_consumed=current_iceberg_consumer_result_ids(active_consumer_docs,stale_set)
        iceberg_nonblocking=iceberg_historical-iceberg_consumed
        for stale_id in sorted(item for item in stale if isinstance(item,str)):
            entry=results.get(stale_id)
            selected_revisions={outcome_selector.get("result_id") if isinstance(outcome_selector,dict) else None,
                                audit_history_selector.get("result_id") if isinstance(audit_history_selector,dict) else None}
            historical_revision=isinstance(entry,dict) and entry.get("artifact_type") in REVISION_SELECTIONS and stale_id not in selected_revisions
            historical_view=(isinstance(entry,dict) and entry.get("artifact_type")=="OPERATOR" and stale_id in iceberg_nonblocking)
            historical_result=historical_revision or stale_id in historical_attempts or historical_view or stale_id in model_history
            if (not isinstance(entry,dict) or (entry.get("artifact_type")!="PLAN" and not historical_result)
                or (entry.get("artifact_type")=="PLAN" and (plan_projection is None or stale_id in plan_projection))):
                blocking.append(stale_id)
        if blocking: errors.append("stale_result_ids:"+",".join(blocking))
    elif stale is not None and not isinstance(stale,list):
        errors.append("stale_result_ids:invalid")
    # The addressable contract is centralized in the pure validator; it is
    # deliberately invoked on the same exact accepted documents used below.
    try:
        _audit_entry,_audit_doc,audit_selector=resolve_current_selection(run,state,current,"FINAL_AUDIT")
    except QSError as exc:
        errors.append(str(exc)); audit_selector=None
    active_critical_decisions,decision_projection_errors=active_critical_decision_result_ids(state,docs)
    errors.extend(decision_projection_errors)
    diagnostics: list[str] = []
    errors.extend(validate_model(run, bank, results, docs, args.phase, load_yaml, sha, plan_projection,
                                 current_source_outcomes_id=outcome_selector.get("result_id") if isinstance(outcome_selector,dict) else None,
                                 current_final_audit_id=audit_selector.get("result_id") if isinstance(audit_selector,dict) else None,
                                 active_critical_decision_ids=active_critical_decisions,
                                 question_gap_proofs=gap_proofs, diagnostics=diagnostics))
    def requirement_refs(value: Any) -> set[str]:
        found:set[str]=set()
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {"r_refs","requirement_ids","requirements"} and isinstance(item,list): found.update(x for x in item if isinstance(x,str))
                else: found.update(requirement_refs(item))
        elif isinstance(value,list):
            for item in value: found.update(requirement_refs(item))
        return found
    required_r={item for item in bank.get("requirements",[]) if isinstance(item,str)}
    planned_r=set().union(*(requirement_refs(doc) for result_id,doc in docs.items() if results.get(result_id,{}).get("artifact_type")=="PLAN" and (plan_projection is None or result_id in plan_projection))) if docs else set()
    unclosed_r=sorted(required_r-planned_r)
    if unclosed_r: errors.append("requirement_plan_coverage")
    unclosed_decisions=sorted({match.group(1) for error in errors for match in [re.match(r"decision:([^:]+):critical_review_coverage",error)] if match})
    report = {"status":"PASS" if not errors else "FAIL","errors":errors,"unclosed_q_ids":sorted(unclosed_q),"unclosed_r_ids":unclosed_r,"unclosed_decision_ids":unclosed_decisions,"scope":"STRUCTURAL_ONLY"}
    if diagnostics:
        report["diagnostics"] = diagnostics
    return report

def _cycle(nodes:set[str], edges:list[tuple[Any,Any]]) -> bool:
    graph={n:[] for n in nodes}
    for a,b in edges:
        if a in graph and b in graph: graph[a].append(b)
    active=set(); done=set()
    def visit(n:str)->bool:
        if n in active:return True
        if n in done:return False
        active.add(n); bad=any(visit(x) for x in graph[n]); active.remove(n); done.add(n); return bad
    return any(visit(n) for n in nodes)

def _install_replay_blob(run: Path, blob: Any) -> None:
    if not isinstance(blob,dict) or not isinstance(blob.get("path"),str) or not isinstance(blob.get("bytes_b64"),str) or not isinstance(blob.get("sha256"),str):
        raise QSError("admission_replay_blob")
    raw=base64.b64decode(blob["bytes_b64"],validate=True)
    if sha(raw)!=blob["sha256"]: raise QSError("admission_replay_hash")
    path=child(run,blob["path"])
    if path.exists():
        if path.read_bytes()!=raw: raise QSError("admission_replay_existing_drift")
    else:
        atomic(path,raw)

def replay_prep_admission_if_needed(run: Path, current: dict[str,Any], snap: dict[str,Any]) -> dict[str,Any]:
    """Replay only a committed PREP_ADMISSION over its exact predecessor."""
    if snap.get("input_mode") not in NEW_MODES: return current
    rows=[]
    for path in sorted((run/"transactions/admissions").glob("*.yaml")):
        tx=load_yaml(path)
        if isinstance(tx,dict) and tx.get("transaction_type")=="PREP_ADMISSION": rows.append(tx)
    if len(rows)>1: raise QSError("admission_replay_cardinality")
    if not rows: return current
    tx=rows[0]
    required=("status","run_id","input_snapshot","bank","admission","expected_current","admission_next_current")
    if any(field not in tx for field in required) or tx.get("status")!="COMMITTED" or tx.get("run_id")!=run.name or tx.get("input_snapshot")!=current.get("input_snapshot"):
        raise QSError("admission_replay_transaction")
    expected=tx["expected_current"]; after=tx["admission_next_current"]
    if not isinstance(expected,dict) or not isinstance(after,dict): raise QSError("admission_replay_current_blob")
    now=(run/"state/current.yaml").read_bytes()
    try:
        expected_raw=base64.b64decode(expected.get("bytes_b64",""),validate=True); after_raw=base64.b64decode(after.get("bytes_b64",""),validate=True)
    except (ValueError,TypeError,binascii.Error): raise QSError("admission_replay_current_blob")
    if sha(expected_raw)!=expected.get("sha256") or sha(after_raw)!=after.get("sha256"): raise QSError("admission_replay_current_hash")
    if now==expected_raw:
        _install_replay_blob(run,tx["bank"]); _install_replay_blob(run,tx["admission"])
        atomic(run/"state/current.yaml",after_raw)
        return load_state(run,"current")
    if now==after_raw or current.get("admission_transaction_id")==tx.get("transaction_id"):
        _install_replay_blob(run,tx["bank"]); _install_replay_blob(run,tx["admission"])
        return current
    raise QSError("admission_replay_current_descendant_or_conflict")

def restore_frontier_command(run, tx, after_doc, before_doc=None):
    command=tx.get("frontier_command")
    if before_doc is not None and not frontier_transition_valid(before_doc,after_doc,command):
        raise QSError("applicability_frontier_replay_transition")
    if not isinstance(command,dict):
        raise QSError("applicability_frontier_replay_command")
    rid=ident(command.get("evidence_result_id"),"evidence_result_id")
    anchor=after_doc.get("applicability_frontier",{}).get(rid)
    if not isinstance(anchor,dict):
        raise QSError("applicability_frontier_replay_anchor")
    position=anchor.get("position")
    if not isinstance(position,str) or not re.fullmatch(r"[1-9][0-9]{0,7}",position):
        raise QSError("applicability_frontier_replay_position")
    index=int(position)
    path=f"transactions/commands/{index:08d}.yaml"
    try:
        raw=base64.b64decode(command.get("bytes_b64"),validate=True)
    except (TypeError,ValueError):
        raise QSError("applicability_frontier_replay_bytes")
    if command.get("path")!=path or command.get("sha256")!=anchor.get("sha256") or sha(raw)!=anchor["sha256"]:
        raise QSError("applicability_frontier_replay_digest")
    target=child(run,path)
    if target.exists() or target.is_symlink():
        if captured_argument(target,1024**2).raw!=raw:
            raise QSError("applicability_frontier_replay_projection_drift")
        return
    row=load_yaml(CapturedInput(path,raw))
    receipt=row.get("receipt") if isinstance(row,dict) else None
    previous="GENESIS" if index==1 else sha(captured_argument(child(run,f"transactions/commands/{index-1:08d}.yaml"),1024**2).raw)
    if (not isinstance(receipt,dict) or row.get("previous_sha256")!=previous
        or row.get("run_id")!=tx.get("run_id") or row.get("input_snapshot")!=tx.get("input_snapshot")
        or row.get("record_sha256")!=sha(canon_yaml({key:value for key,value in row.items() if key!="record_sha256"}))
        or receipt.get("evidence_result_id")!=rid or receipt.get("affected_result_ids")!=anchor.get("affected_result_ids")
        or row.get("operation") not in {"evidence-intake","evidence-disposition"}):
        raise QSError("applicability_frontier_replay_receipt")
    state=load_state(run,"assignments")
    entry,evidence=_admitted_document(run,state,rid,"EVIDENCE")
    scope=evidence.get("command_intake_scope")
    if (receipt.get("evidence_sha256")!=entry["content_hash"] or not isinstance(scope,list)
        or any(not isinstance(item,str) for item in scope) or sorted(scope)!=anchor["affected_result_ids"]):
        raise QSError("applicability_frontier_replay_evidence")
    entries=[entry]
    if row["operation"]=="evidence-disposition":
        review_entry,review=_admitted_document(run,state,receipt.get("review_result_id"),"REVIEW")
        if (review_entry.get("content_hash")!=receipt.get("review_sha256")
            or review.get("target_result_id")!=rid or review.get("target_hash")!=entry["content_hash"]
            or review_entry.get("agent_id")==entry.get("agent_id") or review.get("outcome")!="PASS"
            or review.get("evidence_disposition")!=receipt.get("disposition")):
            raise QSError("applicability_frontier_replay_review")
        entries.append(review_entry)
    backing=[{"kind":entry["artifact_type"],"path":entry["path"],"sha256":entry["content_hash"]} for entry in entries]
    if backing!=tx.get("backing_records"):
        raise QSError("applicability_frontier_replay_backing")
    atomic(target,raw)


def replay_current_transitions_if_needed(run: Path, current: dict[str,Any], snap: dict[str,Any]) -> dict[str,Any]:
    """Validate one CAS chain and replay only the suffix after the current image."""
    paths=sorted((run/"transactions/current").glob("*.yaml"))
    if not paths: return current
    rows=[]; previous_after=None
    for path in paths:
        tx=load_yaml(path)
        if not isinstance(tx,dict) or tx.get("transaction_type")!="CURRENT_TRANSITION" or tx.get("status")!="COMMITTED": raise QSError("current_replay_transaction")
        if tx.get("run_id")!=run.name or tx.get("input_snapshot")!=current.get("input_snapshot"):
            raise QSError("current_replay_binding")
        if snap.get("input_mode") in NEW_MODES and (tx.get("admission_transaction_id")!=current.get("admission_transaction_id") or tx.get("prep_admission_sha256")!=current.get("prep_admission_sha256")):
            raise QSError("current_replay_binding")
        payload={key:value for key,value in tx.items() if key!="transaction_payload_sha256"}
        if tx.get("transaction_payload_sha256")!=sha(canon_yaml(payload)): raise QSError("current_replay_payload")
        try: after=base64.b64decode(tx.get("after_current_bytes_b64",""),validate=True)
        except (ValueError,TypeError,binascii.Error): raise QSError("current_replay_after")
        if sha(after)!=tx.get("after_current_sha256"): raise QSError("current_replay_after")
        if previous_after is not None and tx.get("before_current_sha256")!=previous_after: raise QSError("current_replay_branch_or_gap")
        previous_after=tx.get("after_current_sha256"); rows.append((tx,after))
    now=(run/"state/current.yaml").read_bytes(); now_hash=sha(now); start=None
    for index,(tx,_after) in enumerate(rows):
        if now_hash==tx.get("before_current_sha256"): start=index; break
        if now_hash==tx.get("after_current_sha256"): start=index+1
    if start is None: raise QSError("current_replay_descendant_or_conflict")
    for tx,after in rows[:start]:
        if tx.get("mutation_type")=="APPLICABILITY_FRONTIER_ADVANCE":
            restore_frontier_command(run,tx,load_yaml(CapturedInput("frontier committed image",after)))
    for tx,after in rows[start:]:
        if sha((run/"state/current.yaml").read_bytes())!=tx.get("before_current_sha256"): raise QSError("current_replay_cas_conflict")
        mutation=tx.get("mutation_type")
        if mutation=="APPLICABILITY_FRONTIER_ADVANCE":
            restore_frontier_command(run,tx,load_yaml(CapturedInput("frontier after image",after)),load_state(run,"current"))
        if mutation=="PLAN_PROJECTION_SELECT":
            state,before,bank=accepted(run,recovering=True)
            after_doc=load_yaml(CapturedInput("plan selection after image",after))
            if not plan_projection_transition_valid(before,after_doc):
                raise QSError("plan_selection_replay_transition")
            projection,_reference,backing,authority=plan_selection_proof(
                run,state,before,bank,after_doc["current_plan_projection"])
            request_marker=tx.get("plan_selection_request")
            if (not isinstance(request_marker,dict) or set(request_marker)!={"request_sha256","authority"}
                or not isinstance(request_marker["request_sha256"],str)
                or not re.fullmatch(r"[0-9a-f]{64}",request_marker["request_sha256"])
                or request_marker["authority"]!=authority):
                raise QSError("plan_selection_replay_request")
            if projection!=after_doc["current_plan_projection"] or backing!=tx.get("backing_records"):
                raise QSError("plan_selection_replay_backing")
        if mutation in {"SOURCE_OUTCOMES_SELECT","FINAL_AUDIT_SELECT"}:
            after_doc=load_yaml(CapturedInput("current after image",after))
            field="current_source_outcomes" if mutation=="SOURCE_OUTCOMES_SELECT" else "current_final_audit"
            selector=after_doc.get(field) if isinstance(after_doc,dict) else None
            if not isinstance(selector,dict): raise QSError("current_replay_selector")
            source=captured_argument(child(run,selector["path"]))
            if sha(source.raw)!=selector.get("sha256"): raise QSError("current_replay_selector_backing")
            immutable=load_yaml(captured_argument(child(run,"transactions/"+ident(selector["result_id"],"result_id")+".yaml"),48 * 1024**2))
            if (not isinstance(immutable,dict) or immutable.get("result_sha256")!=selector["sha256"]
                or _selector_for(selector["result_id"],immutable["entry"])!=selector):
                raise QSError("current_replay_selector_backing")
        if tx.get("approved_map_projection")=="STAGED_ARCHIVE":
            after_doc=load_yaml(CapturedInput("current after image",after))
            digest=after_doc.get("approved_map_sha256") if isinstance(after_doc,dict) else None
            if mutation not in {"APPROVED_MAP_SET","APPROVED_MAP_REVISE"} or not isinstance(digest,str):
                raise QSError("current_replay_approved_map_contract")
            archive_path="state/approved-maps/"+ident(digest,"approved_map_sha256")+".yaml"
            if {"kind":"APPROVED_MAP_ARCHIVE","path":archive_path,"sha256":digest} not in tx.get("backing_records",[]):
                raise QSError("current_replay_approved_map_backing")
            approved_raw=captured_argument(child(run,archive_path)).raw
            if sha(approved_raw)!=digest: raise QSError("current_replay_approved_map_backing")
            projection=state_file(run,"approved-map")
            previous=load_state(run,"current").get("approved_map_sha256")
            if projection.exists() and sha(captured_argument(projection).raw) not in {digest,previous}:
                raise QSError("current_replay_approved_map_projection_drift")
            atomic(projection,approved_raw)
        if tx.get("release_projection")=="STAGED_ARCHIVE":
            after_doc=load_yaml(CapturedInput("release after image",after))
            if not isinstance(after_doc,dict) or mutation!="RELEASE_SEAL": raise QSError("release_replay_contract")
            release_id=ident(after_doc.get("release_id"),"release_id")
            digest=after_doc.get("release_sha256")
            rel="releases/"+release_id+".yaml"
            if {"kind":"RELEASE_ARCHIVE","path":rel,"sha256":digest} not in tx.get("backing_records",[]):
                raise QSError("release_replay_backing")
            raw=captured_argument(child(run,rel),48 * 1024**2).raw
            if sha(raw)!=digest: raise QSError("release_replay_archive_drift")
            release=load_yaml(CapturedInput(rel,raw))
            if (not isinstance(release,dict) or release.get("release_id")!=release_id
                or release.get("input_snapshot")!=current.get("input_snapshot") or release.get("run_id")!=run.name):
                raise QSError("release_replay_binding")
            for name in ("audit","gate6"):
                source=captured_argument(child(run,release[name+"_path"]))
                if sha(source.raw)!=release.get(name+"_sha256"): raise QSError("release_replay_"+name+"_drift")
            target=child(run,"release.yaml")
            previous=load_state(run,"current").get("release_sha256")
            if target.exists() and sha(captured_argument(target,48 * 1024**2).raw) not in {digest,previous}:
                raise QSError("release_replay_projection_drift")
            atomic(target,raw)
        atomic(run/"state/current.yaml",after)
    return load_state(run,"current")

def recover(args: argparse.Namespace, projection_override: Any = None) -> dict[str,Any]:
    return locked_command(args, _recover_locked, projection_override)


def launch_transition_chain(run, snapshot, pair, entry, *, modern):
    directory=child(run,"transactions/launch/"+sha(pair.encode()))
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise QSError("launch_transition_path")
    assignment=entry["assignment"]
    initial=initial_launch_record(assignment,entry["assignment_sha256"])
    chain=[initial] if modern else []
    allowed={("NOT_LAUNCHED","READY_TO_DISPATCH"),("NOT_LAUNCHED","DISPATCH_UNKNOWN"),
             ("READY_TO_DISPATCH","DISPATCH_UNKNOWN")}
    immutable={key:value for key,value in initial.items() if key not in {"state","revision"}}
    for path in sorted(directory.glob("*.yaml")):
        value=load_yaml(captured_argument(path))
        if not isinstance(value,dict):
            raise QSError("launch_transition_contract")
        before,after=value.get("before"),value.get("after")
        payload={key:item for key,item in value.items() if key!="record_sha256"}
        if (value.get("schema_version")!=SCHEMA or value.get("record_sha256")!=sha(canon_yaml(payload))
            or value.get("run_id")!=run.name or value.get("input_snapshot")!=snapshot
            or not isinstance(before,dict) or not isinstance(after,dict)
            or value.get("before_sha256")!=sha(canon_yaml(before))):
            raise QSError("launch_transition_integrity")
        revision=before.get("revision")
        if (not isinstance(revision,str) or not revision.isdigit()
            or after.get("revision")!=str(int(revision)+1) or path.name!=after["revision"].zfill(8)+".yaml"
            or any({key:item for key,item in row.items() if key not in {"state","revision"}}!=immutable
                   for row in (before,after))
            or any(not isinstance(row.get("state"),str) or row["state"] not in LAUNCH_STATES
                   for row in (before,after))
            or (before["state"],after["state"]) not in allowed or chain and before!=chain[-1]):
            raise QSError("launch_transition_chain")
        if value.get("receipt")!=launch_receipt(after):
            raise QSError("launch_transition_receipt_binding")
        if not chain:
            chain.append(before)
        chain.append(after)
    return chain


def checked_launch_chain(run,snapshot,pair,entry,record):
    path=child(run,"intents/"+sha(pair.encode()).upper()+".yaml")
    intent=load_yaml(captured_argument(path))
    if (not isinstance(intent,dict) or intent.get("assignment")!=entry["assignment"]
        or intent.get("assignment_sha256")!=entry["assignment_sha256"]):
        raise QSError("launch_intent_binding")
    if intent.get("publication")=="JOURNAL_FIRST":
        try:
            raw=base64.b64decode(intent.get("assignment_bytes_b64",""),validate=True)
        except (ValueError,TypeError,binascii.Error) as exc:
            raise QSError("launch_intent_binding") from exc
        if (sha(raw)!=entry["assignment_sha256"] or intent.get("input_snapshot")!=snapshot
            or intent.get("run_id")!=run.name or intent.get("status")!="ASSIGNED"
            or load_yaml(CapturedInput(str(path),raw))!=entry["assignment"]):
            raise QSError("launch_intent_binding")
    chain=launch_transition_chain(run,snapshot,pair,entry,modern=intent.get("publication")=="JOURNAL_FIRST")
    if chain and record not in chain:
        raise QSError("launch_transition_projection_conflict")
    return chain


def recover_launch_projection(run, snapshot, registered, intents):
    launches=load_launch_intents(run,snapshot)
    records=launches["records"]
    legacy_empty=not records
    if set(records)-set(registered): raise QSError("launch_intents_incomplete")
    for pair,entry in registered.items():
        assignment=entry["assignment"]
        if pair not in records and intents[pair].get("publication")=="JOURNAL_FIRST":
            records[pair]=initial_launch_record(assignment,entry["assignment_sha256"])
        record=records.get(pair)
        if record is None and legacy_empty: continue
        if (not isinstance(record,dict) or record.get("assignment_id")!=assignment["assignment_id"]
            or record.get("attempt_id")!=assignment["attempt_id"] or record.get("assignment_sha256")!=entry["assignment_sha256"]
            or record.get("state") not in LAUNCH_STATES or not isinstance(record.get("revision"),str)
            or not record["revision"].isdigit() or not core_metadata_matches(record,assignment)):
            raise QSError("launch_intent_binding")
    transition_root=child(run,"transactions/launch")
    known={sha(pair.encode()) for pair in records}
    if any(directory.name not in known for directory in transition_root.glob("*")):
        raise QSError("launch_transition_path")
    replayed_records=[]
    for pair,record in records.items():
        entry=registered[pair]
        modern=intents[pair].get("publication")=="JOURNAL_FIRST"
        chain=launch_transition_chain(run,snapshot,pair,entry,modern=modern)
        if chain:
            if record not in chain:
                raise QSError("launch_transition_projection_conflict")
            records[pair]=chain[-1]
            replayed_records.extend(chain[1:])
    for pair,record in records.items():
        registered[pair]["launch_state"]=record["state"]
    return launches,replayed_records


def recovered_event_projection(run, transactions, intents, launches):
    try:
        events=load_state(run,"events")
        entries=events["events"]
        if not isinstance(entries,list): raise QSError("event_projection_contract")
    except (QSError,KeyError):
        events={"schema_version":SCHEMA,"run_id":run.name,"events":[]}
        entries=events["events"]
    verified=[]; seen=set()

    def retain(event):
        payload={key:value for key,value in event.items() if key!="event_hash"}
        digest=sha(canon_yaml(payload))
        if event.get("event_hash")==digest and digest not in seen:
            verified.append(event)
            seen.add(digest)

    def add(payload):
        retain({**payload,"event_hash":sha(canon_yaml(payload))})

    for event in entries:
        if isinstance(event,dict): retain(event)
    for intent in intents.values():
        if intent.get("publication")!="JOURNAL_FIRST": continue
        assignment=intent["assignment"]
        add({"event":"ASSIGNMENT_REGISTERED","assignment_id":assignment["assignment_id"],
             "attempt_id":assignment["attempt_id"],"assignment_sha256":intent["assignment_sha256"]})
    for record in launches:
        add({"event":"LAUNCH_INTENT_RECORDED","assignment_id":record["assignment_id"],
             "attempt_id":record["attempt_id"],"assignment_sha256":record["assignment_sha256"],"state":record["state"]})
    for _,entry in transactions:
        add({"event":"RESULT_ACCEPTED","result_id":entry["result_id"],"content_hash":entry["content_hash"],
             "assignment_id":entry["assignment_id"]})
    return {**events,"events":verified}


def _recover_locked(args: argparse.Namespace, run: Path, projection_override: Any = None) -> dict[str,Any]:
    try:
        current=load_state(run,"current"); snap=load_yaml(run/"input/snapshot.yaml")
        if sha((run/"input/snapshot.yaml").read_bytes())!=current.get("input_snapshot") or not isinstance(snap,dict): raise QSError("input_snapshot_drift")
        if snap.get("input_mode") in NEW_MODES:
            # New-mode inputs are goal/material (and later exact admission),
            # not a legacy original-bank pair. `accepted` checks every saved
            # input/result anchor before replaying mutable indexes.
            accepted(run,recovering=True)
            current=replay_prep_admission_if_needed(run,current,snap)
            current=replay_current_transitions_if_needed(run,current,snap)
        else:
            raw=snap.get("original_bank",{}); raw_path=child(run,raw.get("path", "")) if isinstance(raw,dict) else None
            if not isinstance(raw,dict) or raw.get("provided")!="true" or not raw_path or not raw_path.is_file() or sha(raw_path.read_bytes())!=raw.get("sha256"): raise QSError("original_snapshot_drift")
            current=replay_current_transitions_if_needed(run,current,snap)
        _question_projection(command_journal(run))
    except QSError as exc: return {"status":"FAIL","errors":["index_or_input_corrupt:"+str(exc)],"scope":"STRUCTURAL_ONLY"}
    transactions=[]; tx_errors=[]; pending_results={}; committed_contexts={}; transaction_ids=set(); transaction_paths=set(); transaction_pairs=set()
    prior_index=state_file(run,"assignments")
    prior_index_hash=sha(captured_argument(prior_index,64 * 1024**2).raw) if prior_index.is_file() else None
    for path in sorted((run/"transactions").glob("*.yaml")):
        try:
            tx=load_yaml(captured_argument(path,48 * 1024**2))
            if not isinstance(tx,dict): raise QSError("invalid_transaction")
            entry=tx.get("entry")
            out=child(run,entry.get("path", "")) if isinstance(entry,dict) else None
            assignment_bytes=base64.b64decode(tx.get("assignment_bytes_b64", ""), validate=True)
            result_bytes=base64.b64decode(tx.get("result_bytes_b64", ""), validate=True)
            legacy = tx.get("status")=="COMMITTED_LEGACY_BOOTSTRAP" and tx.get("assignment_sha256")=="LEGACY_VERIFIED_REGISTRY" and tx.get("assignment_bytes_b64")=="" and tx.get("assignment")=={}
            published=out is not None and out.is_file()
            out_raw=captured_argument(out).raw if published else None
            pending=(out is not None and not out.exists() and tx.get("result_publication")=="JOURNAL_FIRST"
                     and prior_index_hash is not None and tx.get("before_assignments_sha256")==prior_index_hash and not legacy)
            if (not isinstance(tx,dict) or (tx.get("status")!="COMMITTED" and not legacy) or tx.get("run_id")!=args.run_id
                or tx.get("input_snapshot")!=current.get("input_snapshot") or not isinstance(entry,dict)
                or not isinstance(tx.get("assignment"),dict) or (not legacy and sha(assignment_bytes)!=tx.get("assignment_sha256"))
                or (not pending and (not published or result_bytes!=out_raw))
                or sha(result_bytes)!=tx.get("result_sha256")
                or entry.get("content_hash")!=tx.get("result_sha256")
                or (not legacy and tx["assignment"].get("assignment_id")!=entry.get("assignment_id"))
                or (not legacy and tx["assignment"].get("attempt_id")!=entry.get("attempt_id"))):
                raise QSError("invalid_transaction")
            result_id=ident(entry.get("result_id"),"result_id")
            if tx.get("request_origin") in ("EXPLICIT","REGISTERED_ASSIGNMENT") and (
                not isinstance(entry.get("artifact_type"),str) or entry["artifact_type"] not in ARTIFACT_TYPES
                or tx.get("receipt")!=result_commit_receipt(entry)
            ):
                raise QSError("invalid_transaction_receipt")
            assignment_id=ident(entry.get("assignment_id"),"assignment_id")
            attempt_id=ident(entry.get("attempt_id"),"attempt_id")
            pair=assignment_id+"\0"+attempt_id
            if (path.name!=result_id+".yaml" or tx.get("transaction_id")!="TX-"+result_id
                or result_id in transaction_ids or out in transaction_paths or pair in transaction_pairs):
                raise QSError("transaction_identity")
            transaction_ids.add(result_id)
            transaction_paths.add(out)
            transaction_pairs.add(pair)
            result=load_yaml(CapturedInput(str(out),result_bytes)) if pending or not legacy else None
            if isinstance(result,dict) and "context_packet_sha256" in result:
                digest=result["context_packet_sha256"]
                if not isinstance(digest,str):
                    raise QSError("result_context_binding")
                committed_contexts[result_id]=digest
            if pending:
                if (common(result,args.run_id,current["input_snapshot"])
                    or output_path(run,tx["assignment"].get("output_path"))!=out
                    or out.relative_to(run).parts[0] not in expected_root(entry.get("artifact_type"))
                    or any(result.get(key)!=entry.get(field) for key,field in (
                        ("id","result_id"),("assignment_id","assignment_id"),("attempt_id","attempt_id"),
                        ("artifact_type","artifact_type"),("author_role","role_id"),("agent_id","agent_id"),
                    )) or result.get("workflow_status")!="ACCEPTED"):
                    raise QSError("invalid_pending_result")
                pending_results[out]=result_bytes
            transactions.append((tx,entry))
        except (QSError, ValueError, OSError, UnicodeDecodeError, binascii.Error): tx_errors.append(str(path.relative_to(run)))
    # Bootstrap of historic records is explicit: no unknown registry entry is
    # silently promoted to an immutable commit during ordinary recovery.
    if not transactions and not tx_errors:
        if not getattr(args,"bootstrap_legacy",False):
            try:
                legacy=load_state(run,"assignments")
                if legacy.get("results"): return {"status":"FAIL","errors":["legacy_bootstrap_required"],"scope":"STRUCTURAL_ONLY"}
            except QSError: pass
        else:
            try:
                legacy=load_state(run,"assignments")
                for entry in legacy.get("results",{}).values():
                    out=child(run,entry.get("path", ""))
                    if not isinstance(entry,dict) or not out.is_file() or sha(out.read_bytes())!=entry.get("content_hash"): raise QSError("legacy_registry_unverified")
                    # A legacy transaction deliberately cannot pretend to carry an
                    # assignment intent that was never durably recorded.
                    tx={"schema_version":SCHEMA,"transaction_id":"TX-"+entry["result_id"],"run_id":args.run_id,"input_snapshot":current["input_snapshot"],"assignment_sha256":"LEGACY_VERIFIED_REGISTRY","assignment_bytes_b64":"","assignment":{},"result_sha256":entry["content_hash"],"result_bytes_b64":base64.b64encode(out.read_bytes()).decode("ascii"),"entry":entry,"status":"COMMITTED_LEGACY_BOOTSTRAP"}; save_yaml(run/"transactions"/(entry["result_id"]+".yaml"),tx); transactions.append((tx,entry))
            except QSError as exc: return {"status":"FAIL","errors":["legacy_bootstrap_failed:"+str(exc)],"scope":"STRUCTURAL_ONLY"}
    if tx_errors: return {"status":"FAIL","errors":["invalid_transactions"],"transactions":tx_errors,"scope":"STRUCTURAL_ONLY"}
    rebuilt={entry["result_id"]:entry for _,entry in transactions}; indexed_paths={e.get("path") for e in rebuilt.values()}
    orphans=[]
    for directory in ROOT_DIRS:
        for path in (run/directory).rglob("*.yaml"):
            if str(path.relative_to(run)) not in indexed_paths: orphans.append(str(path.relative_to(run)))
    if orphans: return {"status":"FAIL","errors":["recovery_orphan_results"],"orphan_results":orphans,"accepted_result_ids":sorted(rebuilt),"scope":"STRUCTURAL_ONLY"}
    try: state=load_state(run,"assignments")
    except QSError: state={"schema_version":SCHEMA,"run_id":args.run_id,"input_snapshot":current["input_snapshot"],"registered":{},"attempts":{},"results":{}}
    recovered_registered={}; intent_by_pair={}
    for path in sorted((run/"intents").glob("*.yaml")):
        try:
            intent=load_yaml(path); assignment=intent.get("assignment") if isinstance(intent,dict) else None
            raw_b64=intent.get("assignment_bytes_b64","") if isinstance(intent,dict) else ""
            raw=base64.b64decode(raw_b64,validate=True)
            parsed_raw=load_yaml(CapturedInput(str(path)+" embedded assignment",raw))
            if not isinstance(intent,dict) or not isinstance(assignment,dict) or intent.get("status")!="ASSIGNED" or intent.get("run_id")!=args.run_id or intent.get("input_snapshot")!=current["input_snapshot"] or sha(raw)!=intent.get("assignment_sha256") or parsed_raw!=assignment or not core_metadata_matches(intent,assignment): raise QSError("intent")
            key=ident(assignment.get("assignment_id"),"assignment_id")+"\0"+ident(assignment.get("attempt_id"),"attempt_id")
            if key in intent_by_pair or path.name!=sha(key.encode()).upper()+".yaml": raise QSError("intent_key")
            if intent.get("publication")=="JOURNAL_FIRST" and (
                intent.get("predecessor_attempt_id")!=assignment.get("supersedes_attempt_id","ABSENT")
                or intent.get("receipt")!=assignment_receipt(assignment)):
                raise QSError("intent_projection_binding")
            intent_by_pair[key]=intent
            recovered_registered[key]={"assignment":assignment,"assignment_sha256":intent.get("assignment_sha256"),"status":"ACCEPTED" if key in {e["assignment_id"]+"\0"+e["attempt_id"] for e in rebuilt.values()} else "ASSIGNED","launch_state":assignment.get("launch_state","NOT_LAUNCHED"),**core_metadata(assignment)}
        except (QSError, ValueError, UnicodeDecodeError, binascii.Error): return {"status":"FAIL","errors":["invalid_assignment_intent"],"scope":"STRUCTURAL_ONLY"}
    for tx, entry in transactions:
        pair=entry["assignment_id"]+"\0"+entry["attempt_id"]
        if tx.get("status")=="COMMITTED" and (pair not in intent_by_pair or tx.get("assignment_bytes_b64") != intent_by_pair[pair].get("assignment_bytes_b64") or tx.get("assignment_sha256") != intent_by_pair[pair].get("assignment_sha256") or not core_metadata_matches(tx,entry) or not core_metadata_matches(tx.get("assignment"),entry) or not core_metadata_matches(tx.get("entry"),entry)):
            return {"status":"FAIL","errors":["transaction_intent_binding_drift"],"scope":"STRUCTURAL_ONLY"}
    context_state={"results":rebuilt,"attempts":{
        entry["assignment_id"]+"\0"+entry["attempt_id"]:{"result_id":rid,"content_hash":entry["content_hash"]}
        for rid,entry in rebuilt.items()}}
    for tx,entry in transactions:
        result_id=entry["result_id"]
        if result_id in committed_contexts:
            try:
                resolve_context_revision(run,tx["assignment"],tx["assignment_sha256"],context_state,committed_contexts[result_id])
            except (QSError,OSError,ValueError) as exc:
                return {"status":"FAIL","errors":["result_context_recovery:"+result_id+":"+str(exc)],"scope":"STRUCTURAL_ONLY"}
    try:
        launches,launch_events=recover_launch_projection(run,current["input_snapshot"],recovered_registered,intent_by_pair)
        for pair,entry in recovered_registered.items():
            assignment=entry["assignment"]
            if terminal_attempt(run,assignment["assignment_id"],assignment["attempt_id"]):
                if entry["status"]=="ACCEPTED": raise QSError("terminal_attempt_has_accepted_result")
                entry["status"]="REJECTED"
    except (QSError,OSError,ValueError) as exc:
        return {"status":"FAIL","errors":["launch_intent_recovery:"+str(exc)],"scope":"STRUCTURAL_ONLY"}
    # Retained retries need one explicit current attempt. A single historical
    # attempt is backwards-compatible; multiple attempts never use id ordering.
    prior_current=state.get("current_attempts",{})
    if not isinstance(prior_current,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in prior_current.items()): return {"status":"FAIL","errors":["current_attempts_corrupt"],"scope":"STRUCTURAL_ONLY"}
    grouped:dict[str,set[str]]={}
    for entry in recovered_registered.values():
        assignment=entry["assignment"]; grouped.setdefault(assignment["assignment_id"],set()).add(assignment["attempt_id"])
    current_attempts:dict[str,str]={}
    for assignment_id,attempts in grouped.items():
        selected=prior_current.get(assignment_id)
        successor={}
        for attempt_id in attempts:
            intent=intent_by_pair[assignment_id+"\0"+attempt_id]
            if intent.get("publication")!="JOURNAL_FIRST": continue
            predecessor=intent["predecessor_attempt_id"]
            if predecessor in successor or predecessor!="ABSENT" and predecessor not in attempts:
                return {"status":"FAIL","errors":["current_attempt_lineage:"+assignment_id],"scope":"STRUCTURAL_ONLY"}
            successor[predecessor]=attempt_id
        if selected is None: selected=next(iter(attempts)) if len(attempts)==1 else successor.get("ABSENT")
        seen=set()
        while selected in successor and selected not in seen:
            seen.add(selected)
            selected=successor[selected]
        if selected not in attempts or selected in seen:
            return {"status":"FAIL","errors":["current_attempt_missing_or_invalid:"+assignment_id],"scope":"STRUCTURAL_ONLY"}
        current_attempts[assignment_id]=selected
    if set(prior_current)-set(grouped): return {"status":"FAIL","errors":["current_attempt_unknown_assignment"],"scope":"STRUCTURAL_ONLY"}
    current_digest=sha(captured_argument(state_file(run,"current")).raw)
    pending_selectors=[(tx,entry) for tx,entry in transactions
                       if tx.get("selection_before_current_sha256")==current_digest]
    try:
        if len(pending_selectors)>1: raise QSError("recovery_selection_fork")
        if pending_selectors:
            tx,entry=pending_selectors[0]
            kind=entry.get("artifact_type")
            if kind not in REVISION_SELECTIONS: raise QSError("recovery_selection_kind")
            result=load_yaml(CapturedInput(entry["path"],base64.b64decode(tx["result_bytes_b64"],validate=True)))
            previous={**state,"results":{rid:row for rid,row in rebuilt.items() if rid!=entry["result_id"]}}
            _validate_revision_candidate(run,previous,current,result,kind)
        for destination,raw in pending_results.items():
            atomic(destination,raw)
        if pending_selectors:
            _select_revision_locked(run,{**state,"results":rebuilt},current,entry["result_id"],kind)
            current=load_state(run,"current")
    except (QSError, OSError, ValueError, binascii.Error) as exc:
        return {"status":"FAIL","errors":["result_projection_recovery:"+str(exc)],"scope":"STRUCTURAL_ONLY"}
    old_ids=set(state.get("results",{})); rebuilt_ids=set(rebuilt)
    replayed=old_ids!=rebuilt_ids
    state["results"]=rebuilt; state["registered"]=recovered_registered; state["current_attempts"]=current_attempts; state["attempts"]={e["assignment_id"]+"\0"+e["attempt_id"]:{"result_id":e["result_id"],"content_hash":e["content_hash"]} for e in rebuilt.values()}
    try:
        plan_projection_for_run(run,state,current,projection_override,allow_stale=True)
        source_selection=resolve_current_selection(run,state,current,"SOURCE_OUTCOMES",allow_stale=True)[2]
        audit_selection=resolve_current_selection(run,state,current,"FINAL_AUDIT",allow_stale=True)[2]
        iceberg_projection,_iceberg_history=iceberg_projection_for_run(
            run,state,current,allow_partial=True,allow_stale_heads=True
        )
        recovery_bank=load_yaml(run/"input/bank.yaml") if (run/"input/bank.yaml").is_file() else {"questions":[],"requirements":[]}
        if not isinstance(recovery_bank,dict): raise QSError("architecture_bank_contract")
        architecture=architecture_projection_for_run(
            run,state,current,recovery_bank,supplied_plan_projection=projection_override,
            allow_unselected=True,allow_stale=True,allow_incomplete=True,
        )
    except QSError as exc: return {"status":"FAIL","errors":["plan_projection_recovery:"+str(exc)],"scope":"STRUCTURAL_ONLY"}
    events=recovered_event_projection(run,transactions,intent_by_pair,launch_events)
    recovered={"assignments":state,"events":events}
    if launches["records"]: recovered["launch-intents"]=launches
    for name,value in recovered.items():
        target=state_file(run,name)
        raw=canon_yaml(value)
        if not target.is_file() or captured_argument(target,64 * 1024**2).raw!=raw:
            atomic(target,raw)
    pending=[]
    for entry in state.get("registered",{}).values():
        if isinstance(entry,dict) and entry.get("status")=="ASSIGNED":
            assignment=entry.get("assignment",{})
            if not terminal_attempt(run,assignment.get("assignment_id"),assignment.get("attempt_id")):
                pending.append(assignment.get("assignment_id"))
    stale_ids={item for item in current.get("stale_result_ids",[]) if isinstance(item,str)}
    iceberg_state={kind:("NEEDS_RECHECK" if binding.get("result_id") in stale_ids else "CURRENT") for kind,binding in iceberg_projection.items()}
    return {"status":"RECOVERED","accepted_result_ids":sorted(rebuilt),"pending_assignments":sorted(x for x in pending if isinstance(x,str)),"replayed_index":replayed,"input_snapshot":current["input_snapshot"],"current_source_outcomes":source_selection,"current_final_audit":audit_selection,"current_iceberg_views":iceberg_projection,"current_iceberg_views_sha256":sha(canon_yaml(iceberg_projection)),"iceberg_selection_state":iceberg_state,**architecture_checkpoint_fields(architecture),"scope":"STRUCTURAL_ONLY"}

def administrative_review_binding(run: Path, state: dict[str,Any], current: dict[str,Any], snapshot: str) -> dict[str,str] | None:
    """Resolve the one post-audit review which may sit outside the core.

    This deliberately derives the path from the accepted GATE-6 relationship;
    a filename is never an authorization to omit a review from the pre-audit
    manifest.  Callers treat a missing binding as invalid rather than silently
    excluding anything.
    """
    try:
        audit,_audit_doc,_selector=resolve_current_selection(run,state,current,"FINAL_AUDIT")
        if not isinstance(audit,dict): return None
        candidates=[]
        legacy=run/"gates/GATE-6.yaml"
        if legacy.is_file(): candidates.append(legacy)
        versioned=run/"gates/GATE-6"/(str(audit.get("result_id"))+".yaml")
        if versioned.is_file(): candidates.append(versioned)
        matches=[]
        for gate6_path in candidates:
            gate6=load_yaml(gate6_path)
            content=gate6.get("content_review") if isinstance(gate6,dict) else None
            review_id=content.get("result_id") if isinstance(content,dict) else None
            review=state.get("results",{}).get(review_id) if isinstance(review_id,str) else None
            if not (isinstance(content,dict) and isinstance(review,dict)
                    and review.get("artifact_type")=="REVIEW" and gate6.get("input_snapshot")==snapshot
                    and content.get("sha256")==review.get("content_hash")): continue
            path=review.get("path")
            if not isinstance(path,str): continue
            doc=load_yaml(child(run,path))
            if not isinstance(doc,dict) or doc.get("target_result_id")!=audit.get("result_id") or doc.get("target_hash")!=audit.get("content_hash"): continue
            refs=gate6.get("evidence_refs")
            if not isinstance(refs,list) or not any(isinstance(ref,dict) and ref.get("result_id")==audit.get("result_id") and ref.get("sha256")==audit.get("content_hash") for ref in refs): continue
            matches.append({"result_id":review_id,"path":path,"sha256":review["content_hash"],"audit_result_id":audit["result_id"],"audit_sha256":audit["content_hash"],"gate6_path":str(gate6_path.relative_to(run)),"gate6_sha256":sha(gate6_path.read_bytes())})
        return matches[0] if len(matches)==1 else None
    except QSError:
        return None

def core_files(run: Path, state: dict[str,Any], snapshot: str, current: dict[str,Any] | None = None) -> list[Path]:
    """Return immutable pre-audit bytes for the current graph, not its history."""
    binding=administrative_review_binding(run,state,current or {},snapshot) if current is not None else None
    audit_ids={rid for rid,entry in state.get("results",{}).items() if isinstance(entry,dict) and entry.get("artifact_type")=="FINAL_AUDIT"}
    excluded=set()
    for rid,entry in state.get("results",{}).items():
        if not isinstance(entry,dict) or entry.get("artifact_type")!="REVIEW": continue
        try: doc=load_yaml(child(run,entry.get("path","")))
        except QSError: continue
        if isinstance(doc,dict) and doc.get("target_result_id") in audit_ids: excluded.add(entry.get("path"))
    if binding: excluded.add(binding["path"])
    if current is not None:
        _iceberg_projection,iceberg_history=iceberg_projection_for_run(
            run,state,current,allow_partial=False
        )
        for result_id in iceberg_history:
            entry=state.get("results",{}).get(result_id)
            if isinstance(entry,dict) and isinstance(entry.get("path"),str): excluded.add(entry["path"])
    selected_plan_paths: set[str] | None=None
    if current is not None:
        mapping,_=plan_projection_for_run(run,state,current)
        selected_plan_paths={entry.get("path") for rid,entry in state.get("results",{}).items() if rid in mapping and isinstance(entry,dict) and isinstance(entry.get("path"),str)}
    files=[]
    for directory in ("input","positions","answers","hypotheses","evidence","decisions","reviews","operators","experiments","synthesis","plan"):
        files.extend(sorted(p for p in (run/directory).rglob("*") if p.is_file() and str(p.relative_to(run)) not in excluded and (directory!="plan" or selected_plan_paths is None or str(p.relative_to(run)) in selected_plan_paths)))
    files.extend(sorted(p for p in (run/"gates").glob("GATE-[1-5].yaml") if p.is_file()))
    if current is not None and _question_arm(run,current) is not None:
        journal=command_journal(run)
        last=max((index for index,row in enumerate(journal,1) if "question_events" in row["receipt"]),default=0)
        files.extend(child(run,"transactions/commands/"+f"{index:08d}.yaml") for index in range(1,last+1))
    return files

def seal(args: argparse.Namespace) -> dict[str,Any]:
    return locked_command(args, _seal_locked)


def _seal_locked(args: argparse.Namespace, run: Path) -> dict[str,Any]:
    release_id=ident(args.release_id,"release_id")
    args.phase = "core" if args.phase == "prepare" else "final"
    check=_validate_locked(args,run)
    if check["status"]=="FAIL": raise QSError("cannot_seal_invalid_run")
    state,current,bank=accepted(run); audit_entry,audit_data,audit_selector=resolve_current_selection(run,state,current,"FINAL_AUDIT"); core=core_files(run,state,current["input_snapshot"],current)
    files={str(p.relative_to(run)):sha(p.read_bytes()) for p in core}; manifest={"release_id":release_id,"run_id":args.run_id,"input_snapshot":current["input_snapshot"],"files":files}
    snapshot_doc=load_yaml(run/"input/snapshot.yaml")
    architecture=architecture_projection_for_run(run,state,current,bank)
    architecture_ref=architecture.get("architecture_ref")
    if isinstance(architecture_ref,dict): manifest["architecture_ref"]=architecture_ref
    iceberg_projection:dict[str,dict[str,Any]]={}
    if double_iceberg_enabled(snapshot_doc):
        iceberg_projection,_iceberg_history=iceberg_projection_for_run(run,state,current,allow_partial=False)
        manifest["current_iceberg_views"]=iceberg_projection
        manifest["current_iceberg_views_sha256"]=sha(canon_yaml(iceberg_projection))
    manifest_hash=sha(canon_yaml(manifest))
    prepared=child(run/"state/release-cores",release_id+".yaml")
    if args.phase == "core":
        value={"schema_version":SCHEMA,"release_id":release_id,"core_manifest":manifest,"core_manifest_sha256":manifest_hash}
        if prepared.exists() and prepared.read_bytes()!=canon_yaml(value): raise QSError("prepared_release_id_conflict")
        if not prepared.exists(): save_yaml(prepared,value)
        return {"status":"CORE_PREPARED","release_id":release_id,"core_manifest_sha256":manifest_hash,"prepared_path":str(prepared.relative_to(run)),**({"current_iceberg_views":iceberg_projection,"current_iceberg_views_sha256":sha(canon_yaml(iceberg_projection))} if double_iceberg_enabled(snapshot_doc) else {}),**architecture_checkpoint_fields(architecture),"scope":"STRUCTURAL_ONLY"}
    if not isinstance(audit_entry,dict) or not isinstance(audit_data,dict) or not isinstance(audit_selector,dict): raise QSError("current_final_audit_missing")
    if isinstance(architecture_ref,dict) and audit_data.get("architecture_ref")!=architecture_ref:
        raise QSError("audit_architecture_ref_mismatch")
    prior=load_yaml(prepared)
    if not isinstance(prior,dict) or prior.get("release_id")!=release_id or prior.get("core_manifest")!=manifest or prior.get("core_manifest_sha256")!=manifest_hash: raise QSError("prepared_core_missing_or_drifted")
    if audit_data.get("core_manifest_sha256") != manifest_hash: raise QSError("audit_core_hash_mismatch")
    administrative=administrative_review_binding(run,state,current,current["input_snapshot"])
    if administrative is None: raise QSError("gate6_administrative_review_binding")
    gate6=child(run,administrative["gate6_path"])
    _,projection=plan_projection_for_run(run,state,current)
    release={"schema_version":SCHEMA,"release_id":release_id,"run_id":args.run_id,"input_snapshot":current["input_snapshot"],"core_manifest":manifest,"core_manifest_sha256":manifest_hash,"audit_result_id":audit_selector["result_id"],"audit_path":audit_selector["path"],"audit_sha256":audit_selector["sha256"],"gate6_path":administrative["gate6_path"],"gate6_sha256":administrative["gate6_sha256"],"administrative_review":administrative,"current_plan_projection":projection,"current_plan_projection_sha256":sha(canon_yaml(projection)),**({"current_iceberg_views":iceberg_projection,"current_iceberg_views_sha256":sha(canon_yaml(iceberg_projection))} if double_iceberg_enabled(snapshot_doc) else {}),"verdict":audit_data.get("verdict"),"readiness":audit_data.get("readiness")}
    if isinstance(architecture_ref,dict): release["architecture_ref"]=architecture_ref
    raw=canon_yaml(release)
    release_hash=sha(raw)
    archive=child(run/"releases",release_id+".yaml")
    if archive.exists() and captured_argument(archive,48 * 1024**2).raw!=raw: raise QSError("release_id_conflict")
    if load_state(run,"current")!=current: raise QSError("release_current_changed")
    receipt={"status":"SEALED","release_id":release_id,"verdict":audit_data.get("verdict"),**architecture_checkpoint_fields(architecture),"scope":"STRUCTURAL_ONLY"}
    if current.get("status")=="SEALED" and current.get("release_id")==release_id:
        if not archive.is_file():
            raise QSError("sealed_release_archive_missing")
        if current.get("release_sha256")!=release_hash or captured_argument(child(run,"release.yaml"),48 * 1024**2).raw!=raw:
            raise QSError("sealed_release_drift")
        return receipt
    if not archive.exists(): atomic(archive,raw)
    after={**current,"status":"SEALED","release_id":release_id,"release_sha256":release_hash,"verdict":audit_data.get("verdict")}
    commit_current_transition(run,current,after,"RELEASE_SEAL",[
        {"kind":"RELEASE_ARCHIVE","path":str(archive.relative_to(run)),"sha256":release_hash}],release_raw=raw)
    return receipt


def verify(args: argparse.Namespace) -> dict[str,Any]:
    run=run_dir(root(args.root),args.run_id); errors=[]
    try: release=load_yaml(run/"release.yaml"); current=load_state(run,"current")
    except QSError as exc: return {"status":"FAIL","errors":[str(exc)],"scope":"STRUCTURAL_ONLY"}
    manifest=release.get("core_manifest",{}) if isinstance(release,dict) else {}; expected=sha(canon_yaml(manifest))
    if release.get("core_manifest_sha256")!=expected: errors.append("manifest_hash")
    for rel,digest in manifest.get("files",{}).items() if isinstance(manifest,dict) else []:
        p=child(run,rel)
        if not p.is_file() or sha(p.read_bytes())!=digest: errors.append("core_tamper:"+str(rel))
    audit=child(run,release.get("audit_path","") if isinstance(release,dict) and isinstance(release.get("audit_path"),str) else "")
    gate6=child(run,release.get("gate6_path","") if isinstance(release,dict) and isinstance(release.get("gate6_path"),str) else "")
    if not audit.is_file() or release.get("audit_sha256")!=sha(audit.read_bytes()): errors.append("audit_tamper")
    if not gate6.is_file() or release.get("gate6_sha256")!=sha(gate6.read_bytes()): errors.append("gate6_tamper")
    administrative=release.get("administrative_review") if isinstance(release,dict) else None
    if not isinstance(administrative,dict):
        errors.append("administrative_review_binding")
    else:
        review_path=administrative.get("path")
        if not isinstance(review_path,str): errors.append("administrative_review_tamper")
        else:
            try:
                review=child(run,review_path)
                if not review.is_file() or sha(review.read_bytes())!=administrative.get("sha256"):
                    errors.append("administrative_review_tamper")
            except QSError:
                errors.append("administrative_review_tamper")
        try:
            state, checked_current, checked_bank=accepted(run)
            if checked_current.get("input_snapshot")!=release.get("input_snapshot") or administrative_review_binding(run,state,checked_current,checked_current["input_snapshot"])!=administrative:
                errors.append("administrative_review_binding")
            _audit_entry,_audit_doc,selected_audit=resolve_current_selection(run,state,checked_current,"FINAL_AUDIT")
            if not isinstance(selected_audit,dict) or release.get("audit_result_id")!=selected_audit.get("result_id") or release.get("audit_path")!=selected_audit.get("path") or release.get("audit_sha256")!=selected_audit.get("sha256"):
                errors.append("current_final_audit_tamper")
            _,projection=plan_projection_for_run(run,state,checked_current)
            if release.get("current_plan_projection")!=projection or release.get("current_plan_projection_sha256")!=sha(canon_yaml(projection)):
                errors.append("current_plan_projection_tamper")
            architecture=architecture_projection_for_run(run,state,checked_current,checked_bank)
            architecture_ref=architecture.get("architecture_ref")
            if isinstance(architecture_ref,dict):
                if release.get("architecture_ref")!=architecture_ref or manifest.get("architecture_ref")!=architecture_ref:
                    errors.append("architecture_ref_tamper")
                if not isinstance(_audit_doc,dict) or _audit_doc.get("architecture_ref")!=architecture_ref:
                    errors.append("audit_architecture_ref_tamper")
            snapshot_doc=load_yaml(run/"input/snapshot.yaml")
            if double_iceberg_enabled(snapshot_doc):
                iceberg_projection,_iceberg_history=iceberg_projection_for_run(run,state,checked_current,allow_partial=False)
                if (release.get("current_iceberg_views")!=iceberg_projection
                    or release.get("current_iceberg_views_sha256")!=sha(canon_yaml(iceberg_projection))
                    or manifest.get("current_iceberg_views")!=iceberg_projection
                    or manifest.get("current_iceberg_views_sha256")!=sha(canon_yaml(iceberg_projection))):
                    errors.append("current_iceberg_views_tamper")
        except QSError as exc:
            errors.append("accepted_registry_or_review_drift:"+str(exc))
    if current.get("release_sha256")!=sha((run/"release.yaml").read_bytes()) or current.get("verdict")!=release.get("verdict"): errors.append("current_pointer")
    try:
        snapshot_doc=load_yaml(run/"input/snapshot.yaml")
        if isinstance(snapshot_doc,dict) and snapshot_doc.get("input_mode") in NEW_MODES:
            _snapshot, state, prep_current, inputs, prep_bank, approval, admission, prep_errors=prep_state(run)
            state_info=resolve_admission_state(snapshot_doc,prep_current,inputs,prep_bank,admission,_prep_transactions(run))
            if prep_errors or state_info.get("state")!="CORE_ADMITTED":
                errors.extend(sorted(set(prep_errors)))
                errors.append("prep:verify_admission_state:"+str(state_info.get("state")))
    except QSError as exc:
        errors.append("prep:verify_guard:"+str(exc))
    return {"status":"PASS" if not errors else "FAIL","errors":sorted(set(errors)),"scope":"STRUCTURAL_ONLY"}

def render(args: argparse.Namespace) -> dict[str,Any]:
    output=Path(args.output).absolute()
    view=getattr(args,"architecture_view",None)
    if view is None:
        if (getattr(args,"root",None) or getattr(args,"run_id",None) or getattr(args,"subsystem",None)
            or getattr(args,"goal_path",False) or getattr(args,"changed_node",[])):
            raise QSError("render_architecture_view_required")
        if not args.source: raise QSError("render_source_required")
        source=Path(args.source).absolute(); load_yaml(source)
        atomic(output,("# Derived YAML view\n\n```yaml\n"+source.read_text(encoding="utf-8")+"\n```\n").encode("utf-8"))
        return {"status":"RENDERED","source_sha256":sha(source.read_bytes()),"scope":"STRUCTURAL_ONLY"}
    if args.source: raise QSError("architecture_render_uses_current_plan_not_source")
    if not args.root or not args.run_id: raise QSError("architecture_render_bound_run_required")
    run=run_dir(root(args.root),args.run_id)
    # Derived artifacts cannot overwrite accepted source bytes or coordinator
    # state.  A run-local rendering belongs only in its separate derived tree.
    try: relative=output.resolve().relative_to(run.resolve())
    except ValueError: relative=None
    if relative is not None and (len(relative.parts)<2 or relative.parts[0]!="derived"):
        raise QSError("architecture_render_output_must_be_external_or_derived")
    state,current,bank=accepted(run)
    architecture=architecture_projection_for_run(run,state,current,bank)
    graph=architecture.get("graph"); reference=architecture.get("architecture_ref")
    if not isinstance(graph,dict) or not isinstance(reference,dict):
        raise QSError("architecture_render_current_graph_required")
    changed=list(getattr(args,"changed_node",[]) or [])
    if len(changed)!=len(set(changed)): raise QSError("architecture_changed_node_duplicate")
    mermaid=render_architecture_mermaid(
        graph,view,subsystem_id=getattr(args,"subsystem",None),goal_path=getattr(args,"goal_path",False),
    )
    selected_view=derive_architecture_views(
        graph,subsystem_id=getattr(args,"subsystem",None),goal_path=getattr(args,"goal_path",False),
    )["data_contracts" if view=="data" else view]
    impact=architecture_impact(graph,changed) if changed else None
    filters={"goal_path":"true" if getattr(args,"goal_path",False) else "false"}
    if getattr(args,"subsystem",None): filters["subsystem_id"]=args.subsystem
    text="# Derived architecture view: "+view+"\n\n"
    text+="Scope: STRUCTURAL_ONLY. The accepted SYNTHESIS graph is the canonical source.\n\n"
    text+="```yaml\n"+canon_yaml({"architecture_ref":reference,"current_plan_projection_sha256":architecture["current_plan_projection_sha256"],"filters":filters}).decode("utf-8")+"```\n\n"
    if view=="functional":
        mechanisms=[{"id":item["id"],"status":item["status"]} for item in graph.get("mechanisms",[]) if isinstance(item,dict)]
        text+="## Mechanisms\n\n```yaml\n"+canon_yaml({"mechanisms":sorted(mechanisms,key=lambda item:item["id"])}).decode("utf-8")+"```\n\n"
    text+="```mermaid\n"+mermaid.rstrip()+"\n```\n"
    boundaries={key:selected_view.get(key,[]) for key in ("internal_node_ids","boundary_node_ids","boundary_edges")}
    text+="\n## Subsystem boundaries\n\n```yaml\n"+canon_yaml(boundaries).decode("utf-8")+"```\n"
    cards=selected_view.get("detail",{}).get("contracts",selected_view.get("contracts",[]))
    text+="\n## Full contract cards\n\n```yaml\n"+canon_yaml({"contracts":cards}).decode("utf-8")+"```\n"
    if impact is not None:
        text+="\n## Change impact\n\n```json\n"+json.dumps(impact,ensure_ascii=False,sort_keys=True,indent=2)+"\n```\n"
    raw=text.encode("utf-8"); atomic(output,raw)
    return {"status":"RENDERED","architecture_view":view,"architecture_ref":reference,
            "current_plan_projection_sha256":architecture["current_plan_projection_sha256"],
            "filters":filters,"output":str(output),"sha256":sha(raw),**({"impact":impact} if impact is not None else {}),"scope":"STRUCTURAL_ONLY"}

PORTABLE_HELPERS=(
    "requirements.txt","schemas/command-admission.yaml","schemas/plan-stage.yaml","schemas/question-ledger.yaml",
    "scripts/architecture_validation.py","scripts/command_admission.py","scripts/model_validation.py",
    "scripts/orchestration.py","scripts/plan_semantics.py","scripts/prep_validation.py","scripts/qs.py",
    "scripts/question_admission.py","scripts/question_ledger.py",
)


def portable_subject(run: Path, task_id: str, check_id: str):
    state,current,_bank=accepted(run)
    results=state["results"]
    docs={}
    for result_id,entry in results.items():
        if not isinstance(entry,dict):
            raise QSError("portable_result_missing:"+result_id)
        document=load_bound_yaml(run,entry.get("path"),entry.get("content_hash"),"portable_result_bytes:"+result_id)
        if not isinstance(document,dict) or document.get("id")!=result_id:
            raise QSError("portable_result_binding:"+result_id)
        docs[result_id]=document
    selected,selection=plan_projection_for_run(run,state,current,documents=docs)
    for path in sorted(child(run,"transactions").glob("*.yaml")):
        raw=captured_argument(path,48 * 1024**2)
        tx=load_yaml(raw)
        entry=tx.get("entry") if isinstance(tx,dict) else None
        if not isinstance(entry,dict) or entry.get("result_id") not in results:
            raise QSError("portable_missing_result:"+path.stem)
        if entry!=results[entry["result_id"]]: raise QSError("portable_result_registry_binding:"+path.stem)
    tasks={}
    checks={}
    for result_id in selected:
        doc=docs.get(result_id)
        if not isinstance(doc,dict) or not isinstance(doc.get("tasks"),list):
            raise QSError("portable_selected_plan_missing:"+result_id)
        for task in doc["tasks"]:
            if not isinstance(task,dict): raise QSError("portable_task_contract:"+result_id)
            task_key=ident(task.get("id"),"task_id")
            if task_key in tasks: raise QSError("portable_task_duplicate:"+task_key)
            tasks[task_key]=(result_id,task)
            for check in task.get("checks",[]):
                check=ident(check,"check_id")
                if check in checks: raise QSError("portable_check_duplicate:"+check)
                checks[check]=task_key
    if task_id not in tasks or checks.get(check_id)!=task_id:
        raise QSError("portable_selected_task_check_binding")
    pending,withdrawn=intake_affected_results(run,state,documents=docs)
    persisted=current.get("stale_result_ids",[])
    if not isinstance(persisted,list) or any(not isinstance(item,str) for item in persisted):
        raise QSError("stale_result_ids_invalid")
    stale=set(persisted) | withdrawn

    def require_current(identifier, owner=None):
        if identifier in stale or owner in stale:
            raise QSError("portable_stale:"+identifier)
        if identifier in pending or owner in pending:
            raise QSError("portable_pending:"+identifier)

    for task_key,(owner,_) in tasks.items():
        require_current(task_key,owner)
    for check,task_key in checks.items():
        require_current(check,tasks[task_key][0])
    proof={}; paths=set(PORTABLE_HELPERS)
    queue=list(selected)
    evidence={}
    reviews={}
    digests={}
    for result_id,entry in results.items():
        doc=docs.get(result_id)
        if not isinstance(entry,dict) or not isinstance(doc,dict): raise QSError("portable_result_missing:"+result_id)
        digests.setdefault(entry.get("content_hash"),[]).append(result_id)
        if entry.get("artifact_type")=="EVIDENCE":
            evidence_key=doc.get("evidence_id",result_id)
            if evidence_key in evidence: raise QSError("portable_evidence_ambiguous:"+str(evidence_key))
            evidence[evidence_key]=result_id
        if entry.get("artifact_type")=="REVIEW":
            reviews.setdefault(doc.get("target_result_id"),[]).append(result_id)

    def relation(owner, field, target, result_id=None):
        key=owner+":"+field+":"+target
        if key in proof: raise QSError("portable_relation_duplicate:"+key)
        proof[key]=result_id or target
        if result_id is not None: queue.append(result_id)

    for task_key,(_owner,task) in tasks.items():
        for field,domain in (("e_refs",evidence),("d_refs",results),("dependencies",tasks),
                             ("predecessor_checks",checks),("checks",checks)):
            refs=task.get(field,[])
            if not isinstance(refs,list): raise QSError("portable_task_field:"+task_key+":"+field)
            for ref in refs:
                if not isinstance(ref,str) or ref not in domain:
                    raise QSError("portable_missing_relation:"+task_key+":"+field+":"+str(ref))
                result_id=evidence[ref] if field=="e_refs" else ref if field=="d_refs" else None
                if field=="d_refs" and results[ref].get("artifact_type")!="DECISION":
                    raise QSError("portable_decision_type:"+ref)
                relation(task_key,field,ref,result_id)
    inspected=set()
    while queue:
        result_id=queue.pop()
        if result_id in inspected: continue
        require_current(result_id)
        inspected.add(result_id)
        entry,doc=results[result_id],docs[result_id]
        rel=entry["path"]; paths.add("subject/"+rel)
        assignment_id,attempt_id=entry["assignment_id"],entry["attempt_id"]
        pair=assignment_id+"\0"+attempt_id
        txn=f"transactions/{result_id}.yaml"
        intent="intents/"+sha(pair.encode()).upper()+".yaml"
        for field,path in (("transaction",txn),("assignment_attempt",intent)):
            relation(result_id,field,path)
            paths.add("subject/"+path)
        tx=load_yaml(captured_argument(child(run,txn),48 * 1024**2))
        intent_doc=load_yaml(captured_argument(child(run,intent),48 * 1024**2))
        if (not isinstance(tx,dict) or tx.get("entry")!=entry
            or not isinstance(intent_doc,dict) or tx.get("assignment_bytes_b64")!=intent_doc.get("assignment_bytes_b64")
            or tx.get("assignment_sha256")!=intent_doc.get("assignment_sha256")
            or tx.get("result_sha256")!=entry["content_hash"]):
            raise QSError("portable_transaction_attempt_binding:"+result_id)
        packet_hash=doc.get("context_packet_sha256")
        if "context_packet_sha256" in doc:
            if not isinstance(packet_hash,str):
                raise QSError("portable_context_binding:"+result_id)
            try:
                revision=resolve_context_revision(run,tx["assignment"],tx["assignment_sha256"],state,packet_hash)
            except (QSError,OSError,ValueError) as exc:
                raise QSError("portable_context_binding:"+result_id) from exc
            packet=revision.path.relative_to(run).as_posix()
            relation(result_id,"context",packet)
            paths.add("subject/"+packet)
        for digest in entry.get("dependency_hashes",[]):
            owners=digests.get(digest,[])
            if len(owners)!=1: raise QSError("portable_dependency_binding:"+result_id+":"+str(digest))
            relation(result_id,"dependency",owners[0],owners[0])
        if entry.get("artifact_type") in {"EVIDENCE","DECISION"}:
            for field in ("grounds","evidence_ids","source_refs"):
                refs=doc.get(field,[])
                if not isinstance(refs,list): raise QSError("portable_provenance_field:"+result_id+":"+field)
                for ref in refs:
                    if not isinstance(ref,str): raise QSError("portable_provenance_reference:"+result_id+":"+field)
                    bound=evidence.get(ref)
                    if bound is not None:
                        relation(result_id,field,ref,bound)
                    elif field in {"grounds","evidence_ids"} or ref.startswith("E-"):
                        raise QSError("portable_missing_relation:"+result_id+":"+field+":"+ref)
                    elif ref.startswith("input/"):
                        if not child(run,ref).is_file(): raise QSError("portable_missing_relation:"+result_id+":"+field+":"+ref)
                        relation(result_id,field,ref)
                        paths.add("subject/"+ref)
        for review_id in sorted(reviews.get(result_id,[])):
            if docs[review_id].get("target_hash")!=entry["content_hash"]:
                raise QSError("portable_review_target_binding:"+review_id)
            relation(result_id,"review",review_id,review_id)
        if entry.get("artifact_type")=="REVIEW":
            review_target=doc.get("target_result_id")
            if review_target in results: relation(result_id,"review_target",review_target,review_target)
            blind=doc.get("blind_input_result_id")
            if blind is not None:
                if blind not in results or doc.get("blind_input_hash")!=results[blind].get("content_hash"):
                    raise QSError("portable_review_blind_binding:"+result_id)
                relation(result_id,"review_blind_input",blind,blind)
    # Full accepted history is retained for independent derivation and exact attempt/selector validation.
    paths.update("subject/"+entry["path"] for entry in results.values())
    for name in ("current","assignments"):
        paths.add("subject/state/"+name+".yaml")
    for directory in ("input","intents","transactions","state/contexts"):
        for path in child(run,directory).rglob("*"):
            if path.is_symlink(): raise QSError("portable_subject_symlink:"+str(path.relative_to(run)))
            if path.is_file(): paths.add("subject/"+path.relative_to(run).as_posix())
    return {"run_id":run.name,"input_snapshot":current["input_snapshot"],"task_id":task_id,"check_id":check_id,
            "plan":selection,"relations":proof},paths


def _portable_file_limit(relative):
    if relative.startswith("subject/transactions/contexts/revisions/"):
        return 66 * 1024**2
    return 48 * 1024**2


def _export_portable_locked(args, run):
    package=Path(__file__).resolve().parent.parent
    output=Path(args.output).absolute()
    if (output.exists() or output.is_symlink() or output.resolve()!=output
        or output.is_relative_to(package) or output.is_relative_to(run.parent)):
        raise QSError("portable_output_protected_or_exists")
    original,paths=portable_subject(run,ident(args.task_id,"task_id"),ident(args.check_id,"check_id"))
    manifest={"schema_version":SCHEMA,"original":original,"files":{}}
    with tempfile.TemporaryDirectory(prefix=".qs-portable-",dir=output.parent) as temporary:
        draft=Path(temporary)/"bundle"
        for relative in sorted(paths):
            if relative.startswith("subject/"):
                source=child(run,relative.removeprefix("subject/"))
                target=draft/"subject"/run.name/relative.removeprefix("subject/")
            else:
                source=child(package,relative)
                target=draft/relative
            raw=captured_argument(source,_portable_file_limit(relative)).raw
            atomic(target,raw)
            manifest["files"][relative]=sha(raw)
        save_yaml(draft/"portable-manifest.yaml",manifest)
        portable_verify(argparse.Namespace(package=str(draft)))
        os.rename(draft,output)
    return {"status":"PORTABLE_EXPORTED","manifest_sha256":sha(canon_yaml(manifest)),"scope":"STRUCTURAL_ONLY"}


def portable_verify(args):
    package=Path(args.package).absolute()
    if package.resolve()!=package: raise QSError("portable_package_path")
    manifest=load_yaml(captured_argument(child(package,"portable-manifest.yaml"),48 * 1024**2))
    original=manifest.get("original") if isinstance(manifest,dict) else None
    inventory=manifest.get("files") if isinstance(manifest,dict) else None
    if not isinstance(original,dict) or not isinstance(inventory,dict):
        raise QSError("portable_manifest_contract")
    for helper in PORTABLE_HELPERS:
        if helper not in inventory or not child(package,helper).is_file():
            raise QSError("portable_missing_helper:"+helper)
    run=child(package,"subject/"+ident(original.get("run_id"),"run_id"))
    derived,paths=portable_subject(run,ident(original.get("task_id"),"task_id"),ident(original.get("check_id"),"check_id"))
    relations=original.get("relations")
    if not isinstance(relations,dict): raise QSError("portable_relations_contract")
    for key,target in derived["relations"].items():
        if relations.get(key)!=target: raise QSError("portable_missing_relation:"+key)
    if derived!=original: raise QSError("portable_authoritative_subject_drift")
    if paths!=set(inventory): raise QSError("portable_inventory:"+",".join(sorted(paths^set(inventory))))
    for path in sorted(paths):
        source=child(run,path.removeprefix("subject/")) if path.startswith("subject/") else child(package,path)
        if sha(captured_argument(source,_portable_file_limit(path)).raw)!=inventory[path]:
            raise QSError("portable_file_hash:"+path)
    return {"status":"PORTABLE_VERIFIED","task_id":derived["task_id"],"check_id":derived["check_id"],
            "check":"OFFLINE_BOUND_ARTIFACT_INTEGRITY","scope":"STRUCTURAL_ONLY"}


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    for name in ("init","accept","commit","command-cut","validate","recover","resume","seal","verify","launch-intent"):
        p=sub.add_parser(name); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True)
        if name=="init":
            p.add_argument("--mode",choices=tuple(sorted(NEW_MODES)))
            p.add_argument("--goal"); p.add_argument("--material",action="append",default=[])
            p.add_argument("--bank"); p.add_argument("--original-bank")
            p.add_argument("--external-bank")
            p.add_argument("--approved-vector"); p.add_argument("--vector-approval"); p.add_argument("--approval-statement")
            p.add_argument("--test-fixture",action="store_true")
            p.add_argument("--effort",choices=("FAST","DETAILED"))
            p.add_argument("--half-budget",type=int)
            p.add_argument("--question-procedure")
        if name in {"accept","commit"}: p.add_argument("--assignment",required=True); p.add_argument("--result",required=True); p.add_argument("--request")
        if name=="validate": p.add_argument("--phase",choices=("core","final"),default="final")
        if name=="recover": p.add_argument("--bootstrap-legacy",action="store_true")
        if name=="resume": p.add_argument("--changed",action="append",default=[]); p.add_argument("--approved-map")
        if name=="seal": p.add_argument("--release-id",required=True); p.add_argument("--phase",choices=("prepare","finalize"),required=True)
        if name=="launch-intent": p.add_argument("--assignment-id",required=True); p.add_argument("--attempt-id",required=True); p.add_argument("--state",required=True,choices=tuple(sorted(LAUNCH_STATES)))
    for name in sorted(COMMAND_JOURNAL_OPERATIONS):
        p=sub.add_parser(name); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True); p.add_argument("--request",required=True)
        if name=="claim-effect": p.add_argument("--external-dispatch",action="store_true")
    p=sub.add_parser("select-plan")
    p.add_argument("--root",required=True); p.add_argument("--run-id",required=True)
    p.add_argument("--selection",required=True)
    p=sub.add_parser("question-compare"); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True); p.add_argument("--other-run-id",required=True)
    p=sub.add_parser("assign"); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True); p.add_argument("--assignment",required=True)
    p=sub.add_parser("approve-vector"); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True); p.add_argument("--approval",required=True)
    p=sub.add_parser("publish-perspective-map"); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True); p.add_argument("--perspective-map",required=True)
    p=sub.add_parser("admit-bank"); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True); p.add_argument("--bank",required=True); p.add_argument("--admission",required=True); p.add_argument("--test-fixture",action="store_true")
    p=sub.add_parser("context"); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True); p.add_argument("--assignment",required=True); p.add_argument("--output",required=True); p.add_argument("--expand",action="append",default=[]); p.add_argument("--historical",action="store_true"); p.add_argument("--packet-sha256")
    p=sub.add_parser("export-portable"); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True)
    p.add_argument("--output",required=True); p.add_argument("--task-id",required=True); p.add_argument("--check-id",required=True)
    p=sub.add_parser("portable-verify"); p.add_argument("--package",required=True)
    p=sub.add_parser("graph-query"); p.add_argument("--root",required=True); p.add_argument("--run-id",required=True)
    p.add_argument("--duty",required=True,choices=sorted(GRAPH_DUTIES)); p.add_argument("--ref",required=True)
    p.add_argument("--assignment",required=True)
    p=sub.add_parser("render"); p.add_argument("--source"); p.add_argument("--output",required=True)
    p.add_argument("--root"); p.add_argument("--run-id")
    p.add_argument("--architecture-view",choices=("functional","data","implementation"))
    p.add_argument("--subsystem"); p.add_argument("--goal-path",action="store_true")
    p.add_argument("--changed-node",action="append",default=[])
    args=parser.parse_args(argv)
    try:
        result={**dict.fromkeys(COMMAND_JOURNAL_OPERATIONS,journal_command),"init":init,"graph-query":lambda args: locked_command(args,_graph_query_locked),"question-compare":_question_compare,"select-plan":select_plan,"assign":assign,"accept":accept,"commit":commit,"command-cut":lambda args: locked_command(args,_read_command_cut),"context":context,"validate":validate,"recover":recover,"resume":resume,"seal":seal,"verify":verify,"launch-intent":launch_intent,"approve-vector":approve_vector,"publish-perspective-map":publish_perspective_map,"admit-bank":admit_bank,"export-portable":lambda args: locked_command(args,_export_portable_locked),"portable-verify":portable_verify,"render":render}[args.command](args); emit(result); return 0 if result.get("status") not in {"FAIL"} else 1
    except Exception as exc: emit({"status":"ERROR","error":str(exc),"scope":"STRUCTURAL_ONLY"},sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
