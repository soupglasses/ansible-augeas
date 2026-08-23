# State and tree reference

This page defines the collection-specific YAML and state behavior. Use the
upstream [Augeas path-expression guide](https://github.com/hercules-team/augeas/wiki/Path-expressions)
for selector syntax and the [stock lens list](https://augeas.net/stock_lenses.html)
for the tree exposed by each standard lens.

## YAML representation

An Augeas node has a label, an optional value, and ordered children. Child
labels may repeat.

An ordinary mapping is the short form for uniquely labelled children:

```yaml
config:
  PermitRootLogin: "no"
  Subsystem:
    sftp: /usr/lib/openssh/sftp-server
```

An ordinary sequence represents numeric children by position. Block and flow
syntax have the same meaning:

```yaml
AcceptEnv: [LANG, LC_CTYPE]

AcceptEnv:
  - LANG
  - LC_CTYPE
```

Use `$children` for ordered named children or duplicate labels:

```yaml
config:
  $children:
    - Port: "22"
    - AcceptEnv: [LANG, LC_CTYPE]
    - AcceptEnv: [LC_ALL]
```

Each `$children` item must contain one child. `$children` cannot be mixed with
ordinary child keys at the same level.

Use `$value` when a node has both its own value and children:

```yaml
config:
  SomeNode:
    $value: parent-value
    Child: child-value
```

For an Augeas label that begins with `$`, double the first dollar sign. For
example, `$$value` addresses a literal label named `$value`. A backslash was
not chosen because it is not an escape in YAML plain or single-quoted keys.

Strings and `null` are preserved as different node values. `null` is a node
without a value; `""` is a node whose value is an empty string. Integers and
floats are converted to strings. Booleans are rejected so an unquoted YAML
value such as `no` cannot become the string `false` by accident.

`gathered` uses the same representation accepted by `config`.

## State behavior

### `merged`

`merged` sets or creates only the paths described by `config`. Unspecified
nodes remain in place, and existing nodes are not reordered.

Children under `$children` are addressed by their one-based occurrence among
siblings with the same label. With this file:

```text
AcceptEnv LANG
AcceptEnv LC_ALL
AcceptEnv FOO
```

the following configuration updates the first two `AcceptEnv` nodes and leaves
the third untouched:

```yaml
config:
  $children:
    - AcceptEnv: [LANG, LC_CTYPE]
    - AcceptEnv: [LC_ALL, LC_MESSAGES]
state: merged
```

Do not use occurrence position as the identity of an unordered resource set.
Use an Augeas predicate when a stable value in the tree can identify the node.

A lens can reject creation at a syntactically invalid position. For example,
the `Sshd` lens requires global settings before `Match` blocks. Augeas rejects
a merge that would append a new global setting after a `Match` block. Use
`replaced` with `$children` when the whole order must be declared.

### `replaced`

`replaced` treats `config` as the complete semantic contents below `path`.
Unspecified children in that subtree are removed. The selected node's own value
is cleared unless `$value` supplies it.

`$children` is recreated in its declared order. Mapping order is not part of
the public contract. Without `path`, `replaced` applies to the whole file tree.

Direct formatting children such as comments and empty lines are retained when
the semantic subtree is rebuilt. Formatting nested inside a semantic node is
removed with that node.

### `deleted`

With `config`, `deleted` removes every node selected by the described paths.
Configuration values are ignored, so `null` is the clearest spelling:

```yaml
config:
  AcceptEnv: null
state: deleted
```

An unindexed expression can select several nodes for deletion. With `path` and
no `config`, the selected subtree is removed. With neither `path` nor `config`,
all semantic children at the file root are removed while root-level comments
and empty lines remain.

### `gathered`

`gathered` parses and returns the selected tree as `gathered`. It never saves
the file and always reports `changed: false`.

## Selectors and repeated resources

`path` and mapping keys accept Augeas expressions relative to the managed file
tree:

```yaml
path: 'Match[Condition/User="deploy"]'
```

Parent steps (`..`) are rejected. Configuration paths cannot leave the subtree
selected by `path`.

Mutating states reject a base `path` that selects more than one node. Config
entries used by `merged` and `replaced` must also resolve to at most one node;
use an index, `$children`, or a predicate. `deleted` allows a config expression
to match several nodes because removing all matches is unambiguous.

Predicates naturally select existing nodes. They cannot always provide all the
data a lens needs to create a missing node. An SSH authorized key is a valued
node, so a merged find-or-create operation includes both the native selector
and the value used when the node is absent:

```yaml
config:
  'key[.="AAAAC3NzaCarol"]':
    $value: AAAAC3NzaCarol
    type: ssh-ed25519
state: merged
```

The predicate is lookup information. `$value` is construction data. Augeas
does not copy the comparison literal from the path into a new node. The
collection does not add another identity or matching language around this
behavior.

## XML labels

The stock `Xml` lens represents attributes below `#attribute`, element text as
`#text`, and CDATA as `#CDATA`:

```yaml
config:
  database:
    "#attribute":
      host: db-new.example.test
      port: "6432"
    "#text": reporting
state: merged
```

Multiple attributes are an ordinary mapping. An omitted attribute stays
untouched under `merged`. Repeated XML elements use `$children` like repeated
nodes from any other lens. Only `#comment` and `#empty` are treated as
formatting labels.

## Formatting and lens behavior

Augeas preserves source formatting associated with unchanged nodes where its
lens supports it. If the semantic tree does not change, the module does not
save and the file remains byte-for-byte unchanged.

When a semantic change needs new or reconstructed syntax, the lens renders
that text. Its stable representation can look unconventional. The `Sudoers`
lens, for example, can render a newly inserted flag as
`Defaults env_reset , mail_badpass`. The new spacing appears when the comma is
created; the next identical task is unchanged.

Some lenses parse several source spellings into one tree and choose one when
writing. The `AptConf` lens maps both `APT::Get::Assume-Yes "true";` and nested
brace syntax to the same tree, then uses brace syntax after a semantic change.
That one-time text change comes from the lens.

Lenses also define required children and legal sibling order without exposing
a generic schema for them. The module lets Augeas validate those constraints
instead of adding format-specific rules. Check mode runs the same render
validation without replacing the managed file.

## YAML and Netplan

The stock `Yaml` lens does not implement general YAML. Its
[upstream documentation](https://augeas.net/docs/references/lenses/files/yaml-aug.html)
labels it as a subset, and its `entry` rule accepts a top-level mapping whose
children are scalar `key: value` entries. It has no recursive rule for another
mapping below those children.

A typical Netplan file reaches that boundary immediately:

```yaml
network:
  version: 2
  ethernets:
    enp1s0:
      dhcp4: false
```

Augeas 1.14.1 parses `network` and `version`, then reports `parse_failed` at
the start of `ethernets` on line 3. The module never receives an Augeas tree,
so its YAML codec and reconciliation states are not involved.

This is not a general depth limit in Augeas. Stock lenses such as
[`Xml`](https://github.com/hercules-team/augeas/blob/master/lenses/xml.aug)
and [`Nginx`](https://github.com/hercules-team/augeas/blob/master/lenses/nginx.aug)
use recursive lens rules for nested configuration. General YAML is harder
because indentation carries structure and the format also includes nested
mappings, scalar and mapping sequences, flow syntax, anchors, tags, and block
scalars. The stock lens has not gained broader structure support since its
top-level sequence update in 2017, and its
[multiline indentation bug](https://github.com/hercules-team/augeas/issues/525)
remains open.

The Augeas-native fix is a better lens, not special YAML handling in this
module. A dedicated `Netplan` lens could model Netplan's schema, or the upstream
`Yaml` lens could be expanded if it can preserve its existing tree contract.
Either can be installed on managed nodes and selected with `load_path`; the
resource module needs no format-specific reconciliation code. Until such a
lens is available and tested, this collection does not claim Netplan support.
