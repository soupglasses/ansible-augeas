# -*- coding: utf-8 -*-

# Copyright (c) 2026, Sofie Finnes <sofie+git@finnes.dev>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import os
import posixpath
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Optional, Tuple

try:
    from augeas import Augeas
    try:
        from _augeas import ffi as augeas_ffi
        from _augeas import lib as augeas_lib
    except ImportError:
        from augeas.ffi import ffi as augeas_ffi
        from augeas.ffi import lib as augeas_lib
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
    value: Optional[str]
    children: Tuple["TreeNode", ...]


def _string_value(value, location):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise ConfigError(
            "%s is a YAML boolean; quote values such as 'yes', 'no', 'on', and 'off'" % location
        )
    if isinstance(value, (int, float)):
        return str(value)
    raise ConfigError("%s must be a string, number, null, mapping, or sequence" % location)


def _mapping_key(key, location):
    if isinstance(key, bool):
        raise ConfigError("%s contains a boolean node label" % location)
    if isinstance(key, (str, int)):
        key = str(key)
    else:
        raise ConfigError("%s contains a node label that is not a string or integer" % location)
    if not key:
        raise ConfigError("%s contains an empty node label" % location)
    if key.startswith("/"):
        raise ConfigError("%s must use paths relative to the selected Augeas subtree" % location)
    if _has_parent_step(key):
        raise ConfigError("%s must not use parent path steps" % location)
    if _INDEX_RE.sub("", key.split("/", 1)[0]) in _FORMATTING_LABELS:
        raise ConfigError("%s cannot manage Augeas formatting nodes" % location)
    if key.startswith("$$"):
        return key[1:]
    if key.startswith("$"):
        raise ConfigError(
            "%s uses reserved key %r; double the first dollar sign for a literal label"
            % (location, key)
        )
    return key


def _literal_label(label, location):
    if isinstance(label, bool):
        raise ConfigError("%s has a boolean label" % location)
    if isinstance(label, (str, int)):
        label = str(label)
    else:
        raise ConfigError("%s has a label that is not a string or integer" % location)
    if not label or any(character in label for character in ("/", "*", "[")):
        raise ConfigError("%s has a label that Augeas cannot address: %r" % (location, label))
    if label in _FORMATTING_LABELS:
        raise ConfigError("%s cannot manage Augeas formatting nodes" % location)
    if label.startswith("$$"):
        return label[1:]
    if label.startswith("$"):
        raise ConfigError(
            "%s uses reserved label %r; double the first dollar sign for a literal label"
            % (location, label)
        )
    return label


def _relative_path(parent, child):
    if not parent:
        return child
    if not child:
        return parent
    return "%s/%s" % (parent, child)


def _child_entry(entry, location):
    if not isinstance(entry, Mapping):
        raise ConfigError("%s must be a mapping containing one child node" % location)
    if len(entry) != 1:
        raise ConfigError("%s must contain exactly one child node" % location)
    label, body = next(iter(entry.items()))
    return _literal_label(label, location), body


def _decode_children(children, parent, entries, location):
    if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
        raise ConfigError("%s.$children must be a sequence" % location)

    parsed = [
        _child_entry(entry, "%s.$children[%d]" % (location, index))
        for index, entry in enumerate(children)
    ]
    occurrences = Counter()

    for index, (label, body) in enumerate(parsed):
        occurrences[label] += 1
        segment = "%s[%d]" % (label, occurrences[label])
        path = _relative_path(parent, segment)
        _decode_body(body, path, entries, "%s.$children[%d]" % (location, index))


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
        raise ConfigError("%s contains unknown control key %r" % (location, unknown_controls[0]))

    ordinary = [key for key in body if key not in ("$children", "$value")]
    if "$children" in body and ordinary:
        raise ConfigError("%s cannot mix $children with ordinary child keys" % location)

    emitted = False
    if "$value" in body:
        entries.append((parent, _string_value(body["$value"], "%s.$value" % location)))
        emitted = True

    if "$children" in body:
        _decode_children(body["$children"], parent, entries, location)
        if not body["$children"] and parent and not emitted:
            entries.append((parent, None))
        return

    for key in ordinary:
        child = _mapping_key(key, location)
        _decode_body(body[key], _relative_path(parent, child), entries, "%s.%s" % (location, child))

    if not body and parent:
        entries.append((parent, None))


