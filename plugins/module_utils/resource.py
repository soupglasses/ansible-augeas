# Copyright (c) 2026, Sofie Finnes <sofie+git@finnes.dev>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import os
import posixpath
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

try:
    from _augeas import ffi as augeas_ffi
    from _augeas import lib as augeas_lib
    from augeas import Augeas
except Exception as import_error:  # pragma: no cover - depends on the target host
    Augeas = None
    augeas_ffi = None
    augeas_lib = None
    AUGEAS_IMPORT_ERROR = import_error
else:
    AUGEAS_IMPORT_ERROR = None


HAS_AUGEAS = Augeas is not None

_CONTROL_KEYS = frozenset(("$children", "$value"))
_FORMATTING_LABELS = frozenset(("#comment", "#empty"))
_INDEX_RE = re.compile(r"\[([1-9][0-9]*)\]$")
_NUMERIC_RE = re.compile(r"[1-9][0-9]*$")
_POSITION_RE = re.compile(r"(?:(?<=/)|^)\*\[([1-9][0-9]*)\](?=/|$)")


class AugeasResourceError(Exception):
    pass


class ConfigError(AugeasResourceError):
    pass


@dataclass(frozen=True)
class TreeNode:
    label: str
    value: str | None
    children: tuple[TreeNode, ...]


def _string_value(value, location):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise ConfigError(
            f"{location} is a YAML boolean; quote values such as 'yes', 'no', 'on', and 'off'"
        )
    if isinstance(value, (int, float)):
        return str(value)
    raise ConfigError(f"{location} must be a string, number, null, mapping, or sequence")


def _mapping_key(key, location):
    if isinstance(key, bool):
        raise ConfigError(f"{location} contains a boolean node label")
    if isinstance(key, (str, int)):
        key = str(key)
    else:
        raise ConfigError(f"{location} contains a node label that is not a string or integer")
    if not key:
        raise ConfigError(f"{location} contains an empty node label")
    if key.startswith("/"):
        raise ConfigError(f"{location} must use paths relative to the selected Augeas subtree")
    if _has_parent_step(key):
        raise ConfigError(f"{location} must not use parent path steps")
    if _INDEX_RE.sub("", key.split("/", 1)[0]) in _FORMATTING_LABELS:
        raise ConfigError(f"{location} cannot manage Augeas formatting nodes")
    return _unescape_label(key, location, "key")


def _literal_label(label, location):
    if isinstance(label, bool):
        raise ConfigError(f"{location} has a boolean label")
    if isinstance(label, (str, int)):
        label = str(label)
    else:
        raise ConfigError(f"{location} has a label that is not a string or integer")
    if not label or any(character in label for character in ("/", "*", "[")):
        raise ConfigError(f"{location} has a label that Augeas cannot address: {label!r}")
    if label in _FORMATTING_LABELS:
        raise ConfigError(f"{location} cannot manage Augeas formatting nodes")
    return _unescape_label(label, location, "label")


def _unescape_label(label, location, noun):
    if label.startswith("$$"):
        return label[1:]
    if label.startswith("$"):
        raise ConfigError(
            f"{location} uses reserved {noun} {label!r}; "
            "double the first dollar sign for a literal label"
        )
    return label


def _is_sequence(value):
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _relative_path(parent, child):
    if not parent:
        return child
    if not child:
        return parent
    return f"{parent}/{child}"


def _child_entry(entry, location):
    if not isinstance(entry, Mapping):
        raise ConfigError(f"{location} must be a mapping containing one child node")
    if len(entry) != 1:
        raise ConfigError(f"{location} must contain exactly one child node")
    label, body = next(iter(entry.items()))
    return _literal_label(label, location), body


def _child_entries(children, location):
    return [
        _child_entry(entry, f"{location}.$children[{index}]")
        for index, entry in enumerate(children)
    ]


def _decode_children(children, parent, entries, location):
    if not _is_sequence(children):
        raise ConfigError(f"{location}.$children must be a sequence")

    parsed = _child_entries(children, location)
    occurrences = Counter()

    for index, (label, body) in enumerate(parsed):
        occurrences[label] += 1
        segment = f"{label}[{occurrences[label]}]"
        path = _relative_path(parent, segment)
        _decode_body(body, path, entries, f"{location}.$children[{index}]")


