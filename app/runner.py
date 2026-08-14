from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator

from app.config import ROOT, Settings
from app.graph import JsonObject, canonical_json, load_object
from app.prompt import Prompt, PromptCatalog

ALLOWED_EXECUTABLES = frozenset({"codex", "git", "dot", "npm", "uv"})
VALIDATION_COMMANDS = (
    ("uv", "sync", "--frozen"),
    ("uv", "run", "coverage", "run", "-m", "pytest"),
    ("uv", "run", "coverage", "json", "-o", "coverage.json"),
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "ruff", "format", "--check", "."),
    ("npm", "--prefix", "prototype", "ci"),
    ("npm", "--prefix", "prototype", "run", "build"),
    ("npm", "--prefix", "prototype", "run", "test:sites"),
)
SEMANTIC_VALIDATOR_PATH = "app/graph.py"
VALIDATION_COMMANDS_PATH = "app/runner.py"


class RunnerError(RuntimeError):
    """固定本地工具调用失败。"""


def _is_test_path(path: str) -> bool:
    name = Path(path).name
    return (
        path.startswith("tests/")
        or "/tests/" in path
        or "/__tests__/" in path
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


def _constant_value(source: str, name: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "<语法错误>"
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.dump(node.value, include_attributes=False)
    return "<缺失>"


def _pytest_node_ids(output: str, path: str) -> tuple[str, ...]:
    prefix = f"{path}::"
    return tuple(line.strip() for line in output.splitlines() if line.strip().startswith(prefix))


class Runner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.agent_result_schema = load_object(settings.agent_result_schema_path)
        self.prompts = PromptCatalog(settings.prompt_dir)

    async def dependency_status(self) -> JsonObject:
        checks = {
            "codex": ["codex", "login", "status"],
            "git": ["git", "--version"],
            "dot": ["dot", "-V"],
            "npm": ["npm", "--version"],
            "uv": ["uv", "--version"],
        }

        async def check(name: str, arguments: list[str]) -> tuple[str, str]:
            try:
                output = await self._run(arguments, cwd=self.settings.repository, timeout=15)
                return name, output.strip() or "可用"
            except RunnerError as error:
                return name, f"不可用：{error}"

        values = await asyncio.gather(
            *(check(name, arguments) for name, arguments in checks.items())
        )
        return {name: value for name, value in values}

    def prompt(self, task: str, context: JsonObject) -> Prompt:
        return self.prompts.render(task, context)

    async def agent(
        self,
        task: str,
        prompt: Prompt,
        agent_run_id: str,
        implementation_run_id: str | None = None,
        worktree: Path | None = None,
    ) -> JsonObject:
        if task == "IMPLEMENTATION":
            await asyncio.to_thread(self.settings.worktree_root.mkdir, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="sdbp-review-agent-") as temporary:
                return await self._run_agent(
                    prompt,
                    Path(temporary),
                    "workspace-write",
                    agent_run_id,
                    implementation_run_id,
                    3600,
                    [self.settings.worktree_root],
                )
        elif task == "SEMANTIC_REVIEW":
            if worktree is None:
                raise RunnerError("语义 Review 缺少开发 worktree")
            cwd = worktree
            sandbox = "read-only"
            timeout = 1800
        else:
            cwd = self.settings.repository
            sandbox = "read-only"
            timeout = 900
        return await self._run_agent(
            prompt,
            cwd,
            sandbox,
            agent_run_id,
            implementation_run_id,
            timeout,
        )

    async def _run_agent(
        self,
        prompt: Prompt,
        cwd: Path,
        sandbox: str,
        agent_run_id: str,
        implementation_run_id: str | None,
        timeout: int,
        writable_directories: list[Path] | None = None,
    ) -> JsonObject:
        result = await self._codex(
            prompt.text,
            cwd,
            sandbox,
            agent_run_id,
            implementation_run_id,
            timeout=timeout,
            writable_directories=writable_directories,
        )
        self._validate_result(result, self.agent_result_schema, "Agent 结果")
        return result

    async def repository_snapshot(self) -> JsonObject:
        repository = self.settings.repository
        commit_sha, tree_hash, paths, status = await asyncio.gather(
            self._run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                cwd=repository,
                timeout=30,
            ),
            self._run(
                ["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"],
                cwd=repository,
                timeout=30,
            ),
            self._run(["git", "-C", str(repository), "ls-files"], cwd=repository, timeout=30),
            self._run(
                ["git", "-C", str(repository), "status", "--porcelain"],
                cwd=repository,
                timeout=30,
            ),
        )
        if status.strip():
            raise RunnerError("目标仓库存在未提交变化，无法建立固定代码快照")
        return {
            "commitSha": commit_sha.strip(),
            "treeHash": tree_hash.strip(),
            "paths": [path for path in paths.splitlines() if path],
        }

    async def change(self, worktree: Path) -> JsonObject:
        status, diff = await asyncio.gather(
            self._run(
                ["git", "-C", str(worktree), "status", "--short"],
                cwd=worktree,
                timeout=30,
            ),
            self._run(
                ["git", "-C", str(worktree), "diff", "--no-ext-diff", "--unified=40"],
                cwd=worktree,
                timeout=120,
            ),
        )
        return {"status": status, "diff": diff[-200_000:]}

    async def create_worktree(self, run_id: str) -> Path:
        repository = self.settings.repository.resolve()
        root_text = await self._run(
            ["git", "-C", str(repository), "rev-parse", "--show-toplevel"],
            cwd=repository,
            timeout=30,
        )
        git_root = Path(root_text.strip()).resolve()
        try:
            relative_repository = repository.relative_to(git_root)
        except ValueError as error:
            raise RunnerError("目标仓库不在 Git 根目录内") from error
        commit = (
            await self._run(
                ["git", "-C", str(git_root), "rev-parse", "--verify", "HEAD"],
                cwd=git_root,
                timeout=30,
            )
        ).strip()
        await asyncio.to_thread(self.settings.worktree_root.mkdir, parents=True, exist_ok=True)
        worktree = (self.settings.worktree_root / run_id).resolve()
        if worktree.exists():
            raise RunnerError("开发 worktree 已存在，拒绝覆盖")
        await self._run(
            ["git", "-C", str(git_root), "worktree", "add", "--detach", str(worktree), commit],
            cwd=git_root,
            timeout=120,
        )
        target = (worktree / relative_repository).resolve()
        if not target.is_dir():
            raise RunnerError("worktree 中不存在目标仓库目录")
        linked_dependencies: list[Path] = []
        for relative_dependency in (Path(".venv"), Path("prototype/node_modules")):
            source_dependency = repository / relative_dependency
            target_dependency = target / relative_dependency
            if not source_dependency.is_dir() or target_dependency.exists():
                continue
            try:
                await self._run(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "check-ignore",
                        "--quiet",
                        str(relative_dependency),
                    ],
                    cwd=repository,
                    timeout=30,
                )
            except RunnerError:
                continue
            await asyncio.to_thread(target_dependency.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target_dependency.symlink_to, source_dependency, True)
            linked_dependencies.append(relative_dependency)
        if linked_dependencies:
            git_dir_text = await self._run(
                ["git", "-C", str(target), "rev-parse", "--git-common-dir"],
                cwd=target,
                timeout=30,
            )
            git_dir = Path(git_dir_text.strip())
            if not git_dir.is_absolute():
                git_dir = (target / git_dir).resolve()
            exclude_path = git_dir / "info" / "exclude"
            await asyncio.to_thread(exclude_path.parent.mkdir, parents=True, exist_ok=True)
            existing_excludes = (
                await asyncio.to_thread(exclude_path.read_text, encoding="utf-8")
                if exclude_path.is_file()
                else ""
            )
            dependency_excludes = "".join(f"/{path.as_posix()}\n" for path in linked_dependencies)
            await asyncio.to_thread(
                exclude_path.write_text,
                existing_excludes + dependency_excludes,
                encoding="utf-8",
            )
        return target

    async def cleanup_worktree(self, run_id: str, stored_worktree: Path) -> None:
        run_path = Path(run_id)
        if (
            not run_id
            or run_path.is_absolute()
            or len(run_path.parts) != 1
            or run_id in {".", ".."}
        ):
            raise RunnerError("开发运行 ID 必须是单个目录名，拒绝清理")
        root = self.settings.worktree_root.resolve()
        expected_worktree = root / run_id
        try:
            resolved_worktree = stored_worktree.resolve()
        except OSError as error:
            raise RunnerError("开发 worktree 路径无法可靠解析，拒绝清理") from error
        if resolved_worktree != expected_worktree:
            raise RunnerError(f"开发 worktree 不匹配受管路径，拒绝清理：{resolved_worktree}")
        repository = self.settings.repository.resolve()
        if resolved_worktree.exists():
            await self._run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "worktree",
                    "remove",
                    "--force",
                    str(resolved_worktree),
                ],
                cwd=repository,
                timeout=120,
            )
        await self._run(
            ["git", "-C", str(repository), "worktree", "prune"],
            cwd=repository,
            timeout=30,
        )

    async def verify(self, worktree: Path, approved_scenario_ids: frozenset[str]) -> str:
        await self.verify_gate(worktree, approved_scenario_ids)
        shared_dependencies = (worktree / "prototype" / "node_modules").is_symlink()
        for command in VALIDATION_COMMANDS:
            if shared_dependencies and command == ("npm", "--prefix", "prototype", "ci"):
                continue
            await self._run(list(command), cwd=worktree, timeout=600)
        evidence = await self._scenario_test_evidence(worktree, approved_scenario_ids)
        return "\n".join(("项目固定测试全部通过", *evidence))

    async def verify_gate(
        self, worktree: Path, approved_scenario_ids: frozenset[str]
    ) -> tuple[str, ...]:
        """公开的最小实现差异门禁入口。"""
        return await self._assert_implementation_gate(worktree, approved_scenario_ids)

    async def merge(self, worktree: Path, revision_id: str) -> str:
        await self._assert_implementation_gate(worktree, frozenset())
        repository = self.settings.repository.resolve()
        base_commit = (
            await self._run(
                ["git", "-C", str(worktree), "rev-parse", "HEAD"], cwd=worktree, timeout=30
            )
        ).strip()
        await self._assert_merge_target(repository, base_commit)
        changes = await self._run(
            ["git", "-C", str(worktree), "status", "--porcelain"], cwd=worktree, timeout=30
        )
        if not changes.strip():
            return "代码无变化，无需合并"
        await self._run(["git", "-C", str(worktree), "add", "-A"], cwd=worktree, timeout=120)
        await self._run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=sdbp-review",
                "-c",
                "user.email=sdbp-review@localhost",
                "-C",
                str(worktree),
                "commit",
                "--no-verify",
                "-m",
                f"feat(review): 实现 {revision_id}",
            ],
            cwd=worktree,
            timeout=120,
        )
        implementation_commit = (
            await self._run(
                ["git", "-C", str(worktree), "rev-parse", "HEAD"], cwd=worktree, timeout=30
            )
        ).strip()
        branch = await self._assert_merge_target(repository, base_commit)
        await self._run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(repository),
                "merge",
                "--ff-only",
                implementation_commit,
            ],
            cwd=repository,
            timeout=120,
        )
        return f"已自动合并到 {branch}（{implementation_commit[:12]}）"

    async def render(self, dot_source: str) -> str:
        return await self._run(
            ["dot", "-Tsvg"],
            cwd=self.settings.repository,
            input_text=dot_source,
            timeout=30,
        )

    async def _assert_implementation_gate(
        self, worktree: Path, approved_scenario_ids: frozenset[str]
    ) -> tuple[str, ...]:
        tracked_text, changed_text, added_text = await asyncio.gather(
            self._run(
                ["git", "-C", str(worktree), "ls-tree", "-r", "--name-only", "HEAD"],
                cwd=worktree,
                timeout=30,
            ),
            self._run(
                ["git", "-C", str(worktree), "diff", "--name-only", "HEAD"],
                cwd=worktree,
                timeout=30,
            ),
            self._run(
                ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard"],
                cwd=worktree,
                timeout=30,
            ),
        )
        tracked = frozenset(tracked_text.splitlines())
        changed = frozenset((*changed_text.splitlines(), *added_text.splitlines()))
        violations: list[str] = []
        added_tests: list[str] = []
        for path in sorted(changed):
            if path.startswith("model/") and path.endswith(".json"):
                violations.append(f"{path}：批准模型或 JSON Schema 不可修改")
            if path == SEMANTIC_VALIDATOR_PATH:
                violations.append(f"{path}：统一图语义校验器不可修改")
            scenario_test = any(scenario_id in path for scenario_id in approved_scenario_ids)
            if path in tracked and _is_test_path(path) and not scenario_test:
                violations.append(f"{path}：基线已有测试不可修改或删除")
            elif path not in tracked and _is_test_path(path):
                added_tests.append(path)

        if VALIDATION_COMMANDS_PATH in tracked:
            baseline_runner = await self._run(
                ["git", "-C", str(worktree), "show", f"HEAD:{VALIDATION_COMMANDS_PATH}"],
                cwd=worktree,
                timeout=30,
            )
            current_path = worktree / VALIDATION_COMMANDS_PATH
            current_runner = (
                current_path.read_text(encoding="utf-8") if current_path.is_file() else ""
            )
            if _constant_value(baseline_runner, "VALIDATION_COMMANDS") != _constant_value(
                current_runner, "VALIDATION_COMMANDS"
            ):
                violations.append(f"{VALIDATION_COMMANDS_PATH}：固定验证命令不可修改或削弱")

        if approved_scenario_ids:
            for path in added_tests:
                if not path.endswith(".py"):
                    violations.append(f"{path}：新增测试文件暂无法提取稳定场景测试名称")
                    continue
                collected = await self._run(
                    ["uv", "run", "pytest", "--collect-only", "-q", path],
                    cwd=worktree,
                    timeout=120,
                )
                node_ids = _pytest_node_ids(collected, path)
                if not node_ids:
                    violations.append(f"{path}：新增测试文件未收集到测试")
                for node_id in node_ids:
                    if not any(scenario_id in node_id for scenario_id in approved_scenario_ids):
                        violations.append(
                            f"{path}：新增测试名称 {node_id} 未包含当前批准 revision 的稳定 SCN- ID"
                        )
        if violations:
            detail = "\n".join(f"- {item}" for item in violations)
            raise RunnerError(f"自动验证门禁拒绝：\n{detail}")
        return tuple(added_tests)

    async def _scenario_test_evidence(
        self, worktree: Path, approved_scenario_ids: frozenset[str]
    ) -> tuple[str, ...]:
        if not approved_scenario_ids:
            return ()
        collected = await self._run(
            ["uv", "run", "pytest", "--collect-only", "-q"],
            cwd=worktree,
            timeout=600,
        )
        node_ids = tuple(
            line.strip()
            for line in collected.splitlines()
            if line.strip().startswith("tests/") and "::" in line
        )
        evidence: list[str] = []
        for scenario_id in sorted(approved_scenario_ids):
            matching = tuple(node_id for node_id in node_ids if scenario_id in node_id)
            if not matching:
                raise RunnerError(f"{scenario_id}：未收集到名称包含稳定场景 ID 的测试")
            output = await self._run(
                ["uv", "run", "pytest", "-q", "-rA", *matching],
                cwd=worktree,
                timeout=600,
            )
            passed = tuple(
                line.removeprefix("PASSED ").strip()
                for line in output.splitlines()
                if line.startswith("PASSED ")
            )
            if set(passed) != set(matching):
                missing = sorted(set(matching) - set(passed))
                raise RunnerError(
                    f"{scenario_id}：场景测试缺少逐测试通过结果：{', '.join(missing)}"
                )
            evidence.extend(f"OBSERVED PASS：{scenario_id}：{node_id}" for node_id in passed)
        return tuple(evidence)

    async def _assert_merge_target(self, repository: Path, base_commit: str) -> str:
        branch = (
            await self._run(
                ["git", "-C", str(repository), "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repository,
                timeout=30,
            )
        ).strip()
        if not branch or branch == "HEAD":
            raise RunnerError("本地主工作区处于 detached HEAD，拒绝自动合并")
        current_commit = (
            await self._run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                cwd=repository,
                timeout=30,
            )
        ).strip()
        if current_commit != base_commit:
            raise RunnerError("本地主工作区 HEAD 已偏离开发基线，拒绝自动合并")
        status = await self._run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            cwd=repository,
            timeout=30,
        )
        if status.strip():
            raise RunnerError("本地主工作区存在未提交变化，拒绝自动合并")
        return branch

    async def _codex(
        self,
        prompt: str,
        cwd: Path,
        sandbox: str,
        agent_run_id: str,
        implementation_run_id: str | None,
        *,
        timeout: int,
        writable_directories: list[Path] | None = None,
    ) -> JsonObject:
        await asyncio.to_thread(self.settings.data_dir.mkdir, parents=True, exist_ok=True)
        model_catalog = await self._bundled_model_catalog()
        with tempfile.TemporaryDirectory(dir=self.settings.data_dir) as temporary:
            output = Path(temporary) / "last-message.json"
            mcp_environment = {
                "SDBP_REVIEW_DATA_DIR": str(self.settings.data_dir),
                "SDBP_REVIEW_REPOSITORY": str(self.settings.repository),
                "SDBP_REVIEW_WORKTREE_ROOT": str(self.settings.worktree_root),
                "SDBP_REVIEW_AGENT_RUN_ID": agent_run_id,
                "SDBP_REVIEW_IMPLEMENTATION_RUN_ID": implementation_run_id or "",
            }
            mcp_environment_toml = (
                "{"
                + ",".join(f"{name}={json.dumps(value)}" for name, value in mcp_environment.items())
                + "}"
            )
            arguments = [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "-c",
                "project_doc_max_bytes=0",
                "-c",
                'approval_policy="never"',
                "-c",
                f'model_catalog_json="{model_catalog}"',
                "-c",
                f"mcp_servers.sdbp-review.command={json.dumps(sys.executable)}",
                "-c",
                'mcp_servers.sdbp-review.args=["-m","app.mcp_server"]',
                "-c",
                f"mcp_servers.sdbp-review.cwd={json.dumps(str(ROOT))}",
                "-c",
                'mcp_servers.sdbp-review.default_tools_approval_mode="approve"',
                "-c",
                f"mcp_servers.sdbp-review.env={mcp_environment_toml}",
                "--skip-git-repo-check",
                "--sandbox",
                sandbox,
                "--json",
                "--output-schema",
                str(self.settings.agent_result_schema_path),
                "--output-last-message",
                str(output),
                "-",
            ]
            for directory in writable_directories or []:
                arguments[arguments.index("--sandbox") : arguments.index("--sandbox")] = [
                    "--add-dir",
                    str(directory),
                ]
            await self._run(
                arguments,
                cwd=cwd,
                input_text=prompt,
                timeout=timeout,
                extra_environment={
                    "SDBP_REVIEW_DATA_DIR": str(self.settings.data_dir),
                    "SDBP_REVIEW_REPOSITORY": str(self.settings.repository),
                    "SDBP_REVIEW_WORKTREE_ROOT": str(self.settings.worktree_root),
                    "SDBP_REVIEW_AGENT_RUN_ID": agent_run_id,
                    "SDBP_REVIEW_IMPLEMENTATION_RUN_ID": implementation_run_id or "",
                },
            )
            try:
                text = await asyncio.to_thread(output.read_text, encoding="utf-8")
                value = json.loads(text)
            except (OSError, json.JSONDecodeError) as error:
                raise RunnerError("Codex 未返回有效 Agent JSON 结果") from error
        if not isinstance(value, dict):
            raise RunnerError("Agent 结果必须是 JSON 对象")
        return cast(JsonObject, value)

    async def _bundled_model_catalog(self) -> Path:
        text = await self._run(
            ["codex", "debug", "models", "--bundled"],
            cwd=self.settings.repository,
            timeout=30,
        )
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise RunnerError("无法读取 Codex 内置模型目录") from error
        if not isinstance(value, dict) or not isinstance(value.get("models"), list):
            raise RunnerError("Codex 内置模型目录格式无效")
        path = self.settings.data_dir / "codex-model-catalog.json"
        await asyncio.to_thread(
            path.write_text, json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
        return path

    @staticmethod
    def _validate_result(result: JsonObject, schema: JsonObject, name: str) -> None:
        errors = list(Draft202012Validator(schema).iter_errors(result))
        if errors:
            raise RunnerError(f"{name}不符合固定 Schema：{errors[0].message}")

    async def _run(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        input_text: str | None = None,
        timeout: int,
        extra_environment: dict[str, str] | None = None,
    ) -> str:
        if not arguments or arguments[0] not in ALLOWED_EXECUTABLES:
            raise RunnerError("拒绝执行未允许的本地命令")
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE
                if input_text is not None
                else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_safe_environment(extra_environment),
            )
        except OSError as error:
            raise RunnerError(f"无法启动 {arguments[0]}：{error}") from error
        try:
            output, stderr = await asyncio.wait_for(
                process.communicate(input_text.encode() if input_text is not None else None),
                timeout=timeout,
            )
        except TimeoutError as error:
            process.kill()
            await process.communicate()
            raise RunnerError(f"{arguments[0]} 执行超时") from error
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        if process.returncode != 0:
            detail = (stderr or output or b"").decode(errors="replace").strip()[-2000:]
            raise RunnerError(f"{arguments[0]} 执行失败：{detail or '无错误详情'}")
        captured = output.decode(errors="replace") if output else ""
        if not captured and stderr:
            captured = stderr.decode(errors="replace")
        return captured


def prompt_input_hash(context: JsonObject) -> str:
    return hashlib.sha256(canonical_json(context).encode()).hexdigest()


def _safe_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "CODEX_HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TERM",
        "USER",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }
    values = {name: value for name, value in os.environ.items() if name in allowed}
    values.update(extra or {})
    return values
