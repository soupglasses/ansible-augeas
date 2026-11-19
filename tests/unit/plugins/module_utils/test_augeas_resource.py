# Copyright (c) 2026, Sofie Finnes <sofie+git@finnes.dev>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

from pathlib import Path

import pytest
from ansible_collections.soupglasses.augeas.plugins.module_utils import (
    augeas_resource as resource_module,
)
from ansible_collections.soupglasses.augeas.plugins.module_utils.augeas_resource import (
    AugeasResource,
    AugeasResourceError,
    ConfigError,
    TreeNode,
    encode_config,
    flatten_config,
)

SSHD_CONFIG = """\
# Managed by the Augeas collection tests
Port 22
PermitRootLogin yes
AcceptEnv LANG LC_CTYPE
AcceptEnv LC_ALL
Subsystem sftp /usr/lib/openssh/sftp-server

Match User deploy
  X11Forwarding yes
  PasswordAuthentication yes
"""


def test_flatten_compact_mapping():
    assert flatten_config(
        {
            "PermitRootLogin": "no",
            "Subsystem": {"sftp": "/usr/lib/openssh/sftp-server"},
        }
    ) == [
        ("PermitRootLogin", "no"),
        ("Subsystem/sftp", "/usr/lib/openssh/sftp-server"),
    ]


def test_flatten_selector_key():
    assert flatten_config({'Match[Condition/User="deploy"]/Settings/X11Forwarding': "no"}) == [
        ('Match[Condition/User="deploy"]/Settings/X11Forwarding', "no")
    ]


def test_flatten_selector_allows_parent_tokens_inside_a_quoted_value():
    assert flatten_config({'key[.="a/../b"]': "value"}) == [('key[.="a/../b"]', "value")]


def test_flatten_ordered_duplicate_and_numeric_children():
    assert flatten_config(
        {
            "$children": [
                {"Port": "22"},
                {"AcceptEnv": ["LANG", "LC_CTYPE"]},
                {"AcceptEnv": ["LC_IDENTIFICATION", "LC_ALL"]},
            ]
        }
    ) == [
        ("Port[1]", "22"),
        ("AcceptEnv[1]/*[1]", "LANG"),
        ("AcceptEnv[1]/*[2]", "LC_CTYPE"),
        ("AcceptEnv[2]/*[1]", "LC_IDENTIFICATION"),
        ("AcceptEnv[2]/*[2]", "LC_ALL"),
    ]


def test_flatten_node_with_value_and_children():
    assert flatten_config({"Node": {"$value": "parent", "Child": "child"}}) == [
        ("Node", "parent"),
        ("Node/Child", "child"),
    ]


def test_plain_sequences_are_always_positional():
    assert flatten_config([{"canonical": "first"}, {"canonical": "second"}]) == [
        ("*[1]/canonical", "first"),
        ("*[2]/canonical", "second"),
    ]


def test_flatten_escaped_reserved_label():
    assert flatten_config({"$$value": "literal"}) == [("$value", "literal")]


def test_reject_yaml_boolean_values():
    with pytest.raises(ConfigError, match="YAML boolean"):
        flatten_config({"PermitRootLogin": False})


def test_reject_ambiguous_children_syntax():
    with pytest.raises(ConfigError, match="cannot mix"):
        flatten_config({"$children": [{"Port": "22"}], "PermitRootLogin": "no"})


def test_codec_uses_compact_mapping_for_unique_children():
    tree = TreeNode(
        label="",
        value=None,
        children=(
            TreeNode("Port", "22", ()),
            TreeNode("Subsystem", None, (TreeNode("sftp", "/usr/lib/sftp-server", ()),)),
        ),
    )
    assert encode_config(tree) == {
        "Port": "22",
        "Subsystem": {"sftp": "/usr/lib/sftp-server"},
    }


def test_codec_retains_duplicate_order_and_numeric_shorthand():
    tree = TreeNode(
        label="",
        value=None,
        children=(
            TreeNode("Port", "22", ()),
            TreeNode("AcceptEnv", None, (TreeNode("1", "LANG", ()),)),
            TreeNode("AcceptEnv", None, (TreeNode("1", "LC_ALL", ()),)),
        ),
    )
    encoded = encode_config(tree)
    assert encoded == {
        "$children": [
            {"Port": "22"},
            {"AcceptEnv": ["LANG"]},
            {"AcceptEnv": ["LC_ALL"]},
        ]
    }
    assert flatten_config(encoded) == [
        ("Port[1]", "22"),
        ("AcceptEnv[1]/*[1]", "LANG"),
        ("AcceptEnv[2]/*[1]", "LC_ALL"),
    ]


def test_codec_escapes_literal_control_labels():
    tree = TreeNode(
        label="",
        value=None,
        children=(TreeNode("$value", "literal", ()),),
    )
    assert encode_config(tree) == {"$$value": "literal"}
    assert flatten_config(encode_config(tree)) == [("$value", "literal")]


