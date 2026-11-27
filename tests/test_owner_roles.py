"""#961: host-owned bot roles deliver real handoff comments without human mentions."""
import json
import unittest
from pathlib import Path
from unittest import mock

from factory import config
from tests import test_handoff as fixtures

BOT = "mikeroysoft-grok"
HOST = f'''[defaults.bot_owners]
fallback = "{BOT}"
[defaults.bot_owners.roles]
"Factory PM" = "{BOT}"
'''


class OwnerRolesTest(unittest.TestCase):
    def setUp(self):
        self.cli = fixtures.HandoffCli()
        self.cli.setUp()
        self.addCleanup(self.cli.doCleanups)
        self.cli.configure(manager=False, collaboration="")
        self.host = Path(self.cli.env["XDG_CONFIG_HOME"]) / "factory" / "config.toml"
        self.host.parent.mkdir(parents=True)
        self.host.write_text(HOST)

    def test_factory_pm_handoff_mentions_configured_bot(self):
        self.cli.state["issue"]["body"] = "**Decision owner**\n\nFactory PM\n\n## Summary\nNot an owner."
        self.cli.manage()
        body = self.cli.requests()[0]["body"]
        self.assertIn(f"@{BOT} — you own this decision (decision_owner)", body)
        self.assertNotIn("invalid", body)
        self.assertEqual(self.cli.events("handoff")[0]["target"], f"@{BOT}")
        self.assertEqual(self.cli.events("handoff")[0]["route"]["status"], "selected")
        self.assertEqual(self.cli.state["issue"]["body"].splitlines()[2], "Factory PM")
        self.assertEqual(self.cli.calls("issue", "edit"), [])

    def test_inline_role_and_host_repository_override(self):
        self.host.write_text(HOST + f'''\n[repo."{fixtures.REPO}".bot_owners.roles]
"Release PM" = "{BOT}"
''')
        self.cli.state["issue"]["body"] = "Decision owner: Release PM\n\n## Summary\nNot an owner."
        self.cli.manage()
        self.assertIn(f"@{BOT} — you own this decision", self.cli.requests()[0]["body"])

    def test_unknown_role_is_logged_and_delivered_to_bot_fallback(self):
        self.cli.state["issue"]["body"] = "**Decision owner**\n\nUnknown Product Role\n\n## Summary\nNot an owner."
        proc = self.cli.manage()
        self.assertIn("unknown decision owner role 'Unknown Product Role'", proc.stdout + proc.stderr)
        body = self.cli.requests()[0]["body"]
        self.assertIn(f"@{BOT} — you own this decision", body)
        self.assertIn("Unknown Product Role", body)
        route = self.cli.events("handoff")[0]["route"]
        self.assertEqual((route["status"], route["owner"]), ("selected", BOT))
        self.assertNotIn("invalid", body)
        self.assertNotIn("unassigned", body)

    def test_personal_login_cannot_be_a_role_target_or_fallback(self):
        for text in (
            HOST.replace(f'"Factory PM" = "{BOT}"', '"Factory PM" = "personal-owner"'),
            HOST.replace(f'fallback = "{BOT}"', 'fallback = "personal-owner"'),
        ):
            with self.subTest(text=text):
                self.host.write_text(text)
                with (
                    mock.patch.dict("os.environ", self.cli.env),
                    self.assertRaisesRegex(config.ConfigError, "approved bot login"),
                ):
                    config.load(self.cli.repo, worktree=True)

    def test_repository_cannot_supply_bot_mapping(self):
        path = self.cli.repo / config.CONFIG_NAME
        path.write_text(path.read_text() + HOST.replace("defaults.bot_owners", "bot_owners"))
        with (
            mock.patch.dict("os.environ", self.cli.env),
            self.assertRaisesRegex(config.ConfigError, "bot_owners.*host-only"),
        ):
            config.load(self.cli.repo, worktree=True)

    def test_mapping_requires_bot_fallback_for_unknown_roles(self):
        self.host.write_text(HOST.replace(f'fallback = "{BOT}"\n', ""))
        with (
            mock.patch.dict("os.environ", self.cli.env),
            self.assertRaisesRegex(config.ConfigError, "fallback"),
        ):
            config.load(self.cli.repo, worktree=True)

    def test_fixture_mentions_only_the_bot(self):
        self.cli.state["issue"]["body"] = "**Decision owner**\nFactory PM"
        self.cli.manage()
        body = self.cli.requests()[0]["body"]
        self.assertEqual(fixtures.handoff.mentions(self.cli.events("handoff")[0]["route"]), [f"@{BOT}"])
        self.assertNotIn("personal-owner", body)
        self.assertNotIn("personal-owner", json.dumps(self.cli.state["issue"]["labels"]))

    def test_unknown_bare_roles_never_become_login_mentions(self):
        for role in ("Maintainer", "Release-PM"):
            for declaration in (f"**Decision owner**\n{role}", f"Decision owner: {role}"):
                with self.subTest(declaration=declaration):
                    self.cli.reset()
                    self.cli.escalate("gate failed after 3 attempts", 1)
                    self.cli.state["issue"]["body"] = declaration
                    proc = self.cli.manage()
                    self.assertIn(f"unknown decision owner role {role!r}", proc.stdout + proc.stderr)
                    route = self.cli.events("handoff")[0]["route"]
                    self.assertEqual((route["status"], route["owner"], route["source"]),
                                     ("selected", BOT, "bot_fallback"))
                    self.assertEqual(fixtures.handoff.mentions(route), [f"@{BOT}"])
                    self.assertNotIn(f"@{role}", self.cli.requests()[0]["body"])

    def test_inline_without_bot_settings_preserves_collaboration_delivery(self):
        self.host.unlink()
        self.cli.configure(manager=False, collaboration=f'[collaboration]\nfallback = "{BOT}"\n')
        for role in ("Factory PM", "Ops team", BOT):
            with self.subTest(role=role):
                self.cli.reset()
                self.cli.escalate("gate failed after 3 attempts", 1)
                self.cli.state["issue"]["body"] = f"Decision owner: {role}"
                self.cli.manage()
                route = self.cli.events("handoff")[0]["route"]
                self.assertEqual((route["status"], route["owner"], route["source"]),
                                 ("selected", BOT, "fallback"))
                self.assertEqual(route["provenance"][0]["outcome"], "absent")
                self.assertIn(f"@{BOT} — you own this decision", self.cli.requests()[0]["body"])

    def test_malformed_inline_value_does_not_block_collaboration_delivery(self):
        self.cli.configure(manager=False, collaboration=f'[collaboration]\nfallback = "{BOT}"\n')
        self.cli.state["issue"]["body"] = "Decision owner: Factory PM (ops)"
        self.cli.manage()
        route = self.cli.events("handoff")[0]["route"]
        self.assertEqual((route["status"], route["owner"], route["source"]),
                         ("selected", BOT, "fallback"))
        self.assertEqual(route["provenance"][0]["outcome"], "absent")
        self.assertIn(f"@{BOT} — you own this decision", self.cli.requests()[0]["body"])

    def test_explicit_bot_login_keeps_direct_routing(self):
        self.cli.state["issue"]["body"] = f"Decision owner: @{BOT}"
        self.cli.manage()
        route = self.cli.events("handoff")[0]["route"]
        self.assertEqual((route["status"], route["owner"], route["source"]),
                         ("selected", BOT, "decision_owner"))
        self.assertIn(f"@{BOT} — you own this decision", self.cli.requests()[0]["body"])

    def test_fallback_only_host_settings_deliver_unknown_roles(self):
        self.host.write_text(f'[defaults.bot_owners]\nfallback = "{BOT}"\n')
        self.cli.state["issue"]["body"] = "**Decision owner**\nUnknown Product Role"
        proc = self.cli.manage()
        self.assertIn("unknown decision owner role 'Unknown Product Role'", proc.stdout + proc.stderr)
        self.assertEqual(self.cli.events("handoff")[0]["target"], f"@{BOT}")
        self.assertIn(f"@{BOT} — you own this decision", self.cli.requests()[0]["body"])

    def test_invalid_host_role_tables_are_configuration_errors(self):
        for roles in ('[]', '["Factory PM"]', '42', '"Factory PM"'):
            with self.subTest(roles=roles):
                self.host.write_text(f'[defaults.bot_owners]\nfallback = "{BOT}"\nroles = {roles}\n')
                with (
                    mock.patch.dict("os.environ", self.cli.env),
                    self.assertRaisesRegex(config.ConfigError, "bot_owners.roles"),
                ):
                    config.load(self.cli.repo, worktree=True)

    def test_invalid_host_role_names_are_configuration_errors(self):
        for role in ("", " Factory PM", "Factory PM ", "Factory_PM", "Factory PM (ops)", "9PM"):
            with self.subTest(role=role):
                self.host.write_text(HOST.replace('"Factory PM"', json.dumps(role)))
                with (
                    mock.patch.dict("os.environ", self.cli.env),
                    self.assertRaisesRegex(config.ConfigError, "bot_owners.roles"),
                ):
                    config.load(self.cli.repo, worktree=True)

    def test_non_string_role_keys_are_configuration_errors(self):
        with self.assertRaisesRegex(config.ConfigError, "bot_owners.roles"):
            config.bot_owner_settings({"fallback": BOT, "roles": {1: BOT}})

    def test_unknown_host_bot_setting_is_rejected(self):
        self.host.write_text(HOST.replace("[defaults.bot_owners]\n",
                                         "[defaults.bot_owners]\nfallbak = true\n"))
        with (
            mock.patch.dict("os.environ", self.cli.env),
            self.assertRaisesRegex(config.ConfigError, "expected only fallback and roles"),
        ):
            config.load(self.cli.repo, worktree=True)
