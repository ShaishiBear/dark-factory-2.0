"""Exact source subjects: byte spans in exact blobs of exact trees (LINE_LEVEL_CLAIMS 2, C03).

A claim about code has to name the code: which tree, which blob, which half-open byte
interval. Lines are presentation. This module is a pure reader/indexer: it never executes
candidate code, never follows a working-tree symlink and never asks the candidate process
to describe its own tree. Identity checks run in a fixed order (C03): allowlisted
repository-relative path, exact tree/blob retrieval, exact bytes hash, bounds and UTF-8
boundaries, parser identity, AST correspondence when the language is supported.

An AST fingerprint locates similar code; it grants no evidence reuse. A changed blob is
never treated as current because line numbers look unchanged; `map_change` records an
explicit old/new mapping or leaves the span unresolved. `trace_coverage` measures how much
of a change is explained by claim bindings; it is coverage of explanation, never proof.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import difflib
import hashlib
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping

from .canonical import sha256_bytes, sha256_value

SCHEMA = "dark-factory/code-subject"
SCHEMA_VERSION = "1.0"
MAX_BLOB = 2_000_000
MAX_FILES = 20_000
PYTHON_PARSER = "cpython-ast"
PYTHON_PARSER_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"
LANGUAGES: Mapping[str, str] = {".py": "python", ".pyi": "python", ".ts": "typescript", ".tsx": "typescript",
                                ".js": "javascript", ".cjs": "javascript", ".mjs": "javascript",
                                ".json": "json", ".md": "markdown", ".yml": "yaml", ".yaml": "yaml",
                                ".sh": "shell", ".toml": "toml", ".css": "css", ".html": "html"}
REASONS = ("unsafe_path", "unknown_path", "source_mismatch", "invalid_span", "invalid_utf8_boundary",
           "not_a_blob", "unsupported_encoding")


# ---------- records ----------

@dataclass(frozen=True)
class Refusal:
    reason_codes: tuple[str, ...]
    detail: str = ""
    status: str = "refused"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason_codes": sorted(self.reason_codes), "detail": self.detail}


@dataclass(frozen=True)
class SourceFile:
    path: str
    mode: str
    blob_oid: str
    raw: bytes

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.raw)


@dataclass(frozen=True)
class SourceIndex:
    repository_id: str
    tree_oid: str
    files: Mapping[str, SourceFile]
    gaps: tuple[dict[str, str], ...] = ()

    def digest(self) -> str:
        return sha256_value({"repository_id": self.repository_id, "tree_oid": self.tree_oid,
                             "files": {p: f.blob_oid for p, f in sorted(self.files.items())}})


@dataclass(frozen=True)
class CodeSubject:
    repository_id: str
    tree_oid: str
    path: str
    blob_oid: str
    source_bytes_sha256: str
    byte_start: int
    byte_end: int
    language: str
    parser_id: str | None
    parser_version: str | None
    symbol_path: str | None
    ast_kind: str | None
    ast_path: str | None
    ast_fingerprint: str | None
    context_refs: tuple[str, ...]
    dependency_coverage: str  # complete-for-profile | partial | unknown
    text: str
    insertion_anchor: bool = False
    status: str = "resolved"

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "repository_id": self.repository_id,
                "tree_oid": self.tree_oid, "path": self.path, "blob_oid": self.blob_oid,
                "source_bytes_sha256": self.source_bytes_sha256, "byte_start": self.byte_start,
                "byte_end": self.byte_end, "language": self.language, "parser_id": self.parser_id,
                "parser_version": self.parser_version, "symbol_path": self.symbol_path, "ast_kind": self.ast_kind,
                "ast_path": self.ast_path, "ast_fingerprint": self.ast_fingerprint,
                "context_refs": list(self.context_refs), "dependency_coverage": self.dependency_coverage,
                "insertion_anchor": self.insertion_anchor}

    def identity(self) -> str:
        """Exact tree/blob/span identity, namespaced (C01). Text is not hashed twice: the blob is."""
        return sha256_value({"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "repository_id": self.repository_id,
                             "tree_oid": self.tree_oid, "path": self.path, "blob_oid": self.blob_oid,
                             "byte_start": self.byte_start, "byte_end": self.byte_end})


# ---------- tree readers (trusted) ----------

def blob_oid(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


class MemoryTreeReader:
    """A tree supplied as bytes (tests, disposable fixtures). Blob identities use Git's own
    formula; the tree identity is a stable digest of the sorted entries, marked synthetic."""

    def __init__(self, files: Mapping[str, bytes], *, repository_id: str = "memory", modes: Mapping[str, str] | None = None):
        self.repository_id = repository_id
        self._files = {path: bytes(raw) for path, raw in files.items()}
        self._modes = dict(modes or {})
        self.tree_oid = "synthetic-" + sha256_value({p: blob_oid(r) for p, r in sorted(self._files.items())})[:40]

    def entries(self) -> Iterable[tuple[str, str, str, bytes]]:
        for path, raw in sorted(self._files.items()):
            yield path, self._modes.get(path, "100644"), blob_oid(raw), raw


class GitTreeReader:
    """Exact committed objects through `git ls-tree`/`git cat-file`, never the working tree."""

    def __init__(self, repo_root: Path, revision: str, *, repository_id: str, runner: Callable[..., Any] = subprocess.run):
        self.repo_root, self.revision, self.repository_id, self.runner = Path(repo_root), revision, repository_id, runner
        self.tree_oid = self._git("rev-parse", f"{revision}^{{tree}}").strip()

    def _git(self, *args: str, binary: bool = False) -> Any:
        proc = self.runner(["git", *args], cwd=self.repo_root, capture_output=True, timeout=120,
                           **({} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}))
        if proc.returncode:
            raise RuntimeError(f"git {' '.join(args)} failed: {str(proc.stderr)[-500:]}")
        return proc.stdout

    def entries(self) -> Iterable[tuple[str, str, str, bytes]]:
        listing = self._git("ls-tree", "-r", "-z", "--full-tree", self.tree_oid)
        for row in listing.split("\0"):
            if not row:
                continue
            meta, path = row.split("\t", 1)
            mode, kind, oid = meta.split(" ")
            if kind != "blob":
                yield path, mode, oid, b""
                continue
            raw = self._git("cat-file", "blob", oid, binary=True)
            yield path, mode, oid, raw


def index_source(tree_reader: Any, language_registry: Mapping[str, str] = LANGUAGES) -> SourceIndex:
    """Index every regular blob of an exact tree. Symlinks, submodules and oversized blobs are
    recorded as gaps, never followed or partially read."""
    files: dict[str, SourceFile] = {}
    gaps: list[dict[str, str]] = []
    count = 0
    for path, mode, oid, raw in tree_reader.entries():
        count += 1
        if count > MAX_FILES:
            raise ValueError("tree exceeds the index bound")
        if safe_path(path) is None:
            gaps.append({"path": path, "reason": "unsafe_path"})
            continue
        if mode == "120000":
            gaps.append({"path": path, "reason": "symlink_not_followed"})
            continue
        if mode == "160000":
            gaps.append({"path": path, "reason": "submodule_not_indexed"})
            continue
        if mode not in {"100644", "100755"}:
            gaps.append({"path": path, "reason": "not_a_regular_blob"})
            continue
        if len(raw) > MAX_BLOB:
            gaps.append({"path": path, "reason": "blob_over_bound"})
            continue
        files[path] = SourceFile(path, mode, oid, bytes(raw))
    return SourceIndex(str(tree_reader.repository_id), str(tree_reader.tree_oid), files, tuple(gaps))


def safe_path(path: Any) -> str | None:
    """Repository-relative, normalised, no traversal, no absolute or drive-qualified forms."""
    if not isinstance(path, str) or not path or len(path) > 512 or "\0" in path or "\\" in path or ":" in path:
        return None
    if path.startswith("/") or path.endswith("/"):
        return None
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if str(PurePosixPath(path)) != path:
        return None
    return path


def language_of(path: str, registry: Mapping[str, str] = LANGUAGES) -> str:
    return registry.get(PurePosixPath(path).suffix.lower(), "unknown")


# ---------- span resolution ----------

def resolve_span(source_index: SourceIndex, requested_span: Mapping[str, Any]) -> CodeSubject | Refusal:
    """C03 order: path -> blob -> bytes hash -> bounds/UTF-8 -> parser -> AST.

    `requested_span`: path, byte_start, byte_end, source_bytes_sha256 (the exact blob hash the
    requester believes it addresses; a mismatch is `source_mismatch`, never a silent
    re-target) and optionally `insertion_anchor` (the only case an empty interval is allowed).
    """
    if not isinstance(requested_span, Mapping):
        return Refusal(("invalid_span",), "span request must be a mapping")
    path = safe_path(requested_span.get("path"))
    if path is None:
        return Refusal(("unsafe_path",), "path must be repository-relative without traversal")
    source = source_index.files.get(path)
    if source is None:
        gap = next((g for g in source_index.gaps if g["path"] == path), None)
        return Refusal(("not_a_blob",) if gap else ("unknown_path",), gap["reason"] if gap else "path is not in the exact tree")
    expected = requested_span.get("source_bytes_sha256")
    if not isinstance(expected, str) or expected != source.sha256:
        return Refusal(("source_mismatch",), "requested source bytes differ from the exact blob")
    start, end = requested_span.get("byte_start"), requested_span.get("byte_end")
    anchor = requested_span.get("insertion_anchor", False)
    if anchor is not True and anchor is not False:
        return Refusal(("invalid_span",), "insertion_anchor must be a boolean")
    if type(start) is not int or type(end) is not int or start < 0 or end > len(source.raw) or start > end:
        return Refusal(("invalid_span",), "byte offsets must be integers within the blob, half-open")
    if start == end and not anchor:
        return Refusal(("invalid_span",), "an empty interval is only an explicitly typed insertion anchor")
    # Strict decoding is the one boundary authority: a span that starts on a continuation byte
    # or ends inside a sequence is not valid UTF-8 on its own, so it is refused, never repaired.
    try:
        text = source.raw[start:end].decode("utf-8")
    except UnicodeDecodeError:
        return Refusal(("invalid_utf8_boundary",), "span splits a multi-byte UTF-8 sequence or is not UTF-8")
    language = language_of(path)
    parser_id = parser_version = symbol_path = ast_kind = ast_path = fingerprint = None
    context: tuple[str, ...] = ()
    coverage = "unknown"
    if language == "python":
        parser_id, parser_version = PYTHON_PARSER, PYTHON_PARSER_VERSION
        analysis = _python_correspondence(source.raw, start, end)
        if analysis is None:
            coverage = "unknown"  # unparsable source: exact bytes stand, analysis does not
        else:
            symbol_path, ast_kind, ast_path, fingerprint, context, exact = analysis
            coverage = "complete-for-profile" if exact else "partial"
    return CodeSubject(source_index.repository_id, source_index.tree_oid, path, source.blob_oid, source.sha256,
                       start, end, language, parser_id, parser_version, symbol_path, ast_kind, ast_path,
                       fingerprint, context, coverage, text, bool(anchor))


def _line_starts(raw: bytes) -> list[int]:
    starts = [0]
    for index, byte in enumerate(raw):
        if byte == 0x0A:
            starts.append(index + 1)
    return starts


def _python_correspondence(raw: bytes, start: int, end: int):
    """The smallest AST node whose exact byte interval equals the span (exact), else the
    smallest enclosing node (partial). `ast` reports UTF-8 byte columns, which is what makes
    byte-exact correspondence possible without re-encoding."""
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (SyntaxError, ValueError, UnicodeDecodeError):
        return None
    starts = _line_starts(raw)

    def offset(line: int, col: int) -> int:
        return starts[line - 1] + col

    best_exact = None
    enclosing: list[tuple[int, ast.AST, str]] = []
    scope: list[str] = []

    def visit(node: ast.AST, path: list[str]) -> None:
        nonlocal best_exact
        name = getattr(node, "name", None)
        here = path + ([name] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and name else [])
        if hasattr(node, "lineno") and getattr(node, "end_lineno", None) is not None:
            s, e = offset(node.lineno, node.col_offset), offset(node.end_lineno, node.end_col_offset)
            if s == start and e == end and best_exact is None:
                best_exact = (node, here)
            if s <= start and end <= e:
                enclosing.append((e - s, node, ".".join(here)))
        for child in ast.iter_child_nodes(node):
            visit(child, here)

    visit(tree, scope)
    if best_exact is not None:
        node, here = best_exact
        return ".".join(here) or None, type(node).__name__, ".".join(here + [type(node).__name__]), _fingerprint(node), tuple(sorted({p for _, _, p in enclosing if p})), True
    if not enclosing:
        return None, None, None, None, (), False
    _, node, here = min(enclosing, key=lambda item: item[0])
    return here or None, None, ".".join(filter(None, [here, type(node).__name__])), None, tuple(sorted({p for _, _, p in enclosing if p})), False


def _fingerprint(node: ast.AST) -> str:
    """Structure without identifiers/constants: a similarity aid, never an identity."""
    def walk(n: ast.AST) -> Any:
        return [type(n).__name__, [walk(c) for c in ast.iter_child_nodes(n)]]
    return sha256_value(walk(node))


# ---------- change mapping ----------

@dataclass(frozen=True)
class SpanMapping:
    old_span: tuple[int, int]
    new_span: tuple[int, int] | None
    status: str  # identical | mapped | changed | deleted | unresolved
    detail: str = ""


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str  # unchanged | modified | added | deleted
    old_blob: str | None
    new_blob: str | None
    # Byte intervals in the NEW blob that differ from the old one (half-open), and their
    # counterparts in the old blob. Line-granular: the pinned diff adapter is difflib on lines.
    changed_new: tuple[tuple[int, int], ...] = ()
    changed_old: tuple[tuple[int, int], ...] = ()
    equal_blocks: tuple[tuple[int, int, int], ...] = ()  # (old_start, new_start, length) in bytes


@dataclass(frozen=True)
class ChangeMap:
    old_index_digest: str
    new_index_digest: str
    files: Mapping[str, FileChange]
    adapter: str = "difflib-lines-v1"

    def map_span(self, path: str, old_span: tuple[int, int]) -> SpanMapping:
        change = self.files.get(path)
        if change is None:
            return SpanMapping(old_span, None, "unresolved", "path not in change map")
        if change.status == "unchanged":
            return SpanMapping(old_span, old_span, "identical")
        if change.status == "deleted":
            return SpanMapping(old_span, None, "deleted")
        if change.status == "added":
            return SpanMapping(old_span, None, "unresolved", "old span in an added file")
        s, e = old_span
        for old_start, new_start, length in change.equal_blocks:
            if old_start <= s and e <= old_start + length:
                shift = new_start - old_start
                return SpanMapping(old_span, (s + shift, e + shift), "mapped")
        for o_s, o_e in change.changed_old:
            if s < o_e and o_s < e:
                return SpanMapping(old_span, None, "changed", "span intersects a changed hunk; needs a fresh subject")
        return SpanMapping(old_span, None, "unresolved", "span crosses block boundaries")


def _lines(raw: bytes) -> list[bytes]:
    return raw.splitlines(keepends=True)


def _line_offsets(lines: list[bytes]) -> list[int]:
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets


def map_change(old_index: SourceIndex, new_index: SourceIndex, exact_diff: Iterable[str] | None = None) -> ChangeMap:
    """Exact blob match first; otherwise explicit old/new byte intervals from the pinned line
    diff adapter. `exact_diff` optionally restricts the paths considered (an observed diff
    listing); a path it names that neither tree has is recorded as unresolved by callers."""
    paths = set(old_index.files) | set(new_index.files)
    if exact_diff is not None:
        paths &= set(exact_diff)
    files: dict[str, FileChange] = {}
    for path in sorted(paths):
        old, new = old_index.files.get(path), new_index.files.get(path)
        if old is None:
            files[path] = FileChange(path, "added", None, new.blob_oid, ((0, len(new.raw)),) if new.raw else (), ())
            continue
        if new is None:
            files[path] = FileChange(path, "deleted", old.blob_oid, None, (), ((0, len(old.raw)),) if old.raw else ())
            continue
        if old.blob_oid == new.blob_oid:
            files[path] = FileChange(path, "unchanged", old.blob_oid, new.blob_oid, (), (), ((0, 0, len(old.raw)),))
            continue
        old_lines, new_lines = _lines(old.raw), _lines(new.raw)
        old_off, new_off = _line_offsets(old_lines), _line_offsets(new_lines)
        matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
        changed_new, changed_old, equal = [], [], []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                equal.append((old_off[i1], new_off[j1], old_off[i2] - old_off[i1]))
            else:
                if j2 > j1:
                    changed_new.append((new_off[j1], new_off[j2]))
                if i2 > i1:
                    changed_old.append((old_off[i1], old_off[i2]))
                if j2 == j1 and i2 > i1:
                    changed_new.append((new_off[j1], new_off[j1]))  # pure deletion: an insertion point in new
        files[path] = FileChange(path, "modified", old.blob_oid, new.blob_oid, tuple(changed_new), tuple(changed_old), tuple(equal))
    return ChangeMap(old_index.digest(), new_index.digest(), files)


# ---------- trace coverage ----------

@dataclass(frozen=True)
class CoverageReport:
    changed_bytes: int
    observed_bytes: int
    proposed_only_bytes: int
    unresolved_bytes: int
    unresolved_intervals: tuple[dict[str, Any], ...]
    analysis_gaps: tuple[dict[str, Any], ...]
    excluded_bytes: int
    per_file: Mapping[str, dict[str, int]] = field(default_factory=dict)

    @property
    def observed_ratio(self) -> float | None:
        return None if self.changed_bytes == 0 else self.observed_bytes / self.changed_bytes

    def to_dict(self) -> dict[str, Any]:
        return {"changed_bytes": self.changed_bytes, "observed_bytes": self.observed_bytes,
                "proposed_only_bytes": self.proposed_only_bytes, "unresolved_bytes": self.unresolved_bytes,
                "excluded_bytes": self.excluded_bytes, "observed_ratio": self.observed_ratio,
                "unresolved_intervals": list(self.unresolved_intervals), "analysis_gaps": list(self.analysis_gaps),
                "per_file": dict(self.per_file), "meaning": "coverage of explanation, not proof of correctness"}


def _union(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for s, e in sorted(intervals):
        if s >= e:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _measure(intervals: list[tuple[int, int]]) -> int:
    return sum(e - s for s, e in intervals)


def _intersect(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = []
    for s1, e1 in a:
        for s2, e2 in b:
            s, e = max(s1, s2), min(e1, e2)
            if s < e:
                out.append((s, e))
    return _union(out)


def _subtract(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = []
    for s, e in a:
        cursor = s
        for bs, be in b:
            if be <= cursor or bs >= e:
                continue
            if bs > cursor:
                out.append((cursor, bs))
            cursor = max(cursor, be)
        if cursor < e:
            out.append((cursor, e))
    return _union(out)


def _non_executable_python(raw: bytes) -> list[tuple[int, int]] | None:
    """Byte intervals of blank and comment-only lines, from the registered parser's own token
    stream. None when the source cannot be tokenised (then nothing is excluded: unknown)."""
    import io
    import tokenize
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(raw).readline))
    except (tokenize.TokenError, SyntaxError, UnicodeDecodeError):
        return None
    starts = _line_starts(raw)
    code_lines = set()
    for tok in tokens:
        if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                        tokenize.ENCODING, tokenize.ENDMARKER):
            continue
        for line in range(tok.start[0], tok.end[0] + 1):
            code_lines.add(line)
    excluded = []
    total_lines = len(starts) if raw.endswith(b"\n") else len(starts)
    for line in range(1, total_lines):
        if line not in code_lines:
            excluded.append((starts[line - 1], starts[line] if line < len(starts) else len(raw)))
    return _union(excluded)


def trace_coverage(change_map: ChangeMap, new_index: SourceIndex, proposed_bindings: Iterable[Mapping[str, Any]],
                   observed_bindings: Iterable[Mapping[str, Any]]) -> CoverageReport:
    """Denominator: changed executable bytes in the new tree. Overlapping bindings are unioned
    so double links do not inflate coverage. Comments/blank lines are excluded only where a
    registered parser classifies them; unsupported syntax stays in the denominator as unknown.
    A binding is {path, byte_start, byte_end}; `observed` bindings were verified by an
    observer, `proposed` ones came from a model or author and count separately."""
    def by_path(bindings: Iterable[Mapping[str, Any]]) -> dict[str, list[tuple[int, int]]]:
        out: dict[str, list[tuple[int, int]]] = {}
        for b in bindings:
            if type(b.get("byte_start")) is int and type(b.get("byte_end")) is int and isinstance(b.get("path"), str):
                out.setdefault(b["path"], []).append((b["byte_start"], b["byte_end"]))
        # Grouped, not yet unioned: `_intersect` is the one place overlaps collapse, so a
        # double link is counted once exactly there and nowhere else.
        return {p: sorted(v) for p, v in out.items()}

    proposed, observed = by_path(proposed_bindings), by_path(observed_bindings)
    changed_total = observed_total = proposed_only_total = unresolved_total = excluded_total = 0
    unresolved: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    per_file: dict[str, dict[str, int]] = {}
    for path, change in sorted(change_map.files.items()):
        if change.status in {"unchanged", "deleted"} or not change.changed_new:
            continue
        source = new_index.files.get(path)
        if source is None:
            gaps.append({"path": path, "reason": "new blob unavailable"})
            continue
        changed = _union(change.changed_new)
        excluded: list[tuple[int, int]] = []
        if language_of(path) == "python":
            classified = _non_executable_python(source.raw)
            if classified is None:
                gaps.append({"path": path, "reason": "python source not tokenisable; nothing excluded"})
            else:
                excluded = _intersect(changed, classified)
        else:
            gaps.append({"path": path, "reason": f"no registered classifier for {language_of(path)}; nothing excluded"})
        executable = _subtract(changed, excluded)
        obs = _intersect(executable, observed.get(path, []))
        prop_only = _subtract(_intersect(executable, proposed.get(path, [])), obs)
        rest = _subtract(_subtract(executable, obs), prop_only)
        for s, e in rest:
            unresolved.append({"path": path, "byte_start": s, "byte_end": e})
        counts = {"changed": _measure(executable), "observed": _measure(obs), "proposed_only": _measure(prop_only),
                  "unresolved": _measure(rest), "excluded": _measure(excluded)}
        per_file[path] = counts
        changed_total += counts["changed"]
        observed_total += counts["observed"]
        proposed_only_total += counts["proposed_only"]
        unresolved_total += counts["unresolved"]
        excluded_total += counts["excluded"]
    return CoverageReport(changed_total, observed_total, proposed_only_total, unresolved_total,
                          tuple(unresolved), tuple(gaps), excluded_total, per_file)
