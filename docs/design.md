# Design notes

## Why a tree-shaped resource

Augeas exposes an ordered tree. Every node has a label, an optional string
value, and ordered children; sibling labels may repeat. Those properties are
part of the lens contract and must survive the Ansible representation.

A plain YAML mapping is a compact shorthand when child labels are unique,
and a plain YAML sequence represents numeric children by position. Neither can
represent repeated named siblings without ambiguity. The collection therefore
adds two structural keys:

- `$children` contains an ordered sequence of child nodes and permits repeated
  labels.
- `$value` stores the current node's value when it also has children.

Sequences used as a node body always represent ordered numeric children. A doubled
leading dollar sign escapes literal labels that collide with structural keys;
for example, `$$value` addresses the Augeas label `$value`. The gathered
representation uses the same codec as desired configuration.

YAML block sequences and flow sequences are the same data type. The codec does
not inspect sequence contents to infer a different meaning: every ordinary
sequence is positional, while every ordered named sequence is under
`$children`.

## Augeas lifecycle

The module initializes Augeas with autoloading disabled, registers exactly one
lens/file transform, and loads only that file. This avoids unrelated parse
errors and ensures a save can affect only the file in the task.

Mutations happen through the `python-augeas` binding. Before a write, the
resulting in-memory tree is compared with the original tree. In check mode,
Augeas runs its `noop` save path: the lens still performs its tree-to-text
transformation and reports invalid trees, but the filesystem is not changed.

`python-augeas` accepts `None` in `set` but currently encodes it as an empty
byte string. The collection calls the binding's bundled low-level `aug_set`
function for null values so an Augeas node with no value remains distinct from
a node whose value is the empty string. All other interaction uses the public
binding object.

For a real write, Augeas renders a candidate and Ansible's atomic move helper
commits it. Existing file metadata is preserved during replacement, then any
declared common file attributes are reconciled. Load, save, validation, and
commit errors are reported instead of treating an invalid or incomplete write
as a successful change.

When `validate` is set, the parsed command checks the rendered candidate before
the commit. Check mode validates the same candidate and removes it without
committing; without a validator it uses Augeas's no-op save. Validation is
skipped when the semantic fingerprint did not change.

## Resource-state interpretation

`merged` writes only nodes represented by `config`. Every child under
`$children` is addressed by its one-based occurrence among siblings with the
same label. It does not reorder existing nodes.

Placement uses the selected node as the source and a parent-relative path to
find sibling anchors. `before` and `after` enforce precedence.
When a move is needed, Augeas variables keep track of the source and anchor
while `insert` creates an empty sibling and `mv` moves the source there.
The module checks the rendered file in a fresh Augeas handle before committing
because some lenses regenerate record labels when loading a file.

`replaced` removes non-formatting direct children below `path`, clears the
selected node's value when `$value` is omitted, and recreates the desired
tree. `$children` order is authoritative; compact mapping order is not part of
the public contract. At the file root this means the whole semantic
configuration is replaced; at a selected node only that subtree is replaced.

`deleted` removes every tree node selected by the supplied configuration
paths. With no configuration, it removes the selected `path`; with neither a
configuration nor a relative path, it removes all semantic children of the
file root while retaining root-level comments and empty lines.

`gathered` parses and returns the selected tree without invoking save.

## Boundaries that cannot be generic

Lenses implicitly define valid node shapes, required fields, and legal sibling
ordering. When the existing tree provides a safe local anchor, the resource
uses Augeas's public `insert` operation for a missing predicate-selected node:
it is appended after siblings with the same label or inserted before the first
different semantic sibling. Other missing nodes retain Augeas's normal `set`
behavior. This avoids imposing a global ordering rule without embedding a
lens-specific schema in the resource.

If a lens requires an ordering that cannot be inferred from its existing tree,
the save still fails safely and is also detected in check mode. Missing parent
identity fields likewise remain the caller's responsibility; selectors identify
existing nodes, while a node's value and children describe what to create.

Repeated nodes do not have generic business identities. `$children` addresses
them positionally, while an explicit Augeas predicate such as
`Match[Condition/User="deploy"]` gives the caller semantic identity when the
lens tree contains suitable fields.

The value domain is strings or null. YAML booleans are rejected because an
unquoted `no` becoming `false` is almost never the intended configuration
value. Numbers are converted to their string representation.

## Primary references

- [Ansible Resource Module dispatcher](https://docs.ansible.com/projects/ansible/latest/collections/ansible/netcommon/network_resource_module.html)
- [Augeas tree model](https://augeas.net/docs/tree.html)
- [Augeas public API](https://augeas.net/docs/api.html)
- [Augeas lens model](https://augeas.net/docs/lenses.html)
- [Augeas quick tour](https://augeas.net/tour.html)
- [Ansible resource-module conventions](https://docs.ansible.com/projects/ansible/latest/network/dev_guide/developing_resource_modules_network.html)
- [Ansible collection testing](https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_collections_testing.html)
