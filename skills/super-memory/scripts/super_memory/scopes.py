from __future__ import annotations

from dataclasses import replace
from fnmatch import fnmatchcase

from .domain import MemoryError, Record, normalize_scopes
from .search import SearchRequest, normalize


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
    if not request.features and not request.paths:
        return request, []
    project = next((item for item in scopes if normalize(item["id"]) == normalize(request.project)), None)
    module = next((item for item in (project or {}).get("modules", []) if normalize(item["id"]) == normalize(request.modules[0])), None)
    features = (module or {}).get("features", [])
    selected, warnings = [], []
    for name in request.features:
        found = next((item for item in features if normalize(name) in {normalize(item["id"]), *(normalize(alias) for alias in item["aliases"])}), None)
        if found is None:
            raise MemoryError("validation_error", f"未找到功能 {name}，请运行 scopes 查看当前项目和模块的功能目录。")
        selected.append(found["id"])
    for path in request.paths:
        relative = path.replace("\\", "/").removeprefix("./")
        if relative.startswith("/") or ":" in relative or ".." in relative.split("/"):
            raise MemoryError("validation_error", "--path 必须是项目内的相对文件路径。")
        matches = [item["id"] for item in features if any(fnmatchcase(normalize(relative), normalize(pattern.replace("\\", "/"))) for pattern in item["paths"])]
        selected.extend(matches)
        if not matches:
            warnings.append(f"路径 {path} 未匹配功能目录；请核对 scopes，必要时显式指定 --feature。")
    return replace(request, features=tuple(dict.fromkeys(selected))), warnings
