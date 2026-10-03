"""Deterministic figures/cards/receipts (U14). Every displayed value cites its
metrics key; charts render as hand-built SVG (byte-identical for identical
data, no timestamps/fonts), plus the exact data JSON beside each figure.
"""
from __future__ import annotations

import html
import json
from pathlib import Path


def _svg(width, height, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="100%" height="100%" fill="#101418"/>{body}</svg>')


def _line(pts, color, width=2):
    points = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
    return (f'<polyline fill="none" stroke="{color}" stroke-width="{width}" '
            f'points="{points}"/>')


def _text(x, y, s, size=11, fill="#e8edf2", anchor="start"):
    s = html.escape(str(s), quote=True)
    return (f'<text x="{x}" y="{y}" font-family="monospace" font-size="{size}" '
            f'fill="{fill}" text-anchor="{anchor}">{s}</text>')


def epidemic_curve(per_arm: dict[str, list[tuple[float, int]]], *,
                   width=640, height=360) -> tuple[str, str]:
    """Cumulative infections over time per arm. per_arm: arm -> [(t, count)]."""
    t_max = max((t for series in per_arm.values() for t, _ in series), default=1) or 1
    y_max = max((c for series in per_arm.values() for _, c in series), default=1) or 1
    colors = ["#6fb4ff", "#ff5d5d", "#4ecf8d", "#ffd27a", "#c792ea"]
    body = [_line([(0, height - 40), (width - 60, height - 40)], "#2a333d")]
    body.append(_line([(40, 20), (40, height - 40)], "#2a333d"))
    body.append(_text(width - 60, 24, f"y_max={y_max}", 10, "#9fb0bf", "end"))
    for i, (arm, series) in enumerate(sorted(per_arm.items())):
        pts = [(40 + (t / t_max) * (width - 110),
                (height - 40) - (c / y_max) * (height - 70))
               for t, c in series]
        color = colors[i % len(colors)]
        body.append(_line(pts, color))
        legend_y = 30 + 14 * i
        body.append(f'<rect x="{width - 96}" y="{legend_y - 9}" width="10" '
                    f'height="3" fill="{color}"/>')
        body.append(_text(width - 80, legend_y, arm, 10))
    return _svg(width, height, "".join(body)), json.dumps(
        {"per_arm": per_arm, "t_max": t_max, "y_max": y_max},
        sort_keys=True, separators=(",", ":"))


def delay_damage(points: list[dict], *, width=640, height=360) -> tuple[str, str]:
    """Damage versus detection delay; points carry measured latency labels
    (injected from run artifacts — never typed here)."""
    x_max = max((p["delta"] for p in points), default=1) or 1
    y_max = max((p["damage"] for p in points), default=1) or 1
    pts = [(40 + (p["delta"] / x_max) * (width - 110),
            (height - 40) - (p["damage"] / y_max) * (height - 70))
           for p in sorted(points, key=lambda p: p["delta"])]
    body = [_line(pts, "#ff5d5d")]
    for p in points:
        x = 40 + (p["delta"] / x_max) * (width - 110)
        y = (height - 40) - (p["damage"] / y_max) * (height - 70)
        body.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="#ff5d5d"/>')
        if p.get("label"):
            body.append(_text(x, y - 8, str(p["label"]), 9, "#9fb0bf", "middle"))
    body.append(_text(40, height - 20, f"delay 0..{x_max:g}s", 10, "#9fb0bf"))
    return _svg(width, height, "".join(body)), json.dumps(
        {"points": points, "x_max": x_max, "y_max": y_max},
        sort_keys=True, separators=(",", ":"))


def r_bar(per_arm: dict[str, dict], *, width=640, height=360) -> tuple[str, str]:
    """R per arm (mean + CI whiskers when measured) against the line at R=1.
    A measured mean with unmeasured CI (single run) draws the bar honestly
    without invented whiskers; the data sidecar records ci_measured."""
    arms = sorted(k for k, v in per_arm.items() if v.get("mean") is not None)
    unmeasured = sorted(k for k, v in per_arm.items() if v.get("mean") is None)
    hi = [v.get("ci95", [v["mean"]])[-1] for v in per_arm.values()
          if v.get("mean") is not None and v.get("ci95")]
    r_max = max([1.0] + [h if h is not None else v["mean"]
                         for v, h in zip(
                             [v for v in per_arm.values()
                              if v.get("mean") is not None], hi)])
    plot_w, plot_h = width - 110, height - 70

    def y(r):
        return (height - 40) - (r / r_max) * plot_h

    body = [_line([(40, y(1.0)), (40 + plot_w, y(1.0))], "#ffd27a", 1)]
    body.append(_text(44, y(1.0) - 4, "R = 1 (containment line)", 10, "#ffd27a"))
    bar_w = plot_w / max(len(arms), 1)
    for i, arm in enumerate(arms):
        v = per_arm[arm]
        cx = 40 + bar_w * (i + 0.5)
        mean_y = y(v["mean"])
        if v.get("ci95"):
            body.append(_line([(cx, y(v["ci95"][0])), (cx, y(v["ci95"][1]))],
                              "#6fb4ff", 3))
        body.append(f'<rect x="{cx - bar_w * 0.3:.2f}" y="{mean_y:.2f}" '
                    f'width="{bar_w * 0.6:.2f}" height="{height - 40 - mean_y:.2f}" '
                    f'fill="#6fb4ff" fill-opacity="0.35"/>')
        body.append(_text(cx, height - 24, arm, 10, "#e8edf2", "middle"))
        body.append(_text(cx, mean_y - 8, f'{v["mean"]:.2f}', 10, "#6fb4ff",
                          "middle"))
        if not v.get("ci95"):
            body.append(_text(cx, mean_y - 20, "CI unmeasured (single run)",
                              9, "#9fb0bf", "middle"))
    for j, arm in enumerate(unmeasured):
        body.append(_text(40, 30 + 14 * j,
                          f"{arm}: R unmeasured (zero infections)", 10,
                          "#9fb0bf"))
    return _svg(width, height, "".join(body)), json.dumps(
        {"per_arm": per_arm}, sort_keys=True, separators=(",", ":"))


def write_figure(directory: Path, name: str, svg: str, data: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.svg").write_text(svg)
    (directory / f"{name}.json").write_text(data + "\n")
    return directory / f"{name}.svg"
