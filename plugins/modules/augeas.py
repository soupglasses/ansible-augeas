#!/usr/bin/python

# Copyright (c) 2026, Sofie Finnes <sofie+git@finnes.dev>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)


DOCUMENTATION = r"""
---
module: augeas
short_description: Manage configuration through Augeas resource semantics
version_added: "1.0.0"
description:
  - Parses and writes one configuration file through an Augeas lens.
  - Uses declarative resource states and a YAML representation of the Augeas tree.
  - Calls the Augeas library directly through C(python-augeas) and never executes C(augtool).
author:
  - Sofie Finnes (@soupglasses)
requirements:
  - Augeas with the lens needed for the selected file
  - python-augeas >= 1.2.0
extends_documentation_fragment:
  - files
attributes:
  check_mode:
    description: Augeas validates the resulting tree with a no-op save.
    support: full
  diff_mode:
    description: Returns structured before and after trees.
    support: full
options:
  file:
    description:
      - Absolute path of the single configuration file to manage.
      - Glob expressions are not accepted.
    type: path
    required: true
  lens:
    description:
      - Augeas lens module or fully qualified lens name used to parse O(file).
      - For example, C(Sshd) selects C(Sshd.lns).
    type: str
    required: true
  load_path:
    description:
      - Colon-separated directory list searched for custom Augeas lenses.
      - Standard Augeas lens directories remain available.
    type: path
  resources:
    description:
      - Apply several declarations to one file and validate the combined result before saving.
      - Each item accepts the same resource options as a single declaration.
        Set file options at the top level and resource options inside the list.
    type: list
    elements: dict
    version_added: "1.1.0"
    suboptions:
      path:
        description: Relative Augeas path selecting the resource. See O(path).
        type: str
        default: ""
      state:
        description: Desired state of this resource. See O(state).
        type: str
        choices: [merged, replaced, deleted]
        default: merged
      config:
        description: Desired Augeas tree below this resource's path. See O(config).
        type: raw
      before:
        description: Keep this resource before the matching siblings. See O(before).
        type: str
      after:
        description: Keep this resource after the matching siblings. See O(after).
        type: str
  path:
    description:
      - Augeas path expression relative to the tree root for O(file).
      - Use an index or predicate when a label can select multiple nodes.
      - Parent path steps are not accepted.
    type: str
    default: ""
  before:
    description:
      - Keep the node selected by O(path) before every sibling this Augeas expression selects.
      - The expression is relative to the parent of O(path) and may select several siblings.
        The selected node itself is ignored. An expression that selects nothing imposes no order.
      - The node is moved with its subtree only when it is on the wrong side.
      - Requires O(path) and O(state=merged) or O(state=replaced).
    type: str
    version_added: "1.1.0"
  after:
    description:
      - Keep the node selected by O(path) after every sibling this Augeas expression selects.
      - Otherwise behaves like O(before).
    type: str
    version_added: "1.1.0"
  validate:
    description:
      - Command used to validate Augeas-rendered candidate content before it replaces O(file).
      - The command must contain exactly one C(%s), which is replaced by the candidate file path.
      - The command is executed without a shell, so expansion, pipes, and redirects are unavailable.
      - Validation runs in check mode, but only when the semantic configuration would change.
    type: str
  config:
    description:
      - Desired Augeas tree below O(path). For O(state=merged), omit O(config) to manage only
        the placement of an existing node.
      - A mapping represents uniquely labelled child nodes.
      - A sequence represents numeric children selected by position.
      - C($children) contains an ordered sequence of named child nodes and permits duplicate labels.
      - C($value) gives the node selected by the surrounding mapping its own value.
      - Double a leading dollar sign to address a literal label, for example C($$value).
      - String values should be quoted so YAML does not convert values such as C(no) to booleans.
    type: raw
  state:
    description:
      - V(merged) sets supplied nodes and preserves unspecified nodes.
      - V(replaced) recreates the selected subtree from O(config), clearing an omitted C($value).
      - V(deleted) removes nodes in O(config), or the selected resource when O(config) is omitted.
      - V(gathered) returns the selected tree without changing the file.
    type: str
    choices:
      - merged
      - replaced
      - deleted
      - gathered
    default: merged
notes:
  - Augeas comments and empty lines are treated as formatting rather than configuration.
  - File attributes apply to mutating states and are not valid with V(gathered).
  - Existing source formatting is preserved where the lens can retain it. Text needed for new or
    structurally changed nodes is rendered using the lens's preferred representation.
  - A lens can impose ordering constraints that a generic merged operation cannot infer. Augeas
    rejects invalid trees, including during check mode.
  - O(validate) checks one candidate file. Validators that require a directory or complete
    configuration set need a wrapper command that accepts the candidate path.
"""

