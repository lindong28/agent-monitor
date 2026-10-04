import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name("machines.json")
DEFAULT_RETIREMENT_ROOT = Path(__file__).resolve().parent / "state" / "generations"
_DEFAULT_RETIREMENT_ROOT = object()
_ROOT_FIELDS = {"machines", "retired_names"}
_MACHINE_FIELDS = {"name", "ssh_host", "self"}
_MACHINE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
_RETIREMENT_LEDGER_FIELDS = {"schema_version", "retired_names", "updated_at"}


class MachineConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Machine:
    name: str
    ssh_host: str
    is_self: bool

    def as_config_dict(self):
        return {"name": self.name, "ssh_host": self.ssh_host, "self": self.is_self}


@dataclass(frozen=True)
class MachineConfig:
    machines: tuple
    retired_names: frozenset

    @property
    def by_name(self):
        return {machine.name: machine for machine in self.machines}

    @property
    def self_machine(self):
        return next(machine for machine in self.machines if machine.is_self)


def load_machine_config(path=DEFAULT_CONFIG_PATH, *, retirement_root=_DEFAULT_RETIREMENT_ROOT):
    path = Path(path)
    local = None
    hub_names = None
    # Installed identity is local state, independent of another Mac's `self` bit.
    if path.resolve() == DEFAULT_CONFIG_PATH.resolve():
        import hub
        local = hub.local_machine()
        if local is not None:
            hub_names = set(hub.configuration()["machines"])
    if retirement_root is _DEFAULT_RETIREMENT_ROOT:
        retirement_root = DEFAULT_RETIREMENT_ROOT if path.resolve() == DEFAULT_CONFIG_PATH.resolve() else None
    try:
        payload = json.loads(
            _config_json(path.read_text(encoding="utf-8")),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise MachineConfigError(f"cannot load machine config {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise MachineConfigError("machine config must be a JSON object")
    unknown_root_fields = set(payload) - _ROOT_FIELDS
    missing_root_fields = _ROOT_FIELDS - set(payload)
    if unknown_root_fields:
        raise MachineConfigError(f"unknown machine config fields: {sorted(unknown_root_fields)}")
    if missing_root_fields:
        raise MachineConfigError(f"missing machine config fields: {sorted(missing_root_fields)}")

    raw_machines = payload["machines"]
    raw_retired_names = payload["retired_names"]
    if not isinstance(raw_machines, list):
        raise MachineConfigError("machines must be a list")
    if not isinstance(raw_retired_names, list):
        raise MachineConfigError("retired_names must be a list")

    retired_names = {
        _validated_machine_name(value, f"retired_names[{index}]")
        for index, value in enumerate(raw_retired_names)
    }
    if len(retired_names) != len(raw_retired_names):
        raise MachineConfigError("retired_names contains duplicates")

    machines = tuple(_parse_machine(item, index) for index, item in enumerate(raw_machines))
    names = [machine.name for machine in machines]
    if len(set(names)) != len(names):
        raise MachineConfigError("duplicate machine name")
    ssh_hosts = [machine.ssh_host.casefold() for machine in machines]
    if len(set(ssh_hosts)) != len(ssh_hosts):
        raise MachineConfigError("duplicate ssh_host pull target")
    reused_names = set(names) & retired_names
    if reused_names:
        raise MachineConfigError(f"retired machine name cannot be active: {sorted(reused_names)}")
    persisted_retired = persisted_retired_names(retirement_root)
    uncommitted_retirements = retired_names - persisted_retired
    if retirement_root is not None and uncommitted_retirements:
        raise MachineConfigError(
            "retired_names are missing the explicit retirement commit: %s"
            % sorted(uncommitted_retirements)
        )
    persistently_reused = set(names) & persisted_retired
    if persistently_reused:
        raise MachineConfigError(
            f"persistently retired machine name cannot be active: {sorted(persistently_reused)}"
        )
    if local is not None:
        machines = tuple(Machine(m.name, m.ssh_host, m.name == local)
                         for m in machines if m.name in hub_names)
    if sum(machine.is_self for machine in machines) != 1:
        raise MachineConfigError("machine config must contain exactly one self machine")

    return MachineConfig(machines=machines, retired_names=frozenset(retired_names))


def _mask_comment_lines(text):
    # Keep offsets and line numbers intact for diagnostics and local retire edits.
    return "".join(
        "".join(char if char in "\r\n" else " " for char in line)
        if line.lstrip().startswith(("#", "//")) else line
        for line in text.splitlines(keepends=True)
    )


def _config_json(text):
    chars = list(_mask_comment_lines(text))
    in_string = escaped = False
    for index, char in enumerate(chars):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == ",":
            after = index + 1
            while after < len(chars) and chars[after] in " \t\r\n":
                after += 1
            if after < len(chars) and chars[after] in "]}":
                chars[index] = " "
    return "".join(chars)


def retirement_config_text(text, retired_names):
    """Edit validated config values without discarding disabled declarations."""
    normalized = _config_json(text)
    json.loads(normalized, object_pairs_hook=_reject_duplicate_keys)
    decoder = json.JSONDecoder()
    comments_masked = _mask_comment_lines(text)
    edits = list(text)

    def skip_space(position):
        while position < len(normalized) and normalized[position] in " \t\r\n":
            position += 1
        return position

    position = skip_space(0) + 1  # Root object; schema was checked by the caller.
    while normalized[skip_space(position)] != "}":
        key, position = decoder.raw_decode(normalized, skip_space(position))
        start = skip_space(skip_space(position) + 1)  # Colon.
        _, end = decoder.raw_decode(normalized, start)
        if key == "retired_names":
            edits[start] = json.dumps(sorted(retired_names))
            edits[start + 1:end] = [""] * (end - start - 1)
        elif key == "machines":
            item_start = skip_space(start + 1)
            preceding_comma = None
            while normalized[item_start] != "]":
                item, item_end = decoder.raw_decode(normalized, item_start)
                after = skip_space(item_end)
                # A trailing comma is masked in normalized, but still belongs
                # to the removed record in the original text.
                comma = item_end
                while comma < end and comments_masked[comma] in " \t\r\n":
                    comma += 1
                following_comma = comma if comments_masked[comma] == "," else None
                if item["name"] in retired_names:
                    edits[item_start:item_end] = [""] * (item_end - item_start)
                    separator = following_comma if following_comma is not None else preceding_comma
                    if separator is not None:
                        edits[separator] = ""
                if normalized[after] == ",":
                    preceding_comma = after
                    item_start = skip_space(after + 1)
                else:
                    break
        position = skip_space(end)
        if normalized[position] == ",":
            position += 1
    result = "".join(edits)
    json.loads(_config_json(result), object_pairs_hook=_reject_duplicate_keys)
    return result


def persisted_retired_names(root):
    if root is None:
        return set()
    root = Path(root)
    if not root.is_dir():
        return set()
    retired = {
        marker.parent.name
        for marker in root.glob("*/retired.json")
        if marker.is_file()
    }
    ledger = root / "retirements.json"
    if not ledger.exists():
        return retired
    try:
        payload = json.loads(
            ledger.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise MachineConfigError("cannot load retirement state %s: %s" % (ledger, exc)) from exc
    if not isinstance(payload, dict) or set(payload) != _RETIREMENT_LEDGER_FIELDS:
        raise MachineConfigError("retirement state fields do not match schema")
    if payload["schema_version"] != 1:
        raise MachineConfigError("unsupported retirement state schema_version")
    raw_names = payload["retired_names"]
    if not isinstance(raw_names, list):
        raise MachineConfigError("retirement state retired_names must be a list")
    ledger_names = {
        _validated_machine_name(value, "retirement state retired_names[%d]" % index)
        for index, value in enumerate(raw_names)
    }
    if len(ledger_names) != len(raw_names):
        raise MachineConfigError("retirement state retired_names contains duplicates")
    if not isinstance(payload["updated_at"], str) or not payload["updated_at"]:
        raise MachineConfigError("retirement state updated_at must be a non-empty timestamp")
    return retired | ledger_names


def machine_config_fingerprint(machine):
    canonical_json = json.dumps(
        machine.as_config_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def admit_machine(config, name, recorded_fingerprint):
    machine = config.by_name.get(name)
    if machine is None:
        return None
    if recorded_fingerprint != machine_config_fingerprint(machine):
        return None
    return machine


def _parse_machine(payload, index):
    if not isinstance(payload, dict):
        raise MachineConfigError(f"machines[{index}] must be an object")
    unknown_fields = set(payload) - _MACHINE_FIELDS
    missing_fields = _MACHINE_FIELDS - set(payload)
    if unknown_fields:
        raise MachineConfigError(f"unknown machine fields at index {index}: {sorted(unknown_fields)}")
    if missing_fields:
        raise MachineConfigError(f"missing machine fields at index {index}: {sorted(missing_fields)}")

    name = _validated_machine_name(payload["name"], f"machines[{index}].name")
    ssh_host = _validated_name(payload["ssh_host"], f"machines[{index}].ssh_host")
    is_self = payload["self"]
    if type(is_self) is not bool:
        raise MachineConfigError(f"machines[{index}].self must be a boolean")
    return Machine(name=name, ssh_host=ssh_host, is_self=is_self)


def _validated_name(value, field):
    if not isinstance(value, str) or not value or value.strip() != value:
        raise MachineConfigError(f"{field} must be a non-empty string without surrounding whitespace")
    return value


def _validated_machine_name(value, field):
    value = _validated_name(value, field)
    if not _MACHINE_NAME.fullmatch(value):
        raise MachineConfigError(
            f"{field} must be a lowercase ASCII slug using only letters, digits, '-' or '_'"
        )
    return value


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MachineConfigError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result
