from __future__ import annotations

from dataclasses import replace
from fnmatch import fnmatchcase

from .domain import CHINESE, MemoryError, Record, identifier_key, normalize_scopes
from .search import SearchRequest, identifier_atoms, normalize


def catalog(configured: list, records: list[Record]) -> list:
    """Combine configured routing metadata with scopes actually used by rules."""
    scopes = normalize_scopes(configured)
    def entry(items, identifier, rule):
        found = next((item for item in items if normalize(item["id"]) == normalize(identifier)), None)
        if found is None:
            found = {"id": identifier, "label": identifier, "created_at": rule["lifecycle"]["created_at"], "updated_at": rule["lifecycle"].get("updated_at")}
            items.append(found)
        return found
    for record in records:
        rule, scope = record.rule, record.rule["scope"]
        if not scope["projects"]:
            continue
        project = entry(scopes, scope["projects"][0], rule)
        if not scope["modules"]:
            continue
        module = entry(project.setdefault("modules", []), scope["modules"][0], rule)
        for feature in scope.get("features", []):
            entry(module.setdefault("features", []), feature, rule)
    return normalize_scopes(scopes)


def resolve_features(request: SearchRequest, scopes: list) -> tuple[SearchRequest, list[str]]:
    if not request.project:
        return request, []
    warnings = []
    project = next((item for item in scopes if normalize(item["id"]) == normalize(request.project)), None)
    if project is None:
        warnings.append(f"未找到项目作用域 {request.project}，请运行 scopes 核对实际 ID。")
    known_modules = (project or {}).get("modules", [])
    for name in request.modules:
        if not any(normalize(item["id"]) == normalize(name) for item in known_modules):
            warnings.append(f"未找到服务模块作用域 {name}，请运行 scopes 核对实际 ID。")
    if len(request.modules) != 1:
        return request, warnings
    module = next((item for item in (project or {}).get("modules", []) if normalize(item["id"]) == normalize(request.modules[0])), None)
    features = (module or {}).get("features", [])
    selected = []
    query_keys = identifier_atoms(request.query)
    normalized_query = normalize(request.query)

    def same_identifier(left: str, right: str) -> bool:
        return normalize(left) == normalize(right) or identifier_key(left) == identifier_key(right)

    for name in request.features:
        found = next((item for item in features if any(same_identifier(name, value) for value in (item["id"], *item["aliases"]))), None)
        if found is None:
            raise MemoryError("validation_error", f"未找到功能 {name}，请运行 scopes 查看当前项目和模块的功能目录。")
        selected.append(found["id"])
    for path in request.paths:
        relative = path.replace("\\", "/").removeprefix("./")
        if relative.startswith("/") or ":" in relative or ".." in relative.split("/"):
            raise MemoryError("validation_error", "--path 必须是项目内的相对文件路径。")
        segments = relative.split("/")
        if segments and normalize(segments[0]) == normalize(request.project):
            relative = "/".join(segments[1:])
        matches = [item["id"] for item in features if any(fnmatchcase(normalize(relative), normalize(pattern.replace("\\", "/"))) for pattern in item["paths"])]
        selected.extend(matches)
        if not matches:
            warnings.append(f"路径 {path} 未匹配功能目录；请核对 scopes，必要时显式指定 --feature。")
    if not request.features and not selected:
        spans: list[tuple[str, int, int]] = []
        exact_identifiers = []
        for item in features:
            for value in (item["id"], *item["aliases"]):
                normalized = normalize(value)
                if CHINESE.search(normalized):
                    start = normalized_query.find(normalized)
                    while start >= 0:
                        spans.append((item["id"], start, start + len(normalized)))
                        start = normalized_query.find(normalized, start + 1)
                elif identifier_key(value) in query_keys and item["id"] not in exact_identifiers:
                    exact_identifiers.append(item["id"])
        selected.extend(exact_identifiers)
        for feature_id, start, end in spans:
            shadowed = any(other_id != feature_id and other_start <= start and end <= other_end and other_end - other_start > end - start for other_id, other_start, other_end in spans)
            if not shadowed:
                selected.append(feature_id)
    return replace(request, features=tuple(dict.fromkeys(selected))), warnings