def test_codec_retains_semantic_xml_hash_labels():
    tree = TreeNode(
        label="",
        value=None,
        children=(
            TreeNode(
                "config",
                None,
                (
                    TreeNode(
                        "database",
                        None,
                        (
                            TreeNode(
                                "#attribute",
                                None,
                                (TreeNode("host", "localhost", ()),),
                            ),
                            TreeNode("#text", "app", ()),
                        ),
                    ),
                ),
            ),
        ),
    )
    encoded = encode_config(tree)

    assert encoded == {
        "config": {
            "database": {
                "#attribute": {"host": "localhost"},
                "#text": "app",
            }
        }
    }
    assert flatten_config(encoded) == [
        ("config/database/#attribute/host", "localhost"),
        ("config/database/#text", "app"),
    ]


def test_codec_preserves_repeated_numeric_children_under_a_node_with_a_value():
    tree = TreeNode("", "parent", (TreeNode("1", "first", ()), TreeNode("1", "second", ())))

    encoded = encode_config(tree)

    assert encoded == {"$value": "parent", "$children": [{"1": "first"}, {"1": "second"}]}
    assert flatten_config(encoded) == [("", "parent"), ("1[1]", "first"), ("1[2]", "second")]


def test_codec_treats_sparse_numeric_labels_as_positional_children():
    tree = TreeNode(
        label="",
        value=None,
        children=(TreeNode("1", "first", ()), TreeNode("3", "third", ())),
    )

    encoded = encode_config(tree)

    assert encoded == ["first", "third"]
    assert flatten_config(encoded) == [("*[1]", "first"), ("*[2]", "third")]


def test_codec_represents_an_empty_root_as_an_empty_mapping():
    assert encode_config(TreeNode(label="", value=None, children=())) == {}


def _resource(tmp_path, check_mode=False, path="", validator=None, commit=None):
    target = tmp_path / "etc" / "ssh" / "sshd_config"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(SSHD_CONFIG)
    resource = AugeasResource(
        file="/etc/ssh/sshd_config",
        lens="Sshd",
        path=path,
        check_mode=check_mode,
        root=str(tmp_path),
        validator=validator,
        commit=commit,
    )
    return resource, target


def test_real_lens_gathers_duplicate_nodes(tmp_path):
    resource, _target = _resource(tmp_path)
    try:
        result = resource.run("gathered")
    finally:
        resource.close()

    children = result["gathered"]["$children"]
    assert {"PermitRootLogin": "yes"} in children
    assert {"AcceptEnv": ["LANG", "LC_CTYPE"]} in children
    assert {"AcceptEnv": ["LC_ALL"]} in children
    assert all(not next(iter(child)).startswith("#") for child in children)


def test_real_lens_merge_is_idempotent(tmp_path):
    resource, target = _resource(tmp_path)
    try:
        first = resource.run("merged", {"PermitRootLogin": "no"})
    finally:
        resource.close()

    assert first["changed"] is True
    assert "PermitRootLogin no" in target.read_text()

    resource, _target = _resource(tmp_path)
    try:
        second = resource.run("merged", {"PermitRootLogin": "no"})
    finally:
        resource.close()
    assert second["changed"] is False


@pytest.mark.parametrize(
    "initial",
    [
        'model = "gpt-5.6-luna"\n\n[features]\nenabled = true\n',
        "[features]\nenabled = true\n",
    ],
)
def test_real_lens_merge_inserts_missing_toml_root_entry_before_tables(tmp_path, initial):
    target = tmp_path / "etc" / "app" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(initial, encoding="utf-8")

    resource = AugeasResource(
        file="/etc/app/config.toml",
        lens="Toml",
        root=str(tmp_path),
    )
    try:
        result = resource.run(
            "merged",
            {
                'entry[.="model_reasoning_summary"]': {
                    "$value": "model_reasoning_summary",
                    "string": "detailed",
                }
            },
        )
    finally:
        resource.close()

    assert result["changed"] is True
    contents = target.read_text(encoding="utf-8")
    assert 'model_reasoning_summary = "detailed"' in contents
    assert contents.index("model_reasoning_summary") < contents.index("[features]")


def test_real_lens_merge_accepts_a_selector_key(tmp_path):
    resource, target = _resource(tmp_path)
    try:
        result = resource.run(
            "merged",
            {'Match[Condition/User="deploy"]/Settings/X11Forwarding': "no"},
        )
    finally:
        resource.close()

    assert result["changed"] is True
    assert "  X11Forwarding no" in target.read_text()


def test_real_lens_ordered_merge_selects_first_occurrence(tmp_path):
    resource, target = _resource(tmp_path)
    try:
        result = resource.run(
            "merged",
            {"$children": [{"AcceptEnv": ["LANG", "LC_MESSAGES"]}]},
        )
    finally:
        resource.close()

    assert result["changed"] is True
    accept_env = [line for line in target.read_text().splitlines() if line.startswith("AcceptEnv")]
    assert accept_env == ["AcceptEnv LANG LC_MESSAGES", "AcceptEnv LC_ALL"]