def _decode_body(body, parent, entries, location):
    if isinstance(body, Mapping):
        _decode_mapping(body, parent, entries, location)
        return
    if isinstance(body, Sequence) and not isinstance(body, (str, bytes)):
        if not body:
            if parent:
                entries.append((parent, None))
            return
        for index, child_body in enumerate(body, 1):
            child = _relative_path(parent, "*[%d]" % index)
            _decode_body(child_body, child, entries, "%s[%d]" % (location, index - 1))
        return
    if not parent:
        raise ConfigError("%s must be a mapping" % location)
    entries.append((parent, _string_value(body, location)))


def flatten_config(config):
    if not isinstance(config, (Mapping, Sequence)) or isinstance(config, (str, bytes)):
        raise ConfigError("config must be a mapping or sequence")

    entries = []
    _decode_body(config, "", entries, "config")

    paths = [path for path, _value in entries]
    normalized = []
    for path, value in entries:
        if value is None and path and any(other.startswith(path + "/") for other in paths):
            continue
        normalized.append((path, value))

    seen = set()
    for path, _value in normalized:
        if path in seen:
            raise ConfigError("config describes Augeas path %r more than once" % path)
        seen.add(path)
    return normalized


def _is_numeric_sequence(children):
    return all(_NUMERIC_RE.fullmatch(child.label) for child in children)


def _has_parent_step(expression):
    quote = None
    bracket_depth = 0
    segment = []
    segments = []

    for character in expression:
        if quote is not None:
            segment.append(character)
            if character == quote:
                quote = None
            continue
        if character in ("'", '"'):
            quote = character
            segment.append(character)
        elif character == "[":
            bracket_depth += 1
            segment.append(character)
        elif character == "]":
            bracket_depth = max(0, bracket_depth - 1)
            segment.append(character)
        elif character == "/" and bracket_depth == 0:
            segments.append("".join(segment))
            segment = []
        else:
            segment.append(character)
    segments.append("".join(segment))
    return ".." in segments


def _uses_ordered_children(body):
    if isinstance(body, Mapping):
        return "$children" in body or any(_uses_ordered_children(value) for value in body.values())
    if isinstance(body, Sequence) and not isinstance(body, (str, bytes)):
        return any(_uses_ordered_children(value) for value in body)
    return False


def _uses_path_expressions(body):
    if isinstance(body, Mapping):
        if "$children" in body:
            return any(
                _uses_path_expressions(child_body)
                for index, entry in enumerate(body["$children"])
                for _label, child_body in (
                    _child_entry(entry, "config.$children[%d]" % index),
                )
            )
        for key, child_body in body.items():
            if key == "$value":
                continue
            label = _mapping_key(key, "config")
            if any(character in label for character in ("/", "[", "*")):
                return True
            if _uses_path_expressions(child_body):
                return True
    elif isinstance(body, Sequence) and not isinstance(body, (str, bytes)):
        return any(_uses_path_expressions(value) for value in body)
    return False


def _node_parts(body, location):
    if isinstance(body, Mapping):
        value = _string_value(body.get("$value"), "%s.$value" % location)
        if "$children" in body:
            children = [
                _child_entry(entry, "%s.$children[%d]" % (location, index))
                for index, entry in enumerate(body["$children"])
            ]
            return value, children, True
        children = [
            (_mapping_key(key, location), child_body)
            for key, child_body in body.items()
            if key != "$value"
        ]
        return value, children, False
    if isinstance(body, Sequence) and not isinstance(body, (str, bytes)):
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
            "%s.%s" % (location, desired_label),
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

    labels = [child.label for child in node.children]
    if _is_numeric_sequence(node.children):
        bodies = [_encode_node(child) for child in node.children]
        if node.value is None:
            return bodies
        children = {"$value": node.value}
        for child, body in zip(node.children, bodies):
            children[child.label] = body
        return children
    if len(labels) != len(set(labels)):
        children = {"$children": [_encoded_child(child) for child in node.children]}
        if node.value is not None:
            children = {"$value": node.value, **children}
        return children

    children = {}
    if node.value is not None:
        children["$value"] = node.value
    for child in node.children:
        label = "$" + child.label if child.label.startswith("$") else child.label
        children[label] = _encode_node(child)
    return children


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
    return "%s/%s" % (base.rstrip("/"), relative)