def _decode_mapping(body, parent, entries, location):
    unknown_controls = [
        key
        for key in body
        if isinstance(key, str)
        and key.startswith("$")
        and not key.startswith("$$")
        and key not in _CONTROL_KEYS
    ]
    if unknown_controls:
        raise ConfigError(f"{location} contains unknown control key {unknown_controls[0]!r}")

    ordinary = [key for key in body if key not in _CONTROL_KEYS]
    if "$children" in body and ordinary:
        raise ConfigError(f"{location} cannot mix $children with ordinary child keys")

    if "$value" in body:
        entries.append((parent, _string_value(body["$value"], f"{location}.$value")))
    if "$children" in body:
        _decode_children(body["$children"], parent, entries, location)
    for key in ordinary:
        child = _mapping_key(key, location)
        _decode_body(body[key], _relative_path(parent, child), entries, f"{location}.{child}")

    if parent and "$value" not in body and not ordinary and not body.get("$children"):
        entries.append((parent, None))


def _decode_body(body, parent, entries, location):
    if isinstance(body, Mapping):
        _decode_mapping(body, parent, entries, location)
        return
    if _is_sequence(body):
        if not body:
            if parent:
                entries.append((parent, None))
            return
        for index, child_body in enumerate(body, 1):
            child = _relative_path(parent, f"*[{index}]")
            _decode_body(child_body, child, entries, f"{location}[{index - 1}]")
        return
    if not parent:
        raise ConfigError(f"{location} must be a mapping")
    entries.append((parent, _string_value(body, location)))


def flatten_config(config):
    if not (isinstance(config, Mapping) or _is_sequence(config)):
        raise ConfigError("config must be a mapping or sequence")

    entries = []
    _decode_body(config, "", entries, "config")

    paths = [path for path, _value in entries]
    normalized = []
    seen = set()
    for path, value in entries:
        if value is None and path and any(other.startswith(path + "/") for other in paths):
            continue
        if path in seen:
            raise ConfigError(f"config describes Augeas path {path!r} more than once")
        seen.add(path)
        normalized.append((path, value))
    return normalized


def _is_numeric_sequence(children):
    return all(_NUMERIC_RE.fullmatch(child.label) for child in children)


def _path_segments(expression):
    quote = None
    bracket_depth = 0
    start = 0
    segments = []

    for index, character in enumerate(expression):
        if quote is not None:
            if character == quote:
                quote = None
        elif character in ("'", '"'):
            quote = character
        elif character == "[":
            bracket_depth += 1
        elif character == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif character == "/" and bracket_depth == 0:
            segments.append(expression[start:index])
            start = index + 1
    segments.append(expression[start:])
    return segments


def _has_parent_step(expression):
    return ".." in _path_segments(expression)


def _uses_ordered_children(body):
    if isinstance(body, Mapping):
        return "$children" in body or any(_uses_ordered_children(value) for value in body.values())
    if _is_sequence(body):
        return any(_uses_ordered_children(value) for value in body)
    return False


def _uses_path_expressions(body):
    if isinstance(body, Mapping):
        if "$children" in body:
            return any(
                _uses_path_expressions(child_body)
                for _label, child_body in _child_entries(body["$children"], "config")
            )
        for key, child_body in body.items():
            if key == "$value":
                continue
            label = _mapping_key(key, "config")
            if any(character in label for character in ("/", "[", "*")):
                return True
            if _uses_path_expressions(child_body):
                return True
    elif _is_sequence(body):
        return any(_uses_path_expressions(value) for value in body)
    return False


def _node_parts(body, location):
    if isinstance(body, Mapping):
        value = _string_value(body.get("$value"), f"{location}.$value")
        if "$children" in body:
            return value, _child_entries(body["$children"], location), True
        children = [
            (_mapping_key(key, location), child_body)
            for key, child_body in body.items()
            if key != "$value"
        ]
        return value, children, False
    if _is_sequence(body):
        children = [(str(index), child_body) for index, child_body in enumerate(body, 1)]
        return None, children, True
    return _string_value(body, location), [], False