def test_real_lens_null_clears_a_node_value(tmp_path):
    resource, _target = _resource(tmp_path, path='Match[Condition/User="deploy"]')
    try:
        result = resource.run("merged", {"$value": None})
    finally:
        resource.close()

    assert result["changed"] is False
    assert "$value" not in result["after"]


def test_low_level_clear_creates_a_valueless_node(tmp_path):
    resource, _target = _resource(tmp_path)
    try:
        resource.aug.set("/test/node", "value")
        resource._clear_value("/test/node")
        value = resource.aug.get("/test/node")
    finally:
        resource.close()

    assert value is None


def test_real_lens_check_mode_validates_without_writing(tmp_path):
    resource, target = _resource(tmp_path, check_mode=True)
    original = target.read_text()
    try:
        result = resource.run("merged", {"PermitRootLogin": "no"})
    finally:
        resource.close()

    assert result["changed"] is True
    assert result["after"]["$children"][1] == {"PermitRootLogin": "no"}
    assert target.read_text() == original


def test_external_validator_sees_candidate_before_commit(tmp_path):
    observed = []

    def validator(candidate):
        observed.append((candidate, Path(candidate).read_text()))

    resource, target = _resource(tmp_path, validator=validator)
    try:
        result = resource.run("merged", {"PermitRootLogin": "no"})
    finally:
        resource.close()

    assert result["changed"] is True
    assert observed[0][0] == str(target) + ".augnew"
    assert "PermitRootLogin no" in observed[0][1]
    assert "PermitRootLogin no" in target.read_text()
    assert not Path(str(target) + ".augnew").exists()


def test_external_validator_runs_and_cleans_up_in_check_mode(tmp_path):
    observed = []

    def validator(candidate):
        observed.append(Path(candidate).read_text())

    resource, target = _resource(tmp_path, check_mode=True, validator=validator)
    original = target.read_text()
    try:
        result = resource.run("merged", {"PermitRootLogin": "no"})
    finally:
        resource.close()

    assert result["changed"] is True
    assert "PermitRootLogin no" in observed[0]
    assert target.read_text() == original
    assert not Path(str(target) + ".augnew").exists()


def test_external_validator_failure_preserves_source(tmp_path):
    def validator(_candidate):
        raise AugeasResourceError("application rejected the candidate")

    resource, target = _resource(tmp_path, validator=validator)
    original = target.read_text()
    try:
        with pytest.raises(AugeasResourceError, match="application rejected"):
            resource.run("merged", {"PermitRootLogin": "no"})
    finally:
        resource.close()

    assert target.read_text() == original
    assert not Path(str(target) + ".augnew").exists()


def test_external_validator_does_not_run_without_a_change(tmp_path):
    def validator(_candidate):
        raise AssertionError("validator should not run")

    resource, _target = _resource(tmp_path, validator=validator)
    try:
        result = resource.run("merged", {"PermitRootLogin": "yes"})
    finally:
        resource.close()

    assert result["changed"] is False


def test_custom_commit_receives_candidate_without_a_validator(tmp_path):
    committed = []

    def commit(candidate, destination):
        committed.append((candidate, destination, Path(candidate).read_text()))
        Path(candidate).replace(destination)

    resource, target = _resource(tmp_path, commit=commit)
    try:
        result = resource.run("merged", {"PermitRootLogin": "no"})
    finally:
        resource.close()

    assert result["changed"] is True
    assert committed[0][0] == str(target) + ".augnew"
    assert committed[0][1] == str(target)
    assert "PermitRootLogin no" in committed[0][2]
    assert "PermitRootLogin no" in target.read_text()
    assert not Path(str(target) + ".augnew").exists()


def test_custom_commit_failure_preserves_source_and_cleans_candidate(tmp_path):
    def commit(_candidate, _destination):
        raise OSError("atomic replacement failed")

    resource, target = _resource(tmp_path, commit=commit)
    original = target.read_text()
    try:
        with pytest.raises(AugeasResourceError, match="could not commit"):
            resource.run("merged", {"PermitRootLogin": "no"})
    finally:
        resource.close()

    assert target.read_text() == original
    assert not Path(str(target) + ".augnew").exists()


def test_real_lens_replaces_selected_match_block(tmp_path):
    resource, target = _resource(tmp_path, path='Match[Condition/User="deploy"]')
    try:
        result = resource.run(
            "replaced",
            {
                "Condition": {"User": "deploy"},
                "Settings": {"X11Forwarding": "no"},
            },
        )
    finally:
        resource.close()

    assert result["changed"] is True
    assert result["after"] == {
        "Condition": {"User": "deploy"},
        "Settings": {"X11Forwarding": "no"},
    }
    contents = target.read_text()
    assert "Match User deploy" in contents
    assert "  X11Forwarding no" in contents
    assert "PasswordAuthentication yes" not in contents