def _creation_path(path):
    return _POSITION_RE.sub(lambda match: match.group(1), path)


def _delete_sort_key(path):
    indexes = tuple(int(match.group(1)) if match else 0 for match in (_INDEX_RE.search(part) for part in path.split("/")))
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
            raise AugeasResourceError("python-augeas could not be imported: %s" % AUGEAS_IMPORT_ERROR)
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
        self.tree_path = "/files%s" % self.file
        self.base_expression = _join_path(self.tree_path, self.path)
        self.check_mode = check_mode
        self.validator = validator
        self.commit = commit or os.replace
        self.candidate_save = validator is not None or (commit is not None and not check_mode)
        self.source_path = os.path.join(root or os.sep, self.file.lstrip("/"))

        flags = Augeas.NO_LOAD | Augeas.NO_MODL_AUTOLOAD
        if validator is not None or (commit is not None and not check_mode):
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
            raise AugeasResourceError("failed to initialize Augeas: %s" % error) from error

    def close(self):
        if self.aug is not None:
            self.aug.close()
            self.aug = None

    def _file_errors(self):
        metadata = "/augeas/files%s/error" % self.file
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
            context = ", ".join("%s=%s" % item for item in details.items())
            errors.append(": ".join(part for part in (value, context, message) if part))
        return errors

    def _raise_file_errors(self, operation):
        errors = self._file_errors()
        if errors:
            raise AugeasResourceError("Augeas %s failed for %s: %s" % (operation, self.file, "; ".join(errors)))

    def _resolve_base(self):
        try:
            matches = self._confined_matches(self.base_expression, "path %r" % self.path)
        except Exception as error:
            if isinstance(error, AugeasResourceError):
                raise
            raise AugeasResourceError("invalid Augeas path %r: %s" % (self.path, error)) from error
        if len(matches) > 1:
            raise AugeasResourceError(
                "Augeas path %r selects %d nodes; use an index or predicate" % (self.path, len(matches))
            )
        return (matches[0], True) if matches else (self.base_expression, False)

    def _confined_matches(self, expression, description, boundary=None):
        matches = self.aug.match(expression)
        prefix = (boundary or self.tree_path).rstrip("/")
        escaped = [
            match
            for match in matches
            if match != prefix and not match.startswith(prefix + "/")
        ]
        if escaped:
            scope = (
                "managed file tree"
                if prefix == self.tree_path.rstrip("/")
                else "selected Augeas subtree"
            )
            raise AugeasResourceError(
                "Augeas %s selects a node outside the %s: %s"
                % (description, scope, escaped[0])
            )
        return matches

    def _read_node(self, path, label=""):
        matches = self.aug.match(path)
        if not matches:
            return None
        if len(matches) > 1:
            raise AugeasResourceError("Augeas path %r unexpectedly became ambiguous" % path)
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
        for relative, value in entries:
            full_path = _join_path(base, relative)
            boundary = base if base_exists else None
            matches = self._confined_matches(
                full_path,
                "config path %r" % relative,
                boundary=boundary,
            )
            if len(matches) > 1:
                raise AugeasResourceError(
                    "config path %r selects %d nodes; use $children, an index, or a predicate"
                    % (relative, len(matches))
                )
            try:
                target = matches[0] if matches else _creation_path(full_path)
                if value is None:
                    self._clear_value(target)
                else:
                    self.aug.set(target, value)
                base_matches = self._confined_matches(
                    self.base_expression,
                    "path %r" % self.path,
                )
                if len(base_matches) != 1:
                    raise AugeasResourceError(
                        "config path %r did not create one selected subtree" % relative
                    )
                base = base_matches[0]
                base_exists = True
                created = self._confined_matches(
                    full_path,
                    "config path %r" % relative,
                    boundary=base,
                )
                if not created:
                    self._confined_matches(
                        target,
                        "config path %r" % relative,
                        boundary=base,
                    )
            except Exception as error:
                if isinstance(error, AugeasResourceError):
                    raise
                raise AugeasResourceError("could not set config path %r: %s" % (relative, error)) from error

    def _clear_value(self, path):
        handle = getattr(self.aug, "_Augeas__handle", None)
        if handle is None:
            raise AugeasResourceError("python-augeas did not expose its Augeas handle")
        # python-augeas encodes None as b"", which loses Augeas's valueless-node state.
        result = augeas_lib.aug_set(handle, path.encode("utf-8"), augeas_ffi.NULL)
        if result != 0:
            message = "Augeas rejected the operation"
            error_message = getattr(augeas_lib, "aug_error_message", None)
            if error_message is not None:
                message_pointer = error_message(handle)
                if message_pointer != augeas_ffi.NULL:
                    message = augeas_ffi.string(message_pointer).decode("utf-8", "replace")
            raise AugeasResourceError(message)

    def _remove_paths(self, paths):
        for path in sorted(set(paths), key=_delete_sort_key, reverse=True):
            try:
                self.aug.remove(path)
            except Exception as error:
                raise AugeasResourceError("could not remove Augeas path %r: %s" % (path, error)) from error

    def _clear_children(self, base):
        self._remove_paths(child for child in self.aug.match(base + "/*") if not _is_formatting(child))

    def _save(self):
        candidate = self.source_path + ".augnew" if self.candidate_save else None
        if candidate is not None and os.path.lexists(candidate):
            raise AugeasResourceError(
                "refusing to overwrite existing Augeas candidate %s" % candidate
            )
        try:
            self.aug.save()
        except Exception as error:
            details = self._file_errors()
            reason = "; ".join(details) if details else str(error)
            raise AugeasResourceError("Augeas refused to save %s: %s" % (self.file, reason)) from error
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
                        "validation failed for %s: %s" % (self.file, error)
                    ) from error
            if not self.check_mode:
                try:
                    self.commit(candidate, self.source_path)
                except Exception as error:
                    raise AugeasResourceError(
                        "could not commit %s: %s" % (self.file, error)
                    ) from error
        finally:
            if os.path.lexists(candidate):
                os.unlink(candidate)

    def run(self, state, config=None):
        base, base_exists = self._resolve_base()
        before = self._configuration(base) if base_exists else {}
        before_fingerprint = self._fingerprint()
        base_removed = False

        if state == "gathered":
            return {"changed": False, "gathered": before}

        entries = flatten_config(config) if config is not None else []
        if state == "merged":
            self._set_entries(base, entries, base_exists)
        elif state == "replaced":
            if not _configurations_match(before, config):
                if base_exists:
                    self._clear_children(base)
                    if not any(relative == "" for relative, _value in entries):
                        self._clear_value(base)
                self._set_entries(base, entries, base_exists)
        elif state == "deleted":
            if config is not None:
                matches = []
                for relative, _value in entries:
                    matches.extend(
                        self._confined_matches(
                            _join_path(base, relative),
                            "config path %r" % relative,
                            boundary=base if base_exists else None,
                        )
                    )
                base_removed = base in matches
                self._remove_paths(matches)
            elif self.path and base_exists:
                self._remove_paths((base,))
                base_removed = True
            elif not self.path and base_exists:
                self._clear_children(base)
        else:
            raise AugeasResourceError("unsupported state %r" % state)

        after = {} if base_removed else self._configuration(base)
        changed = before_fingerprint != self._fingerprint()
        if changed:
            self._save()
        return {"changed": changed, "before": before, "after": after}
