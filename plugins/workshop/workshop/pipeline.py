"""Pipelines are data. Loading validates them, so a bad file fails before any work starts."""

import os
import re
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

KINDS = ("agent", "gate", "command", "draft_pr")
PUBLISH = ("pr", "ticket")
SECTIONS = ("Requirements", "Design")  # what a gate can write to a ticket, in the order they read
STAGES = ("planned", "designed")
STEP_KEYS = {"id", "stage", "queues", "kind", "prompt", "again", "read_only", "fresh", "verdict", "then", "model", "minutes", "output", "publish", "report", "interactive", "section", "label", "proposes", "proposals", "ask", "go", "change"}
GATE_KEYS = {"id", "stage", "kind", "report", "section", "label", "proposals", "ask", "go", "change", "then"}
COMMAND_KEYS = {"id", "stage", "kind", "minutes", "verdict", "then"}
COMMAND_VERDICTS = ["PASS", "FAILED"]
RULE_KEYS = {"goto", "max", "prompt", "park"}
SHIPPED = Path(__file__).resolve().parent.parent / "pipelines"
STEPS = SHIPPED.parent / "steps.toml"  # workshop's steps, which pipelines list by name
MARK = "<!-- workshop -->"  # above it, how to do the step, which a repo or you can replace; below, how it reports back


class PipelineError(ValueError):
    pass


def folders(kind: str, path: Path | None = None, repo: str = "") -> list[tuple[str, Path]]:
    """Where your and a repo's pipelines or prompts live, the first with the file winning: yours for this repo, the repo's, then yours for every repo."""
    # The repo's main checkout, never a run's worktree: a PR under review can't rewrite its own reviewer.
    repos = Path(os.environ.get("WORKSHOP_REPOS", "~/.config/workshop/repos")).expanduser()
    mine = Path(os.environ.get(f"WORKSHOP_{kind.upper()}", f"~/.config/workshop/{kind}")).expanduser()
    return ([("yours for this repo", repos / repo / kind)] if repo else []) + ([("repo", path / ".workshop" / kind)] if path else []) + [("yours", mine)]


def contract_text(name: str) -> str:
    return resources.files("workshop.prompts").joinpath(name).read_text(encoding="utf-8")


def prompt_dirs(path: Path | None = None, repo: str = "") -> list[tuple[str, Path]]:
    return folders("prompts", path, repo)


def prompt_source(name: str, path: Path | None = None, repo: str = "") -> str:
    """Where a step's method comes from: one of prompt_dirs, or workshop."""
    return next((where for where, folder in prompt_dirs(path, repo) if (folder / name).is_file()), "workshop")


def prompt_text(name: str, path: Path | None = None, repo: str = "") -> str:
    """A step's method, from the first folder that has it, then workshop's contract, which nothing replaces."""
    method, _, contract = (contract_text(name) if shipped(name) else "").partition(MARK)
    if (where := prompt_source(name, path, repo)) != "workshop":
        method = (dict(prompt_dirs(path, repo))[where] / name).read_text(encoding="utf-8").partition(MARK)[0]
    elif not shipped(name):
        raise PipelineError(f"no prompt called {name}: put one in {' or '.join(str(folder) for _, folder in prompt_dirs(path, repo))}")
    return "\n\n".join(part for part in (method.strip(), contract and contract_text("contract.md").strip(), contract.strip()) if part)


def shipped(name: str) -> bool:
    return resources.files("workshop.prompts").joinpath(name).is_file()


def prompts_of(pipeline: dict[str, Any]) -> list[str]:
    steps = pipeline["steps"]
    named = [s[k] for s in steps for k in ("prompt", "again") if k in s] + [r["prompt"] for s in steps for r in s.get("then", {}).values() if "prompt" in r]
    return list(dict.fromkeys(named))


def _check_prompt(where: str, name: Any) -> None:
    # A prompt of your own may live in a repo, so whether it exists is for the run and doctor to say.
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.md", name):
        raise PipelineError(f"{where}: a prompt is a file name, like security.md")


def _check_rule(where: str, rule: Any, ids: set[str]) -> None:
    if not isinstance(rule, dict) or set(rule) - RULE_KEYS:
        raise PipelineError(f"{where}: a rule takes only {sorted(RULE_KEYS)}")
    if ("goto" in rule) == ("park" in rule):
        raise PipelineError(f"{where}: a rule needs exactly one of goto or park")
    if "goto" in rule:
        if rule["goto"] not in ids:
            raise PipelineError(f"{where}: goto names an unknown step: {rule['goto']}")
        if not isinstance(rule.get("max"), int) or rule["max"] < 1:
            raise PipelineError(f"{where}: a goto needs a max of 1 or more, so it can't loop forever")
    if "prompt" in rule:
        _check_prompt(where, rule["prompt"])


