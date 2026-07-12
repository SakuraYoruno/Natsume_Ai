from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCAN_SCRIPT = ROOT / "tools" / "static_scan" / "nle_boundary_scan.py"
RUN_ALL_SCRIPT = ROOT / "tools" / "static_scan" / "run_all.py"
FIXTURES = ROOT / "tests" / "fixtures" / "static_scan" / "nle_boundary"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nle_boundary_scan_rejects_each_unlisted_high_risk_edge() -> None:
    scanner = _load_module("nle_boundary_scan_unit", SCAN_SCRIPT)
    report = scanner.scan_report(
        FIXTURES / "unlisted_repo",
        policy_path=FIXTURES / "unlisted_policy.json",
    )

    assert report["existing_debt"] == []
    assert len(report["violations"]) == 8
    assert {row["rule_id"] for row in report["violations"]} == {
        "NLE-BND-EDGE-HOST-IMPL",
        "NLE-BND-AGENTRUNTIME-APP",
        "NLE-BND-PENTEST-WORLDCANON-IMPORT",
        "NLE-BND-PENTEST-WORLDCANON-UPSERT",
        "NLE-BND-PENTEST-CANON-VECTOR-READER",
        "NLE-BND-NO-PARALLEL-LIFE-ROOT",
    }
    assert {row["violation_type"] for row in report["violations"]} == {
        scanner.UNLISTED_DEPENDENCY
    }
    vector_violations = [
        row
        for row in report["violations"]
        if row["rule_id"] == "NLE-BND-PENTEST-CANON-VECTOR-READER"
    ]
    assert {row["path"] for row in vector_violations} == {
        "host_brain/pentest/new_canon_vector_alias.py",
        "host_brain/pentest/new_canon_vector_module.py",
        "host_brain/pentest/new_canon_vector_reader.py",
    }
    assert {row["symbol"] for row in vector_violations} == {"CanonVectorQuery"}


def test_nle_boundary_policy_rejects_glob_allowlist_entries() -> None:
    scanner = _load_module("nle_boundary_scan_glob", SCAN_SCRIPT)
    policy = json.loads((FIXTURES / "stale_policy.json").read_text(encoding="utf-8"))
    policy["allowlist"][0]["path"] = "edge_client/**/*.py"

    with pytest.raises(ValueError, match="must not contain glob"):
        scanner.validate_policy(policy)


def test_run_all_includes_nle_boundary_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    run_all = _load_module("run_all_nle_integration", RUN_ALL_SCRIPT)

    class EmptyScanner:
        @staticmethod
        def scan_repo(*_args, **_kwargs):
            return []

    class FailingNleScanner:
        @staticmethod
        def scan_repo(*_args, **_kwargs):
            return [
                {
                    "rule_id": "NLE-BND-FIXTURE",
                    "path": "edge_client/new_debt.py",
                    "line": 7,
                    "message": "unlisted dependency",
                }
            ]

    monkeypatch.setattr(run_all, "_load_bond_scan_module", lambda: EmptyScanner())
    monkeypatch.setattr(run_all, "_load_no_rule_degrade_module", lambda: EmptyScanner())
    monkeypatch.setattr(run_all, "_load_nle_boundary_scan_module", lambda: FailingNleScanner())
    monkeypatch.setattr(run_all, "_load_edge_contracts_guard_module", lambda: EmptyScanner())

    report = run_all.run_static_scans(ROOT)

    assert report["status"] == "failed"
    assert report["failures"] == [
        {
            "check_id": "static_scan",
            "reason_code": "STATIC_SCAN_VIOLATION",
            "failed_artifact": "edge_client/new_debt.py",
            "line": 7,
            "message": "unlisted dependency",
            "rule_id": "NLE-BND-FIXTURE",
        }
    ]