EXAMPLES = r"""
- name: Merge simple sshd settings
  soupglasses.augeas.augeas:
    file: /etc/ssh/sshd_config
    lens: Sshd
    owner: root
    group: root
    mode: "0600"
    config:
      PermitRootLogin: "no"
      PasswordAuthentication: "no"
    state: merged

- name: Replace an identified Match block
  soupglasses.augeas.augeas:
    file: /etc/ssh/sshd_config
    lens: Sshd
    path: 'Match[Condition/User="deploy"]'
    config:
      Condition:
        User: deploy
      Settings:
        X11Forwarding: "no"
        PasswordAuthentication: "no"
    state: replaced

- name: Replace repeated entries while retaining their order
  soupglasses.augeas.augeas:
    file: /etc/ssh/sshd_config
    lens: Sshd
    config:
      $children:
        - Port: "22"
        - AcceptEnv: [LANG, LC_CTYPE]
        - AcceptEnv: [LC_ALL]
    state: replaced

- name: Keep Quad9 as the first resolver
  soupglasses.augeas.augeas:
    file: /etc/resolv.conf
    lens: Resolv
    path: 'nameserver[. = "9.9.9.9"]'
    before: nameserver
    config:
      $value: "9.9.9.9"
    state: merged

- name: Gather the Augeas tree
  soupglasses.augeas.augeas:
    file: /etc/ssh/sshd_config
    lens: Sshd
    state: gathered
  register: sshd

- name: Delete all AcceptEnv entries
  soupglasses.augeas.augeas:
    file: /etc/ssh/sshd_config
    lens: Sshd
    config:
      AcceptEnv: null
    state: deleted

- name: Disable nginx gzip after validating the rendered candidate
  soupglasses.augeas.augeas:
    file: /etc/nginx/nginx.conf
    lens: Nginx
    config:
      http:
        gzip: "off"
    state: merged
    validate: nginx -t -c %s
"""

RETURN = r"""
before:
  description: Selected tree before the change, or the full file tree with O(resources).
  returned: state is not gathered
  type: raw
after:
  description: Selected tree after the change, or the full file tree with O(resources).
  returned: state is not gathered
  type: raw
gathered:
  description: Structured Augeas tree read from the file.
  returned: state is gathered
  type: raw
diff:
  description: Augeas-tree and file-attribute changes when diff mode is enabled.
  returned: when diff mode is enabled and the resource changed
  type: list
  elements: dict
"""

import os
import shlex

from ansible.module_utils.basic import (
    FILE_COMMON_ARGUMENTS,
    AnsibleModule,
    missing_required_lib,
)
from ansible_collections.soupglasses.augeas.plugins.module_utils.augeas_resource import (
    AUGEAS_IMPORT_ERROR,
    HAS_AUGEAS,
    PLACEMENT_KEYS,
    AugeasResource,
    AugeasResourceError,
    placement_from_params,
)


