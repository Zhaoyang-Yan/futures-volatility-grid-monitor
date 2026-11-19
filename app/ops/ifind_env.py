import os
import tempfile
from datetime import datetime
from pathlib import Path


IFIND_ENV_KEYS = {
    "IFIND_REFRESH_TOKEN",
    "IFIND_ACCOUNT_EXPIRE_DATE",
    "IFIND_TOKEN_RENEW_DAY",
}


def update_ifind_env_file(env_path, values: dict[str, str], backup_dir=None) -> Path:
    path = Path(env_path)
    if not path.exists():
        raise FileNotFoundError("env file not found")

    backup_root = Path(backup_dir) if backup_dir else path.parent / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / f"env_before_ifind_update_{datetime.now():%Y%m%d_%H%M%S}"
    backup_path.write_bytes(path.read_bytes())
    os.chmod(backup_path, 0o600)

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    rendered = _render_env_lines(lines, {key: str(value or "") for key, value in values.items() if key in IFIND_ENV_KEYS})
    _atomic_write(path, rendered)
    os.chmod(path, 0o600)
    return backup_path


def _render_env_lines(lines: list[str], values: dict[str, str]) -> str:
    remaining = dict(values)
    output = []
    for line in lines:
        key = _line_key(line)
        if key in remaining:
            output.append(f"{key}={_quote_env_value(remaining.pop(key))}\n")
        else:
            output.append(line)

    if output and not output[-1].endswith("\n"):
        output[-1] = f"{output[-1]}\n"
    for key, value in remaining.items():
        output.append(f"{key}={_quote_env_value(value)}\n")
    return "".join(output)


def _line_key(line: str) -> str | None:
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    if not key:
        return None
    return key


def _quote_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(char.isspace() or char in {'"', "'", "#", "$", "`", "\\"} for char in value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _atomic_write(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