def test_replaced_creates_a_missing_selected_subtree(tmp_path):
    target = tmp_path / "etc" / "ssh" / "sshd_config"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("Port 22\n")
    desired = {
        "Condition": {"User": "backup"},
        "Settings": {"X11Forwarding": "no"},
    }

    resource = AugeasResource(
        file="/etc/ssh/sshd_config",
        lens="Sshd",
        path='Match[Condition/User="backup"]',
        root=str(tmp_path),
    )
    try:
        result = resource.run("replaced", desired)
    finally:
        resource.close()

    assert result["changed"] is True
    assert result["after"] == desired
    assert target.read_text() == ('Port 22\nMatch User "backup"\n  X11Forwarding no\n')


def test_replaced_ignores_order_in_an_ordinary_mapping(tmp_path):
    target = tmp_path / "etc" / "example.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("FIRST=one\nSECOND=two\n")
    resource = AugeasResource(
        file="/etc/example.conf",
        lens="Simplevars",
        root=str(tmp_path),
    )
    try:
        result = resource.run("replaced", {"SECOND": "two", "FIRST": "one"})
    finally:
        resource.close()

    assert result["changed"] is False
    assert target.read_text() == "FIRST=one\nSECOND=two\n"


def test_replaced_honors_ordered_children_with_unique_labels_idempotently(tmp_path):
    target = tmp_path / "etc" / "example.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("FIRST=one\nSECOND=two\n")
    desired = {"$children": [{"FIRST": "one"}, {"SECOND": "two"}]}

    resource = AugeasResource(
        file="/etc/example.conf",
        lens="Simplevars",
        root=str(tmp_path),
    )
    try:
        result = resource.run("replaced", desired)
    finally:
        resource.close()

    assert result["changed"] is False
    assert target.read_text() == "FIRST=one\nSECOND=two\n"


def test_replaced_reorders_unique_labels_declared_with_ordered_children(tmp_path):
    target = tmp_path / "etc" / "example.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("FIRST=one\nSECOND=two\n")
    desired = {"$children": [{"SECOND": "two"}, {"FIRST": "one"}]}

    resource = AugeasResource(
        file="/etc/example.conf",
        lens="Simplevars",
        root=str(tmp_path),
    )
    try:
        result = resource.run("replaced", desired)
    finally:
        resource.close()

    assert result["changed"] is True
    assert target.read_text() == "SECOND=two\nFIRST=one\n"


def test_replaced_ignores_mapping_order_around_nested_ordered_children(tmp_path):
    lens_directory = tmp_path / "lenses"
    lens_directory.mkdir()
    (lens_directory / "nestedconfig.aug").write_text(
        """\
module NestedConfig =
let eol = del /\\n/ "\\n"
let entry = [ key /[A-Z]+/ . del /=/ "=" . store /[^\\n]*/ . eol ]
let section = [ del "[" "[" . key /[a-z]+/ . del "]\\n" "]\\n" . entry* ]
let lns = entry* . section*
"""
    )
    target = tmp_path / "etc" / "nested.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("TOP=value\n[section]\nFIRST=one\nSECOND=two\n")
    desired = {
        "section": {"$children": [{"FIRST": "one"}, {"SECOND": "two"}]},
        "TOP": "value",
    }

    resource = AugeasResource(
        file="/etc/nested.conf",
        lens="NestedConfig",
        load_path=str(lens_directory),
        root=str(tmp_path),
    )
    try:
        result = resource.run("replaced", desired)
    finally:
        resource.close()

    assert result["changed"] is False
    assert target.read_text() == "TOP=value\n[section]\nFIRST=one\nSECOND=two\n"


def test_real_lens_replaces_ordered_root_idempotently(tmp_path):
    desired = {
        "$children": [
            {"Port": "2222"},
            {"PermitRootLogin": "no"},
            {"AcceptEnv": ["LANG", "LC_CTYPE"]},
            {"AcceptEnv": ["LC_ALL"]},
            {"Subsystem": {"sftp": "/usr/lib/openssh/sftp-server"}},
        ]
    }
    resource, target = _resource(tmp_path)
    try:
        first = resource.run("replaced", desired)
    finally:
        resource.close()

    assert first["changed"] is True
    assert first["after"] == desired
    semantic_lines = [
        line for line in target.read_text().splitlines() if line and not line.startswith("#")
    ]
    assert semantic_lines == [
        "Port 2222",
        "PermitRootLogin no",
        "AcceptEnv LANG LC_CTYPE",
        "AcceptEnv LC_ALL",
        "Subsystem sftp /usr/lib/openssh/sftp-server",
    ]

    resource, _target = _resource(tmp_path)
    try:
        second = resource.run("replaced", desired)
    finally:
        resource.close()
    assert second["changed"] is False


def test_real_lens_deletes_all_repeated_matches(tmp_path):
    resource, target = _resource(tmp_path)
    try:
        result = resource.run("deleted", {"AcceptEnv": None})
    finally:
        resource.close()

    assert result["changed"] is True
    assert "AcceptEnv" not in target.read_text()