def _semantic_bodies_match(current, desired, location="config"):
    current_value, current_children, _current_ordered = _node_parts(current, location)
    desired_value, desired_children, desired_ordered = _node_parts(desired, location)
    if current_value != desired_value or len(current_children) != len(desired_children):
        return False

    if desired_ordered:
        pairs = zip(current_children, desired_children)
    else:
        current_by_label = dict(current_children)
        desired_by_label = dict(desired_children)
        if len(current_by_label) != len(current_children):
            return False
        if current_by_label.keys() != desired_by_label.keys():
            return False
        pairs = (
            ((label, current_by_label[label]), (label, desired_by_label[label]))
            for label in desired_by_label
        )

    for (current_label, current_body), (desired_label, desired_body) in pairs:
        if current_label != desired_label:
            return False
        if not _semantic_bodies_match(
            current_body,
            desired_body,
            f"{location}.{desired_label}",
        ):
            return False
    return True


def _configurations_match(current_config, desired_config):
    if not _uses_path_expressions(desired_config):
        return _semantic_bodies_match(current_config, desired_config)

    current = flatten_config(current_config)
    desired = flatten_config(desired_config)
    if _uses_ordered_children(desired_config):
        return current == desired
    return dict(current) == dict(desired)


def _encoded_child(node):
    body = _encode_node(node)
    label = "$" + node.label if node.label.startswith("$") else node.label
    return {label: body}


def _encode_node(node, root=False):
    if not node.children:
        if root:
            return {} if node.value is None else {"$value": node.value}
        return node.value

    numeric = _is_numeric_sequence(node.children)
    if numeric and node.value is None:
        return [_encode_node(child) for child in node.children]

    encoded = {} if node.value is None else {"$value": node.value}
    labels = [child.label for child in node.children]
    if not numeric and len(labels) != len(set(labels)):
        encoded["$children"] = [_encoded_child(child) for child in node.children]
    else:
        for child in node.children:
            encoded.update(_encoded_child(child))
    return encoded


def encode_config(node):
    if node is None:
        return {}
    return _encode_node(node, root=True)


def _normalized_value(value):
    return "" if value == b"" else value


def _canonical_label(path):
    segment = path.rsplit("/", 1)[-1]
    return _INDEX_RE.sub("", segment)


def _is_formatting(path):
    return _canonical_label(path) in _FORMATTING_LABELS


def _join_path(base, relative):
    if not relative:
        return base
    return f"{base.rstrip('/')}/{relative}"


def _creation_path(path):
    return _POSITION_RE.sub(lambda match: match.group(1), path)


def _delete_sort_key(path):
    indexes = tuple(
        int(match.group(1)) if match else 0
        for match in (_INDEX_RE.search(part) for part in path.split("/"))
    )
    return path.count("/"), indexes