def test_canon_boundary_resolves_aliases_permit_issuer_and_file_writes_without_chroma_noise(
    tmp_path: Path,
) -> None:
    scanner = _load_module("nle_boundary_scan_canon", SCAN_SCRIPT)
    source_root = tmp_path / "host_brain"
    source_root.mkdir()
    (source_root / "bypass.py").write_text(
        """
import os
from pathlib import Path
import host_brain.memory.canon_world_store as cws
from host_brain.memory.canon_world_store import WorldCanonStore as StoreAlias
from host_brain.governor.tokens import CanonApprovalTokenIssuer

ReExportedStore = StoreAlias

def helper() -> StoreAlias:
    return ReExportedStore()

writer = helper()
write_alias = writer.upsert
write_alias(doc_id='x', title='x', body='x', proposal_id='x')
permit_one = getattr(cws, '_CANON_APPLY_PERMIT')
permit_two = vars(cws)['_CANON_APPLY_PERMIT']
root = Path('host_brain/canon/world')
target = root / 'x.json'
target.write_text('{}', encoding='utf-8')
os.replace(target, root / 'y.json')
os.open(target, os.O_WRONLY)
issuer = CanonApprovalTokenIssuer('db', authority_key_id='x', private_key=None, reviewer_id='x')
issuer.issue_approved_token()
""",
        encoding="utf-8",
    )
    (source_root / "memory" / "canon_safe_ops.py").parent.mkdir()
    (source_root / "memory" / "canon_safe_ops.py").write_text(
        """
import os

def normalize(value, directory):
    cleaned = value.replace('-', '').replace('_', '')
    descriptor = os.open(
        str(directory),
        os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0),
    )
    os.close(descriptor)
    return cleaned
""",
        encoding="utf-8",
    )
    (source_root / "chroma_ok.py").write_text(
        "def project(collection, rows):\n    collection.upsert(ids=['x'], documents=rows)\n",
        encoding="utf-8",
    )
    policy = {
        "schema_version": 1,
        "policy_id": "canon-fixture",
        "rules": [
            {
                "rule_id": "CANON",
                "kind": "world_canon_mutation",
                "source_roots": ["host_brain"],
                "store_modules": ["host_brain.memory.canon_world_store"],
                "mutator_names": [
                    "upsert",
                    "upsert_document",
                    "approve_proposal_with_content",
                    "rollback_proposal",
                    "_replace_authorized_payload",
                    "_delete_authorized_document",
                ],
                "issuer_names": ["CanonApprovalTokenIssuer", "issue_approved_token"],
                "trusted_call_sites": [],
            }
        ],
        "allowlist": [],
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = scanner.scan_report(tmp_path, policy_path=policy_path)

    symbols = {row["symbol"] for row in report["violations"]}
    assert "WorldCanonStore.upsert" in symbols
    assert "WorldCanonStore._CANON_APPLY_PERMIT" in symbols
    assert "CanonFile.write_text" in symbols
    assert "CanonFile.replace" in symbols
    assert "CanonFile.open" in symbols
    assert "CanonApprovalIssuer.CanonApprovalTokenIssuer" in symbols
    assert "CanonApprovalIssuer.issue_approved_token" in symbols
    assert not any(row["path"].endswith("chroma_ok.py") for row in report["violations"])
    assert not any(row["path"].endswith("canon_safe_ops.py") for row in report["violations"])


@pytest.fixture
def _load_scanner():
    serial = 0

    def load():
        nonlocal serial
        serial += 1
        return _load_module(f"nle_boundary_scan_synthetic_{serial}", SCAN_SCRIPT)

    return load


def _scan_synthetic_canon_source(
    scanner,
    tmp_path: Path,
    source: str,
    *,
    source_root_name: str = "host_brain",
) -> dict:
    source_root = tmp_path / source_root_name
    source_root.mkdir(parents=True)
    (source_root / "synthetic.py").write_text(source, encoding="utf-8")
    policy = {
        "schema_version": 1,
        "policy_id": "canon-synthetic-fixture",
        "rules": [
            {
                "rule_id": "CANON-SYNTHETIC",
                "kind": "world_canon_mutation",
                "source_roots": [source_root_name],
                "store_modules": ["host_brain.memory.canon_world_store"],
                "mutator_names": [
                    "upsert",
                    "upsert_document",
                    "approve_proposal_with_content",
                    "rollback_proposal",
                    "_replace_authorized_payload",
                    "_delete_authorized_document",
                ],
                "issuer_names": [
                    "CanonApprovalTokenIssuer",
                    "issue_approved_token",
                ],
                "trusted_call_sites": [],
            }
        ],
        "allowlist": [],
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    return scanner.scan_report(tmp_path, policy_path=policy_path)


def test_canon_boundary_policy_covers_all_production_python_roots_and_edge_client(
    _load_scanner,
    tmp_path: Path,
) -> None:
    scanner = _load_scanner()
    policy = scanner.load_policy(SCAN_SCRIPT.with_name("nle_boundary_policy.json"))
    canon_rule = next(
        rule
        for rule in policy["rules"]
        if rule["rule_id"] == "NLE-BND-WORLDCANON-RAW-MUTATION"
    )
    assert canon_rule["source_roots"] == [
        "edge_client",
        "host_brain",
        "scripts",
        "skills",
        "tools",
    ]

    report = _scan_synthetic_canon_source(
        scanner,
        tmp_path,
        """
from pathlib import Path
import host_brain.memory.canon_world_store as canon_store

writer = canon_store.WorldCanonStore()
writer.upsert(doc_id="x", title="x", body="x", proposal_id="x")
permit = canon_store._CANON_APPLY_PERMIT
target = Path("host_brain") / "canon" / "world" / "entry.json"
target.write_text("{}", encoding="utf-8")
""",
        source_root_name="edge_client",
    )

    assert {row["symbol"] for row in report["violations"]} == {
        "CanonFile.write_text",
        "WorldCanonStore._CANON_APPLY_PERMIT",
        "WorldCanonStore.upsert",
    }


def test_canon_boundary_propagates_exported_sink_summaries_across_modules(
    _load_scanner,
    tmp_path: Path,
) -> None:
    scanner = _load_scanner()
    package = tmp_path / "host_brain" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "writer.py").write_text(
        """
def persist(path):
    path.write_text("{}", encoding="utf-8")
""",
        encoding="utf-8",
    )
    (package / "forward_from.py").write_text(
        """
from host_brain.pkg.writer import persist as write

def forward(path):
    write(path)
""",
        encoding="utf-8",
    )
    (package / "forward_module.py").write_text(
        """
import host_brain.pkg.writer as writer

def forward(path):
    writer.persist(path)
""",
        encoding="utf-8",
    )
    (package / "caller_from.py").write_text(
        """
from pathlib import Path
from host_brain.pkg.forward_from import forward as install

install(Path("host_brain") / "canon" / "world" / "from.json")
""",
        encoding="utf-8",
    )
    (package / "caller_module.py").write_text(
        """
from pathlib import Path
import host_brain.pkg.forward_module as bridge

bridge.forward(Path("host_brain") / "canon" / "world" / "module.json")
""",
        encoding="utf-8",
    )
    (package / "reader.py").write_text(
        """
def read(path):
    return path.read_text(encoding="utf-8")
""",
        encoding="utf-8",
    )
    (package / "negative.py").write_text(
        """
from pathlib import Path
from host_brain.pkg.reader import read

read(Path("host_brain") / "canon" / "world" / "read-only.json")
""",
        encoding="utf-8",
    )
    policy = {
        "schema_version": 1,
        "policy_id": "canon-cross-module-fixture",
        "rules": [
            {
                "rule_id": "CANON-CROSS-MODULE",
                "kind": "world_canon_mutation",
                "source_roots": ["host_brain"],
                "store_modules": ["host_brain.memory.canon_world_store"],
                "mutator_names": [
                    "upsert",
                    "upsert_document",
                    "approve_proposal_with_content",
                    "rollback_proposal",
                    "_replace_authorized_payload",
                    "_delete_authorized_document",
                ],
                "issuer_names": [
                    "CanonApprovalTokenIssuer",
                    "issue_approved_token",
                ],
                "trusted_call_sites": [],
            }
        ],
        "allowlist": [],
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = scanner.scan_report(tmp_path, policy_path=policy_path)

    assert {
        (row["path"], row["symbol"])
        for row in report["violations"]
    } == {
        ("host_brain/pkg/caller_from.py", "CanonFile.write_text"),
        ("host_brain/pkg/caller_module.py", "CanonFile.write_text"),
    }


@pytest.mark.parametrize(
    ("case_name", "source", "expected_symbols"),
    [
        pytest.param(
            "import_module_concat_getattr_callable_alias",
            """
import importlib as loader

PACKAGE = "host_brain.memory."
LEAF_PREFIX = "canon_"
LEAF = LEAF_PREFIX + "world_store"
MODULE_NAME = PACKAGE + LEAF
module = loader.import_module(MODULE_NAME)
module_alias = module
CLASS_PREFIX = "World"
CLASS_NAME = f"{CLASS_PREFIX}CanonStore"
constructor = getattr(module_alias, CLASS_NAME)
constructor_alias = constructor
store = constructor_alias()
MUTATOR_PREFIX = "up"
MUTATOR_NAME = f"{MUTATOR_PREFIX}sert"
callable_alias = getattr(store, MUTATOR_NAME)
second_alias = callable_alias
second_alias(doc_id="x", title="x", body="x", proposal_id="x")
""",
            {"WorldCanonStore.upsert"},
            id="import-module-concat-getattr-callable-alias",
        ),
        pytest.param(
            "import_module_fstring_vars_permit_alias",
            """
from importlib import import_module as load_module

PACKAGE = "host_brain.memory"
LEAF = "canon_world_store"
MODULE_NAME = f"{PACKAGE}.{LEAF}"
module = load_module(MODULE_NAME)
module_alias = module
PERMIT_PREFIX = "_CANON_APPLY"
PERMIT_NAME = f"{PERMIT_PREFIX}_PERMIT"
permit = vars(module_alias)[PERMIT_NAME]
permit_alias = permit
consume = permit_alias
""",
            {"WorldCanonStore._CANON_APPLY_PERMIT"},
            id="import-module-fstring-vars-permit-alias",
        ),
        pytest.param(
            "vars_store_and_raw_mutator_alias",
            """
import importlib

MODULE_NAME = "host_brain.memory." + "canon_world_store"
module = importlib.import_module(MODULE_NAME)
module_alias = module
CLASS_NAME = f"World{'Canon'}Store"
Store = vars(module_alias)[CLASS_NAME]
StoreAlias = Store
writer = StoreAlias()
MUTATOR_NAME = f"{'up'}sert"
raw_mutator = vars(StoreAlias)[MUTATOR_NAME]
callable_alias = raw_mutator
callable_alias(writer, doc_id="x", title="x", body="x", proposal_id="x")
""",
            {"WorldCanonStore.upsert"},
            id="vars-store-and-raw-mutator-alias",
        ),
        pytest.param(
            "getattr_permit_alias",
            """
from importlib import import_module

MODULE_NAME = f"host_brain.memory.{'canon_world_store'}"
module = import_module(MODULE_NAME)
module_alias = module
PERMIT_NAME = f"_CANON_{'APPLY'}_PERMIT"
permit = getattr(module_alias, PERMIT_NAME)
permit_alias = permit
consume = permit_alias
""",
            {"WorldCanonStore._CANON_APPLY_PERMIT"},
            id="getattr-permit-alias",
        ),
        pytest.param(
            "dunder_dict_store_and_raw_mutator",
            """
import importlib

MODULE_PREFIX = "host_brain.memory.canon"
MODULE_NAME = MODULE_PREFIX + "_world_store"
module = importlib.import_module(MODULE_NAME)
CLASS_NAME = "World" + "CanonStore"
Store = module.__dict__[CLASS_NAME]
StoreAlias = Store
writer = StoreAlias()
MUTATOR_NAME = "up" + "sert"
raw_mutator = StoreAlias.__dict__[MUTATOR_NAME]
callable_alias = raw_mutator
callable_alias(writer, doc_id="x", title="x", body="x", proposal_id="x")
""",
            {"WorldCanonStore.upsert"},
            id="dunder-dict-store-and-raw-mutator",
        ),
        pytest.param(
            "dunder_dict_permit_alias",
            """
import importlib

MODULE_NAME = "host_brain.memory." + "canon_world_store"
module = importlib.import_module(MODULE_NAME)
module_alias = module
PERMIT_NAME = "_CANON" + "_APPLY_PERMIT"
permit = module_alias.__dict__[PERMIT_NAME]
permit_alias = permit
consume = permit_alias
""",
            {"WorldCanonStore._CANON_APPLY_PERMIT"},
            id="dunder-dict-permit-alias",
        ),
        pytest.param(
            "dynamic_issuer_constructor",
            """
import host_brain.governor.tokens as token_module

PREFIX = "CanonApproval"
MIDDLE = "Token"
NAME = f"{PREFIX}{MIDDLE}Issuer"
factory = getattr(token_module, NAME)
factory_alias = factory
factory_alias("db", authority_key_id="key", private_key=None, reviewer_id="reviewer")
""",
            {"CanonApprovalIssuer.CanonApprovalTokenIssuer"},
            id="dynamic-issuer-constructor",
        ),
        pytest.param(
            "dynamic_issue_approved_token",
            """
class Issuer:
    pass

issuer = Issuer()
APPROVED = "approved"
METHOD_NAME = "issue_" + APPROVED + f"_{'token'}"
issue = vars(issuer)[METHOD_NAME]
issue_alias = issue
issue_alias()
""",
            {"CanonApprovalIssuer.issue_approved_token"},
            id="dynamic-issue-approved-token",
        ),
        pytest.param(
            "fstring_canon_root_write",
            """
from pathlib import Path

CANON = "host_brain/canon"
WORLD = "world"
ROOT_NAME = f"{CANON}/{WORLD}"
root = Path(ROOT_NAME)
root_alias = root
target = root_alias / "entry.json"
target.write_text("{}", encoding="utf-8")
""",
            {"CanonFile.write_text"},
            id="fstring-canon-root-write",
        ),
        pytest.param(
            "segmented_path_division_canon_root_write",
            """
from pathlib import Path

HOST = f"host_{'brain'}"
CANON_SEGMENT = "ca" + "non"
WORLD = f"{'world'}"
base = Path(HOST)
canon_directory = base / CANON_SEGMENT
root = canon_directory / WORLD
target = root / "entry.json"
target.write_text("{}", encoding="utf-8")
""",
            {"CanonFile.write_text"},
            id="segmented-path-division-canon-root-write",
        ),
        pytest.param(
            "builtin_import_reflection_aliases",
            """
IMPORTER = __import__
PACKAGE = "host_brain.memory"
LEAF = f"canon_{'world'}_store"
MODULE_NAME = f"{PACKAGE}.{LEAF}"
module = IMPORTER(MODULE_NAME, fromlist=["*"])
module_alias = module
CLASS_NAME = "World" + "CanonStore"
Store = module_alias.__dict__[CLASS_NAME]
StoreAlias = Store
writer = StoreAlias()
PERMIT_NAME = f"_CANON_{'APPLY'}_PERMIT"
permit = vars(module_alias)[PERMIT_NAME]
permit_alias = permit
MUTATOR_NAME = "up" + "sert"
raw_mutator = getattr(writer, MUTATOR_NAME)
callable_alias = raw_mutator
callable_alias(doc_id="x", title="x", body="x", proposal_id="x")
""",
            {"WorldCanonStore._CANON_APPLY_PERMIT", "WorldCanonStore.upsert"},
            id="builtin-import-reflection-aliases",
        ),
        pytest.param(
            "bound_dunder_getattribute_reflection",
            """
import host_brain.memory.canon_world_store as canon_module

CLASS_NAME = f"World{'Canon'}Store"
Store = canon_module.__getattribute__(CLASS_NAME)
writer = Store()
PERMIT_NAME = "_CANON" + "_APPLY_PERMIT"
permit = canon_module.__getattribute__(PERMIT_NAME)
permit_alias = permit
MUTATOR_NAME = f"_replace_{'authorized'}_payload"
mutate = writer.__getattribute__(MUTATOR_NAME)
callable_alias = mutate
callable_alias("doc", "{}", permit=permit_alias)
""",
            {
                "WorldCanonStore._CANON_APPLY_PERMIT",
                "WorldCanonStore._replace_authorized_payload",
            },
            id="bound-dunder-getattribute-reflection",
        ),
        pytest.param(
            "destination_sensitive_file_sink_matrix",
            """
import os
import shutil
from pathlib import Path

destination = Path("host_brain") / "canon" / "world" / "entry.json"
Path("staged.json").replace(destination)
shutil.move("move-source.json", destination)
shutil.copy("copy-source.json", destination)
shutil.copy2("copy2-source.json", destination)
shutil.copyfile("copyfile-source.json", destination)
os.link("link-source.json", destination)
os.symlink("symlink-source.json", destination)
destination.symlink_to("pathlib-symlink-source.json")
destination.hardlink_to("pathlib-hardlink-source.json")
""",
            {
                "CanonFile.copy",
                "CanonFile.copy2",
                "CanonFile.copyfile",
                "CanonFile.hardlink_to",
                "CanonFile.link",
                "CanonFile.move",
                "CanonFile.replace",
                "CanonFile.symlink",
                "CanonFile.symlink_to",
            },
            id="destination-sensitive-file-sink-matrix",
        ),
        pytest.param(
            "two_layer_write_text_wrapper",
            """
from pathlib import Path

def sink(path):
    path.write_text("{}", encoding="utf-8")

def forward(path):
    sink(path)

forward(Path("host_brain") / "canon" / "world" / "entry.json")
""",
            {"CanonFile.write_text"},
            id="two-layer-write-text-wrapper",
        ),
        pytest.param(
            "param_to_return_identity_factory",
            """
from pathlib import Path

def identity(path):
    return path

target = identity(Path("host_brain") / "canon" / "world" / "entry.json")
target.write_text("{}", encoding="utf-8")
""",
            {"CanonFile.write_text"},
            id="param-to-return-identity-factory",
        ),
        pytest.param(
            "name_propagated_canon_env_open",
            """
import os

ENV_PREFIX = "NATSUME_WORLD"
ENV_NAME = f"{ENV_PREFIX}_CANON_ROOT"
root = os.getenv(ENV_NAME)
root_alias = root
target = os.path.join(root_alias, "entry.json")
open(target, "w", encoding="utf-8")
""",
            {"CanonFile.open"},
            id="name-propagated-canon-env-open",
        ),
        pytest.param(
            "subscripted_canon_env_root",
            """
import os
from pathlib import Path

PREFIX = f"Natsume_{'WORLD'}"
ENV_KEY = f"{PREFIX}_CANON_ROOT"
target = Path(os.environ[ENV_KEY]) / "entry.json"
target.write_text("{}", encoding="utf-8")
""",
            {"CanonFile.write_text"},
            id="subscripted-canon-env-root",
        ),
        pytest.param(
            "one_layer_write_text_wrapper",
            """
from pathlib import Path

def persist(path, payload):
    forwarded = path
    forwarded.write_text(payload, encoding="utf-8")

root = Path("host_brain/canon/world")
persist(root / "entry.json", "{}")
""",
            {"CanonFile.write_text"},
            id="one-layer-write-text-wrapper",
        ),
        pytest.param(
            "one_layer_open_wrapper",
            """
from pathlib import Path

def persist(path, payload):
    forwarded = path
    with open(forwarded, "w", encoding="utf-8") as handle:
        handle.write(payload)

root = Path("host_brain/canon/world")
persist(root / "entry.json", "{}")
""",
            {"CanonFile.open"},
            id="one-layer-open-wrapper",
        ),
        pytest.param(
            "one_layer_os_replace_wrapper",
            """
import os
from pathlib import Path

def install(source, destination):
    source_alias = source
    destination_alias = destination
    os.replace(source_alias, destination_alias)

root = Path("host_brain/canon/world")
install(root / "entry.tmp", root / "entry.json")
""",
            {"CanonFile.replace"},
            id="one-layer-os-replace-wrapper",
        ),
        pytest.param(
            "one_layer_world_canon_upsert_wrapper",
            """
from host_brain.memory.canon_world_store import WorldCanonStore as Store

def persist(subject, payload):
    forwarded = subject
    forwarded.upsert(**payload)

writer = Store()
persist(writer, {"doc_id": "x", "title": "x", "body": "x", "proposal_id": "x"})
""",
            {"WorldCanonStore.upsert"},
            id="one-layer-world-canon-upsert-wrapper",
        ),
        pytest.param(
            "direct_builtin_eval_exec",
            """
eval("1 + 1")
exec("value = 1")
""",
            {"DynamicExecution.eval", "DynamicExecution.exec"},
            id="direct-builtin-eval-exec",
        ),
        pytest.param(
            "builtins_module_alias",
            """
import builtins as runtime_builtins

runtime_builtins.eval("1 + 1")
runtime_builtins.exec("value = 1")
""",
            {"DynamicExecution.eval", "DynamicExecution.exec"},
            id="builtins-module-alias",
        ),
        pytest.param(
            "builtins_from_import_alias",
            """
from builtins import eval as runtime_eval
from builtins import exec as runtime_exec

runtime_eval("1 + 1")
runtime_exec("value = 1")
""",
            {"DynamicExecution.eval", "DynamicExecution.exec"},
            id="builtins-from-import-alias",
        ),
        pytest.param(
            "builtins_callable_alias_propagation",
            """
import builtins as runtime_builtins

runtime_eval = runtime_builtins.eval
runtime_eval_alias = runtime_eval
runtime_exec = runtime_builtins.exec
runtime_exec_alias = runtime_exec
runtime_eval_alias("1 + 1")
runtime_exec_alias("value = 1")
""",
            {"DynamicExecution.eval", "DynamicExecution.exec"},
            id="builtins-callable-alias-propagation",
        ),
        pytest.param(
            "builtins_from_import_alias_propagation",
            """
from builtins import eval as imported_eval
from builtins import exec as imported_exec

runtime_eval = imported_eval
runtime_exec = imported_exec
runtime_eval("1 + 1")
runtime_exec("value = 1")
""",
            {"DynamicExecution.eval", "DynamicExecution.exec"},
            id="builtins-from-import-alias-propagation",
        ),
    ],
)
def test_canon_boundary_rejects_synthetic_dynamic_bypasses(
    _load_scanner,
    tmp_path: Path,
    case_name: str,
    source: str,
    expected_symbols: set[str],
) -> None:
    scanner = _load_scanner()
    report = _scan_synthetic_canon_source(scanner, tmp_path, source)

    assert report["existing_debt"] == [], case_name
    assert {row["symbol"] for row in report["violations"]} == expected_symbols, (
        case_name,
        report["violations"],
    )


@pytest.mark.parametrize(
    ("case_name", "source"),
    [
        pytest.param(
            "chroma_direct_and_wrapper_upsert",
            """
def persist(collection, rows):
    collection_alias = collection
    collection_alias.upsert(ids=["x"], documents=rows)

collection.upsert(ids=["x"], documents=["row"])
persist(collection, ["row"])
""",
            id="chroma-direct-and-wrapper-upsert",
        ),
        pytest.param(
            "unknown_import_getattr_vars_dunder_dict",
            """
import importlib

module = importlib.import_module("third_party.canon_store")
Store = getattr(module, "World" + "CanonStore")
writer = Store()
getattr(writer, "up" + "sert")()
permit_one = vars(module)["_CANON" + "_APPLY_PERMIT"]
permit_two = module.__dict__["_CANON_APPLY" + "_PERMIT"]
""",
            id="unknown-import-getattr-vars-dunder-dict",
        ),
        pytest.param(
            "self_eval_and_locally_bound_eval_exec",
            """
class Runner:
    def eval(self, value):
        return value

    def execute(self, value):
        return self.eval(value)

def eval(value):
    return value

def exec(value):
    return value

Runner().execute("safe")
eval("safe")
exec("safe")
""",
            id="self-eval-and-locally-bound-eval-exec",
        ),
        pytest.param(
            "read_only_open_and_os_open",
            """
import os
from pathlib import Path

root = Path("host_brain/canon/world")
target = root / "entry.json"
with open(target, "r", encoding="utf-8") as handle:
    handle.read()
with target.open("rb") as handle:
    handle.read()
descriptor = os.open(
    target,
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
)
os.close(descriptor)
""",
            id="read-only-open-and-os-open",
        ),
        pytest.param(
            "ordinary_segmented_destinations_and_path_operations",
            """
import os
import shutil
from pathlib import Path

ROOT_NAME = f"runtime/{'ordinary'}"
destination = Path(ROOT_NAME) / "entry.json"
destination.write_text("{}", encoding="utf-8")
Path("staged.json").replace(destination)
shutil.move("move-source.json", destination)
shutil.copy("copy-source.json", destination)
shutil.copy2("copy2-source.json", destination)
shutil.copyfile("copyfile-source.json", destination)
os.link("link-source.json", destination)
os.symlink("symlink-source.json", destination)
destination.symlink_to("pathlib-source.json")
""",
            id="ordinary-segmented-destinations-and-path-operations",
        ),
        pytest.param(
            "canon_sources_are_read_only_for_copy_and_link_families",
            """
import os
import shutil
from pathlib import Path

source = Path("host_brain") / "canon" / "world" / "entry.json"
destination = Path("runtime/export/entry.json")
shutil.copy(source, destination)
shutil.copy2(source, destination)
shutil.copyfile(source, destination)
os.link(source, destination)
os.symlink(source, destination)
""",
            id="canon-sources-are-read-only-for-copy-and-link-families",
        ),
        pytest.param(
            "locally_shadowed_builtin_import",
            """
class FakeModule:
    def __getattribute__(self, name):
        return object

def __import__(name, fromlist=None):
    return FakeModule()

MODULE = "host_brain.memory." + "canon_world_store"
module = __import__(MODULE, fromlist=["*"])
Store = module.__getattribute__("WorldCanonStore")
writer = Store()
permit = module.__getattribute__("_CANON_APPLY_PERMIT")
mutate = writer.__getattribute__("upsert")
mutate()
""",
            id="locally-shadowed-builtin-import",
        ),
        pytest.param(
            "unknown_object_bound_dunder_getattribute",
            """
class Unknown:
    pass

unknown = Unknown()
permit = unknown.__getattribute__("_CANON" + "_APPLY_PERMIT")
mutate = unknown.__getattribute__(f"{'up'}sert")
callable_alias = mutate
callable_alias()
""",
            id="unknown-object-bound-dunder-getattribute",
        ),
        pytest.param(
            "ordinary_path_through_identity_and_two_wrappers",
            """
from pathlib import Path

def sink(path):
    path.write_text("{}", encoding="utf-8")

def forward(path):
    sink(path)

def identity(path):
    return path

forward(identity(Path("runtime/ordinary/entry.json")))
""",
            id="ordinary-path-through-identity-and-two-wrappers",
        ),
        pytest.param(
            "unrelated_subscripted_environment_path",
            """
import os
from pathlib import Path

PREFIX = f"Natsume_{'CACHE'}"
ENV_KEY = f"{PREFIX}_ROOT"
target = Path(os.environ[ENV_KEY]) / "entry.json"
target.write_text("{}", encoding="utf-8")
""",
            id="unrelated-subscripted-environment-path",
        ),
    ],
)
def test_canon_boundary_synthetic_negative_controls_have_zero_findings(
    _load_scanner,
    tmp_path: Path,
    case_name: str,
    source: str,
) -> None:
    scanner = _load_scanner()
    report = _scan_synthetic_canon_source(scanner, tmp_path, source)

    assert report["existing_debt"] == [], case_name
    assert report["violations"] == [], (case_name, report["violations"])