def test_path_cannot_select_outside_the_managed_file_tree(tmp_path):
    with pytest.raises(AugeasResourceError, match="must not use parent path steps"):
        _resource(tmp_path, path="../../../..")


def test_config_expression_cannot_select_outside_the_managed_file_tree(tmp_path):
    resource, target = _resource(tmp_path)
    original = target.read_text()
    try:
        with pytest.raises(ConfigError, match="must not use parent path steps"):
            resource.run("merged", {"../../../../augeas/save": "newfile"})
    finally:
        resource.close()

    assert target.read_text() == original


def test_config_expression_cannot_select_outside_the_selected_subtree(tmp_path):
    resource, target = _resource(tmp_path, path='Match[Condition/User="deploy"]')
    original = target.read_text()
    try:
        with pytest.raises(ConfigError, match="must not use parent path steps"):
            resource.run("merged", {"../../PermitRootLogin": "no"})
    finally:
        resource.close()

    assert target.read_text() == original


def test_real_lens_rejects_invalid_merged_order_in_check_mode(tmp_path):
    resource, target = _resource(tmp_path, check_mode=True)
    original = target.read_text()
    try:
        with pytest.raises(AugeasResourceError, match="refused to save"):
            resource.run("merged", {"Banner": "/etc/issue"})
    finally:
        resource.close()
    assert target.read_text() == original


def test_hosts_lens_round_trip_uses_positional_records(tmp_path):
    target = tmp_path / "etc" / "hosts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "127.0.0.1 localhost localhost.localdomain\n192.0.2.10 app.example.test app\n"
    )

    resource = AugeasResource(file="/etc/hosts", lens="Hosts", root=str(tmp_path))
    try:
        gathered = resource.run("gathered")["gathered"]
    finally:
        resource.close()

    assert gathered == [
        {
            "ipaddr": "127.0.0.1",
            "canonical": "localhost",
            "alias": "localhost.localdomain",
        },
        {
            "ipaddr": "192.0.2.10",
            "canonical": "app.example.test",
            "alias": "app",
        },
    ]

    resource = AugeasResource(file="/etc/hosts", lens="Hosts", root=str(tmp_path))
    try:
        replaced = resource.run("replaced", gathered)
    finally:
        resource.close()
    assert replaced["changed"] is False


def test_simplevars_empty_string_round_trip(tmp_path):
    target = tmp_path / "etc" / "example.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("EMPTY=\n")

    resource = AugeasResource(
        file="/etc/example.conf",
        lens="Simplevars",
        root=str(tmp_path),
    )
    try:
        gathered = resource.run("gathered")["gathered"]
        merged = resource.run("merged", gathered)
    finally:
        resource.close()

    assert gathered == {"EMPTY": ""}
    assert merged["changed"] is False


def test_properties_valueless_node_round_trip(tmp_path):
    target = tmp_path / "etc" / "example.properties"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("empty=\n")

    resource = AugeasResource(
        file="/etc/example.properties",
        lens="Properties",
        root=str(tmp_path),
    )
    try:
        gathered = resource.run("gathered")["gathered"]
        merged = resource.run("merged", gathered)
    finally:
        resource.close()

    assert gathered == {"empty": None}
    assert merged["changed"] is False


def test_custom_lens_from_load_path(tmp_path):
    lens_directory = tmp_path / "lenses"
    lens_directory.mkdir()
    (lens_directory / "appconfig.aug").write_text(
        """\
module AppConfig =
let eol = del /\\n/ "\\n"
let entry = [ key /[A-Za-z][A-Za-z0-9_]*/
              . del /[ \\t]*=[ \\t]*/ " = "
              . store /[^\\n]*/ . eol ]
let lns = entry*
"""
    )
    target = tmp_path / "etc" / "app.conf"
    target.parent.mkdir(parents=True)
    target.write_text("PORT = 8080\nMODE = production\n")

    resource = AugeasResource(
        file="/etc/app.conf",
        lens="AppConfig",
        load_path=str(lens_directory),
        root=str(tmp_path),
    )
    try:
        result = resource.run("merged", {"PORT": "9090"})
    finally:
        resource.close()

    assert result["changed"] is True
    assert target.read_text() == "PORT = 9090\nMODE = production\n"


def test_initialization_failure_closes_the_augeas_handle(monkeypatch):
    class BrokenAugeas:
        NO_LOAD = 1
        NO_MODL_AUTOLOAD = 2
        SAVE_NEWFILE = 4
        SAVE_NOOP = 8
        instance = None

        def __init__(self, **_kwargs):
            self.closed = False
            BrokenAugeas.instance = self

        def transform(self, _lens, _file):
            raise RuntimeError("broken transform")

        def close(self):
            self.closed = True

    monkeypatch.setattr(resource_module, "Augeas", BrokenAugeas)

    with pytest.raises(AugeasResourceError, match="failed to initialize"):
        AugeasResource(file="/etc/example.conf", lens="Broken")

    assert BrokenAugeas.instance.closed is True


