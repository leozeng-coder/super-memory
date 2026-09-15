from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "super-memory" / "scripts"
sys.path.insert(0, str(SCRIPTS))

def sample_rule(status: str = "active") -> dict[str, object]:
        return {
            "id": "RULE-PYTHON-TEST",
            "type": "Rule",
            "title": "运行 Python 单元测试",
            "status": status,
            "priority": "high",
            "confidence": 0.9,
            "scope": {
                "level": "global",
                "languages": ["Python"],
                "technologies": ["unittest"],
                "projects": [],
                "modules": [],
            },
            "trigger": {
                "description": "修改 Python 代码后需要验证行为",
                "keywords": ["python", "unittest"],
            },
            "instruction": {
                "do": "运行相关单元测试。",
                "avoid": "不要跳过失败用例。",
            },
            "rationale": {
                "problem": "未验证的修改可能引入回归。",
                "root_cause": "缺少自动化验证。",
            },
            "exceptions": [],
            "relations": {
                "conflicts_with": [],
                "supersedes": [],
                "replacement": None,
            },
            "evidence": [
                {
                    "id": "EVIDENCE-1",
                    "source": "user-instruction",
                    "summary": "用户要求用单元测试验证。",
                    "project": None,
                    "commit": None,
                    "files": [],
                    "observed_at": "2026-08-26T00:00:00+00:00",
                }
            ],
            "lifecycle": {
                "created_at": "2026-08-26T00:00:00+00:00",
                "updated_at": None,
                "last_validated_at": None,
                "review_after": None,
                "success_count": 0,
                "violation_count": 0,
                "exception_count": 0,
            },
            "tags": ["python", "测试"],
        }