def validate(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not {"name", "steps"} <= set(raw) <= {"name", "ticket_stage", "steps"}:
        raise PipelineError("a pipeline has name, steps and optionally ticket_stage")
    steps = raw["steps"]
    if not isinstance(steps, list) or not steps:
        raise PipelineError(f"{raw['name']}: steps must be a non-empty list")
    ids = [s.get("id") for s in steps if isinstance(s, dict)]
    if len(ids) != len(steps) or None in ids or len(set(ids)) != len(ids):
        raise PipelineError(f"{raw['name']}: every step needs a unique id")
    for step in steps:
        where = f"{raw['name']}.{step['id']}"
        if unknown := set(step) - STEP_KEYS:
            raise PipelineError(f"{where}: unknown keys {sorted(unknown)}")
        if not isinstance(step.get("stage", ""), str):
            raise PipelineError(f"{where}: stage is a name")
        if step.get("kind") not in KINDS:
            raise PipelineError(f"{where}: kind must be one of {KINDS}")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", step["id"]):
            raise PipelineError(f"{where}: ids are lowercase letters, digits and hyphens")
        if step["kind"] == "agent":
            if "prompt" not in step:
                raise PipelineError(f"{where}: an agent step needs a prompt")
            _check_prompt(where, step["prompt"])
            if "again" in step:
                _check_prompt(where, step["again"])
            if "minutes" in step and (not isinstance(step["minutes"], int) or step["minutes"] < 1):
                raise PipelineError(f"{where}: minutes must be 1 or more")
            if not re.fullmatch(r"[a-z0-9-]+\.[a-z]+", step.get("output", "report.md")):
                raise PipelineError(f"{where}: output is a plain file name, like spec.md")
            if step.get("publish", "pr") not in PUBLISH:
                raise PipelineError(f"{where}: publish must be one of {PUBLISH}")
            if step.get("interactive") and not step.get("verdict"):
                raise PipelineError(f"{where}: an interactive step needs a verdict, the only sign you've agreed")
        elif step["kind"] == "gate":
            if set(step) - GATE_KEYS:
                raise PipelineError(f"{where}: a gate takes only {sorted(GATE_KEYS)}")
            if step.get("report") not in ids:
                raise PipelineError(f"{where}: a gate's report names the step whose report you approve")
            if ("section" in step) != ("label" in step):
                raise PipelineError(f"{where}: a gate that writes to the ticket needs a section and a label")
            if "section" in step and step["section"] not in SECTIONS:
                raise PipelineError(f"{where}: section must be one of {SECTIONS}")
            if "label" in step and step["label"] not in STAGES:
                raise PipelineError(f"{where}: label must be one of {STAGES}")
        elif step["kind"] == "command":
            if set(step) - COMMAND_KEYS:
                raise PipelineError(f"{where}: a command takes only {sorted(COMMAND_KEYS)}")
            if step.get("verdict") != COMMAND_VERDICTS:
                raise PipelineError(f"{where}: a command's verdict is {COMMAND_VERDICTS}")
            if "minutes" in step and (not isinstance(step["minutes"], int) or step["minutes"] < 1):
                raise PipelineError(f"{where}: minutes must be 1 or more")
        elif set(step) - {"id", "stage", "kind"}:
            raise PipelineError(f"{where}: {step['kind']} takes no options")
        for verdict, rule in step.get("then", {}).items():
            if verdict not in (["REJECTED"] if step["kind"] == "gate" else step.get("verdict", [])):
                raise PipelineError(f"{where}: then names {verdict}, which isn't in verdict")
            _check_rule(f"{where}.then.{verdict}", rule, set(ids))
    # They share the main agent, which has one model.
    shared = {s.get("model") for s in steps if s["kind"] == "agent" and not s.get("fresh")}
    if len(shared) > 1:
        raise PipelineError(f"{raw['name']}: steps that aren't fresh share an agent, so they need the same model")
    return raw


def blocks() -> dict[str, dict[str, Any]]:
    return tomllib.loads(STEPS.read_text(encoding="utf-8"))["step"]


def expand(raw: Any, name: str) -> Any:
    """A list of step names to the steps themselves: workshop's, changed by any [step.<name>] table, and yours, which a table defines."""
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list) or not all(isinstance(step, str) for step in raw["steps"]):
        return raw  # [[steps]] tables, each a whole step, which still load
    tables = raw.get("step", {})
    if not isinstance(tables, dict) or not all(isinstance(table, dict) and isinstance(table.get("then", {}), dict) for table in tables.values()):
        raise PipelineError(f"{name}: each [step.<name>] is a table of settings")
    if extra := sorted(set(tables) - set(raw["steps"])):
        raise PipelineError(f"{name}: [step.{extra[0]}] isn't in steps")
    if named := sorted(id for id, table in tables.items() if "id" in table):
        raise PipelineError(f"{name}.{named[0]}: a step's id is its name in steps")
    ours = blocks()
    steps = []
    for id in raw["steps"]:
        if id not in ours and id not in tables:
            raise PipelineError(f"{name}: workshop has no step called {id}, so define it with [step.{id}]")
        base, mine = ours.get(id, {"kind": "agent"}), tables.get(id, {})
        # Workshop's rules are for workshop's verdicts, so new verdicts start without them.
        then = ({} if "verdict" in mine else base.get("then", {})) | mine.get("then", {})
        step = {"id": id} | {k: v for k, v in (base | mine).items() if k != "then"} | ({"then": then} if then else {})
        steps.append(step)
    return {k: v for k, v in raw.items() if k != "step"} | {"name": raw.get("name", name), "steps": steps}


def pipeline_source(name: str, path: Path | None = None, repo: str = "") -> str:
    """Where a pipeline comes from: one of folders, or workshop."""
    return next((where for where, folder in folders("pipelines", path, repo) if (folder / f"{name}.toml").is_file()), "workshop")


def load(name: str, path: Path | None = None, repo: str = "") -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise PipelineError(f"bad pipeline name: {name}")
    where = pipeline_source(name, path, repo)
    file = (dict(folders("pipelines", path, repo))[where] if where != "workshop" else SHIPPED) / f"{name}.toml"
    if not file.exists():
        raise PipelineError(f"no pipeline called {name}")
    return validate(expand(tomllib.loads(file.read_text(encoding="utf-8")), name))
