#!/usr/bin/env python3
"""M12 发布清单生成脚本（TASK-0161）。

生成 m12_release.json，包含：
  - 核心文件 SHA-256 哈希
  - 测试通过数统计
  - 里程碑完成状态
  - 生成时间戳

用法：
  python scripts/generate_m12_release.py
  python scripts/generate_m12_release.py --output docs/50_release/m12_release.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# M12 核心文件清单
# ---------------------------------------------------------------------------

M12_CORE_FILES = [
    # M12-A ToolBroker
    "host_brain/tools/broker.py",
    "host_brain/tools/catalog.py",
    "host_brain/tools/selector.py",
    "host_brain/tools/mcp_adapter.py",
    "host_brain/app/tool_routes.py",
    # M12-B ContextCompressor
    "host_brain/memory/context_compressor.py",
    # M12-C Reranker
    "host_brain/retrieval/reranker.py",
    "host_brain/retrieval/evidence_pack.py",
    # M12-D SkillHotLoader
    "host_brain/skills/hot_loader.py",
    # 错误码
    "host_brain/core/errors.py",
    # Migration
    "host_brain/audit/migrations/0017_canon_proposal_id_unique.sql",
]

M12_TEST_FILES = [
    "tests/unit/test_tool_broker.py",
    "tests/unit/test_reranker_service.py",
    "tests/unit/test_context_compressor.py",
    "tests/unit/test_skill_hot_loader.py",
    "tests/integration/test_tool_routes_api.py",
]

M12_MILESTONES = [
    {
        "id": "M12-A",
        "name": "ToolBroker 单入口 + tool_visibility_hash",
        "fr_refs": ["FR-TOOL-001", "FR-TOOL-002", "FR-TOOL-003"],
        "status": "DONE",
        "tasks": ["TASK-0155", "TASK-0156"],
    },
    {
        "id": "M12-B",
        "name": "多轮对话上下文压缩（ContextCompressor）",
        "fr_refs": ["FR-MEM-005"],
        "status": "DONE",
        "tasks": ["TASK-0157"],
    },
    {
        "id": "M12-C",
        "name": "Reranker 服务强化 + EvidencePack",
        "fr_refs": ["FR-MEM-004"],
        "status": "DONE",
        "tasks": ["TASK-0158"],
    },
    {
        "id": "M12-D",
        "name": "Skill 热加载 + 版本管理",
        "fr_refs": ["FR-SKILL-001", "FR-SKILL-002"],
        "status": "DONE",
        "tasks": ["TASK-0159"],
    },
    {
        "id": "M12-E",
        "name": "错误码补全 + Migration 0017",
        "fr_refs": [],
        "status": "DONE",
        "tasks": ["TASK-0160"],
    },
]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def sha256_file(path: str) -> str:
    """计算文件 SHA-256 哈希。"""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return "FILE_NOT_FOUND"


def run_pytest_count(test_files: list[str]) -> dict:
    """运行指定测试文件，返回通过/失败/跳过数。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *test_files, "-q", "--tb=no", "--no-header"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout + result.stderr
        # 解析最后一行：例如 "17 passed, 2 skipped in 0.34s"
        for line in reversed(output.splitlines()):
            line = line.strip()
            if "passed" in line or "failed" in line or "error" in line:
                passed = _extract_count(line, "passed")
                failed = _extract_count(line, "failed")
                skipped = _extract_count(line, "skipped")
                errors = _extract_count(line, "error")
                return {
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "errors": errors,
                    "success": failed == 0 and errors == 0,
                    "summary": line,
                }
        return {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "success": False, "summary": "no output"}
    except Exception as exc:
        return {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "success": False, "summary": str(exc)}


def _extract_count(line: str, keyword: str) -> int:
    """从 pytest 输出行中提取数字，例如 '17 passed' → 17。"""
    import re
    match = re.search(rf"(\d+)\s+{keyword}", line)
    return int(match.group(1)) if match else 0


def get_git_commit() -> str:
    """获取当前 git commit hash。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def generate_release_manifest(workspace: str, output_path: str) -> dict:
    """生成 M12 发布清单。"""
    ts_ms = int(time.time() * 1000)
    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 1) 核心文件哈希
    file_hashes: dict[str, str] = {}
    missing_files: list[str] = []
    for rel_path in M12_CORE_FILES:
        abs_path = os.path.join(workspace, rel_path)
        digest = sha256_file(abs_path)
        file_hashes[rel_path] = digest
        if digest == "FILE_NOT_FOUND":
            missing_files.append(rel_path)

    # 2) 测试统计
    test_abs = [os.path.join(workspace, f) for f in M12_TEST_FILES]
    existing_tests = [p for p in test_abs if os.path.isfile(p)]
    test_results = run_pytest_count(existing_tests)

    # 3) 全量测试
    all_tests_result = run_pytest_count([os.path.join(workspace, "tests")])

    # 4) 构建清单
    manifest = {
        "schema_version": "Natsume.release.v1",
        "milestone": "M12",
        "release_name": "ToolBroker + ContextCompressor + Reranker + SkillHotLoader",
        "git_commit": get_git_commit(),
        "generated_at": ts_iso,
        "ts_ms": ts_ms,
        "milestones": M12_MILESTONES,
        "core_files": {
            "total": len(M12_CORE_FILES),
            "missing": len(missing_files),
            "missing_files": missing_files,
            "hashes": file_hashes,
        },
        "test_results": {
            "m12_tests": {
                "files": M12_TEST_FILES,
                **test_results,
            },
            "all_tests": {
                **all_tests_result,
            },
        },
        "quality_gates": {
            "all_core_files_present": len(missing_files) == 0,
            "m12_tests_pass": test_results["success"],
            "all_tests_pass": all_tests_result["success"],
            "zero_regressions": all_tests_result["failed"] == 0 and all_tests_result["errors"] == 0,
        },
        "release_ready": (
            len(missing_files) == 0
            and test_results["success"]
            and all_tests_result["success"]
        ),
    }
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 M12 发布清单")
    parser.add_argument(
        "--output",
        default="docs/50_release/m12_release.json",
        help="输出路径（默认: docs/50_release/m12_release.json）",
    )
    parser.add_argument(
        "--workspace",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="项目根目录（默认: 脚本所在目录的上级）",
    )
    args = parser.parse_args()

    workspace = args.workspace
    output_path = os.path.join(workspace, args.output)

    print(f"[M12 Release] 生成发布清单...")
    print(f"  workspace: {workspace}")
    print(f"  output:    {output_path}")

    manifest = generate_release_manifest(workspace, output_path)

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n[M12 Release] 清单已生成: {output_path}")
    print(f"  核心文件: {manifest['core_files']['total']} 个，缺失: {manifest['core_files']['missing']} 个")
    print(f"  M12 测试: {manifest['test_results']['m12_tests']['passed']} passed")
    print(f"  全量测试: {manifest['test_results']['all_tests']['passed']} passed")
    print(f"  发布就绪: {'[YES]' if manifest['release_ready'] else '[NO]'}")

    if not manifest["release_ready"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
