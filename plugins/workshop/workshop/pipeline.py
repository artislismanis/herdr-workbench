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
STEP_KEYS = {"id", "stage", "kind", "prompt", "again", "read_only", "fresh", "verdict", "then", "model", "minutes", "output", "publish", "report", "interactive", "section", "label", "proposes", "proposals", "ask"}
GATE_KEYS = {"id", "stage", "kind", "report", "section", "label", "proposals", "ask", "then"}
COMMAND_KEYS = {"id", "stage", "kind", "minutes", "verdict", "then"}
COMMAND_VERDICTS = ["PASS", "FAILED"]
RULE_KEYS = {"goto", "max", "prompt", "park"}


class PipelineError(ValueError):
    pass


def user_dir() -> Path:
    return Path(os.environ.get("WORKSHOP_PIPELINES", "~/.config/workshop/pipelines")).expanduser()


def prompt_text(name: str) -> str:
    return resources.files("workshop.prompts").joinpath(name).read_text(encoding="utf-8")


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
        prompt_text(rule["prompt"])


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
            prompt_text(step["prompt"])
            if "again" in step:
                prompt_text(step["again"])
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


def load(name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        raise PipelineError(f"bad pipeline name: {name}")
    user = user_dir() / f"{name}.toml"
    shipped = Path(__file__).resolve().parent.parent / "pipelines" / f"{name}.toml"
    path = user if user.exists() else shipped
    if not path.exists():
        raise PipelineError(f"no pipeline called {name}")
    return validate(tomllib.loads(path.read_text(encoding="utf-8")))
