from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .domain import MemoryError, RULE_ID, json_bytes
from .repository import RuleStore, resolve_root, write_bytes
from .search import SearchRequest
from .service import MemoryService


def rule_id(value: str) -> str:
    if not RULE_ID.fullmatch(value):
        raise argparse.ArgumentTypeError(f"无效的规则 ID：{value}")
    return value


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Super Memory：检索、审核后的规则操作和存储维护。")
    command.add_argument("--root", type=Path, help="规则数据目录；优先于 SUPER_MEMORY_HOME")
    actions = command.add_subparsers(dest="command", required=True)
    for name, help_text in (("info", "显示实际数据目录"), ("init", "初始化规则库"), ("validate", "校验规则与配置"), ("reindex", "重建派生索引"), ("list", "读取规则和作用域快照"), ("scopes", "查看项目、模块和功能目录")):
        actions.add_parser(name, help=help_text)
    get = actions.add_parser("get", help="读取完整规则及版本号")
    get.add_argument("id")
    search = argparse.ArgumentParser(add_help=False)
    search.add_argument("--query", required=True)
    search.add_argument("--project")
    for option in ("module", "feature", "path", "language", "technology"):
        search.add_argument("--" + option, action="append", default=[])
    search.add_argument("--limit", type=int, default=12)
    search.add_argument("--max-chars", type=int, default=32000)
    search.add_argument("--full", action="store_true", help="返回完整检索结果及诊断字段")
    search.add_argument("--include-candidates", action="store_true")
    actions.add_parser("search", parents=[search], help="检索适用条款")
    review = actions.add_parser("review", parents=[search], help="按实际改动检索，合并此前命中的待审条款")
    review.add_argument("--before", type=rule_id, nargs="+", action="extend", default=[],
                        metavar="RULE-ID", help="首轮或上一次复查清单中的规则 ID；首次检索无命中时省略")
    for name in ("create", "update", "delete"):
        action = actions.add_parser(name)
        if name != "create":
            action.add_argument("id")
            action.add_argument("--revision", required=True, help="读取时返回的内容版本号")
        if name != "delete":
            action.add_argument("--input", default="-", help="完整规则 JSON 文件；- 表示标准输入")
        action.add_argument("--reason", required=True, help="包含中文的操作原因")
    exporter = actions.add_parser("export")
    exporter.add_argument("--output", type=Path, help="直接写入 UTF-8 JSON 文件；省略则输出快照")
    importer = actions.add_parser("import")
    importer.add_argument("--input", default="-")
    importer.add_argument("--conflict", choices=("skip", "replace"), default="skip")
    importer.add_argument("--reason", required=True)
    return command


def read_input(value: str):
    raw = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    return json.loads(raw)


def execute(args: argparse.Namespace, service: MemoryService):
    if args.command == "init":
        return service.store.initialize()
    if args.command in ("info", "list", "scopes", "validate", "reindex"):
        return getattr(service, args.command)()
    if args.command == "get":
        return service.get(args.id)
    if args.command in ("search", "review"):
        result = service.search(SearchRequest(
            query=args.query, project=args.project, modules=tuple(args.module),
            languages=tuple(args.language), technologies=tuple(args.technology),
            limit=args.limit, max_chars=args.max_chars, include_candidates=args.include_candidates,
            features=tuple(args.feature), paths=tuple(args.path), compact=not args.full,
        ))
        if args.command == "review":
            found = [rule["id"] for rule in result["rules"]]
            return {"review_ids": list(dict.fromkeys([*args.before, *found])), "search": result}
        return result
    if args.command == "create":
        return service.create(read_input(args.input), args.reason)
    if args.command == "update":
        return service.update(args.id, read_input(args.input), args.revision, args.reason)
    if args.command == "delete":
        return service.delete(args.id, args.revision, args.reason)
    if args.command == "import":
        return service.import_bundle(read_input(args.input), args.conflict, args.reason)
    bundle = service.export()
    if args.output:
        write_bytes(args.output, json_bytes(bundle))
        return {"output": str(args.output.resolve()), "rules": len(bundle["rules"])}
    return bundle


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result = execute(args, MemoryService(RuleStore(resolve_root(args.root))))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except MemoryError as exc:
        error = {"code": exc.code, "message": str(exc)}
    except (OSError, ValueError) as exc:
        error = {"code": "invalid_input", "message": f"读取或处理输入失败：{exc}"}
    print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
    return 1
