"""Check direct dependency declarations, locked versions, and the active environment."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    declared = {
        canonicalize_name(req.name): req
        for req in map(Requirement, project["project"]["dependencies"])
    }
    requirements = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        content = line.split("#", 1)[0].strip()
        if content:
            requirement = Requirement(content)
            requirements[canonicalize_name(requirement.name)] = requirement
    errors = []
    if declared != requirements:
        errors.append("pyproject.toml and requirements.txt declarations differ")
    for name, requirement in declared.items():
        versions = {
            item["version"]
            for item in lock["package"]
            if canonicalize_name(item["name"]) == name
        }
        if not versions or any(item not in requirement.specifier for item in versions):
            errors.append(f"{name}: missing or incompatible locked version")
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = version(name)
        except PackageNotFoundError:
            errors.append(f"{name}: not installed")
            continue
        if installed not in versions:
            errors.append(f"{name}: installed {installed} differs from the lock")
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print(
        f"PASS: {len(declared)} direct dependencies agree across declarations, lock and environment"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
