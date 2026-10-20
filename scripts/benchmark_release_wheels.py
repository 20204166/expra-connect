"""Benchmark newly built wheels and preserve immutable release measurements."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_coverage import SUPPLEMENT_CASES
from scripts.benchmark_support import (
    BenchmarkRun,
    RunMetadata,
    load_results,
    write_results,
)

_WHEEL_NAME = re.compile(
    r"^expra_connect-(?P<version>\d+\.\d+\.\d+\.\d+)-"
    r"(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>.+)\.whl$"
)
_SUITES = ("cluster_state", "persistence", "connect")
_LEGACY_BUILD_VERSION = "2.2.0.4"


def run_release_benchmarks(
    wheel_dir: Path,
    results_dir: Path,
    python_bin: str,
    repository_root: Path,
) -> tuple[Path, ...]:
    """Benchmark each wheel once, reusing complete immutable result sets."""

    wheels = tuple(sorted(wheel_dir.glob("expra_connect-*.whl")))
    if not wheels:
        raise ValueError(f"no release wheels found in {wheel_dir}")
    identities = tuple(_wheel_identity(wheel) for wheel in wheels)
    if len(set(identities)) != len(identities):
        raise ValueError("wheel directory contains duplicate version/flavor artifacts")

    result_sets = tuple(
        (wheel, version, flavor, _result_paths(results_dir, version, flavor))
        for wheel, (version, flavor) in zip(wheels, identities, strict=True)
    )
    for _, version, flavor, paths in result_sets:
        present = tuple(path.exists() for path in paths)
        if any(present) and not all(present):
            raise ValueError(
                f"partial benchmark results for {version} {flavor}; refusing overwrite"
            )
        if all(present):
            for suite, path in zip(_SUITES, paths, strict=True):
                _validate_saved_run(load_results(path), suite, version)

    results_dir.mkdir(parents=True, exist_ok=True)
    produced: list[Path] = []
    for wheel, version, flavor, paths in result_sets:
        if all(path.exists() for path in paths):
            produced.extend(paths)
            continue
        _benchmark_wheel(wheel, version, flavor, paths, python_bin, repository_root)
        produced.extend(paths)
    return tuple(produced)


def run_release_supplement_benchmarks(
    wheel_dir: Path,
    results_dir: Path,
    python_bin: str,
    repository_root: Path,
    *,
    target_version: str | None = None,
) -> tuple[Path, ...]:
    """Create paired, append-only supplemental workload measurements."""

    wheels = tuple(sorted(wheel_dir.glob("expra_connect-*.whl")))
    if target_version is not None:
        wheels = tuple(
            wheel for wheel in wheels if _wheel_identity(wheel)[0] == target_version
        )
    if not wheels:
        raise ValueError(f"no release wheels found in {wheel_dir}")
    identities = tuple(_wheel_identity(wheel) for wheel in wheels)
    if len(set(identities)) != len(identities):
        raise ValueError("wheel directory contains duplicate version/flavor artifacts")
    by_version: dict[str, dict[str, Path]] = {}
    for wheel, (version, flavor) in zip(wheels, identities, strict=True):
        by_version.setdefault(version, {})[flavor] = wheel
    if any(set(flavors) != {"native", "universal"} for flavors in by_version.values()):
        raise ValueError(
            "supplement benchmarking requires paired native/universal wheels"
        )

    targets = tuple(
        results_dir / f"{version}-{flavor}-{suite}-supplement.json"
        for version in sorted(by_version)
        for flavor in ("native", "universal")
        for suite in _SUITES
    )
    present = tuple(path.is_file() for path in targets)
    if any(present):
        if not all(present):
            raise ValueError("partial supplement results exist; refusing overwrite")
        _validate_saved_supplement_pairs(targets)
        return targets

    results_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".release-supplement-stage-", dir=results_dir
    ) as staging:
        stage_root = Path(staging)
        staged_runs: dict[tuple[str, str, str], BenchmarkRun] = {}
        for version, flavors in sorted(by_version.items()):
            for flavor in ("native", "universal"):
                runs = _benchmark_supplement_wheel(
                    flavors[flavor], version, flavor, python_bin, repository_root
                )
                for suite, run in zip(_SUITES, runs, strict=True):
                    staged_runs[(version, flavor, suite)] = run
            for suite in _SUITES:
                _validate_supplement_pair(
                    suite,
                    staged_runs[(version, "native", suite)],
                    staged_runs[(version, "universal", suite)],
                    version,
                )

        staged_paths: list[tuple[Path, Path]] = []
        for version, flavor, suite in (
            (version, flavor, suite)
            for version in sorted(by_version)
            for flavor in ("native", "universal")
            for suite in _SUITES
        ):
            run = staged_runs[(version, flavor, suite)]
            staged_path = stage_root / f"{version}-{flavor}-{suite}-supplement.json"
            write_results(staged_path, run.metadata, run.outcomes)
            staged_paths.append(
                (
                    staged_path,
                    results_dir / staged_path.name,
                )
            )
        if any(destination.exists() for _, destination in staged_paths):
            raise FileExistsError("supplement result already exists")
        published: list[tuple[Path, Path]] = []
        try:
            for staged_path, destination in staged_paths:
                os.link(staged_path, destination)
                published.append((staged_path, destination))
        except OSError:
            for staged_path, destination in reversed(published):
                if destination.exists() and os.path.samefile(staged_path, destination):
                    destination.unlink()
            raise
    return tuple(destination for _, destination in staged_paths)


def _benchmark_supplement_wheel(
    wheel: Path,
    version: str,
    flavor: str,
    python_bin: str,
    repository_root: Path,
) -> tuple[BenchmarkRun, ...]:
    with tempfile.TemporaryDirectory(prefix="expra-connect-supplement-") as temporary:
        temporary_root = Path(temporary)
        extracted = temporary_root / "wheel"
        extracted.mkdir()
        _extract_package(wheel, extracted)
        outputs = {suite: temporary_root / f"{suite}.json" for suite in _SUITES}
        commands = (
            (
                "cluster_state",
                repository_root / "scripts" / "benchmark_cluster_state.py",
                (
                    "--sizes",
                    "1000",
                    "30000",
                    "100000",
                    "--operation",
                    "role authorization",
                    "--skip-allocation-tracking",
                    "--json-output",
                    str(outputs["cluster_state"]),
                ),
            ),
            (
                "persistence",
                repository_root / "scripts" / "benchmark_persistence.py",
                (
                    "--migration-file-counts",
                    "1",
                    "16",
                    "64",
                    "--operation",
                    "Profile migration",
                    "--json-output",
                    str(outputs["persistence"]),
                ),
            ),
            (
                "connect",
                repository_root / "tools" / "connect_performance_benchmark.py",
                (
                    "--feature-workloads-only",
                    "--json-output",
                    str(outputs["connect"]),
                ),
            ),
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(extracted), str(repository_root))
        )
        environment["PYTHONNOUSERSITE"] = "1"
        runs: list[BenchmarkRun] = []
        for suite, script, options in commands:
            subprocess.run(
                (python_bin, str(script), *options),
                cwd=repository_root,
                env=environment,
                check=True,
            )
            run = load_results(outputs[suite])
            _validate_saved_run(run, suite, version)
            _validate_module_provenance(run, extracted)
            case_ids = {outcome.case_id for outcome in run.outcomes}
            if case_ids != SUPPLEMENT_CASES[suite]:
                raise ValueError(f"supplement {suite} case inventory differs")
            if _run_flavor(run) != flavor:
                raise ValueError(f"supplement module provenance differs for {flavor}")
            runs.append(run)
        return tuple(runs)


def _run_flavor(run: BenchmarkRun) -> str | None:
    module_paths = tuple(
        Path(path).suffix.lower()
        for name, path in run.metadata.modules.items()
        if name.startswith("expra_connect.")
    )
    if any(suffix in {".so", ".pyd", ".dll"} for suffix in module_paths):
        return "native"
    return "universal" if module_paths else None


def _validate_supplement_pair(
    suite: str,
    native: BenchmarkRun,
    universal: BenchmarkRun,
    version: str,
) -> None:
    for flavor, run in (("native", native), ("universal", universal)):
        if (
            run.metadata.suite != suite
            or run.metadata.package_version != version
            or _run_flavor(run) != flavor
            or {outcome.case_id for outcome in run.outcomes} != SUPPLEMENT_CASES[suite]
        ):
            raise ValueError(f"{suite}: invalid {version} {flavor} supplement")
    first, second = native.metadata, universal.metadata
    if (
        first.python_version != second.python_version
        or first.platform != second.platform
        or first.config != second.config
    ):
        raise ValueError(f"{suite}: native and universal supplement metadata differs")
    native_outcomes = {outcome.case_id: outcome for outcome in native.outcomes}
    universal_outcomes = {outcome.case_id: outcome for outcome in universal.outcomes}
    for case_id in SUPPLEMENT_CASES[suite]:
        left, right = native_outcomes[case_id], universal_outcomes[case_id]
        if (
            left.operation != right.operation
            or left.fixture != right.fixture
            or left.size != right.size
            or left.payload_bytes != right.payload_bytes
            or left.config != right.config
        ):
            raise ValueError(f"{suite}: supplement fixture differs for {case_id}")


def _validate_saved_supplement_pairs(paths: tuple[Path, ...]) -> None:
    by_identity: dict[tuple[str, str, str], BenchmarkRun] = {}
    for path in paths:
        stem = path.name.removesuffix("-supplement.json")
        version, flavor, suite = stem.split("-", 2)
        run = load_results(path)
        key = (version, flavor, suite)
        by_identity[key] = run
    versions = {version for version, _, _ in by_identity}
    for version in versions:
        for suite in _SUITES:
            _validate_supplement_pair(
                suite,
                by_identity[(version, "native", suite)],
                by_identity[(version, "universal", suite)],
                version,
            )


def _wheel_identity(wheel: Path) -> tuple[str, str]:
    match = _WHEEL_NAME.fullmatch(wheel.name)
    if match is None:
        raise ValueError(f"unsupported wheel filename: {wheel.name}")
    if (match.group("python"), match.group("abi"), match.group("platform")) == (
        "py3",
        "none",
        "any",
    ):
        return match.group("version"), "universal"
    if not match.group("python").startswith("cp"):
        raise ValueError(f"unsupported native wheel tag: {wheel.name}")
    return match.group("version"), "native"


def _result_paths(results_dir: Path, version: str, flavor: str) -> tuple[Path, ...]:
    versioned = tuple(
        results_dir / f"{version}-{flavor}-{suite}.json" for suite in _SUITES
    )
    if version != _LEGACY_BUILD_VERSION:
        return versioned
    legacy = tuple(results_dir / f"{flavor}-{suite}.json" for suite in _SUITES)
    versioned_present = any(path.exists() for path in versioned)
    legacy_present = tuple(path.exists() for path in legacy)
    if versioned_present and any(legacy_present):
        raise ValueError(
            f"both legacy and versioned benchmark results exist for {version} {flavor}"
        )
    if any(legacy_present):
        if not all(legacy_present):
            raise ValueError(f"partial legacy benchmark results for {version} {flavor}")
        return legacy
    return versioned


def _benchmark_wheel(
    wheel: Path,
    version: str,
    flavor: str,
    result_paths: tuple[Path, ...],
    python_bin: str,
    repository_root: Path,
) -> None:
    results_dir = result_paths[0].parent
    with tempfile.TemporaryDirectory(
        prefix="expra-connect-wheel-benchmark-"
    ) as temporary:
        temporary_root = Path(temporary)
        extracted = temporary_root / "wheel"
        extracted.mkdir()
        _extract_package(wheel, extracted)
        raw_paths = {suite: temporary_root / f"{suite}.json" for suite in _SUITES}
        migration_path = temporary_root / "persistence-migration.json"
        commands = (
            (
                "cluster_state",
                repository_root / "scripts" / "benchmark_cluster_state.py",
                (
                    "--sizes",
                    "1000",
                    "30000",
                    "100000",
                    "--skip-allocation-tracking",
                    "--json-output",
                    str(raw_paths["cluster_state"]),
                ),
            ),
            (
                "persistence",
                repository_root / "scripts" / "benchmark_persistence.py",
                (
                    "--sizes",
                    "30000",
                    "100000",
                    "200000",
                    "--json-output",
                    str(raw_paths["persistence"]),
                ),
            ),
            (
                "connect",
                repository_root / "tools" / "connect_performance_benchmark.py",
                ("--json-output", str(raw_paths["connect"])),
            ),
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(extracted), str(repository_root))
        )
        environment["PYTHONNOUSERSITE"] = "1"
        runs: list[BenchmarkRun] = []
        for suite, script, options in commands:
            subprocess.run(
                (python_bin, str(script), *options),
                cwd=repository_root,
                env=environment,
                check=True,
            )
            run = load_results(raw_paths[suite])
            _validate_saved_run(run, suite, version)
            _validate_module_provenance(run, extracted)
            runs.append(run)

        subprocess.run(
            (
                python_bin,
                str(repository_root / "scripts" / "benchmark_persistence.py"),
                "--migration-file-counts",
                "1",
                "16",
                "64",
                "--operation",
                "Profile migration",
                "--json-output",
                str(migration_path),
            ),
            cwd=repository_root,
            env=environment,
            check=True,
        )
        migration_run = load_results(migration_path)
        _validate_saved_run(migration_run, "persistence", version)
        _validate_module_provenance(migration_run, extracted)
        runs[1] = _merge_persistence_runs(runs[1], migration_run)

        with tempfile.TemporaryDirectory(
            prefix=".release-benchmark-stage-", dir=results_dir
        ) as staging:
            staged_paths: list[tuple[Path, Path]] = []
            for path, run in zip(result_paths, runs, strict=True):
                staged = Path(staging) / path.name
                write_results(staged, run.metadata, run.outcomes)
                staged_paths.append((staged, path))
            if any(path.exists() for _, path in staged_paths):
                raise FileExistsError("versioned benchmark result already exists")
            published: list[tuple[Path, Path]] = []
            try:
                for staged, path in staged_paths:
                    os.link(staged, path)
                    published.append((staged, path))
            except OSError:
                for staged, path in reversed(published):
                    if path.exists() and os.path.samefile(staged, path):
                        path.unlink()
                raise


def _extract_package(wheel: Path, destination: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            name = PurePosixPath(member.filename)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError(f"unsafe path in wheel: {member.filename}")
            if not name.parts or name.parts[0] != "expra_connect":
                continue
            mode = member.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ValueError(f"symlink in wheel package: {member.filename}")
            target = destination.joinpath(*name.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
    if not (destination / "expra_connect" / "__init__.py").is_file():
        raise ValueError(f"wheel has no importable expra_connect package: {wheel}")


def _validate_saved_run(run: BenchmarkRun, suite: str, version: str) -> None:
    if run.metadata.suite != suite or run.metadata.package_version != version:
        raise ValueError(f"benchmark output does not match {version} {suite}")
    if not run.outcomes:
        raise ValueError(f"benchmark produced no outcomes for {version} {suite}")
    if not any(name.startswith("expra_connect.") for name in run.metadata.modules):
        raise ValueError(f"benchmark has no package module provenance for {suite}")


def _validate_module_provenance(run: BenchmarkRun, extracted: Path) -> None:
    wheel_root = extracted.resolve()
    for name, module_path in run.metadata.modules.items():
        if name.startswith("expra_connect.") and not Path(
            module_path
        ).resolve().is_relative_to(wheel_root):
            raise ValueError(f"benchmark imported {name} outside the built wheel")


def _merge_persistence_runs(
    base: BenchmarkRun, supplement: BenchmarkRun
) -> BenchmarkRun:
    """Add profile-migration outcomes to the ordinary persistence run."""

    first, second = base.metadata, supplement.metadata
    if (
        first.suite != "persistence"
        or second.suite != "persistence"
        or first.package_version != second.package_version
        or first.python_version != second.python_version
        or first.platform != second.platform
        or first.config != second.config
        or any(second.modules.get(name) != path for name, path in first.modules.items())
    ):
        raise ValueError("persistence benchmark metadata differs")
    case_ids = {outcome.case_id for outcome in base.outcomes}
    if any(outcome.case_id in case_ids for outcome in supplement.outcomes):
        raise ValueError("persistence benchmark case IDs overlap")
    metadata = RunMetadata(
        schema_version=first.schema_version,
        suite=first.suite,
        package_version=first.package_version,
        python_version=first.python_version,
        platform=first.platform,
        modules={**first.modules, **second.modules},
        config=first.config,
    )
    return BenchmarkRun(metadata, base.outcomes + supplement.outcomes)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--supplements-only",
        action="store_true",
        help="measure append-only supplemental feature coverage cases",
    )
    parser.add_argument(
        "--version",
        help="limit supplemental measurement to one four-part package version",
    )
    parser.add_argument(
        "--repository-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    try:
        if args.supplements_only:
            paths = run_release_supplement_benchmarks(
                args.wheel_dir,
                args.results_dir,
                args.python,
                args.repository_root,
                target_version=args.version,
            )
        else:
            if args.version is not None:
                parser.error("--version requires --supplements-only")
            paths = run_release_benchmarks(
                args.wheel_dir, args.results_dir, args.python, args.repository_root
            )
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as error:
        print(f"release wheel benchmarking failed: {error}", file=sys.stderr)
        return 2
    print("Release benchmark results:")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