def main():
    resource_spec = {
        "path": {"type": "str", "default": ""},
        "config": {"type": "raw"},
        "state": {"type": "str", "choices": ["merged", "replaced", "deleted"], "default": "merged"},
        **{key: {"type": "str"} for key in PLACEMENT_KEYS},
    }
    merged_inputs = ("config", *PLACEMENT_KEYS)
    module = AnsibleModule(
        argument_spec={
            **resource_spec,
            "state": {
                **resource_spec["state"],
                "choices": [*resource_spec["state"]["choices"], "gathered"],
            },
            "file": {"type": "path", "required": True},
            "lens": {"type": "str", "required": True},
            "load_path": {"type": "path"},
            "validate": {"type": "str"},
            "resources": {
                "type": "list",
                "elements": "dict",
                "options": resource_spec,
                "mutually_exclusive": [PLACEMENT_KEYS],
                "required_if": [
                    ("state", "merged", merged_inputs, True),
                    ("state", "replaced", ("config",)),
                ],
            },
        },
        mutually_exclusive=[PLACEMENT_KEYS, *(("resources", key) for key in resource_spec)],
        required_if=[
            ("state", "merged", (*merged_inputs, "resources"), True),
            ("state", "replaced", ("config",)),
        ],
        add_file_common_args=True,
        supports_check_mode=True,
    )

    if not HAS_AUGEAS:
        module.fail_json(
            msg=missing_required_lib(
                "python-augeas and libaugeas",
                url="https://github.com/hercules-team/python-augeas",
                reason=AUGEAS_IMPORT_ERROR,
            )
        )
    if module.params["state"] == "gathered":
        if module.params["config"] is not None:
            module.fail_json(msg="config is not valid with state=gathered")
        if module.params["validate"] is not None:
            module.fail_json(msg="validate is not valid with state=gathered")
        if any(
            module.params[name] is not None
            for name in FILE_COMMON_ARGUMENTS
            if name != "unsafe_writes"
        ):
            module.fail_json(msg="file attributes are not valid with state=gathered")

    validate = module.params["validate"]
    validation_command = None
    if validate is not None:
        if validate.count("%s") != 1:
            module.fail_json(msg="validate must contain exactly one %s placeholder")
        try:
            validation_command = shlex.split(validate)
        except ValueError as error:
            module.fail_json(msg=f"could not parse validate: {error}")
        if not validation_command:
            module.fail_json(msg="validate must contain a command")

    def validator(candidate):
        command = [part.replace("%s", candidate) for part in validation_command]
        return_code, stdout, stderr = module.run_command(command)
        if return_code != 0:
            output = stderr.strip() or stdout.strip() or "no output"
            raise AugeasResourceError(
                f"validation command failed with return code {return_code}: {output}"
            )

    def commit(candidate, destination):
        module.atomic_move(
            candidate,
            destination,
            unsafe_writes=module.params["unsafe_writes"],
        )

    resource = None
    try:
        resource = AugeasResource(
            file=module.params["file"],
            lens=module.params["lens"],
            path=module.params["path"],
            load_path=module.params["load_path"],
            check_mode=module.check_mode,
            validator=validator if validation_command is not None else None,
            commit=commit,
        )
        if module.params["resources"] is not None:
            result = resource.run_resources(module.params["resources"])
        else:
            anchor, after = placement_from_params(module.params)
            result = resource.run(
                module.params["state"],
                module.params["config"],
                anchor=anchor,
                place_after=after,
            )
    except AugeasResourceError as error:
        module.fail_json(msg=str(error))
    finally:
        if resource is not None:
            resource.close()

    position = result.pop("position", None)
    resource_changed = result["changed"]
    attribute_diff = {} if module._diff else None
    attributes_changed = False
    if os.path.lexists(module.params["file"]) or (module.check_mode and resource_changed):
        file_args = module.load_file_common_arguments(
            module.params,
            path=module.params["file"],
        )
        attributes_changed = module.set_fs_attributes_if_different(
            file_args,
            False,
            diff=attribute_diff,
        )
    result["changed"] = resource_changed or attributes_changed

    if module._diff and result["changed"]:
        diffs = []
        if resource_changed:
            diffs.append(
                {
                    "before_header": f"{module.params['file']} (Augeas tree)",
                    "after_header": f"{module.params['file']} (Augeas tree)",
                    "before": result["before"],
                    "after": result["after"],
                }
            )
        if position and position["before"] != position["after"]:
            header = f"{module.params['path']} (sibling position)"
            diffs.append(
                {
                    "before_header": header,
                    "after_header": header,
                    "before": {"position": position["before"]},
                    "after": {"position": position["after"]},
                }
            )
        if attribute_diff:
            attribute_diff["before_header"] = f"{module.params['file']} (file attributes)"
            attribute_diff["after_header"] = f"{module.params['file']} (file attributes)"
            diffs.append(attribute_diff)
        result["diff"] = diffs
    module.exit_json(**result)


if __name__ == "__main__":
    main()
