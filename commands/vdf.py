"""Strict, comment-aware VDF reader/editor that preserves unrelated source text."""
from __future__ import annotations
import re


def escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


class Entry:
    def __init__(self, key, value, start, end, value_start, value_end):
        self.key, self.value = key, value
        self.start, self.end = start, end
        self.value_start, self.value_end = value_start, value_end


def parse(text):
    position = 0
    def whitespace():
        nonlocal position
        while position < len(text):
            if text[position].isspace():
                position += 1
            elif text.startswith("//", position):
                end = text.find("\n", position)
                position = len(text) if end < 0 else end + 1
            else:
                break
    def quoted():
        nonlocal position
        if position >= len(text) or text[position] != '"':
            raise ValueError(f"Invalid VDF: expected quoted string at {position}")
        position += 1
        start = position
        value = ""
        while position < len(text):
            char = text[position]
            position += 1
            if char == '"':
                return value, start, position - 1
            if char == "\\":
                if position >= len(text):
                    break
                following = text[position]
                position += 1
                value += following if following in ('"', "\\") else "\\" + following
            else:
                value += char
        raise ValueError("Invalid VDF: unterminated string")
    def entries(nested=False, depth=0):
        nonlocal position
        if depth > 64:
            raise ValueError("VDF nesting exceeds safety limit")
        result = []
        while True:
            whitespace()
            if position == len(text):
                if nested:
                    raise ValueError("Invalid VDF: missing closing brace")
                return result
            if text[position] == "}":
                if not nested:
                    raise ValueError("Invalid VDF: unexpected closing brace")
                return result
            start = position
            key, _, _ = quoted()
            whitespace()
            if position < len(text) and text[position] == "{":
                position += 1
                value_start = position
                value = entries(True, depth + 1)
                value_end = position
                position += 1
            else:
                value, value_start, value_end = quoted()
            result.append(Entry(key, value, start, position, value_start, value_end))
    return entries()


def find(entries, key):
    matches = [entry for entry in entries if entry.key.casefold() == key.casefold()]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous duplicate VDF key: {key}")
    return matches[0] if matches else None


def object_entry(text, key):
    matches = []
    def visit(entries):
        for entry in entries:
            if isinstance(entry.value, list):
                if entry.key.casefold() == key.casefold():
                    matches.append(entry)
                visit(entry.value)
    visit(parse(text))
    if len(matches) > 1:
        raise ValueError(f"Ambiguous VDF object: {key}")
    return matches[0] if matches else None


def scalar(entry, key, default=""):
    found = find(entry.value, key) if entry and isinstance(entry.value, list) else None
    return found.value if found and isinstance(found.value, str) else default


def set_in_object(text, key, child, value):
    parent = object_entry(text, key)
    if parent is None:
        raise ValueError(f"Missing VDF object: {key}")
    entry = find(parent.value, child)
    if entry:
        if not isinstance(entry.value, str):
            raise ValueError(f"Expected scalar: {child}")
        return text[:entry.value_start] + escape(value) + text[entry.value_end:]
    return text[:parent.value_end] + f'\n\t"{escape(child)}" "{escape(value)}"\n' + text[parent.value_end:]


def mappings(text):
    section = object_entry(text, "CompatToolMapping")
    if section is None:
        return {}
    result = {}
    for entry in section.value:
        if not isinstance(entry.value, list):
            raise ValueError("Unexpected scalar compatibility mapping")
        if entry.key in result:
            raise ValueError("Duplicate compatibility mapping")
        result[entry.key] = {"name": scalar(entry, "name"), "source": text[entry.start:entry.end]}
    return result


def set_mapping(text, appid, name, *, source=None):
    if not str(appid).isdigit():
        raise ValueError("Compatibility mapping requires numeric app ID")
    section = object_entry(text, "CompatToolMapping")
    if section is None:
        parent = object_entry(text, "Steam")
        if parent is None:
            raise ValueError("Steam configuration has no Steam object")
        text = text[:parent.value_end] + '\n"CompatToolMapping" {}\n' + text[parent.value_end:]
        section = object_entry(text, "CompatToolMapping")
    entry = find(section.value, str(appid))
    replacement = source if source is not None else (f'"{appid}" {{ "name" "{escape(name)}" "config" "" "priority" "250" }}')
    if replacement:
        restored = parse(replacement)
        if len(restored) != 1 or restored[0].key != str(appid) or not isinstance(restored[0].value, list):
            raise ValueError("Invalid saved compatibility entry")
    else:
        replacement = ""
    if entry:
        return text[:entry.start] + replacement + text[entry.end:]
    return text[:section.value_end] + "\n" + replacement + "\n" + text[section.value_end:]


def managed(name):
    return name in ("steam-shared-library-proton", "steam-per-user-proton") or name.startswith("steam-shared-library-proton-proton_")


def value(text, key):
    matches = []
    def visit(entries):
        for entry in entries:
            if isinstance(entry.value, list):
                visit(entry.value)
            elif entry.key.casefold() == key.casefold():
                matches.append(entry.value)
    visit(parse(text))
    if len(set(matches)) > 1:
        raise ValueError(f"Ambiguous scalar VDF key: {key}")
    return matches[0] if matches else ""


def library_indexes(text, include_primary=True):
    section = object_entry(text, "libraryfolders")
    result = {}
    if section:
        for entry in section.value:
            if entry.key.isdigit() and isinstance(entry.value, list) and (include_primary or entry.key != "0"):
                path = scalar(entry, "path")
                if path:
                    if path in result or int(entry.key) in result.values():
                        raise ValueError("Duplicate Steam library path/index")
                    result[path] = int(entry.key)
    return result