QUAD9 = 'nameserver[. = "9.9.9.9"]'


def _resolvers(tmp_path, addresses):
    target = tmp_path / "resolv.conf"
    target.write_text(
        "# DNS resolvers\n" + "".join(f"nameserver {address}\n" for address in addresses)
    )
    return target


def _nameservers(target):
    return [line.split()[1] for line in target.read_text().splitlines()[1:]]


def _place_quad9(tmp_path, check_mode=False, path=QUAD9, **placement):
    resource = AugeasResource(
        file="/resolv.conf", lens="Resolv", root=str(tmp_path), check_mode=check_mode, path=path
    )
    try:
        return resource.run("merged", {"$value": "9.9.9.9"}, **placement)
    finally:
        resource.close()


@pytest.mark.parametrize(
    ("servers", "anchor", "place_after", "expected"),
    [
        ([], "nameserver", False, ["9.9.9.9"]),
        (["1.1.1.1"], "nameserver", False, ["9.9.9.9", "1.1.1.1"]),
        (["1.1.1.1", "8.8.8.8", "9.9.9.9"], "nameserver", False, ["9.9.9.9", "1.1.1.1", "8.8.8.8"]),
        (
            ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
            "nameserver[1]",
            False,
            ["9.9.9.9", "1.1.1.1", "8.8.8.8"],
        ),
        (
            ["1.1.1.1", "9.9.9.9", "8.8.8.8"],
            'nameserver[. = "8.8.8.8"]',
            False,
            ["1.1.1.1", "9.9.9.9", "8.8.8.8"],
        ),
        (["9.9.9.9", "1.1.1.1", "8.8.8.8"], "nameserver", True, ["1.1.1.1", "8.8.8.8", "9.9.9.9"]),
        (
            ["1.1.1.1", "9.9.9.9", "8.8.8.8"],
            "nameserver[1]",
            True,
            ["1.1.1.1", "9.9.9.9", "8.8.8.8"],
        ),
        (["1.1.1.1", "8.8.8.8"], "domain", True, ["1.1.1.1", "8.8.8.8", "9.9.9.9"]),
    ],
)
def test_resolver_placement_previews_applies_and_repeats(
    tmp_path, servers, anchor, place_after, expected
):
    target = _resolvers(tmp_path, servers)
    placement = {"anchor": anchor, "place_after": place_after}

    preview = _place_quad9(tmp_path, check_mode=True, **placement)
    assert preview["changed"] is (servers != expected)
    assert _nameservers(target) == servers

    applied = _place_quad9(tmp_path, **placement)
    assert applied["changed"] is preview["changed"]
    assert applied["after"] == {"$value": "9.9.9.9"}
    assert _nameservers(target) == expected
    assert target.read_text().startswith("# DNS resolvers\n")

    assert _place_quad9(tmp_path, **placement)["changed"] is False


def test_placement_moves_a_match_block_with_its_formatting(tmp_path):
    target = tmp_path / "etc" / "ssh" / "sshd_config"
    target.parent.mkdir(parents=True)
    target.write_text(
        SSHD_CONFIG + "Match User backup\n  # Keep this setting\n  X11Forwarding yes\n"
    )
    resource, _target = _resource(tmp_path, path='Match[Condition/User="backup"]')
    try:
        result = resource.run("merged", {"Settings": {"X11Forwarding": "no"}}, anchor="Match")
    finally:
        resource.close()

    assert result["changed"] is True
    contents = target.read_text()
    assert contents.index("Match User backup") < contents.index("Match User deploy")
    assert "  # Keep this setting\n  X11Forwarding no" in contents
    assert contents.endswith(
        "Match User deploy\n  X11Forwarding yes\n  PasswordAuthentication yes\n"
    )


def test_placement_creates_a_global_setting_before_match_blocks(tmp_path):
    resource, target = _resource(tmp_path, path="AllowTcpForwarding")
    try:
        result = resource.run("merged", {"$value": "no"}, anchor="Match")
    finally:
        resource.close()

    assert result["changed"] is True
    contents = target.read_text()
    assert contents.index("AllowTcpForwarding no") < contents.index("Match User deploy")


BACKUP_MATCH = "Match User backup\n  X11Forwarding no\n"
DEPLOY_MATCH = "Match User deploy\n  X11Forwarding yes\n  PasswordAuthentication yes\n"


@pytest.mark.parametrize("already_first", [False, True])
def test_replaced_places_a_block_whose_contents_already_match(tmp_path, already_first):
    target = tmp_path / "etc" / "ssh" / "sshd_config"
    target.parent.mkdir(parents=True)
    blocks = BACKUP_MATCH + DEPLOY_MATCH if already_first else DEPLOY_MATCH + BACKUP_MATCH
    target.write_text("Port 22\n" + blocks)
    resource, _target = _resource(tmp_path, path='Match[Condition/User="backup"]')
    try:
        result = resource.run(
            "replaced",
            {"Condition": {"User": "backup"}, "Settings": {"X11Forwarding": "no"}},
            anchor="Match",
        )
    finally:
        resource.close()

    assert result["changed"] is not already_first
    assert result["before"] == result["after"]
    assert target.read_text() == "Port 22\n" + BACKUP_MATCH + DEPLOY_MATCH


