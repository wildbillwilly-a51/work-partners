"""Offline invariants for native desktop consultations; no real model calls."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = load("desktop_partner", ROOT / "skills/work-partners/scripts/partner.py")
installer = load("partner_install", ROOT / "scripts/install-desktop-partners.py")
manual = load("partner_manual", ROOT / "tests/manual_desktop_partners.py")
RESPONSE = dict(reply="Read the project records.", question_for_lead="Which concern should I examine next?",
                evidence=["docs/project-summary.md:1"])


class Partners(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        availability = patch.object(p, "availability_root", return_value=self.root / "availability")
        availability.start()
        self.addCleanup(availability.stop)
        self.project = self.root / "repo"
        self.project.mkdir()
        for args in (["init", "-q"], ["config", "user.name", "Fixture"], ["config", "user.email", "fixture@local"]):
            subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True)
        (self.project / "source.txt").write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", "source.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.project), "commit", "-qm", "fixture"], check=True, capture_output=True)
        self.request = self.root / "question.txt"
        self.request.write_text("Review the local source; no changes.", encoding="utf-8")
        self.args = argparse.Namespace(project=str(self.project), state_root=str(self.root / "state"),
            lead="codex", request_file=str(self.request), task=None, retry=False, timeout=30)
        self.fake_command = patch.object(p, "command", return_value=["fixture"])
        self.command_mock = self.fake_command.start()
        self.addCleanup(self.fake_command.stop)

    def claude(self, *_args):
        return json.dumps(dict(type="result", subtype="success", is_error=False, structured_output={"consultation_json": json.dumps(RESPONSE)}, session_id="native-fixture"))

    def codex(self, *_args):
        return "\n".join(json.dumps(v) for v in [
            dict(type="thread.started", thread_id="native-codex"),
            dict(type="item.completed", item=dict(type="agent_message", text="Reading relevant files.")),
            dict(type="item.completed", item=dict(type="agent_message", text=json.dumps(RESPONSE))),
            dict(type="turn.completed")])

    def test_both_directions_and_cached_delivery(self):
        for lead, run in [("codex", self.claude), ("claude", self.codex)]:
            self.args.lead, self.args.task = lead, None
            before = p.snapshot(self.project)
            result = p.ask(self.args, run=run)
            self.assertEqual(result["reply"], RESPONSE["reply"])
            self.assertEqual(p.snapshot(self.project), before)
            self.args.task = result["task_id"]
            cached = p.ask(self.args, run=lambda *_: self.fail("duplicate inference"))
            self.assertTrue(cached["cached"])

    def test_followup_has_actual_previous_question_and_answer(self):
        result = p.ask(self.args, run=self.claude)
        self.args.task = result["task_id"]
        self.request.write_text("Examine the backup concern next.", encoding="utf-8")
        def followup(_cmd, prompt, *_rest):
            self.assertIn(RESPONSE["reply"], prompt)
            self.assertIn(RESPONSE["question_for_lead"], prompt)
            self.assertIn("backup concern", prompt)
            return self.claude()
        p.ask(self.args, run=followup)

    def test_both_peers_receive_the_native_response_contract_in_the_prompt(self):
        for lead, response in (("codex", self.claude), ("claude", self.codex)):
            self.args.lead, self.args.task = lead, None
            def inspect(_args, prompt, *_rest):
                payload = json.loads(prompt.split("\n", 1)[1])
                self.assertEqual(payload["response_contract"], p.SCHEMA)
                self.assertEqual(payload["request_from_lead"], self.request.read_text().strip())
                if lead == "codex":
                    self.assertIn("Use StructuredOutput with exactly one transport field consultation_json", prompt)
                    self.assertNotIn("Return one unfenced JSON object", prompt)
                else:
                    self.assertIn("Return one unfenced JSON object", prompt)
                return response()
            self.assertEqual(p.ask(self.args, run=inspect)["reply"], RESPONSE["reply"])

    def test_native_structured_output_failures_are_never_salvaged_or_retried(self):
        for nonzero in (False, True):
            with self.subTest(nonzero=nonzero):
                self.args.task = None
                calls = []
                before = set((self.root / "state").glob("*/task.json"))
                raw = json.dumps(dict(type="result", is_error=True,
                    subtype="error_max_structured_output_retries", session_id="native-format-failure",
                    structured_output={"consultation_json": json.dumps(RESPONSE)}, modelUsage={"claude-opus-5-5": {}, "claude-opus-4-8": {}},
                    errors=["Private validation detail must remain in the native receipt"]))
                def native(*_):
                    calls.append(1)
                    if nonzero:
                        raise p.PeerFailure(1, raw)
                    return raw
                with self.assertRaises(p.StructuredOutputFailure) as failure:
                    p.ask(self.args, run=native)
                self.assertNotIn("Private validation detail", str(failure.exception))
                created = set((self.root / "state").glob("*/task.json")) - before
                self.assertEqual(len(created), 1)
                record = json.loads(created.pop().read_text())
                op = record["operations"][0]
                self.assertEqual(op["status"], "failed")
                self.assertEqual(op["error_class"], "StructuredOutputFailure")
                self.assertEqual(op["models_used"], ["claude-opus-4-8", "claude-opus-5-5"])
                self.assertNotIn("result", op)
                self.assertFalse((self.root / "availability/claude.json").exists())
                self.args.task = record["task_id"]
                with self.assertRaisesRegex(p.Refused, "--retry"):
                    p.ask(self.args, run=native)
                self.assertEqual(len(calls), 1)

    def test_complete_schema_is_still_required_after_fallback(self):
        values = [dict(RESPONSE, reply=""), dict(RESPONSE, reply="  "),
                  dict(RESPONSE, question_for_lead=None), dict(RESPONSE, evidence="source:1"),
                  dict(RESPONSE, extra="unexpected"), dict(RESPONSE, evidence=[None])]
        for missing in ("question_for_lead", "evidence"):
            partial = dict(RESPONSE)
            del partial[missing]
            values.append(partial)
        for value in values:
            with self.subTest(value=value):
                raw = json.dumps(dict(type="result", subtype="success", is_error=False, session_id="native-fallback", structured_output={"consultation_json": json.dumps(value)},
                                      modelUsage={"claude-opus-4-8": {}}))
                with self.assertRaises(p.Refused):
                    p.parse("claude", raw)
        empty_optional = dict(RESPONSE, question_for_lead="", evidence=[])
        parsed, _ = p.parse("claude", json.dumps(dict(type="result", subtype="success", is_error=False, session_id="native-fallback",
                                                      structured_output={"consultation_json": json.dumps(empty_optional)})))
        self.assertEqual(parsed, empty_optional)

    def test_native_failure_precedence_and_success_text_are_unchanged(self):
        envelope = dict(is_error=True, subtype="error_max_structured_output_retries", api_error_status=429)
        with self.assertRaises(p.QuotaLimited):
            p.parse("claude", json.dumps(envelope))
        del envelope["api_error_status"]
        envelope["stop_reason"] = "refusal"
        with self.assertRaises(p.ModelRefusal):
            p.parse("claude", json.dumps(envelope))
        quoted = dict(RESPONSE, reply="Source mentions error_max_structured_output_retries")
        parsed, _ = p.parse("claude", json.dumps(dict(type="result", subtype="success", is_error=False, session_id="quoted", structured_output={"consultation_json": json.dumps(quoted)})))
        self.assertEqual(parsed, quoted)

    def test_single_string_transport_requires_complete_success_and_never_salvages(self):
        inner = json.dumps(dict(RESPONSE, question_for_lead="", evidence=[]))
        good = dict(type="result", subtype="success", is_error=False, session_id="transport",
                    structured_output={"consultation_json": inner}, result="Native final commentary is not the payload")
        for change in ({"subtype": "error_max_turns"}, {"type": "other"}, {"is_error": True},
                       {"subtype": None}, {"is_error": None}, {"stop_reason": "refusal"},
                       {"api_error_status": 429, "is_error": True}):
            with self.subTest(change=change), self.assertRaises(p.Refused):
                p.parse("claude", json.dumps(dict(good, **change)))
        bad_transports = [None, {}, RESPONSE, {"consultation_json": inner, "extra": "unexpected"}]
        for value in ("```json\n" + inner + "\n```", "Here: " + inner, "invalid", None, {}, "[]", " "):
            bad_transports.append({"consultation_json": value})
        for transport in bad_transports:
            with self.subTest(transport=transport), self.assertRaises(p.StructuredOutputFailure):
                # A valid final-text result is never a fallback source.
                p.parse("claude", json.dumps(dict(good, structured_output=transport, result=inner)))
        for raw in ("invalid", "[]", "null"):
            with self.subTest(raw=raw), self.assertRaises(p.StructuredOutputFailure):
                p.parse("claude", raw)

        fresh = p.ask(self.args, run=lambda *_: json.dumps(good))
        self.assertEqual(fresh["question_for_lead"], "")
        self.assertEqual(fresh["evidence"], [])
        record = json.loads(Path(fresh["receipt"]).read_text())
        opdir = Path(record["operations"][0]["directory"])
        self.assertEqual(json.loads((opdir / "transport-schema.json").read_text()), p.CLAUDE_TRANSPORT_SCHEMA)
        self.args.task = fresh["task_id"]
        self.assertTrue(p.ask(self.args, run=lambda *_: self.fail("duplicate inference"))["cached"])
        self.args.task = None
        raw = json.dumps(dict(good, structured_output={"consultation_json": json.dumps({"reply": "partial"})}))
        with self.assertRaises(p.StructuredOutputFailure):
            p.ask(self.args, run=lambda *_: raw)
        paths = list((self.root / "state").glob("*/task.json"))
        record = next(json.loads(path.read_text()) for path in paths
                      if path.parent.name != fresh["task_id"])
        self.assertEqual(record["operations"][0]["error_class"], "StructuredOutputFailure")
        self.assertNotIn("result", record["operations"][0])
        self.args.task = record["task_id"]
        with self.assertRaisesRegex(p.Refused, "--retry"):
            p.ask(self.args, run=lambda *_: self.fail("automatic retry"))
        self.assertFalse((self.root / "availability/claude.json").exists())

    def test_policy_three_failure_still_needs_retry_after_contract_upgrade(self):
        def timeout(*_):
            raise subprocess.TimeoutExpired("fixture", 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            p.ask(self.args, run=timeout)
        path = next((self.root / "state").glob("*/task.json"))
        record = json.loads(path.read_text())
        op = record["operations"][0]
        op["cache_version"] = 3
        op["key"] = p.digest(json.dumps([3, op["review_key"], op["requested_selection"]], sort_keys=True).encode())
        p.save(path, record)
        self.args.task = record["task_id"]
        with self.assertRaisesRegex(p.Refused, "--retry"):
            p.ask(self.args, run=lambda *_: self.fail("Policy3 failure bypassed retry gate"))
        self.args.retry = True
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_policy_three_success_is_not_reused_for_the_new_contract(self):
        first = p.ask(self.args, run=self.claude)
        path = Path(first["receipt"])
        record = json.loads(path.read_text())
        op = record["operations"][0]
        op["cache_version"] = 3
        op["key"] = p.digest(json.dumps([3, op["review_key"], op["requested_selection"]], sort_keys=True).encode())
        p.save(path, record)
        self.args.task = first["task_id"]
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_policy_four_failure_still_needs_retry_after_contract_upgrade(self):
        def timeout(*_):
            raise subprocess.TimeoutExpired("fixture", 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            p.ask(self.args, run=timeout)
        path = next((self.root / "state").glob("*/task.json"))
        record = json.loads(path.read_text())
        op = record["operations"][0]
        op["cache_version"] = 4
        op["key"] = p.digest(json.dumps([4, op["review_key"], op["requested_selection"]], sort_keys=True).encode())
        p.save(path, record)
        self.args.task = record["task_id"]
        with self.assertRaisesRegex(p.Refused, "--retry"):
            p.ask(self.args, run=lambda *_: self.fail("Policy4 failure bypassed retry gate"))
        self.args.retry = True
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_policy_four_success_is_not_reused_for_the_new_contract(self):
        first = p.ask(self.args, run=self.claude)
        path = Path(first["receipt"])
        record = json.loads(path.read_text())
        op = record["operations"][0]
        op["cache_version"] = 4
        op["key"] = p.digest(json.dumps([4, op["review_key"], op["requested_selection"]], sort_keys=True).encode())
        p.save(path, record)
        self.args.task = first["task_id"]
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_policy_five_failure_still_needs_retry_after_contract_upgrade(self):
        def timeout(*_):
            raise subprocess.TimeoutExpired("fixture", 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            p.ask(self.args, run=timeout)
        path = next((self.root / "state").glob("*/task.json"))
        record = json.loads(path.read_text())
        op = record["operations"][0]
        op["cache_version"] = 5
        op["key"] = p.digest(json.dumps([5, op["review_key"], op["requested_selection"]], sort_keys=True).encode())
        p.save(path, record)
        self.args.task = record["task_id"]
        with self.assertRaisesRegex(p.Refused, "--retry"):
            p.ask(self.args, run=lambda *_: self.fail("Policy5 failure bypassed retry gate"))
        self.args.retry = True
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_policy_five_success_is_not_reused_for_the_new_contract(self):
        first = p.ask(self.args, run=self.claude)
        path = Path(first["receipt"])
        record = json.loads(path.read_text())
        op = record["operations"][0]
        op["cache_version"] = 5
        op["key"] = p.digest(json.dumps([5, op["review_key"], op["requested_selection"]], sort_keys=True).encode())
        p.save(path, record)
        self.args.task = first["task_id"]
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_selection_defaults_config_precedence_and_explicit_overrides(self):
        with patch.object(p, "configuration", return_value=({}, set())):
            for peer, model in p.DEFAULT_MODELS.items():
                selected, _ = p.peer_selection(peer, self.project)
                self.assertEqual(selected["model"], model)
                self.assertEqual(selected["reasoning_effort"], "high")
                self.assertEqual(selected["native_fallback"], peer == "claude")
        with patch.object(p, "configuration", return_value=({"model": "configured-model"}, set())):
            selected, _ = p.peer_selection("codex", self.project)
            self.assertEqual(selected["model"], "configured-model")
            self.assertEqual(selected["reasoning_effort"], "high")
            selected, _ = p.peer_selection("codex", self.project, "explicit-model", "xhigh")
            self.assertEqual((selected["model"], selected["reasoning_effort"]), ("explicit-model", "xhigh"))

    def test_changed_model_or_effort_invalidates_cache_for_both_peers(self):
        for lead, run in (("codex", self.claude), ("claude", self.codex)):
            self.args.lead, self.args.task = lead, None
            self.args.model, self.args.effort = "model-a", "high"
            first = p.ask(self.args, run=run)
            self.args.task = first["task_id"]
            cached = p.ask(self.args, run=lambda *_: self.fail("Identical selection was not cached"))
            self.assertEqual(cached["requested_selection"], first["requested_selection"])
            self.args.effort = "xhigh"
            second = p.ask(self.args, run=run)
            self.assertFalse(second["cached"])
            self.assertEqual(second["requested_selection"]["reasoning_effort"], "xhigh")
            self.args.model = "model-b"
            third = p.ask(self.args, run=run)
            self.assertFalse(third["cached"])
            record = json.loads(Path(third["receipt"]).read_text())
            self.assertEqual(len({op["review_key"] for op in record["operations"]}), 1)
            self.assertEqual(len({op["key"] for op in record["operations"]}), 3)
            self.assertEqual(record["operations"][-1]["requested_selection"], third["requested_selection"])
            self.assertEqual(self.command_mock.call_args.args[3], third["requested_selection"])
            self.args.model, self.args.effort = "model-a", "high"
            returned = p.ask(self.args, run=run)
            self.assertFalse(returned["cached"])
            self.assertEqual(returned["requested_selection"], first["requested_selection"])
            self.assertTrue(p.ask(self.args, run=lambda *_: self.fail("Fourth completed result was not cached"))["cached"])

    def test_changed_codex_config_is_resolved_before_cache_and_only_once(self):
        self.args.lead = "claude"
        self.fake_command.stop()
        config = {"model": "gpt-6.1-sol", "model_reasoning_effort": "high"}
        with patch.object(p, "executable", return_value="fixture"), patch.object(p, "configuration", return_value=(config, set())) as resolver:
            def native(args, *_):
                self.assertIn('model_reasoning_effort=' + json.dumps(config["model_reasoning_effort"]), args)
                return self.codex()
            first = p.ask(self.args, run=native)
            self.assertEqual(resolver.call_count, 1)
            self.args.task = first["task_id"]
            config["model_reasoning_effort"] = "xhigh"
            self.assertFalse(p.ask(self.args, run=native)["cached"])
            self.assertEqual(resolver.call_count, 2)

    def test_changing_selection_cannot_bypass_latest_failure_or_reuse_older_success(self):
        first = p.ask(self.args, run=self.claude)
        self.args.task, self.args.effort = first["task_id"], "xhigh"
        def timeout(*_):
            raise subprocess.TimeoutExpired("fixture", 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            p.ask(self.args, run=timeout)
        self.args.effort = "high"
        with self.assertRaisesRegex(p.Refused, "--retry"):
            p.ask(self.args, run=lambda *_: self.fail("Selection change bypassed failure"))
        self.args.retry = True
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_invalid_selection_never_spends_a_task_call(self):
        for model, effort in (("bad model", "high"), ("valid-model", "ultra")):
            self.args.model, self.args.effort = model, effort
            with self.assertRaises(p.Refused):
                p.ask(self.args, run=lambda *_: self.fail("Invalid selection launched inference"))
        self.assertEqual(list((self.root / "state").glob("*/task.json")), [])

    def test_child_does_not_inherit_effort_override(self):
        directory = self.root / "environment-child"
        directory.mkdir()
        code = "import os,json;print(json.dumps(os.environ.get('CLAUDE_CODE_EFFORT_LEVEL')))"
        with patch.dict(os.environ, {"CLAUDE_CODE_EFFORT_LEVEL": "low"}):
            stdout = p.invoke([sys.executable, "-B", "-c", code], "fixture", self.project, directory, 10)
            self.assertEqual(os.environ["CLAUDE_CODE_EFFORT_LEVEL"], "low")
        self.assertIsNone(json.loads(stdout))

    def test_native_fallback_result_preserves_models_in_receipt_and_cache(self):
        envelope = dict(type="result", subtype="success", is_error=False, structured_output={"consultation_json": json.dumps(RESPONSE)}, session_id="native-fallback",
                        modelUsage={"claude-opus-5-5": {}, "claude-opus-4-8": {}})
        calls = []
        def native(*_):
            calls.append(1)
            return json.dumps(envelope)
        fresh = p.ask(self.args, run=native)
        self.assertEqual(fresh["reply"], RESPONSE["reply"])
        self.assertEqual(fresh["models_used"], ["claude-opus-4-8", "claude-opus-5-5"])
        record = json.loads(Path(fresh["receipt"]).read_text())
        self.assertEqual(record["operations"][0]["models_used"], fresh["models_used"])
        self.args.task = fresh["task_id"]
        cached = p.ask(self.args, run=native)
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["models_used"], fresh["models_used"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(record["operations"]), 1)

    def test_native_refusal_is_saved_without_retry_or_shared_cooldown(self):
        for nonzero in (False, True):
            with self.subTest(nonzero=nonzero):
                self.args.task = None
                calls = []
                previous = set((self.root / "state").glob("*/task.json"))
                raw = json.dumps(dict(is_error=nonzero, stop_reason="refusal", session_id="declined",
                                      structured_output={"consultation_json": json.dumps(RESPONSE)}))
                def native(*_):
                    calls.append(1)
                    if nonzero:
                        raise p.PeerFailure(1, raw)
                    return raw
                with self.assertRaisesRegex(p.ModelRefusal, "Continue independent allowed work"):
                    p.ask(self.args, run=native)
                created = set((self.root / "state").glob("*/task.json")) - previous
                self.assertEqual(len(created), 1)
                record = json.loads(created.pop().read_text())
                # No refused response, even one with valid JSON, can become a cached success.
                self.assertEqual(len(record["operations"]), 1)
                self.assertEqual(record["operations"][0]["error_class"], "ModelRefusal")
                self.assertEqual(record["operations"][0]["status"], "failed")
                self.assertNotIn("result", record["operations"][0])
                self.assertEqual(len(calls), 1)
                self.assertFalse((self.root / "availability/claude.json").exists())

    def test_model_metadata_is_optional_and_review_text_is_not_a_refusal_signal(self):
        for usage in (None, [], {}):
            raw = json.dumps(dict(type="result", subtype="success", is_error=False, structured_output={"consultation_json": json.dumps(RESPONSE)},
                                  session_id="native-fixture", modelUsage=usage))
            self.assertEqual(p.models_used("claude", raw), [])
        quoted = dict(RESPONSE, reply='Source text contains stop_reason: refusal, not a native refusal')
        parsed, _ = p.parse("claude", json.dumps(dict(type="result", subtype="success", is_error=False, structured_output={"consultation_json": json.dumps(quoted)},
                                                     session_id="quoted")))
        self.assertEqual(parsed, quoted)
        with self.assertRaisesRegex(p.Refused, "no structured consultation"):
            p.parse("claude", json.dumps(dict(type="result", subtype="success", is_error=False, structured_output={"consultation_json": "No structured answer"}, session_id="none")))

    def test_owner_change_and_state_inside_project_are_refused(self):
        result = p.ask(self.args, run=self.claude)
        self.args.task, self.args.lead = result["task_id"], "claude"
        with self.assertRaisesRegex(p.Refused, "ownership"):
            p.ask(self.args, run=self.codex)
        self.args.state_root = str(self.project / "state")
        with self.assertRaisesRegex(p.Refused, "outside"):
            p.ask(self.args, run=self.codex)

    def test_failed_operation_requires_explicit_retry(self):
        def failure(*_):
            raise subprocess.TimeoutExpired("fixture", 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            p.ask(self.args, run=failure)
        self.args.task = next((self.root / "state").iterdir()).name
        with self.assertRaisesRegex(p.Refused, "--retry"):
            p.ask(self.args, run=self.claude)
        self.args.retry = True
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_mutated_checkout_is_reported_without_rollback(self):
        def mutate(*_):
            (self.project / "source.txt").write_text("concurrent change\n", encoding="utf-8")
            return self.claude()
        with self.assertRaisesRegex(p.Refused, "Checkout changed"):
            p.ask(self.args, run=mutate)
        task = next((self.root / "state").iterdir()) / "task.json"
        self.assertEqual(json.loads(task.read_text())["operations"][0]["status"], "project_changed")
        self.assertEqual((self.project / "source.txt").read_text(), "concurrent change\n")

    def test_changed_source_invalidates_cache(self):
        result = p.ask(self.args, run=self.claude)
        self.args.task = result["task_id"]
        (self.project / "source.txt").write_text("new lead implementation\n", encoding="utf-8")
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_untracked_edits_never_reuse_cache_or_read_private_contents(self):
        helper = self.project / "new-helper.py"
        helper.write_text("print(1)\n")
        read_bytes = Path.read_bytes
        def guarded(path):
            if path == helper:
                self.fail("Untracked contents must not be read by the fingerprint")
            return read_bytes(path)
        with patch.object(Path, "read_bytes", guarded):
            first = p.ask(self.args, run=self.claude)
            self.args.task = first["task_id"]
            helper.write_text("raise RuntimeError('different code')\n")
            self.assertFalse(p.ask(self.args, run=self.claude)["cached"])
            self.assertFalse(p.ask(self.args, run=self.claude)["cached"])
        record = json.loads((self.root / "state" / first["task_id"] / "task.json").read_text())
        self.assertEqual(len(record["operations"]), 3)
        self.assertTrue(all(not op["cache_eligible"] for op in record["operations"]))

    def test_legacy_cached_receipt_is_readable_but_not_reused(self):
        first = p.ask(self.args, run=self.claude)
        self.args.task = first["task_id"]
        path = self.root / "state" / first["task_id"] / "task.json"
        record = json.loads(path.read_text())
        del record["operations"][0]["cache_version"]
        p.save(path, record)
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])
        self.assertEqual(len(json.loads(path.read_text())["operations"]), 2)

    def test_legacy_failure_cannot_silently_bypass_explicit_retry(self):
        def timeout(*_):
            raise subprocess.TimeoutExpired("fixture", 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            p.ask(self.args, run=timeout)
        path = next((self.root / "state").glob("*/task.json"))
        record = json.loads(path.read_text())
        before = p.snapshot(self.project)
        fingerprint = p.digest(json.dumps([before["head"], before["status"], before["tracked_files"]],
                                          sort_keys=True).encode())
        record["operations"][0]["key"] = p.digest((self.request.read_text().strip() + "\0" + fingerprint).encode())
        del record["operations"][0]["review_key"]
        del record["operations"][0]["cache_version"]
        p.save(path, record)
        self.args.task = record["task_id"]
        with self.assertRaisesRegex(p.Refused, "--retry"):
            p.ask(self.args, run=lambda *_: self.fail("Legacy failure was retried"))
        self.args.retry = True
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_version_two_failures_require_retry_after_selection_and_policy_change(self):
        def timeout(*_):
            raise subprocess.TimeoutExpired("fixture", 30)
        with self.assertRaises(subprocess.TimeoutExpired):
            p.ask(self.args, run=timeout)
        path = next((self.root / "state").glob("*/task.json"))
        record = json.loads(path.read_text())
        before = p.snapshot(self.project)
        fingerprint = p.digest(json.dumps([2, before["head"], before["status"], before["tracked_files"]], sort_keys=True).encode())
        operation = record["operations"][0]
        operation.update(key=p.digest((self.request.read_text().strip() + "\0" + fingerprint).encode()),
                         project_fingerprint=fingerprint, cache_version=2)
        del operation["review_key"]
        del operation["requested_selection"]
        p.save(path, record)
        self.args.task, self.args.effort = record["task_id"], "xhigh"
        with self.assertRaisesRegex(p.Refused, "--retry"):
            p.ask(self.args, run=lambda *_: self.fail("Version 2 failure was retried"))
        self.args.retry = True
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_version_two_success_is_readable_but_never_reused(self):
        first = p.ask(self.args, run=self.claude)
        path = Path(first["receipt"])
        record = json.loads(path.read_text())
        before = p.snapshot(self.project)
        fingerprint = p.digest(json.dumps([2, before["head"], before["status"], before["tracked_files"]], sort_keys=True).encode())
        operation = record["operations"][0]
        operation.update(key=p.digest((self.request.read_text().strip() + "\0" + fingerprint).encode()), cache_version=2)
        del operation["review_key"]
        p.save(path, record)
        self.args.task = first["task_id"]
        self.assertFalse(p.ask(self.args, run=self.claude)["cached"])

    def test_ignored_files_are_not_read_or_certified(self):
        (self.project / ".git/info/exclude").write_text("private.bin\n")
        secret = self.project / "private.bin"
        secret.write_text("private fixture data")
        before = p.snapshot(self.project)
        secret.write_text("changed private fixture data")
        self.assertEqual(before, p.snapshot(self.project))
        self.assertNotIn("private.bin", before["tracked_files"])

    def test_nonzero_quota_output_is_saved_and_blocks_other_projects_and_stores(self):
        instant = 1792262157  # 2026-10-17 18:35:57 UTC
        envelope = dict(type="result", is_error=True, api_error_status=429,
                        result="You've hit your session limit", resets_at=instant + 3600)
        code = "import json,sys;print(json.dumps(" + repr(envelope) + "));sys.exit(2)"
        def native(_args, prompt, project, directory, timeout):
            return p.invoke([sys.executable, "-B", "-c", code], prompt, project, directory, timeout)
        with patch.object(p.time, "time", return_value=instant):
            with self.assertRaises(p.QuotaLimited) as failure:
                p.ask(self.args, run=native)
            self.assertEqual(failure.exception.details["retry_at"], p.utc_stamp(instant + 3600))
            task = next((self.root / "state").iterdir())
            record = json.loads((task / "task.json").read_text())
            self.assertEqual(record["operations"][0]["error_class"], "QuotaLimited")
            self.assertEqual(record["operations"][0]["quota"]["code"], "quota_exceeded")
            self.assertTrue((task / "1/stdout.jsonl").exists())
            other = self.root / "other-project"
            subprocess.run(["git", "clone", "-q", "--local", str(self.project), str(other)],
                           check=True, capture_output=True)
            self.args.project, self.args.state_root = str(other), str(self.root / "other-state")
            self.args.task, self.args.retry = None, True
            with self.assertRaises(p.QuotaLimited):
                p.ask(self.args, run=lambda *_: self.fail("Cooldown allowed another native call"))
            self.assertEqual(list((self.root / "other-state").glob("*/task.json")), [])
            # Codex has independent availability; a Claude limit must not block it.
            self.args.lead, self.args.retry = "claude", False
            self.assertFalse(p.ask(self.args, run=self.codex)["cached"])

    def test_expired_cooldown_still_requires_explicit_retry(self):
        instant = 1792262157
        def limited(*_):
            return json.dumps(dict(is_error=True, api_error_status=429, retry_after_seconds=60))
        with patch.object(p.time, "time", return_value=instant):
            with self.assertRaises(p.QuotaLimited):
                p.ask(self.args, run=limited)
        self.args.task = next((self.root / "state").iterdir()).name
        with patch.object(p.time, "time", return_value=instant + 61):
            with self.assertRaisesRegex(p.Refused, "--retry"):
                p.ask(self.args, run=self.claude)
            self.args.retry = True
            self.assertFalse(p.ask(self.args, run=self.claude)["cached"])
            p.shared_cooldown("claude", p.QuotaLimited("claude", p.utc_stamp(instant + 500), "native_timestamp"))
            self.assertTrue(p.ask(self.args, run=lambda *_: self.fail("Cached delivery called peer"))["cached"])

    def test_native_reset_time_uses_named_timezone_and_rolls_forward(self):
        observed = p.datetime(2026, 10, 17, 18, 35, 57, tzinfo=p.timezone.utc).timestamp()
        with patch.object(p.time, "time", return_value=observed):
            error = p.quota_error("claude", json.dumps(dict(type="result", is_error=True,
                api_error_status=429, result="You've hit your session limit · resets 3pm (America/Chicago)")))
        self.assertEqual(error.details["retry_at"], "2026-10-17T20:00:00Z")
        self.assertEqual(error.details["reset_source"], "native_reset_message")
        later = p.datetime(2026, 10, 17, 21, tzinfo=p.timezone.utc).timestamp()
        self.assertEqual(p.utc_stamp(p.reset_from_message("resets 3pm (America/Chicago)", later)),
                         "2026-10-18T20:00:00Z")

    def test_quota_detection_is_error_only_and_preserves_safe_metadata(self):
        instant = 1792262157
        with patch.object(p.time, "time", return_value=instant):
            with self.assertRaises(p.QuotaLimited) as failure:
                p.parse("codex", json.dumps(dict(type="turn.failed", error=dict(
                    code="usage_limit_reached", reset_at="2026-10-17T20:00:00Z"))))
            self.assertEqual(failure.exception.details["reset_source"], "native_timestamp")
            fallback = p.quota_error("claude", json.dumps(dict(is_error=True, api_error_status=429,
                result="private detail that must stay in the native receipt")))
            self.assertEqual(fallback.details["retry_at"], p.utc_stamp(instant + 900))
            self.assertEqual(fallback.details["reset_source"], "fallback_15_minutes")
            self.assertNotIn("private detail", str(fallback))
            pretty = p.quota_error("claude", json.dumps(dict(is_error=True, api_error_status=429,
                retry_after_seconds=120), indent=2))
            self.assertEqual(pretty.details["retry_at"], p.utc_stamp(instant + 120))
        response = dict(RESPONSE, reply="You've hit your session limit: quoted source, not an error")
        parsed, _ = p.parse("claude", json.dumps(dict(type="result", subtype="success", is_error=False, structured_output={"consultation_json": json.dumps(response)}, session_id="ok")))
        self.assertEqual(parsed, response)
        self.assertIsNone(p.quota_error("claude", json.dumps(dict(is_error=True, api_error_status=401))))

    def test_invalid_shared_state_blocks_preflight_without_spending_a_call(self):
        path = self.root / "availability/claude.json"
        path.parent.mkdir()
        path.write_text('{"peer":"claude","code":"quota_exceeded","retry_at":"invalid"}')
        with self.assertRaisesRegex(p.Refused, "cooldown state is invalid"):
            p.ask(self.args, run=lambda *_: self.fail("Invalid state allowed a native call"))
        self.assertEqual(list((self.root / "state").glob("*/task.json")), [])

    def test_quota_receipt_survives_shared_state_write_failure(self):
        real = p.shared_cooldown
        def unavailable(peer, failure=None):
            if failure is not None:
                raise OSError("Fixture disk failure")
            return real(peer)
        with patch.object(p, "shared_cooldown", side_effect=unavailable):
            with self.assertRaises(OSError):
                p.ask(self.args, run=lambda *_: json.dumps(dict(is_error=True, api_error_status=429)))
        path = next((self.root / "state").glob("*/task.json"))
        operation = json.loads(path.read_text())["operations"][0]
        self.assertEqual(operation["status"], "failed")
        self.assertEqual(operation["quota"]["code"], "quota_exceeded")

    def test_shared_cooldown_updates_are_atomic_and_never_shorten_across_processes(self):
        script = '''import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('peer',sys.argv[1])
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
p.availability_root=lambda:Path(sys.argv[2])
p.shared_cooldown('claude',p.QuotaLimited('claude',sys.argv[3],'native_timestamp'))
'''
        root = self.root / "availability"
        deadlines = [p.utc_stamp(time.time() + delta) for delta in (120, 60)]
        children = [subprocess.Popen([sys.executable, "-B", "-c", script,
                    str(ROOT / "skills/work-partners/scripts/partner.py"), str(root), deadline],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for deadline in deadlines]
        for child in children:
            _, stderr = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, stderr.decode())
        self.assertEqual(json.loads((root / "claude.json").read_text())["retry_at"], max(deadlines))

    def test_exclusive_lock_and_automatic_release(self):
        path = self.root / "lock"
        with p.lock(path):
            with self.assertRaises(p.Refused):
                with p.lock(path):
                    self.fail("overlapping consultation")
        with p.lock(path):
            pass

    def test_nested_partner_is_refused_before_cli_launch(self):
        with patch.dict(os.environ, {p.GUARD: "1"}), patch.object(sys, "argv", ["partner", "status", "--task", "00000000-0000-0000-0000-000000000000"]):
            with self.assertRaisesRegex(p.Refused, "cannot launch"):
                p.main()

    def test_malformed_or_failed_response_is_not_success(self):
        for raw in ['{"is_error":true}', '{"structured_output":{"reply":"partial"}}']:
            with self.assertRaises(p.Refused):
                p.parse("claude", raw)
        with self.assertRaises(p.Refused):
            p.parse("codex", json.dumps(dict(type="turn.failed")))
        partial = '\n'.join(self.codex().splitlines()[:-1])
        with self.assertRaisesRegex(p.Refused, "completed turn"):
            p.parse("codex", partial)

    def test_timeout_terminates_the_owned_process(self):
        directory = self.root / 'process'
        directory.mkdir()
        with self.assertRaises(subprocess.TimeoutExpired):
            p.invoke([sys.executable, '-u', '-c', 'import time; print("started", flush=True); time.sleep(30)'],
                     'fixture', self.project, directory, .5)
        self.assertIn('started', (directory / 'stdout.jsonl').read_text())

    def test_exhausted_task_refuses_more_inference(self):
        for index in range(4):
            self.request.write_text('Question '+str(index), encoding='utf-8')
            result = p.ask(self.args, run=self.claude)
            self.args.task = result['task_id']
        self.request.write_text('One more', encoding='utf-8')
        with self.assertRaisesRegex(p.Refused, 'Four-call'):
            p.ask(self.args, run=lambda *_:self.fail('extra inference'))

    def test_native_commands_restrict_peer_capabilities(self):
        self.fake_command.stop()
        with patch.object(p.shutil, "which", return_value="native"), patch.object(p, "configuration", return_value=({}, {"unsafe"})):
            claude = p.command("claude", self.project, self.root / "schema")
            self.assertIn("--restricted", claude)
            self.assertIn("--safe-mode", claude)
            self.assertIn("--strict-mcp-config", claude)
            self.assertEqual(json.loads(claude[claude.index("--settings") + 1]), {"switchModelsOnFlag": True})
            self.assertEqual(claude[claude.index("--model") + 1], "claude-opus-5-5")
            self.assertEqual(claude[claude.index("--effort") + 1], "high")
            self.assertEqual(claude[claude.index("--permission-mode") + 1], "dontAsk")
            self.assertEqual(claude[claude.index("--tools") + 1], "Read,Glob,Grep")
            self.assertEqual(claude[claude.index("--output-format") + 1], "json")
            self.assertEqual(claude[claude.index("--permission-prompts") + 1], "none")
            self.assertEqual(json.loads(claude[claude.index("--json-schema") + 1]), p.CLAUDE_TRANSPORT_SCHEMA)
            codex = p.command("codex", self.project, self.root / "schema")
            self.assertEqual(codex[codex.index("--output-schema") + 1], str(self.root / "schema"))
            self.assertEqual(codex[codex.index("--sandbox") + 1], "read-only")
            self.assertIn('mcp_servers."unsafe"={command="python",enabled=false}', codex)
            self.assertIn("features.plugins=false", codex)
            self.assertIn('model="gpt-6.1-sol"', codex)
            self.assertIn('model_reasoning_effort="high"', codex)

    def doctor_fixture(self, lead="both", help_override=None, failure=None):
        self.fake_command.stop()
        args = argparse.Namespace(project=str(self.project), state_root=str(self.root / "private/state"), lead=lead, timeout=1)
        calls = []
        helps = {
            "claude": "-p --restricted --safe-mode --strict-mcp-config --settings --model --effort "
                      "--permission-mode --permission-prompts --tools --output-format --json-schema",
            "codex": "--ignore-user-config --sandbox --json --color --output-schema -c"}
        def probe(argv, _timeout):
            calls.append(argv)
            peer = Path(argv[0]).name
            if argv[1:] == ["--version"]:
                return "git version 2.50.0" if peer == "git" else "native-cli 0.1.0"
            if peer == "git" and argv[1:] == ["--no-optional-locks", "-C", str(self.project), "rev-parse", "--show-toplevel"]:
                return str(self.project)
            expected = ["exec", "--help"] if peer == "codex" else ["--help"]
            self.assertEqual(argv[1:], expected)  # No auth command, prompt or model inference.
            if failure and peer == "claude":
                raise failure
            if help_override is not None and peer == "claude":
                return help_override
            return "\n".join("  " + flag + " <value>" for flag in helps[peer].split())
        with patch.object(p.shutil, "which", side_effect=lambda name: str(self.root / name)), \
                patch.object(p, "executable", side_effect=lambda name: str(self.root / name)), \
                patch.object(p, "configuration", return_value=({}, set())):
            result = p.doctor(args, probe=probe)
        return result, calls

    def test_doctor_checks_real_command_flags_without_writes_or_model_calls(self):
        before = p.snapshot(self.project)
        result, calls = self.doctor_fixture()
        self.assertTrue(result["local_checks_passed"])
        self.assertEqual(result["model_calls"], 0)
        self.assertTrue(result["unverified"])
        self.assertEqual(len(calls), 6)
        self.assertFalse((self.root / "private").exists())
        self.assertFalse((self.root / "availability").exists())
        self.assertEqual(p.snapshot(self.project), before)

    def test_doctor_missing_hidden_or_prose_only_flags_fail_closed(self):
        result, _ = self.doctor_fixture(help_override="Usage: native --safe-mode\n  --restricted-extra <arg>\n  -p, --print <arg>")
        check = next(c for c in result["checks"] if c["check"] == "claude_cli")
        self.assertFalse(result["local_checks_passed"])
        self.assertIn("--safe-mode", check["not_advertised"])
        self.assertIn("--restricted", check["not_advertised"])
        self.assertNotIn("-p", check["not_advertised"])

    def test_doctor_direction_and_probe_failures(self):
        for lead, expected in (("codex", "claude_cli"), ("claude", "codex_cli")):
            result, calls = self.doctor_fixture(lead=lead)
            self.assertEqual(len(calls), 4)
            self.assertEqual([c["check"] for c in result["checks"] if c["check"].endswith("_cli")], [expected])
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("fixture", 1), p.Refused("metadata failed")):
            result, _ = self.doctor_fixture(failure=error)
            self.assertFalse(result["local_checks_passed"])
            self.assertEqual(result["checks"][-1]["status"], "pass")  # Other peer still checked.

    def test_doctor_invalid_project_state_and_untrusted_executable(self):
        args = argparse.Namespace(project=str(self.project), state_root=str(self.project / "state"), lead="both", timeout=1)
        with patch.object(p.shutil, "which", return_value=None):
            result = p.doctor(args, probe=lambda *_: self.fail("No Git executable"))
        self.assertFalse(result["local_checks_passed"])
        def probe(argv, _timeout):
            return "git version 2.50.0" if argv[-1] == "--version" else str(self.project)
        with patch.object(p.shutil, "which", return_value=str(self.root / "git")):
            self.assertFalse(p.doctor(args, probe=probe)["local_checks_passed"])
        self.fake_command.stop()
        binary = self.project / "native.exe"
        binary.write_text("untrusted", encoding="utf-8")
        with patch.object(p.shutil, "which", return_value=str(binary)):
            with self.assertRaisesRegex(p.Refused, "inside the reviewed project"):
                p.command("claude", self.project, self.root / "schema.json")

    def test_diagnostic_probe_child_timeout_and_guard(self):
        code = "import os,sys;print(os.environ.get('A51_PARTNER_CHILD'));print(sys.stdin.read())"
        self.assertEqual(p.diagnostic_probe([sys.executable, "-B", "-c", code], 3).strip(), "1")
        with self.assertRaises(subprocess.TimeoutExpired):
            p.diagnostic_probe([sys.executable, "-B", "-c", "import time;time.sleep(30)"], 1)

    def test_checked_peer_path_is_the_absolute_path_returned(self):
        relative = "./doctor-fixture-native"
        with patch.object(p, "executable", return_value=relative):
            result = p.trusted_executable("claude", self.project)
        self.assertTrue(Path(result).is_absolute())
        self.assertEqual(result, str((Path.cwd() / "doctor-fixture-native").resolve()))

    def test_git_uses_same_external_absolute_resolution_and_rejects_repo_parent(self):
        subdirectory = self.project / "nested"
        subdirectory.mkdir()
        with patch.object(p.shutil, "which", return_value=str(self.project / "git.exe")):
            with self.assertRaisesRegex(p.Refused, "outside the reviewed checkout"):
                p.git_executable(subdirectory)
        relative = "./doctor-fixture-git"
        with patch.object(p.shutil, "which", return_value=relative), \
                patch.object(p.subprocess, "check_output", return_value=b"fixture") as child:
            p.git(self.project, "status", "--porcelain")
        self.assertEqual(child.call_args.args[0][0], str((Path.cwd() / "doctor-fixture-git").resolve()))

    def test_installer_custom_roots_isolation_overlap_and_relocated_source(self):
        home = self.root / "home"
        codex, claude = self.root / "custom Codex", self.root / "custom Claude"
        result = installer.install(home, codex, claude)
        self.assertIn(str(codex / "skills/work-partners/SKILL.md"), result["files"])
        self.assertFalse((home / ".codex").exists())
        with self.assertRaisesRegex(RuntimeError, "overlap"):
            installer.install(home, codex, codex)
        with self.assertRaisesRegex(RuntimeError, "outside the source"):
            installer.install(home, ROOT / "temporary-agent")
        isolated = self.root / "isolated"
        with patch.dict(os.environ, {"CODEX_HOME": str(codex), "CLAUDE_CONFIG_DIR": str(claude)}):
            command = [sys.executable, "-B", str(ROOT / "scripts/install-desktop-partners.py"), "--home", str(isolated)]
            subprocess.run(command, check=True, capture_output=True)
        self.assertTrue((isolated / ".codex/skills/work-partners/SKILL.md").exists())
        relocated = self.root / "relocated source"
        for name in ("scripts/install-desktop-partners.py", "skills/work-partners/SKILL.md",
                     "skills/work-partners/agents/openai.yaml", "skills/work-partners/scripts/partner.py"):
            target = relocated / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        installed = subprocess.run([sys.executable, "-B", str(relocated / "scripts/install-desktop-partners.py"),
                                   "--home", str(self.root / "relocated home")], check=True, capture_output=True)
        self.assertEqual(json.loads(installed.stdout)["source"], str(relocated))

    def test_python_version_gate_precedes_runtime_dependencies(self):
        for script in (ROOT / "skills/work-partners/scripts/partner.py", ROOT / "scripts/install-desktop-partners.py"):
            code = "import sys,runpy;sys.version_info=(3,11,0);runpy.run_path(sys.argv[1],run_name='__main__')"
            result = subprocess.run([sys.executable, "-B", "-c", code, str(script)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires Python 3.12", result.stderr)

    def test_doctor_cli_exit_code_and_bad_project_without_task_state(self):
        state = self.root / "no-state"
        command = [sys.executable, "-B", str(ROOT / "skills/work-partners/scripts/partner.py"),
                   "--state-root", str(state), "doctor", "--lead", "codex", "--project", str(self.project)]
        env = dict(os.environ, A51_PARTNER_CLAUDE_CLI=str(self.root / "missing-native"))
        env.pop(p.GUARD, None)
        result = subprocess.run(command, env=env, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["local_checks_passed"])
        self.assertFalse(state.exists())
        command[-1] = str(self.root / "no-project")
        result = subprocess.run(command, env=env, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["checks"][-1]["check"], "git_project")

    def test_installer_environment_roots_and_link_rejection_before_writes(self):
        home = self.root / "environment home"
        codex, claude = self.root / "environment Codex", self.root / "environment Claude"
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CODEX_HOME=str(codex), CLAUDE_CONFIG_DIR=str(claude))
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/install-desktop-partners.py")],
                                env=env, capture_output=True, check=True)
        files = json.loads(result.stdout)["files"]
        self.assertIn(str(codex / "skills/work-partners/SKILL.md"), files)
        self.assertIn(str(claude / "skills/work-partners/SKILL.md"), files)
        untouched = self.root / "untouched home"
        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "symlinks"):
                installer.install(untouched)
        self.assertFalse(untouched.exists())

    def test_installer_matches_both_skills_and_preserves_modified_files(self):
        home = self.root / "home"
        result = installer.install(home)
        first = home / ".codex/skills/work-partners/SKILL.md"
        other = home / ".claude/skills/work-partners/SKILL.md"
        self.assertEqual(first.read_bytes(), other.read_bytes())
        self.assertEqual(len(result["files"]), 4)
        installer.install(home)
        other.write_text("local work", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "modified"):
            installer.install(home)
        self.assertEqual(other.read_text(), "local work")

    def test_live_fixture_is_isolated_committed_and_reproducible(self):
        first = manual.create_fixture(self.root)
        second = manual.create_fixture(self.root)
        self.assertNotEqual(first, second)
        for project in (first, second):
            self.assertNotEqual(project, self.project)
            self.assertEqual(p.git(project, "status", "--porcelain"), b"")
            namespace = {}
            exec((project / "calculator.py").read_text(), namespace)
            # The live reviewer must be able to find an actual unmet requirement.
            self.assertNotEqual(namespace["total"]([2, 3]), 5)
            self.assertTrue((project / "README.md").exists())


if __name__ == "__main__":
    unittest.main()