class AugeasResource:
    def __init__(
        self,
        file,
        lens,
        path="",
        load_path=None,
        check_mode=False,
        root=None,
        validator=None,
        commit=None,
    ):
        if not HAS_AUGEAS:
            raise AugeasResourceError(f"python-augeas could not be imported: {AUGEAS_IMPORT_ERROR}")
        if not isinstance(file, str) or not os.path.isabs(file):
            raise AugeasResourceError("file must be an absolute path")
        if any(character in file for character in ("*", "?", "[", "\x00")):
            raise AugeasResourceError("file must identify one literal file, not a glob")
        if not isinstance(lens, str) or not lens:
            raise AugeasResourceError("lens must be a non-empty string")
        if not isinstance(path, str) or path.startswith("/"):
            raise AugeasResourceError("path must be relative to the Augeas file tree")
        if _has_parent_step(path):
            raise AugeasResourceError("path must not use parent path steps")

        self.file = posixpath.normpath(file)
        self.lens = lens
        self.path = path.strip("/")
        self.tree_path = f"/files{self.file}"
        self.base_expression = _join_path(self.tree_path, self.path)
        self.check_mode = check_mode
        self.validator = validator
        self.commit = commit or os.replace
        self.candidate_save = validator is not None or (commit is not None and not check_mode)
        self.source_path = os.path.join(root or os.sep, self.file.lstrip("/"))

        flags = Augeas.NO_LOAD | Augeas.NO_MODL_AUTOLOAD
        if self.candidate_save:
            flags |= Augeas.SAVE_NEWFILE
        elif check_mode:
            flags |= Augeas.SAVE_NOOP
        self.aug = None
        try:
            self.aug = Augeas(root=root, loadpath=load_path, flags=flags)
            self.aug.transform(self.lens, self.file)
            self.aug.load()
            self._raise_file_errors("load")
        except AugeasResourceError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise AugeasResourceError(f"failed to initialize Augeas: {error}") from error

    def close(self):
        if self.aug is not None:
            self.aug.close()
            self.aug = None

    def _file_errors(self):
        metadata = f"/augeas/files{self.file}/error"
        errors = []
        for error_path in self.aug.match(metadata):
            value = self.aug.get(error_path)
            details = {}
            for child in self.aug.match(error_path + "/*"):
                child_value = _normalized_value(self.aug.get(child))
                if child_value is not None:
                    details[_canonical_label(child)] = child_value
            message = details.pop("message", None)
            if message:
                message = message.split("\n\n  with pattern", 1)[0].strip()
            context = ", ".join(f"{label}={detail}" for label, detail in details.items())
            errors.append(": ".join(part for part in (value, context, message) if part))
        return errors

    def _raise_file_errors(self, operation):
        errors = self._file_errors()
        if errors:
            raise AugeasResourceError(
                f"Augeas {operation} failed for {self.file}: {'; '.join(errors)}"
            )

    def _resolve_base(self):
        try:
            matches = self._confined_matches(self.base_expression, f"path {self.path!r}")
        except AugeasResourceError:
            raise
        except Exception as error:
            raise AugeasResourceError(f"invalid Augeas path {self.path!r}: {error}") from error
        if len(matches) > 1:
            raise AugeasResourceError(
                f"Augeas path {self.path!r} selects {len(matches)} nodes; use an index or predicate"
            )
        return (matches[0], True) if matches else (self.base_expression, False)

    def _confined_matches(self, expression, description, boundary=None):
        matches = self.aug.match(expression)
        prefix = (boundary or self.tree_path).rstrip("/")
        escaped = [
            match for match in matches if match != prefix and not match.startswith(prefix + "/")
        ]
        if escaped:
            scope = (
                "managed file tree"
                if prefix == self.tree_path.rstrip("/")
                else "selected Augeas subtree"
            )
            raise AugeasResourceError(
                f"Augeas {description} selects a node outside the {scope}: {escaped[0]}"
            )
        return matches

    def _read_node(self, path, label=""):
        matches = self.aug.match(path)
        if not matches:
            return None
        if len(matches) > 1:
            raise AugeasResourceError(f"Augeas path {path!r} unexpectedly became ambiguous")
        exact = matches[0]
        children = tuple(
            self._read_node(child, _canonical_label(child))
            for child in self.aug.match(exact + "/*")
            if not _is_formatting(child)
        )
        return TreeNode(
            label=label,
            value=_normalized_value(self.aug.get(exact)),
            children=children,
        )

    def _configuration(self, base):
        return encode_config(self._read_node(base))

    def _fingerprint(self):
        def visit(path):
            return (
                _canonical_label(path),
                _normalized_value(self.aug.get(path)),
                tuple(visit(child) for child in self.aug.match(path + "/*")),
            )

        matches = self.aug.match(self.tree_path)
        return tuple(visit(path) for path in matches)

    def _set_entries(self, base, entries, base_exists):
        boundary = base if base_exists else None
        for relative, value in entries:
            full_path = _join_path(base, relative)
            description = f"config path {relative!r}"
            matches = self._confined_matches(full_path, description, boundary=boundary)
            if len(matches) > 1:
                raise AugeasResourceError(
                    f"{description} selects {len(matches)} nodes; "
                    "use $children, an index, or a predicate"
                )
            try:
                target = (
                    matches[0]
                    if matches
                    else self._creation_target(full_path, description, boundary=boundary)
                )
                if value is None:
                    self._clear_value(target)
                else:
                    self.aug.set(target, value)
                base_matches = self._confined_matches(self.base_expression, f"path {self.path!r}")
                if len(base_matches) != 1:
                    raise AugeasResourceError(f"{description} did not create one selected subtree")
                boundary = base = base_matches[0]
                if not self._confined_matches(full_path, description, boundary=base):
                    self._confined_matches(target, description, boundary=base)
            except AugeasResourceError:
                raise
            except Exception as error:
                raise AugeasResourceError(
                    f"could not set config path {relative!r}: {error}"
                ) from error

    def _creation_target(self, expression, description, boundary=None):
        """Insert a missing sibling when its existing tree reveals its position."""
        creation_path = _creation_path(expression)
        *parent_segments, segment = _path_segments(creation_path)
        parent_expression = "/".join(parent_segments)
        label = segment.split("[", 1)[0]
        parents = self._confined_matches(
            parent_expression,
            f"{description} parent",
            boundary=boundary,
        )
        if len(parents) != 1 or not label or "[" not in segment or _INDEX_RE.search(segment):
            return creation_path

        parent = parents[0]
        siblings = self.aug.match(parent + "/*")
        same_label = [sibling for sibling in siblings if _canonical_label(sibling) == label]
        if same_label:
            anchor = same_label[-1]
            before = False
        else:
            semantic = [sibling for sibling in siblings if not _is_formatting(sibling)]
            if not semantic:
                return creation_path
            anchor = semantic[0]
            before = True

        self.aug.insert(anchor, label, before)
        candidates = [
            sibling
            for sibling in self.aug.match(parent + "/*")
            if _canonical_label(sibling) == label
        ]
        if not candidates:
            raise AugeasResourceError(f"Augeas did not create {description}")
        return candidates[-1] if same_label else candidates[0]

    def _clear_value(self, path):
        handle = getattr(self.aug, "_Augeas__handle", None)
        if handle is None:
            raise AugeasResourceError("python-augeas did not expose its Augeas handle")
        # python-augeas encodes None as b"", which loses Augeas's valueless-node state.
        result = augeas_lib.aug_set(handle, path.encode("utf-8"), augeas_ffi.NULL)
        if result != 0:
            message = "Augeas rejected the operation"
            message_pointer = augeas_lib.aug_error_message(handle)
            if message_pointer != augeas_ffi.NULL:
                message = augeas_ffi.string(message_pointer).decode("utf-8", "replace")
            raise AugeasResourceError(message)

    def _remove_paths(self, paths):
        for path in sorted(set(paths), key=_delete_sort_key, reverse=True):
            try:
                self.aug.remove(path)
            except Exception as error:
                raise AugeasResourceError(
                    f"could not remove Augeas path {path!r}: {error}"
                ) from error

    def _clear_children(self, base):
        self._remove_paths(
            child for child in self.aug.match(base + "/*") if not _is_formatting(child)
        )

    def _save(self):
        candidate = self.source_path + ".augnew" if self.candidate_save else None
        if candidate is not None and os.path.lexists(candidate):
            raise AugeasResourceError(
                f"refusing to overwrite existing Augeas candidate {candidate}"
            )
        try:
            self.aug.save()
        except Exception as error:
            details = self._file_errors()
            reason = "; ".join(details) if details else str(error)
            raise AugeasResourceError(f"Augeas refused to save {self.file}: {reason}") from error
        self._raise_file_errors("save")

        if candidate is None:
            return
        try:
            if self.validator is not None:
                try:
                    self.validator(candidate)
                except AugeasResourceError:
                    raise
                except Exception as error:
                    raise AugeasResourceError(
                        f"validation failed for {self.file}: {error}"
                    ) from error
            if not self.check_mode:
                try:
                    self.commit(candidate, self.source_path)
                except Exception as error:
                    raise AugeasResourceError(f"could not commit {self.file}: {error}") from error
        finally:
            if os.path.lexists(candidate):
                os.unlink(candidate)

    def run(self, state, config=None):
        base, base_exists = self._resolve_base()
        before = self._configuration(base) if base_exists else {}
        if state == "gathered":
            return {"changed": False, "gathered": before}

        before_fingerprint = self._fingerprint()
        base_removed = False
        entries = flatten_config(config) if config is not None else []
        if state == "merged":
            self._set_entries(base, entries, base_exists)
        elif state == "replaced":
            if not _configurations_match(before, config):
                if base_exists:
                    self._clear_children(base)
                    if all(relative for relative, _value in entries):
                        self._clear_value(base)
                self._set_entries(base, entries, base_exists)
        elif state == "deleted":
            if config is not None:
                matches = []
                for relative, _value in entries:
                    matches.extend(
                        self._confined_matches(
                            _join_path(base, relative),
                            f"config path {relative!r}",
                            boundary=base if base_exists else None,
                        )
                    )
                base_removed = base in matches
                self._remove_paths(matches)
            elif base_exists:
                if self.path:
                    self._remove_paths((base,))
                    base_removed = True
                else:
                    self._clear_children(base)
        else:
            raise AugeasResourceError(f"unsupported state {state!r}")

        after = {} if base_removed else self._configuration(base)
        changed = before_fingerprint != self._fingerprint()
        if changed:
            self._save()
        return {"changed": changed, "before": before, "after": after}