def test_placement_rejects_a_path_that_loses_the_moved_node(tmp_path):
    target = _resolvers(tmp_path, ["1.1.1.1", "8.8.8.8", "9.9.9.9"])
    original = target.read_text()
    with pytest.raises(AugeasResourceError, match="no longer selects the moved node"):
        _place_quad9(tmp_path, path="nameserver[3]", anchor="nameserver")
    assert target.read_text() == original


@pytest.mark.parametrize(
    ("path", "state", "config", "anchor", "message"),
    [
        ("Port", "merged", {"$value": "2222"}, "../Port", "relative to the parent"),
        ("Port", "merged", {"$value": "2222"}, "/files", "relative to the parent"),
        ("Port", "merged", {"$value": "2222"}, "", "siblings of path"),
        ("Port", "merged", {"$value": "2222"}, "Match/Settings", "siblings of path"),
        ("Port", "deleted", None, "Match", "state=merged or replaced"),
        ("", "merged", {"Port": "2222"}, "Match", "require path"),
    ],
)
def test_invalid_placement_preserves_file(tmp_path, path, state, config, anchor, message):
    resource, target = _resource(tmp_path, path=path)
    original = target.read_bytes()
    try:
        with pytest.raises(AugeasResourceError, match=message):
            resource.run(state, config, anchor=anchor)
    finally:
        resource.close()
    assert target.read_bytes() == original


@pytest.mark.parametrize("check", [True, False])
def test_placement_rejects_renumbered_host_identity(tmp_path, check):
    target = tmp_path / "hosts"
    initial = "127.0.0.1 localhost\n192.0.2.1 old\n192.0.2.2 keep\n"
    target.write_text(initial)
    resource = AugeasResource("/hosts", "Hosts", path="3", root=str(tmp_path), check_mode=check)
    try:
        with pytest.raises(AugeasResourceError, match="different node after rendering"):
            resource.run("merged", {"alias": "owned"}, anchor="1")
    finally:
        resource.close()
    assert target.read_text() == initial
    assert not (tmp_path / "hosts.augnew").exists()


def test_placement_rejects_moving_anchor(tmp_path):
    target = _resolvers(tmp_path, ["1.1.1.1", "8.8.8.8", "9.9.9.9"])
    original = target.read_bytes()
    with pytest.raises(AugeasResourceError, match="second run"):
        _place_quad9(tmp_path, anchor=QUAD9 + "/preceding-sibling::nameserver[1]")
    assert target.read_bytes() == original


def test_placement_requires_managed_node(tmp_path):
    resource, target = _resource(tmp_path, path="MaxSessions")
    original = target.read_bytes()
    try:
        with pytest.raises(AugeasResourceError, match="select a node"):
            resource.run("merged", {}, anchor="Port")
    finally:
        resource.close()
    assert target.read_bytes() == original


def test_placement_saves_changed_subtree_when_order_already_matches(tmp_path):
    target = tmp_path / "hosts"
    original = "192.0.2.1 web old-alias\n192.0.2.2 db\n"
    target.write_text(original)
    for check_mode, changed in [(True, True), (False, True), (False, False)]:
        resource = AugeasResource(
            "/hosts", "Hosts", path='*[canonical="web"]', root=str(tmp_path), check_mode=check_mode
        )
        try:
            result = resource.run("merged", {"alias": "new-alias"}, anchor='*[canonical="db"]')
        finally:
            resource.close()
        assert result["changed"] is changed
        assert target.read_text() == (
            original if check_mode else original.replace("old-alias", "new-alias")
        )


@pytest.mark.parametrize("permutation", [(0, 1, 2, 3), (3, 2, 1, 0), (2, 0, 3, 1)])
def test_batch_reverses_selected_siblings_in_one_commit_and_converges(tmp_path, permutation):
    target = tmp_path / "resolv.conf"
    addresses = [f"192.0.2.{n}" for n in range(1, 6)]

    def contents(servers):
        lines = [f"nameserver {address}\n" for address in servers]
        lines[2:2] = ["# Local DNS policy\n", "search example.test\n"]
        return "".join(lines)

    initial = contents(addresses)
    expected = contents(addresses[::-1])
    target.write_text(initial)
    order = [f'nameserver[. = "{address}"]' for address in reversed(addresses)]
    declarations = [{"path": order[i], "before": order[i + 1]} for i in permutation]
    commits = []

    def commit(candidate, destination):
        commits.append(Path(candidate).read_text())
        Path(candidate).replace(destination)

    def apply(check_mode=False):
        resource = AugeasResource(
            "/resolv.conf", "Resolv", root=str(tmp_path), check_mode=check_mode, commit=commit
        )
        try:
            return resource.run_resources(declarations)
        finally:
            resource.close()

    assert apply(check_mode=True)["changed"]
    assert target.read_text() == initial
    assert commits == []

    assert apply()["changed"]
    assert target.read_text() == expected
    assert commits == [expected]

    assert not apply()["changed"]
    assert target.read_text() == expected
    assert commits == [expected]


