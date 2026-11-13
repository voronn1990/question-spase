"""Public portable ZIP and independently derived closure controls."""

import binascii
import io
import json
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

import test_qs
from test_command_admission import build_portable_fixture

qs = test_qs.qs
SOURCE = "input/materials/Данные проверки.txt"


def make_zip(rows, *, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Duplicate name: ", category=UserWarning)
            for entry, raw in rows:
                archive.writestr(entry, raw)
    return stream.getvalue()


class PortableArchiveTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sentinel = self.root / "sentinel"
        self.sentinel.write_bytes(b"protected")

    def reject_archive(self, raw, error, *, digest=None):
        source = self.root / "input.zip"
        source.write_bytes(raw)
        output = self.root / "outside-the-archive"
        before = set(self.root.iterdir())
        proc = subprocess.run(
            [sys.executable, "-B", str(test_qs.QS), "portable-verify", "--archive", str(source),
             "--archive-sha256", digest or qs.sha(raw), "--output", str(output)],
            cwd=self.root, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stderr)["error"].startswith(error), proc.stderr)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(self.sentinel.read_bytes(), b"protected")
        self.assertEqual(source.read_bytes(), raw)

    def test_rejects_traversal_absolute_and_windows_special_names(self):
        for name in ("../sentinel", "/tmp/absolute", "nested//name", "nested/./name",
                     r"nested\..\sentinel", "C:/file", "a/stream:ads", "AUX.txt", "a/COM¹.txt",
                     "a/name.", "a/name ", "a/comment\x01.txt"):
            with self.subTest(name=name):
                self.reject_archive(make_zip([("would-have-been-written", b"x"), (name, b"x")]),
                                    "portable_archive_path:")

    def test_rejects_duplicate_case_unicode_and_directory_aliases(self):
        for names, reason in (
            (("same", "same"), "duplicate"),
            (("A/file", "a/other"), "alias"),
            (("Кафе/e\u0301.txt", "Кафе/é.txt"), "alias"),
            (("Straße/one", "STRASSE/two"), "alias"),
            (("subject", "subject/subdir/file"), "alias"),
            (("subject/", "subject"), "duplicate"),
        ):
            with self.subTest(names=names):
                self.reject_archive(make_zip([(name, b"" if name.endswith("/") else b"x") for name in names]),
                                    "portable_archive_" + reason + ":")

    def test_rejects_nonregular_links_and_local_only_extra_before_writes(self):
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.reject_archive(make_zip([(link, b"../sentinel")]), "portable_archive_member_type:")
        hardlink = zipfile.ZipInfo("hardlink")
        hardlink.create_system = 3
        path = b"../sentinel"
        unix_extra = struct.pack("<IIHH", 0, 0, 0, 0) + path
        hardlink.extra = struct.pack("<HH", 0x000D, len(unix_extra)) + unix_extra
        typical = make_zip([(hardlink, b"")])
        self.reject_archive(typical, "portable_archive_member_type:")
        # Retain the link metadata only in the local header and repair central offsets.
        raw = bytearray(typical)
        central = raw.index(b"PK\x01\x02")
        length = struct.unpack_from("<H", raw, central + 30)[0]
        name_length = struct.unpack_from("<H", raw, central + 28)[0]
        extra_start = central + 46 + name_length
        del raw[extra_start:extra_start + length]
        struct.pack_into("<H", raw, central + 30, 0)
        end = len(raw) - 22
        struct.pack_into("<I", raw, end + 12, end - central)
        self.reject_archive(bytes(raw), "portable_archive_local_header:")
        fifo = zipfile.ZipInfo("fifo")
        fifo.external_attr = stat.S_IFIFO << 16
        self.reject_archive(make_zip([(fifo, b"")]), "portable_archive_member_type:")

    def test_rejects_raw_hash_compressed_encrypted_size_footer_and_crc_drift(self):
        raw = make_zip([("file", b"payload")])
        self.reject_archive(raw, "portable_archive_raw_hash", digest="0" * 64)
        self.reject_archive(make_zip([("file", b"payload")], compression=zipfile.ZIP_DEFLATED),
                            "portable_archive_member_encoding:")
        central = raw.index(b"PK\x01\x02")
        for local_field, central_field, fmt, value, error in (
            (6, 8, "<H", 1, "portable_archive_member_encoding:"),
            (22, 24, "<I", 2, "portable_archive_local_header:"),
            (14, 16, "<I", binascii.crc32(b"other"), "portable_archive_member_bytes:"),
        ):
            with self.subTest(error=error):
                bad = bytearray(raw)
                struct.pack_into(fmt, bad, local_field, value)
                struct.pack_into(fmt, bad, central + central_field, value)
                self.reject_archive(bytes(bad), error)
        self.reject_archive(raw + b"x", "portable_archive_directory")

    def test_rejects_truncated_central_record_lengths_before_writes(self):
        raw = make_zip([("file", b"payload")])
        central = raw.index(b"PK\x01\x02")
        for label, field, length in (
            ("filename", 28, 5), ("extra", 30, 1), ("comment", 32, 1),
        ):
            with self.subTest(field=label):
                bad = bytearray(raw)
                struct.pack_into("<H", bad, central + field, length)
                self.reject_archive(bytes(bad), "portable_archive_central_record")
        for count in (0, 2):
            with self.subTest(count=count):
                bad = bytearray(raw)
                struct.pack_into("<HH", bad, len(raw) - 14, count, count)
                self.reject_archive(bytes(bad), "portable_archive_member_budget" if count == 0
                                    else "portable_archive_central_record")

    def test_member_and_byte_budgets_are_checked_before_staging(self):
        raw = make_zip([("first", b"123"), ("second", b"456")])
        for key, limit, error in (
            ("PORTABLE_ARCHIVE_BYTES", len(raw) - 1, "portable_archive_input_budget"),
            ("PORTABLE_ARCHIVE_MEMBERS", 1, "portable_archive_member_budget"),
            ("PORTABLE_EXPANDED_BYTES", 5, "portable_archive_expansion_budget:second"),
        ):
            with self.subTest(key=key), patch.object(qs, key, limit), self.assertRaisesRegex(qs.QSError, error):
                qs._portable_zip_preflight(raw)
        with patch.object(qs, "_portable_file_limit", return_value=2):
            with self.assertRaisesRegex(qs.QSError, "portable_archive_expansion_budget:first"):
                qs._portable_zip_preflight(raw)

    def test_atomic_publication_preserves_competing_empty_and_nonempty_directory(self):
        for nonempty in (False, True):
            with self.subTest(nonempty=nonempty):
                draft = self.root / f"draft-{nonempty}"
                draft.mkdir()
                (draft / "ready").write_bytes(b"verified")
                output = qs._portable_archive_output(self.root / f"output-{nonempty}")
                output.mkdir()
                if nonempty:
                    (output / "owned").write_bytes(b"untouched")
                with self.assertRaisesRegex(qs.QSError, "portable_output_protected_or_exists"):
                    qs._publish_portable_archive(draft, output)
                self.assertEqual((draft / "ready").read_bytes(), b"verified")
                self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()},
                                 {"owned": b"untouched"} if nonempty else {})


class PortableClosureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = test_qs.QsTest()
        cls.fixture.setUp()
        cls.addClassCleanup(cls.fixture.tearDown)
        cls.late, cls.early, cls.expanded, cls.plan = build_portable_fixture(cls.fixture, transitive=True)
        cls.archive = cls.fixture.base / "closure.zip"
        cls.export = cls.fixture.call(
            "export-portable", "--root", str(cls.fixture.workspace), "--run-id", cls.fixture.run.name,
            "--task-id", "T-EARLY", "--check-id", "C-EARLY", "--archive", "--output", str(cls.archive),
        )

    def check_archive(self, archive, destination, *, ok=True):
        proc = subprocess.run(
            [sys.executable, "-B", str(test_qs.QS), "portable-verify", "--archive", str(archive),
             "--archive-sha256", qs.sha(archive.read_bytes()), "--output", str(destination)],
            cwd=self.fixture.base, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 0 if ok else 2, proc.stdout + proc.stderr)
        self.assertEqual(destination.is_dir(), ok)
        return json.loads(proc.stdout if ok else proc.stderr)

    def test_archive_verifies_authoritative_closure_and_performs_bounded_consumer(self):
        target = self.fixture.base / "independent extraction"
        receipt = self.check_archive(self.archive, target)
        manifest_path = target / "portable-manifest.yaml"
        self.assertEqual(receipt["status"], "PORTABLE_VERIFIED")
        self.assertEqual(receipt["archive_sha256"], self.export["archive_sha256"])
        self.assertEqual(receipt["manifest_sha256"], self.export["manifest_sha256"])
        self.assertEqual(receipt["manifest_sha256"], qs.sha(manifest_path.read_bytes()))
        manifest = qs.load_yaml(manifest_path)
        original = manifest["original"]
        run = target / "subject" / original["run_id"]
        plan_path = run / "plan/PLAN-L.yaml"
        self.assertEqual(qs.sha(plan_path.read_bytes()), self.plan["content_hash"])
        plan = qs.load_yaml(plan_path)
        early, late = plan["tasks"]
        self.assertEqual((early["id"], early["checks"]), (receipt["task_id"], [receipt["check_id"]]))
        self.assertEqual(late["dependencies"], [early["id"]])
        self.assertEqual(original["relations"]["T-LATE:e_refs:E-LATE"], "E-LATE")
        self.assertEqual(original["relations"]["E-LATE:review:REVIEW-LATE"], "REVIEW-LATE")
        self.assertEqual(original["relations"]["E-LATE:source_refs:" + SOURCE], SOURCE)
        self.assertEqual((run / SOURCE).read_bytes(), (self.fixture.run / SOURCE).read_bytes())
        self.assertEqual(qs.load_yaml(run / "operators/VECTOR-PORT.yaml")["G"], "bounded goal")
        action = early["fixture_action"]
        self.assertEqual(action["operation"], "ASSERT_EVIDENCE_UNCHANGED")
        result = run / "evidence" / (action["result_id"] + ".yaml")
        self.assertEqual(qs.sha(result.read_bytes()), action["sha256"])
        self.assertEqual(qs.load_yaml(result)["changed"], "UNCHANGED")

    def archive_rows(self):
        with zipfile.ZipFile(self.archive) as source:
            return {entry.filename: source.read(entry) for entry in source.infolist()}

    def check_mutated_archive(self, label, members, error):
        archive = self.fixture.base / f"{label}.zip"
        archive.write_bytes(make_zip(members.items()))
        receipt = self.check_archive(archive, self.fixture.base / label, ok=False)
        self.assertEqual(receipt["error"], error)

    def test_omitted_and_tampered_authoritative_proofs_fail_before_publish(self):
        original = self.archive_rows()
        snapshot = "subject/R1/"
        manifest_name = "portable-manifest.yaml"
        manifest = qs.load_yaml(qs.CapturedInput(manifest_name, original[manifest_name]))
        intent = next(value for key, value in manifest["original"]["relations"].items()
                      if key.startswith("REVIEW-LATE:assignment_attempt:"))
        schema = "schemas/plan-stage.yaml"
        for label, omitted, expected_error in (
            ("reviewer-assignment", intent, "portable_missing_relation:REVIEW-LATE:assignment_attempt:" + intent),
            ("reviewer-attempt", "transactions/REVIEW-LATE.yaml",
             "portable_missing_relation:REVIEW-LATE:transaction:transactions/REVIEW-LATE.yaml"),
            ("late-source", SOURCE, "portable_missing_relation:E-LATE:source_refs:" + SOURCE),
            ("schema-helper", schema, "portable_missing_helper:" + schema),
        ):
            with self.subTest(label=label):
                changed = dict(original)
                is_subject = label != "schema-helper"
                del changed[(snapshot if is_subject else "") + omitted]
                self.check_mutated_archive(label, changed, expected_error)

        changed = dict(original)
        edited = qs.load_yaml(qs.CapturedInput(manifest_name, changed[manifest_name]))
        del edited["original"]["relations"]["T-LATE:e_refs:E-LATE"]
        del edited["files"]["subject/evidence/E-LATE.yaml"]
        changed[manifest_name] = qs.canon_yaml(edited)
        self.check_mutated_archive("dual-omission", changed, "portable_missing_relation:T-LATE:e_refs:E-LATE")
        for label, member, payload, expected_error in (
            ("raw-evidence", snapshot + "evidence/E-LATE.yaml",
             original[snapshot + "evidence/E-LATE.yaml"] + b"\n", "immutable_result_tamper:E-LATE"),
            ("unknown-schema", manifest_name, qs.canon_yaml({**manifest, "schema_version": "unknown"}),
             "portable_manifest_schema"),
            ("unknown-member", "unreferenced.py", b"raise SystemExit(1)\n",
             "portable_archive_inventory:unreferenced.py"),
        ):
            with self.subTest(label=label):
                self.check_mutated_archive(label, {**original, member: payload}, expected_error)


    def test_rehashed_index_cannot_omit_reviewer_assignment_or_attempt(self):
        original = self.archive_rows()
        manifest_name = "portable-manifest.yaml"
        state_member = "subject/R1/state/assignments.yaml"
        state_key = "subject/state/assignments.yaml"
        for field in ("registered", "attempts"):
            with self.subTest(field=field):
                state = qs.load_yaml(qs.CapturedInput(state_member, original[state_member]))
                reviewer = state["results"]["REVIEW-LATE"]
                pair = reviewer["assignment_id"] + "\0" + reviewer["attempt_id"]
                del state[field][pair]
                raw_state = qs.canon_yaml(state)
                manifest = qs.load_yaml(qs.CapturedInput(manifest_name, original[manifest_name]))
                manifest["files"][state_key] = qs.sha(raw_state)
                self.check_mutated_archive(
                    f"review-missing-{field}",
                    {**original, state_member: raw_state, manifest_name: qs.canon_yaml(manifest)},
                    "portable_reviewer_attempt_binding:REVIEW-LATE",
                )

    def test_rehashed_document_with_unknown_artifact_type_fails(self):
        members = self.archive_rows()
        manifest_name = "portable-manifest.yaml"
        state_member = "subject/R1/state/assignments.yaml"
        material_member = "subject/R1/evidence/E-LATE.yaml"
        state = qs.load_yaml(qs.CapturedInput(state_member, members[state_member]))
        result = qs.load_yaml(qs.CapturedInput(material_member, members[material_member]))
        result["artifact_type"] = "UNKNOWN_ARTIFACT"
        raw_result = qs.canon_yaml(result)
        state["results"]["E-LATE"].update(artifact_type="UNKNOWN_ARTIFACT", content_hash=qs.sha(raw_result))
        raw_state = qs.canon_yaml(state)
        manifest = qs.load_yaml(qs.CapturedInput(manifest_name, members[manifest_name]))
        manifest["files"].update({
            "subject/state/assignments.yaml": qs.sha(raw_state), "subject/evidence/E-LATE.yaml": qs.sha(raw_result),
        })
        members.update({
            state_member: raw_state, material_member: raw_result, manifest_name: qs.canon_yaml(manifest),
        })
        self.check_mutated_archive("unknown-artifact", members, "portable_result_type:E-LATE")

    def test_archive_publication_holds_when_current_state_drifts_after_capture(self):
        fixture = self.fixture
        current = fixture.run / "state/current.yaml"
        raw = current.read_bytes()
        output = fixture.base / "drifted-selector.zip"
        invocation = (
            "export-portable", "--root", str(fixture.workspace), "--run-id", fixture.run.name,
            "--task-id", "T-EARLY", "--check-id", "C-EARLY", "--archive", "--output", str(output),
        )
        # This fault changes the raw current cut during capture without changing
        # the selected object, so detached verification can still succeed.
        try:
            proc = test_qs.source_swap(invocation, current, b"\n", b"\n\n")
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(json.loads(proc.stderr)["error"], "portable_publish_cut_drift")
            self.assertNotEqual(current.read_bytes(), raw)
            self.assertFalse(output.exists())
        finally:
            current.write_bytes(raw)


if __name__ == "__main__":
    unittest.main()
