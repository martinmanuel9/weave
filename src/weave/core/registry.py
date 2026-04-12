"""Provider contract registry — load and look up provider contracts."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from weave.schemas.provider_contract import ProviderContract


logger = logging.getLogger(__name__)


class ProviderRegistryError(Exception):
    """Raised when a built-in contract is malformed. Weave exits 1."""


def _builtin_dir() -> Path:
    """Return the absolute path to the in-tree built-in providers directory."""
    import weave
    return Path(weave.__file__).parent / "providers" / "builtin"


class ProviderRegistry:
    """Registry of provider contracts — merges built-ins with user overrides.

    Built-ins ship inside the weave package. User contracts live under
    `<project_root>/.harness/providers/`. On name collision, the user wins
    (a warning is logged). Built-in load failure is fatal; user load
    failure skips the single provider and logs an error.
    """

    def __init__(self) -> None:
        self._contracts: dict[str, ProviderContract] = {}
        self._manifest_dirs: dict[str, Path] = {}
        self._loaded_root: Path | None = None

    def load(self, project_root: Path) -> None:
        """Load built-ins then user contracts.

        Idempotent when called with the same `project_root`. A call with a
        different `project_root` resets all state and reloads.
        """
        project_root = Path(project_root)
        if self._loaded_root == project_root.resolve():
            return

        self._contracts.clear()
        self._manifest_dirs.clear()

        self._load_builtins()
        self._load_user(project_root)

        self._loaded_root = project_root.resolve()

    def _load_builtins(self) -> None:
        builtin_dir = _builtin_dir()
        if not builtin_dir.is_dir():
            raise ProviderRegistryError(
                f"built-in provider directory missing: {builtin_dir}"
            )
        for manifest_path in sorted(builtin_dir.glob("*.contract.json")):
            try:
                contract = self._parse_manifest(manifest_path, source="builtin")
            except Exception as exc:
                raise ProviderRegistryError(
                    f"failed to load built-in contract {manifest_path.name}: {exc}"
                ) from exc
            self._contracts[contract.name] = contract
            self._manifest_dirs[contract.name] = manifest_path.parent

    def _load_user(self, project_root: Path) -> None:
        user_dir = project_root / ".harness" / "providers"
        if not user_dir.is_dir():
            return

        loaded_stems: set[str] = set()
        for manifest_path in sorted(user_dir.glob("*.contract.json")):
            try:
                contract = self._parse_manifest(manifest_path, source="user")
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                logger.error(
                    "failed to load user contract %s: %s",
                    manifest_path.name,
                    exc,
                )
                continue

            if contract.name in self._contracts and self._contracts[contract.name].source == "builtin":
                logger.warning(
                    "user contract %s overrides built-in with the same name",
                    contract.name,
                )
            self._contracts[contract.name] = contract
            self._manifest_dirs[contract.name] = manifest_path.parent
            loaded_stems.add(manifest_path.stem.removesuffix(".contract"))

        # Orphan adapter scan: adapters without a matching manifest.
        for adapter_path in sorted(user_dir.glob("*.sh")):
            stem = adapter_path.stem
            if stem in loaded_stems:
                continue
            if stem in self._contracts and self._contracts[stem].source == "user":
                continue
            logger.error(
                "adapter %s has no contract manifest; provider unavailable. "
                "Create %s.contract.json or delete the adapter.",
                adapter_path.name,
                stem,
            )

    def _parse_manifest(self, manifest_path: Path, source: str) -> ProviderContract:
        raw = json.loads(manifest_path.read_text())
        # Strip any author-supplied 'source' field; we inject our own.
        raw.pop("source", None)
        contract = ProviderContract.model_validate(raw)

        expected_stem = manifest_path.name.removesuffix(".contract.json")
        if contract.name != expected_stem:
            raise ValueError(
                f"contract 'name' ({contract.name!r}) must match filename stem "
                f"({expected_stem!r})"
            )

        adapter_path = manifest_path.parent / contract.adapter
        if not adapter_path.exists():
            raise ValueError(
                f"adapter file not found: {adapter_path} "
                f"(declared in {manifest_path.name})"
            )

        # Patch the source field post-validation.
        contract = contract.model_copy(update={"source": source})
        return contract

    def get(self, name: str) -> ProviderContract:
        """Return the contract for `name`. Raises KeyError if unknown."""
        return self._contracts[name]

    def has(self, name: str) -> bool:
        return name in self._contracts

    def list(self) -> list[ProviderContract]:
        """Return all loaded contracts in name-sorted order."""
        return [self._contracts[name] for name in sorted(self._contracts)]

    def resolve_adapter_path(self, name: str) -> Path:
        """Return the absolute path to the adapter file for `name`.

        Raises KeyError if `name` is not loaded.
        """
        contract = self._contracts[name]
        return (self._manifest_dirs[name] / contract.adapter).resolve()


_REGISTRY_SINGLETON: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Return the process-wide registry singleton.

    Callers must invoke `.load(project_root)` before `.get()` or `.list()`.
    `.load()` is idempotent for the same project_root.
    """
    global _REGISTRY_SINGLETON
    if _REGISTRY_SINGLETON is None:
        _REGISTRY_SINGLETON = ProviderRegistry()
    return _REGISTRY_SINGLETON