def _sshd_batch(tmp_path, declarations, check_mode=False):
    resource = AugeasResource("/sshd_config", "Sshd", root=str(tmp_path), check_mode=check_mode)
    try:
        return resource.run_resources(declarations)
    finally:
        resource.close()


@pytest.mark.parametrize("reverse", [False, True])
def test_batch_places_created_and_updated_nodes_and_accepts_compatible_duplicates(
    tmp_path, reverse
):
    target = tmp_path / "sshd_config"
    original = "Port 22\n# handwritten\nPermitTTY yes\n"
    target.write_text(original)
    note = '#comment[. = "access policy"]'
    declarations = [
        {"path": note, "before": "Banner", "config": {"$value": "access policy"}},
        {"path": "Banner", "after": "PermitTTY", "config": {"$value": "/etc/issue.net"}},
        {"path": "PermitTTY", "config": {"$value": "no"}},
        {"path": "Port", "before": note},
        {"path": note, "before": "Banner"},
    ]
    if reverse:
        declarations.reverse()
    expected = "Port 22\n# handwritten\n# access policy\nPermitTTY no\nBanner /etc/issue.net\n"
    for check_mode, changed in [(True, True), (False, True), (False, False)]:
        assert _sshd_batch(tmp_path, declarations, check_mode)["changed"] is changed
        assert target.read_text() == (original if check_mode else expected)


@pytest.mark.parametrize(
    "conflict",
    [
        [{"path": "Port", "before": "Banner"}, {"path": "Banner", "before": "Port"}],
        [
            {"path": 'Match[Condition/User="deploy"]', "config": {"Settings": {"PermitTTY": "no"}}},
            {"path": "Match/Settings/PermitTTY", "config": {"$value": "yes"}},
        ],
    ],
    ids=["cycle", "overlapping-content"],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_batch_rejects_incompatible_declarations_without_committing(tmp_path, conflict, reverse):
    target = tmp_path / "sshd_config"
    original = "Port 22\nPermitTTY yes\nBanner /notice\nMatch User deploy\n  PermitTTY no\n"
    target.write_text(original)
    declarations = [{"path": "Port", "config": {"$value": "2222"}}, *conflict]
    if reverse:
        declarations.reverse()
    with pytest.raises(AugeasResourceError):
        _sshd_batch(tmp_path, declarations)
    assert target.read_text() == original
    assert not (tmp_path / "sshd_config.augnew").exists()


def test_batch_moves_ancestor_and_orders_descendants(tmp_path):
    target = tmp_path / "sshd_config"
    backup = "Match User backup\n  X11Forwarding yes\n  PermitTTY no\n"
    deploy = "Match User deploy\n  PermitTTY yes\n"
    target.write_text("Port 22\n" + deploy + backup)
    backup_path = 'Match[Condition/User="backup"]'
    declarations = [
        {"path": backup_path, "before": 'Match[Condition/User="deploy"]'},
        {"path": backup_path + "/Settings/PermitTTY", "before": "X11Forwarding"},
    ]
    for changed in (True, False):
        assert _sshd_batch(tmp_path, declarations)["changed"] is changed
        assert (
            target.read_text()
            == "Port 22\nMatch User backup\n  PermitTTY no\n  X11Forwarding yes\n" + deploy
        )


@pytest.mark.parametrize(
    "deleted",
    ["Port[1]", 'Port[. = "22"]'],
    ids=["shifts-the-subject", "deletes-the-subject"],
)
def test_batch_content_edit_cannot_retarget_an_existing_resource(tmp_path, deleted):
    target = tmp_path / "sshd_config"
    original = "Port 21\nPort 22\nPort 23\n"
    target.write_text(original)
    with pytest.raises(AugeasResourceError, match="identity"):
        _sshd_batch(
            tmp_path,
            [
                {"path": deleted, "state": "deleted"},
                {"path": "Port[2]", "config": {"$value": "2222"}},
            ],
        )
    assert target.read_text() == original


def test_check_mode_leaves_existing_candidate_untouched(tmp_path):
    target = _resolvers(tmp_path, ["1.1.1.1", "8.8.8.8", "9.9.9.9"])
    original = target.read_text()
    stale = tmp_path / "resolv.conf.augnew"
    stale.write_text("stale\n")

    assert _place_quad9(tmp_path, check_mode=True, anchor="nameserver")["changed"]
    resource = AugeasResource("/resolv.conf", "Resolv", root=str(tmp_path), check_mode=True)
    try:
        assert resource.run_resources([{"path": QUAD9, "before": "nameserver"}])["changed"]
    finally:
        resource.close()

    assert target.read_text() == original
    assert stale.read_text() == "stale\n"
