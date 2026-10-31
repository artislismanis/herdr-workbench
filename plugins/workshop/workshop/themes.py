"""herdr's themes for the board. They colour herdr's own chrome, not panes, and herdr has no API for them, so the board reads the name from its config.

Palettes are from herdr v0.9.5, src/app/state.rs (Apache-2.0, https://github.com/herdrdev/herdr). A herdr bump can add or change one.
"""

import os
import re
import tomllib
from pathlib import Path

TOKENS = ("accent", "panel_bg", "selection_bg", "surface0", "overlay1", "mauve", "green", "yellow", "red")
DEFAULT = "catppuccin"
# The terminal's own colours: 0 black, 1 to 7 the usual hues, 8 and up the bright ones.
TERMINAL = dict(zip(TOKENS, (4, 0, 8, 8, -1, 5, 2, 3, 9), strict=True))
THEMES: dict[str, dict[str, str | int]] = {
    "terminal": TERMINAL,
    "catppuccin": dict(zip(TOKENS, ("#89b4fa", "#181825", "#313244", "#313244", "#7f849c", "#cba6f7", "#a6e3a1", "#f9e2af", "#f38ba8"), strict=True)),
    "catppuccin-latte": dict(zip(TOKENS, ("#1e66f5", "#eff1f5", "#bdd0f5", "#ccd0da", "#8c8fa1", "#8839ef", "#40a02b", "#df8e1d", "#d20f39"), strict=True)),
    "tokyo-night": dict(zip(TOKENS, ("#7aa2f7", "#1a1b26", "#2d3650", "#24283b", "#697196", "#bb9af7", "#9ece6a", "#e0af68", "#f7768e"), strict=True)),
    "tokyo-night-day": dict(zip(TOKENS, ("#2e7de9", "#e1e2e7", "#b6cae7", "#c4c8da", "#68709a", "#7847bd", "#587539", "#8c6c3e", "#f52a65"), strict=True)),
    "dracula": dict(zip(TOKENS, ("#bd93f9", "#282a36", "#463f5d", "#44475a", "#828cb4", "#ff79c6", "#50fa7b", "#f1fa8c", "#ff5555"), strict=True)),
    "nord": dict(zip(TOKENS, ("#88c0d0", "#2e3440", "#40505d", "#3b4252", "#646e82", "#b48ead", "#a3be8c", "#ebcb8b", "#bf616a"), strict=True)),
    "gruvbox": dict(zip(TOKENS, ("#d79921", "#282828", "#4b3f27", "#3c3836", "#a89984", "#d3869b", "#b8bb26", "#fabd2f", "#fb4934"), strict=True)),
    "gruvbox-light": dict(zip(TOKENS, ("#076678", "#fbf1c7", "#ebdbb2", "#ebdbb2", "#7c6f64", "#8f3f71", "#79740e", "#b57614", "#9d0006"), strict=True)),
    "one-dark": dict(zip(TOKENS, ("#61afef", "#282c34", "#334659", "#2c313a", "#737a87", "#c678dd", "#98c379", "#e5c07b", "#e06c75"), strict=True)),
    "one-light": dict(zip(TOKENS, ("#4078f2", "#fafafa", "#cddbf8", "#f0f0f1", "#686b77", "#a626a4", "#50a14f", "#c18401", "#e45649"), strict=True)),
    "solarized": dict(zip(TOKENS, ("#268bd2", "#002b36", "#083e55", "#073642", "#657b83", "#d33682", "#859900", "#b58900", "#dc322f"), strict=True)),
    "solarized-light": dict(zip(TOKENS, ("#268bd2", "#fdf6e3", "#c9dcdf", "#eee8d5", "#586e75", "#d33682", "#859900", "#b58900", "#dc322f"), strict=True)),
    "kanagawa": dict(zip(TOKENS, ("#7e9cd8", "#1f1f28", "#32384b", "#2a2a37", "#87867d", "#957fb8", "#76946a", "#c0a36e", "#c34043"), strict=True)),
    "kanagawa-lotus": dict(zip(TOKENS, ("#4d699b", "#f2ecbc", "#dcd5ac", "#dcd5ac", "#8a8980", "#624c83", "#6f894e", "#77713f", "#c84053"), strict=True)),
    "rose-pine": dict(zip(TOKENS, ("#c4a7e7", "#191724", "#3b344b", "#1f1d2e", "#908caa", "#c4a7e7", "#31748f", "#f6c177", "#eb6f92"), strict=True)),
    "rose-pine-dawn": dict(zip(TOKENS, ("#907aa9", "#faf4ed", "#f2e9e1", "#f2e9e1", "#797593", "#907aa9", "#286983", "#ea9d34", "#b4637a"), strict=True)),
    "vesper": dict(zip(TOKENS, ("#ffc799", "#1a1a1a", "#232323", "#232323", "#7e7e7e", "#ffd1a8", "#99ffe4", "#ffc799", "#ff8080"), strict=True)),
}
ALIASES = {
    "catppuccin-mocha": "catppuccin",
    "latte": "catppuccin-latte",
    "light": "catppuccin-latte",
    "tokyonight": "tokyo-night",
    "tokyo-day": "tokyo-night-day",
    "tokyonight-day": "tokyo-night-day",
    "gruvbox-dark": "gruvbox",
    "onedark": "one-dark",
    "onelight": "one-light",
    "solarized-dark": "solarized",
    "lotus": "kanagawa-lotus",
    "rosepine": "rose-pine",
    "rosepine-dawn": "rose-pine-dawn",
    "dawn": "rose-pine-dawn",
}
LEVELS = (0, 95, 135, 175, 215, 255)  # each step of the 256-colour cube


def config_path() -> Path:
    if path := os.environ.get("HERDR_CONFIG_PATH"):
        return Path(path)
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "herdr" / "config.toml"


def palette(path: Path) -> dict[str, str | int]:
    """The theme herdr shows, with its custom colours. With auto_switch the board can't see which way herdr went, so it follows the dark one."""
    try:
        theme = tomllib.loads(path.read_text(encoding="utf-8")).get("theme", {})
    except (OSError, tomllib.TOMLDecodeError):
        theme = {}
    wanted = (theme.get("auto_switch") and theme.get("dark_name")) or theme.get("name") or DEFAULT
    name = re.sub(r"[ _]", "-", str(wanted).lower())
    base = THEMES.get(ALIASES.get(name, name), THEMES[DEFAULT])
    custom = {k: v for k, v in theme.get("custom", {}).items() if k in TOKENS and re.fullmatch(r"#[0-9a-fA-F]{6}", str(v))}
    return base | custom


def nearest(colour: str | int) -> int:
    """The closest of the 256 colours, because curses can't draw exact ones on this terminal type."""
    if isinstance(colour, int):
        return colour
    rgb = [int(colour[i : i + 2], 16) for i in (1, 3, 5)]

    def gap(other: list[int]) -> int:
        return sum((a - b) ** 2 for a, b in zip(rgb, other, strict=True))

    cube = [min(range(6), key=lambda step: abs(LEVELS[step] - value)) for value in rgb]
    grey = max(0, min(23, (sum(rgb) // 3 - 8) // 10))
    index = 16 + 36 * cube[0] + 6 * cube[1] + cube[2]
    return index if gap([LEVELS[step] for step in cube]) <= gap([8 + 10 * grey] * 3) else 232 + grey
