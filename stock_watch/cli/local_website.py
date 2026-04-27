from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_watch.paths import REPO_ROOT
from stock_watch.paths import SITE_OUTDIR
from stock_watch.paths import THEME_OUTDIR
from stock_watch.paths import VERIFICATION_OUTDIR

DEFAULT_SITE_DIR = SITE_OUTDIR
DEFAULT_INDEX = DEFAULT_SITE_DIR / "index.html"
REVIEWABLE_SUFFIXES = {".md", ".csv", ".json", ".txt"}


def report_specs(theme_outdir: Path, verification_outdir: Path) -> tuple[tuple[str, Path, str], ...]:
    return (
        ("Local Run Status", theme_outdir / "local_run_status.md", "Daily workflow status, runtimes, and verification gate."),
        ("Daily Watchlist", theme_outdir / "daily_report.md", "Ranked names, short/midlong candidates, spec-risk notes, and feedback."),
        ("Portfolio Review", theme_outdir / "portfolio_report.md", "Holdings review and market context."),
        ("Weekly Review", theme_outdir / "weekly_review.md", "Strategy decisions, data gate, research diagnostics, and tuning watchlist."),
        ("Verification Report", verification_outdir / "verification_report.md", "Snapshot-level recommendation verification."),
        ("Outcomes Summary", verification_outdir / "outcomes_summary.md", "Full-history realized outcomes, factors, tail risk, and sensitivity."),
        ("Feedback Sensitivity", verification_outdir / "feedback_weight_sensitivity.md", "Feedback-weight sensitivity across action labels."),
        ("Local Doctor", theme_outdir / "local_doctor.md", "Environment and local artifact health."),
        ("Local Housekeeping", theme_outdir / "local_housekeeping.md", "Generated-file cleanup and backup notes."),
        ("Shadow 開高不追", theme_outdir / "shadow_open_not_chase.md", "Shadow-only action-level tuning candidates."),
        ("Watchlist Additions", theme_outdir / "watchlist_addition_draft.md", "Next-wave watchlist expansion draft."),
        ("New Additions Priority", theme_outdir / "new_additions_priority.md", "Priority read for recently added names."),
        ("Local Runbook", REPO_ROOT / "docs" / "runbooks" / "LOCAL_RUNBOOK.md", "How to run the local daily workflow."),
        ("Signal Glossary", REPO_ROOT / "docs" / "runbooks" / "SIGNAL_GLOSSARY.md", "Signal rules, report semantics, and template bundles."),
        ("Public Repo Scouting", REPO_ROOT / "docs" / "research" / "PUBLIC_REPO_SCOUTING.md", "External repo ideas worth borrowing without copying stock rules."),
        ("Structure Plan", REPO_ROOT / "docs" / "refactor" / "STRUCTURE_PLAN.md", "Phased folder structure cleanup plan."),
    )


def artifact_links(theme_outdir: Path, verification_outdir: Path) -> tuple[tuple[str, Path], ...]:
    return (
        ("Daily report HTML", theme_outdir / "daily_report.html"),
        ("Daily report Markdown", theme_outdir / "daily_report.md"),
        ("Daily rank CSV", theme_outdir / "daily_rank.csv"),
        ("Portfolio HTML", theme_outdir / "portfolio_report.html"),
        ("Portfolio Markdown", theme_outdir / "portfolio_report.md"),
        ("Weekly review JSON", theme_outdir / "weekly_review.json"),
        ("Local status JSON", theme_outdir / "local_run_status.json"),
        ("Local doctor JSON", theme_outdir / "local_doctor.json"),
        ("Verification report", verification_outdir / "verification_report.md"),
        ("Outcome summary", verification_outdir / "outcomes_summary.md"),
        ("Recommendation snapshots", verification_outdir / "reco_snapshots.csv"),
        ("Recommendation outcomes", verification_outdir / "reco_outcomes.csv"),
        ("Shadow candidates CSV", theme_outdir / "shadow_open_not_chase_candidates.csv"),
        ("Watchlist additions JSON", theme_outdir / "watchlist_addition_draft.json"),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local static website for stock-watch artifacts.")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_SITE_DIR, help="Directory that receives index.html.")
    parser.add_argument("--theme-outdir", type=Path, default=THEME_OUTDIR)
    parser.add_argument("--verification-outdir", type=Path, default=VERIFICATION_OUTDIR)
    return parser.parse_args(argv)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_csv_rows(path: Path, *, limit: int = 10) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return [clean_csv_row(row) for _, row in zip(range(limit), reader)]
    except Exception:
        return []


def clean_csv_row(row: dict[str, Any]) -> dict[str, str]:
    return {str(k).lstrip("\ufeff"): "" if v is None else str(v) for k, v in row.items()}


def load_all_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return [clean_csv_row(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return max(sum(1 for _ in handle) - 1, 0)
    except Exception:
        return 0


def rel_href(path: Path, outdir: Path) -> str:
    return os.path.relpath(path, outdir)


def artifact_relative_path(path: Path) -> Path:
    try:
        return path.resolve().relative_to(REPO_ROOT)
    except Exception:
        return Path(path.name)


def site_artifact_path(path: Path, outdir: Path) -> Path:
    return outdir / "artifacts" / artifact_relative_path(path)


def site_review_path(path: Path, outdir: Path) -> Path:
    return outdir / "views" / Path(str(artifact_relative_path(path)) + ".html")


def site_href(path: Path, outdir: Path) -> str:
    try:
        path.resolve().relative_to(outdir.resolve())
        return rel_href(path, outdir)
    except Exception:
        return rel_href(site_artifact_path(path, outdir), outdir)


def site_review_href(path: Path, outdir: Path) -> str:
    if path.suffix.lower() in REVIEWABLE_SUFFIXES:
        return rel_href(site_review_path(path, outdir), outdir)
    return site_href(path, outdir)


def collect_artifact_paths(theme_outdir: Path, verification_outdir: Path) -> list[Path]:
    seen: set[Path] = set()
    paths: list[Path] = []
    for _, path, _ in report_specs(theme_outdir, verification_outdir):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            paths.append(path)
    for _, path in artifact_links(theme_outdir, verification_outdir):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            paths.append(path)
    return paths


def copy_site_artifacts(outdir: Path, theme_outdir: Path, verification_outdir: Path) -> None:
    artifacts_dir = outdir / "artifacts"
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    for source in collect_artifact_paths(theme_outdir, verification_outdir):
        if not source.exists() or not source.is_file():
            continue
        destination = site_artifact_path(source, outdir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        root_compat_destination = outdir / source.name
        if root_compat_destination.name != "index.html":
            shutil.copy2(source, root_compat_destination)


def _site_chrome(title: str, body: str, *, back_href: str = "../index.html") -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{
  --bg: #0b1020;
  --panel: rgba(255,255,255,.085);
  --line: rgba(255,255,255,.16);
  --text: #eef4ff;
  --muted: #aab8d8;
  --accent: #7dd3fc;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background:
    radial-gradient(circle at top left, rgba(59,130,246,.28), transparent 34rem),
    var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", sans-serif;
  line-height: 1.58;
}}
a {{ color: var(--accent); text-decoration: none; }}
.shell {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px 70px; }}
.topbar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; margin-bottom: 18px; }}
.pill {{ border: 1px solid var(--line); background: var(--panel); color: var(--muted); border-radius: 999px; padding: 7px 12px; }}
h1 {{ margin: 0 0 14px; font-size: clamp(1.8rem, 4vw, 3.6rem); letter-spacing: -.04em; }}
.panel {{ border: 1px solid var(--line); background: var(--panel); border-radius: 20px; padding: 18px; overflow: hidden; }}
.markdown-body {{ max-height: none; overflow: visible; }}
.markdown-body code, pre code {{ background: rgba(255,255,255,.1); border-radius: 7px; padding: 1px 5px; }}
pre {{ overflow: auto; background: rgba(0,0,0,.25); padding: 14px; border-radius: 14px; }}
.muted {{ color: var(--muted); }}
.table-wrap {{ overflow: auto; border: 1px solid var(--line); border-radius: 14px; }}
table {{ width: 100%; border-collapse: collapse; min-width: 720px; }}
th, td {{ padding: 9px 10px; border-bottom: 1px solid rgba(255,255,255,.1); text-align: left; vertical-align: top; }}
th {{ background: rgba(255,255,255,.08); color: #dbeafe; position: sticky; top: 0; }}
td {{ color: #dbe7ff; }}
</style>
</head>
<body>
<main class="shell">
  <div class="topbar"><a class="pill" href="{html.escape(back_href)}">← Dashboard</a></div>
  {body}
</main>
</body>
</html>
"""


def render_csv_table(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "<p class=\"muted\">No rows available.</p>"
    columns = list(rows[0].keys())
    parts = ["<div class=\"table-wrap\"><table>"]
    parts.append("<thead><tr>" + "".join(f"<th>{html.escape(column)}</th>" for column in columns) + "</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>" + "".join(f"<td>{html.escape(row.get(column, ''))}</td>" for column in columns) + "</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def render_review_body(path: Path, raw_href: str) -> str:
    content = read_text(path)
    raw_href_escaped = html.escape(raw_href)
    title = html.escape(path.name)
    updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S") if path.exists() else "n/a"
    header = (
        f"<h1>{title}</h1>"
        f"<p class=\"muted\">Updated: {html.escape(updated)} · "
        f"<a href=\"{raw_href_escaped}\">raw file</a></p>"
    )
    if not content:
        return header + "<section class=\"panel\"><p class=\"muted\">File not found or unreadable.</p></section>"
    suffix = path.suffix.lower()
    if suffix == ".md":
        body = f"<div class=\"markdown-body\">{markdown_to_html(content)}</div>"
    elif suffix == ".csv":
        rows = load_csv_rows(path, limit=500)
        body = render_csv_table(rows)
        body += f"<p class=\"muted\">Showing up to 500 rows. Raw file: <a href=\"{raw_href_escaped}\">{html.escape(path.name)}</a></p>"
    elif suffix == ".json":
        try:
            pretty = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
        except Exception:
            pretty = content
        body = f"<pre><code>{html.escape(pretty)}</code></pre>"
    else:
        body = f"<pre><code>{html.escape(content)}</code></pre>"
    return header + f"<section class=\"panel\">{body}</section>"


def write_review_pages(outdir: Path, theme_outdir: Path, verification_outdir: Path) -> None:
    views_dir = outdir / "views"
    if views_dir.exists():
        shutil.rmtree(views_dir)
    for source in collect_artifact_paths(theme_outdir, verification_outdir):
        if source.suffix.lower() not in REVIEWABLE_SUFFIXES:
            continue
        if not source.exists() or not source.is_file():
            continue
        destination = site_review_path(source, outdir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        back_href = rel_href(outdir / "index.html", destination.parent)
        raw_href = rel_href(site_artifact_path(source, outdir), destination.parent)
        destination.write_text(
            _site_chrome(source.name, render_review_body(source, raw_href), back_href=back_href),
            encoding="utf-8",
        )


def _inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def _close_list(lines: list[str], list_open: bool) -> bool:
    if list_open:
        lines.append("</ul>")
    return False


def _render_markdown_table(block: list[str]) -> str:
    rows: list[list[str]] = []
    for line in block:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    parts = ["<div class=\"table-wrap\"><table>"]
    parts.append("<thead><tr>" + "".join(f"<th>{_inline_markdown(cell)}</th>" for cell in header) + "</tr></thead>")
    if body:
        parts.append("<tbody>")
        for row in body:
            padded = row + [""] * max(0, len(header) - len(row))
            parts.append("<tr>" + "".join(f"<td>{_inline_markdown(cell)}</td>" for cell in padded[: len(header)]) + "</tr>")
        parts.append("</tbody>")
    parts.append("</table></div>")
    return "".join(parts)


def markdown_to_html(markdown: str) -> str:
    output: list[str] = []
    lines = markdown.splitlines()
    list_open = False
    in_code = False
    code_lines: list[str] = []
    index = 0
    while index < len(lines):
        raw = lines[index]
        line = raw.rstrip()

        if line.startswith("```"):
            if in_code:
                output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                list_open = _close_list(output, list_open)
                in_code = True
            index += 1
            continue
        if in_code:
            code_lines.append(raw)
            index += 1
            continue

        if not line.strip():
            list_open = _close_list(output, list_open)
            index += 1
            continue

        if line.startswith("|"):
            list_open = _close_list(output, list_open)
            block = [line]
            index += 1
            while index < len(lines) and lines[index].startswith("|"):
                block.append(lines[index].rstrip())
                index += 1
            output.append(_render_markdown_table(block))
            continue

        heading_match = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading_match:
            list_open = _close_list(output, list_open)
            level = len(heading_match.group(1))
            output.append(f"<h{level}>{_inline_markdown(heading_match.group(2))}</h{level}>")
            index += 1
            continue

        if line.startswith("- "):
            if not list_open:
                output.append("<ul>")
                list_open = True
            output.append(f"<li>{_inline_markdown(line[2:].strip())}</li>")
            index += 1
            continue

        list_open = _close_list(output, list_open)
        output.append(f"<p>{_inline_markdown(line.strip())}</p>")
        index += 1

    _close_list(output, list_open)
    if in_code:
        output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    return "\n".join(part for part in output if part)


def status_class(value: object) -> str:
    text = str(value or "").lower()
    if text in {"ok", "completed", "pass", "passed"}:
        return "ok"
    if text in {"warn", "watch", "review", "mixed"}:
        return "warn"
    if text in {"failed", "error", "blocked"}:
        return "bad"
    return "neutral"


def metric_card(label: str, value: object, detail: str = "", *, kind: str = "neutral") -> str:
    return (
        f"<article class=\"metric {html.escape(kind)}\">"
        f"<span>{html.escape(label)}</span>"
        f"<strong>{html.escape(str(value))}</strong>"
        f"<small>{html.escape(detail)}</small>"
        "</article>"
    )


def artifact_cards(outdir: Path, theme_outdir: Path, verification_outdir: Path) -> str:
    cards: list[str] = []
    for label, path in artifact_links(theme_outdir, verification_outdir):
        exists = path.exists()
        href = html.escape(site_review_href(path, outdir))
        badge = "ready" if exists else "missing"
        cards.append(
            f"<a class=\"artifact {badge}\" href=\"{href}\">"
            f"<strong>{html.escape(label)}</strong>"
            f"<span>{'review' if exists else 'missing'}</span>"
            "</a>"
        )
    return "\n".join(cards)


def render_csv_preview(title: str, rows: list[dict[str, str]], preferred_columns: list[str]) -> str:
    if not rows:
        return f"<section class=\"panel\"><h2>{html.escape(title)}</h2><p class=\"muted\">No rows available.</p></section>"
    columns = [column for column in preferred_columns if column in rows[0]]
    if not columns:
        columns = list(rows[0].keys())[:8]
    parts = [f"<section class=\"panel\"><h2>{html.escape(title)}</h2><div class=\"table-wrap\"><table>"]
    parts.append("<thead><tr>" + "".join(f"<th>{html.escape(column)}</th>" for column in columns) + "</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>" + "".join(f"<td>{html.escape(row.get(column, ''))}</td>" for column in columns) + "</tr>")
    parts.append("</tbody></table></div></section>")
    return "".join(parts)


def _number(row: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        value = row.get(key, "")
        return float(value) if value not in {"", None} else default
    except Exception:
        return default


def _rank(row: dict[str, str]) -> int:
    try:
        return int(float(row.get("rank", "9999")))
    except Exception:
        return 9999


def format_metric(value: object, suffix: str = "") -> str:
    if value in {"", None}:
        return "n/a"
    try:
        number = float(value)
    except Exception:
        return str(value)
    if number.is_integer():
        return f"{int(number)}{suffix}"
    return f"{number:.2f}{suffix}"


def ticker_slug(ticker: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ticker.strip()).strip("_")
    return slug or "unknown"


def ticker_detail_path(outdir: Path, ticker: str) -> Path:
    return outdir / "views" / "tickers" / f"{ticker_slug(ticker)}.html"


def ticker_detail_href(outdir: Path, ticker: str) -> str:
    return rel_href(ticker_detail_path(outdir, ticker), outdir)


def attach_ticker_hrefs(rows: list[dict[str, str]], outdir: Path) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []
    for row in rows:
        work = dict(row)
        work["_detail_href"] = ticker_detail_href(outdir, work.get("ticker", ""))
        enriched.append(work)
    return enriched


def build_daily_conclusion(
    *,
    local_status: dict[str, Any],
    weekly: dict[str, Any],
    rank_rows: list[dict[str, str]],
) -> list[str]:
    metrics = local_status.get("metrics", {}) if isinstance(local_status.get("metrics"), dict) else {}
    decisions = weekly.get("decisions", {}) if isinstance(weekly.get("decisions"), dict) else {}
    tables = weekly.get("tables", {}) if isinstance(weekly.get("tables"), dict) else {}

    short_rows = [row for row in rank_rows if row.get("layer") == "short_attack" and row.get("grade") in {"A", "B"}]
    midlong_rows = [row for row in rank_rows if row.get("layer") == "midlong_core" and row.get("grade") in {"A", "B"}]
    risk_rows = [row for row in rank_rows if row.get("spec_risk_label") and row.get("spec_risk_label") != "正常"]
    decision_statuses = {key: (value.get("status") if isinstance(value, dict) else "unknown") for key, value in decisions.items()}
    review_items = [key for key, status in decision_statuses.items() if status == "review"]
    full_short = tables.get("full_short_gate_promotion_watch") or []
    shadow_upgrade = next((row for row in full_short if row.get("verdict") == "watch_upgrade"), {})

    short_names = "、".join(f"{row.get('ticker')} {row.get('name')}" for row in short_rows[:3]) or "暫無"
    midlong_names = "、".join(f"{row.get('ticker')} {row.get('name')}" for row in midlong_rows[:3]) or "暫無"
    risk_names = "、".join(f"{row.get('ticker')} {row.get('name')}" for row in risk_rows[:3]) or "暫無"
    gate = metrics.get("verification_gate_status", "unknown")
    latest = metrics.get("latest_snapshot_signal_date") or metrics.get("latest_outcome_signal_date") or "n/a"

    lines = [
        f"資料健康度：verification gate `{gate}`，最新訊號日 `{latest}`，duplicate/missing price 目前 `{metrics.get('snapshot_dup_keys', 0)}/{metrics.get('outcome_dup_keys', 0)}`、`{metrics.get('signal_date_missing_rows', 0)}/{metrics.get('no_price_series_rows', 0)}`。",
        f"今日候選：Short Attack A/B 共 `{len(short_rows)}` 檔，先看 {short_names}；Mid-Long Core A/B 共 `{len(midlong_rows)}` 檔，先看 {midlong_names}。",
        f"風險焦點：非正常 spec-risk 共 `{len(risk_rows)}` 檔，前面幾檔是 {risk_names}；這區先當「不要追太快」清單。",
    ]
    if review_items:
        lines.append(f"規則決策：大多維持 hold，目前需要 review 的是 `{', '.join(review_items)}`；不要直接放寬 live gate。")
    else:
        lines.append("規則決策：目前沒有足夠證據支持改 live gate，維持現行規則、繼續累積樣本。")
    if shadow_upgrade:
        lines.append(
            f"研究線索：`{shadow_upgrade.get('action')}` 仍是 shadow 候選，"
            f"{shadow_upgrade.get('horizon_days')}D below-ok `{format_metric(shadow_upgrade.get('delta_avg_ret_below_minus_ok'), '%')}`，先觀察不升正式規則。"
        )
    return lines


def render_daily_conclusion(lines: list[str]) -> str:
    return "<ul class=\"conclusion-list\">" + "".join(f"<li>{_inline_markdown(line)}</li>" for line in lines) + "</ul>"


def ticker_card(row: dict[str, str], *, mode: str) -> str:
    rank = html.escape(row.get("rank", ""))
    ticker = html.escape(row.get("ticker", ""))
    name = html.escape(row.get("name", ""))
    grade = html.escape(row.get("grade", ""))
    spec = html.escape(row.get("spec_risk_label", ""))
    subtitle = html.escape(f"{row.get('group', '')} / {row.get('layer', '')}".strip(" /"))
    stats = [
        ("setup", format_metric(row.get("setup_score"))),
        ("risk", format_metric(row.get("risk_score"))),
        ("5D", format_metric(row.get("ret5_pct"), "%")),
        ("20D", format_metric(row.get("ret20_pct"), "%")),
    ]
    stat_html = "".join(f"<span><b>{html.escape(label)}</b>{html.escape(value)}</span>" for label, value in stats)
    risk_class = "risk-high" if "高" in spec else "risk-watch" if spec and spec != "正常" else "risk-normal"
    href = html.escape(row.get("_detail_href", "#"))
    return (
        f"<a class=\"ticker-card {html.escape(mode)} {risk_class}\" href=\"{href}\">"
        f"<div class=\"ticker-top\"><span class=\"rank\">#{rank}</span><span class=\"grade\">{grade}</span></div>"
        f"<h3>{ticker} <small>{name}</small></h3>"
        f"<p>{subtitle}</p>"
        f"<div class=\"mini-stats\">{stat_html}</div>"
        f"<em>{spec or 'n/a'}</em>"
        "</a>"
    )


def render_ticker_lane(title: str, rows: list[dict[str, str]], *, mode: str, empty: str) -> str:
    if not rows:
        return f"<section class=\"lane\"><div class=\"lane-head\"><h2>{html.escape(title)}</h2></div><p class=\"muted\">{html.escape(empty)}</p></section>"
    cards = "".join(ticker_card(row, mode=mode) for row in rows)
    return f"<section class=\"lane\"><div class=\"lane-head\"><h2>{html.escape(title)}</h2></div><div class=\"ticker-grid\">{cards}</div></section>"


def render_decision_cards(decisions: dict[str, Any]) -> str:
    labels = {
        "threshold": "Threshold",
        "short_gate": "Short Gate",
        "atr": "ATR Exit",
        "feedback": "Feedback Weight",
        "spec_risk": "Spec Risk",
    }
    cards: list[str] = []
    for key, label in labels.items():
        item = decisions.get(key, {}) if isinstance(decisions.get(key), dict) else {}
        status = item.get("status", "unknown")
        detail = item.get("detail", "No decision available.")
        cards.append(
            f"<article class=\"decision {status_class(status)}\">"
            f"<span>{html.escape(label)}</span>"
            f"<strong>{html.escape(str(status))}</strong>"
            f"<p>{_inline_markdown(str(detail))}</p>"
            "</article>"
        )
    return "".join(cards)


def render_research_cards(weekly: dict[str, Any]) -> str:
    tables = weekly.get("tables", {}) if isinstance(weekly.get("tables"), dict) else {}
    cards: list[str] = []

    full_factor = tables.get("full_factor_high_low_spread") or []
    if full_factor:
        best = max(full_factor, key=lambda row: abs(float(row.get("delta_avg_ret_high_minus_low", 0) or 0)))
        cards.append(
            f"<article class=\"insight\"><span>Factor clue</span><strong>{html.escape(str(best.get('factor_name', 'n/a')))}</strong>"
            f"<p>{html.escape(str(best.get('horizon_days', '')))}D {html.escape(str(best.get('watch_type', '')))} high-low "
            f"{format_metric(best.get('delta_avg_ret_high_minus_low'), '%')} · n≥{html.escape(str(best.get('min_n', '')))}</p></article>"
        )

    full_sensitivity = tables.get("full_sensitivity_matrix") or []
    candidates = [row for row in full_sensitivity if row.get("config_name") != "baseline_all"]
    if candidates:
        best = max(candidates, key=lambda row: float(row.get("delta_avg_ret_vs_baseline", 0) or 0))
        cards.append(
            f"<article class=\"insight\"><span>Sensitivity</span><strong>{html.escape(str(best.get('config_name', 'n/a')))}</strong>"
            f"<p>{html.escape(str(best.get('horizon_days', '')))}D {html.escape(str(best.get('watch_type', '')))} "
            f"{format_metric(best.get('delta_avg_ret_vs_baseline'), '%')} vs baseline · n={html.escape(str(best.get('n', '')))}</p></article>"
        )

    full_tail = tables.get("full_tail_risk_by_action") or []
    if full_tail:
        worst = min(full_tail, key=lambda row: float(row.get("worst_ret", 0) or 0))
        cards.append(
            f"<article class=\"insight warn\"><span>Tail risk</span><strong>{html.escape(str(worst.get('action', 'n/a')))}</strong>"
            f"<p>{html.escape(str(worst.get('horizon_days', '')))}D {html.escape(str(worst.get('watch_type', '')))} "
            f"worst {format_metric(worst.get('worst_ret'), '%')} · n={html.escape(str(worst.get('n', '')))}</p></article>"
        )

    full_short = tables.get("full_short_gate_promotion_watch") or []
    upgrade = next((row for row in full_short if row.get("verdict") == "watch_upgrade"), None)
    if upgrade:
        cards.append(
            f"<article class=\"insight\"><span>Shadow candidate</span><strong>{html.escape(str(upgrade.get('action', 'n/a')))}</strong>"
            f"<p>{html.escape(str(upgrade.get('horizon_days', '')))}D short below-ok "
            f"{format_metric(upgrade.get('delta_avg_ret_below_minus_ok'), '%')} · confidence {html.escape(str(upgrade.get('confidence', '')))}</p></article>"
        )

    return "".join(cards) if cards else "<p class=\"muted\">No research insights available yet.</p>"


def render_reading_queue(outdir: Path, theme_outdir: Path, verification_outdir: Path) -> str:
    queue = [
        ("1", "今天先看", "Daily Watchlist", theme_outdir / "daily_report.md"),
        ("2", "確認健康度", "Local Run Status", theme_outdir / "local_run_status.md"),
        ("3", "看規則是否該動", "Weekly Review", theme_outdir / "weekly_review.md"),
        ("4", "驗算細節", "Outcomes Summary", verification_outdir / "outcomes_summary.md"),
        ("5", "環境異常", "Local Doctor", theme_outdir / "local_doctor.md"),
    ]
    items = []
    for index, hint, title, path in queue:
        items.append(
            f"<a class=\"queue-item\" href=\"{html.escape(site_review_href(path, outdir))}\">"
            f"<b>{html.escape(index)}</b><span>{html.escape(hint)}</span><strong>{html.escape(title)}</strong>"
            "</a>"
        )
    return "".join(items)


def render_report_library(outdir: Path, theme_outdir: Path, verification_outdir: Path) -> str:
    cards: list[str] = []
    for title, path, description in report_specs(theme_outdir, verification_outdir):
        exists = path.exists()
        cards.append(
            f"<a class=\"library-card {'ready' if exists else 'missing'}\" href=\"{html.escape(site_review_href(path, outdir))}\">"
            f"<strong>{html.escape(title)}</strong>"
            f"<span>{html.escape(description)}</span>"
            f"<small>{'review' if exists else 'missing'}</small>"
            "</a>"
        )
    return "".join(cards)


def render_key_value_grid(items: list[tuple[str, object]]) -> str:
    return "<div class=\"kv-grid\">" + "".join(
        f"<span><b>{html.escape(label)}</b>{html.escape(str(value if value not in {'', None} else 'n/a'))}</span>" for label, value in items
    ) + "</div>"


def ticker_notes(row: dict[str, str]) -> list[str]:
    notes: list[str] = []
    grade = row.get("grade", "")
    spec = row.get("spec_risk_label", "")
    layer = row.get("layer", "")
    if grade in {"A", "B"}:
        notes.append(f"Grade `{grade}`：可以列入今日閱讀名單，但仍要搭配 risk/action label。")
    elif grade:
        notes.append(f"Grade `{grade}`：偏觀察，不建議直接追價。")
    if spec and spec != "正常":
        notes.append(f"Spec risk `{spec}`：先確認是否過熱、爆量或延伸過遠。")
    if layer == "short_attack":
        notes.append("屬於 short attack：重點看隔日/短線風險，不要用 mid-long 邏輯硬套。")
    elif layer == "midlong_core":
        notes.append("屬於 mid-long core：重點看主線延續與回檔承接。")
    signals = row.get("signals", "")
    if signals and signals != "NONE":
        notes.append(f"Signals: `{signals}`。")
    return notes or ["目前沒有額外註記，先回到 daily report 看上下文。"]


def render_ticker_detail_body(row: dict[str, str], outcomes: list[dict[str, str]]) -> str:
    ticker = html.escape(row.get("ticker", "unknown"))
    name = html.escape(row.get("name", ""))
    title = f"<h1>{ticker} <small>{name}</small></h1>"
    subtitle = html.escape(f"rank #{row.get('rank', 'n/a')} · {row.get('group', 'n/a')} / {row.get('layer', 'n/a')} · grade {row.get('grade', 'n/a')}")
    key_values = render_key_value_grid(
        [
            ("close", row.get("close", "n/a")),
            ("setup", row.get("setup_score", "n/a")),
            ("risk", row.get("risk_score", "n/a")),
            ("ret5", format_metric(row.get("ret5_pct"), "%")),
            ("ret20", format_metric(row.get("ret20_pct"), "%")),
            ("volume ratio", row.get("volume_ratio20", "n/a")),
            ("spec risk", row.get("spec_risk_label", "n/a")),
            ("spec subtype", row.get("spec_risk_subtype", "n/a")),
        ]
    )
    notes = "<ul>" + "".join(f"<li>{_inline_markdown(note)}</li>" for note in ticker_notes(row)) + "</ul>"
    outcome_rows = sorted(outcomes, key=lambda item: (item.get("signal_date", ""), item.get("horizon_days", ""), item.get("watch_type", "")), reverse=True)
    outcome_table = render_csv_table(
        [
            {
                "signal_date": item.get("signal_date", ""),
                "horizon": item.get("horizon_days", ""),
                "watch": item.get("watch_type", ""),
                "reco": item.get("reco_status", ""),
                "action": item.get("action", ""),
                "ret": item.get("realized_ret_pct", ""),
                "status": item.get("status", ""),
            }
            for item in outcome_rows[:30]
        ]
    )
    all_fields = render_csv_table([row])
    return (
        f"{title}<p class=\"muted\">{subtitle}</p>"
        f"<section class=\"panel\"><h2>At a glance</h2>{key_values}</section>"
        f"<section class=\"panel\"><h2>How to read it</h2>{notes}</section>"
        f"<section class=\"panel\"><h2>Recent verification outcomes</h2>{outcome_table}</section>"
        f"<section class=\"panel\"><h2>Current rank row</h2>{all_fields}</section>"
    )


def write_ticker_pages(outdir: Path, rank_rows: list[dict[str, str]], outcome_rows: list[dict[str, str]]) -> None:
    tickers_dir = outdir / "views" / "tickers"
    if tickers_dir.exists():
        shutil.rmtree(tickers_dir)
    by_ticker: dict[str, list[dict[str, str]]] = {}
    for row in outcome_rows:
        by_ticker.setdefault(row.get("ticker", ""), []).append(row)
    seen: set[str] = set()
    for row in rank_rows:
        ticker = row.get("ticker", "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        destination = ticker_detail_path(outdir, ticker)
        destination.parent.mkdir(parents=True, exist_ok=True)
        back_href = rel_href(outdir / "index.html", destination.parent)
        destination.write_text(
            _site_chrome(f"{ticker} {row.get('name', '')}", render_ticker_detail_body(row, by_ticker.get(ticker, [])), back_href=back_href),
            encoding="utf-8",
        )


def render_report(title: str, path: Path, description: str, outdir: Path, *, open_by_default: bool = False) -> str:
    content = read_text(path)
    href = html.escape(site_review_href(path, outdir))
    if not content:
        body = "<p class=\"muted\">Report not found yet.</p>"
    elif path.suffix == ".md":
        body = markdown_to_html(content)
    else:
        body = f"<pre><code>{html.escape(content[:5000])}</code></pre>"
    open_attr = " open" if open_by_default else ""
    updated = ""
    if path.exists():
        updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"<details class=\"report\"{open_attr}>"
        f"<summary><span><strong>{html.escape(title)}</strong><small>{html.escape(description)}</small></span>"
        f"<a href=\"{href}\">review page</a></summary>"
        f"<p class=\"muted\">Updated: {html.escape(updated or 'n/a')}</p>"
        f"<div class=\"markdown-body\">{body}</div>"
        "</details>"
    )


def build_site_html(*, outdir: Path, theme_outdir: Path, verification_outdir: Path) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    local_status = load_json(theme_outdir / "local_run_status.json")
    status_metrics = local_status.get("metrics", {}) if isinstance(local_status.get("metrics"), dict) else {}
    doctor = load_json(theme_outdir / "local_doctor.json")
    weekly = load_json(theme_outdir / "weekly_review.json")
    weekly_summary = weekly.get("summary", {}) if isinstance(weekly.get("summary"), dict) else {}
    weekly_decisions = weekly.get("decisions", {}) if isinstance(weekly.get("decisions"), dict) else {}
    rank_rows = sorted(load_all_csv_rows(theme_outdir / "daily_rank.csv"), key=_rank)
    outcome_rows = load_all_csv_rows(verification_outdir / "reco_outcomes.csv")

    metrics_html = "\n".join(
        [
            metric_card("Local Run", local_status.get("overall_status", "unknown"), str(local_status.get("mode", "")), kind=status_class(local_status.get("overall_status"))),
            metric_card("Doctor", doctor.get("overall", "unknown"), "Telegram warn is acceptable locally", kind=status_class(doctor.get("overall"))),
            metric_card("Verification Gate", status_metrics.get("verification_gate_status", "unknown"), "duplicate/missing checks", kind=status_class(status_metrics.get("verification_gate_status"))),
            metric_card("Snapshots", status_metrics.get("snapshot_rows", "n/a"), f"latest {status_metrics.get('latest_snapshot_signal_date', 'n/a')}", kind="neutral"),
            metric_card("Outcomes", status_metrics.get("outcome_rows", "n/a"), f"ok {status_metrics.get('outcome_ok_rows', 0)} / pending {status_metrics.get('outcome_pending_rows', 0)}", kind="neutral"),
            metric_card("Missing Price Rows", f"{status_metrics.get('signal_date_missing_rows', 0)} / {status_metrics.get('no_price_series_rows', 0)}", "signal_date_missing / no_price_series", kind="ok" if not status_metrics.get("signal_date_missing_rows") and not status_metrics.get("no_price_series_rows") else "warn"),
            metric_card("Duplicate Keys", f"{status_metrics.get('snapshot_dup_keys', 0)} / {status_metrics.get('outcome_dup_keys', 0)}", "snapshot / outcome", kind="ok" if not status_metrics.get("snapshot_dup_keys") and not status_metrics.get("outcome_dup_keys") else "bad"),
            metric_card("Weekly Status", weekly_summary.get("status", "ready"), str(weekly.get("generated_at", "")), kind=status_class(weekly_summary.get("status", "ok"))),
        ]
    )

    short_rows = [row for row in rank_rows if row.get("layer") == "short_attack" and row.get("grade") in {"A", "B"}][:6]
    midlong_rows = [row for row in rank_rows if row.get("layer") == "midlong_core" and row.get("grade") in {"A", "B"}][:6]
    risk_rows = [
        row
        for row in rank_rows
        if row.get("spec_risk_label") and row.get("spec_risk_label") != "正常"
    ][:6]
    conclusion_lines = build_daily_conclusion(local_status=local_status, weekly=weekly, rank_rows=rank_rows)

    daily_preview = render_csv_preview(
        "Daily Rank Preview",
        load_csv_rows(theme_outdir / "daily_rank.csv", limit=12),
        ["rank", "ticker", "name", "group", "layer", "grade", "setup_score", "risk_score", "spec_risk_label"],
    )
    outcomes_preview = render_csv_preview(
        "Outcome Preview",
        load_csv_rows(verification_outdir / "reco_outcomes.csv", limit=12),
        ["signal_date", "horizon_days", "watch_type", "ticker", "name", "reco_status", "action", "realized_ret_pct", "status"],
    )
    shadow_preview = render_csv_preview(
        "Shadow 開高不追 Preview",
        load_csv_rows(theme_outdir / "shadow_open_not_chase_candidates.csv", limit=12),
        ["rank", "ticker", "name", "scenario_label", "market_heat", "spec_risk_bucket", "shadow_eligible", "shadow_status"],
    )

    report_library = render_report_library(outdir, theme_outdir, verification_outdir)

    daily_rows = csv_row_count(theme_outdir / "daily_rank.csv")
    outcome_rows = csv_row_count(verification_outdir / "reco_outcomes.csv")

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stock Watch Local Dashboard</title>
<style>
:root {{
  --bg: #0b1020;
  --panel: rgba(255,255,255,.075);
  --panel-strong: rgba(255,255,255,.12);
  --line: rgba(255,255,255,.14);
  --text: #eef4ff;
  --muted: #aab8d8;
  --accent: #7dd3fc;
  --ok: #86efac;
  --warn: #fde68a;
  --bad: #fca5a5;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background:
    radial-gradient(circle at top left, rgba(59,130,246,.35), transparent 36rem),
    radial-gradient(circle at top right, rgba(20,184,166,.22), transparent 34rem),
    var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", sans-serif;
  line-height: 1.55;
}}
a {{ color: inherit; }}
.shell {{ max-width: 1240px; margin: 0 auto; padding: 32px 20px 80px; }}
.hero {{ display: grid; gap: 18px; margin-bottom: 24px; }}
.hero h1 {{ font-size: clamp(2rem, 5vw, 4.6rem); line-height: 1; margin: 0; letter-spacing: -.05em; }}
.hero p {{ max-width: 860px; color: var(--muted); margin: 0; font-size: 1.05rem; }}
.pill-row {{ display: flex; flex-wrap: wrap; gap: 10px; }}
.pill {{ border: 1px solid var(--line); background: var(--panel); border-radius: 999px; padding: 7px 12px; color: var(--muted); font-size: .9rem; text-decoration: none; }}
.grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }}
.metric, .panel, .report, .artifact {{
  border: 1px solid var(--line);
  background: linear-gradient(180deg, var(--panel-strong), var(--panel));
  border-radius: 20px;
  box-shadow: 0 20px 70px rgba(0,0,0,.25);
}}
.metric {{ padding: 18px; min-height: 132px; }}
.metric span, .metric small {{ display: block; color: var(--muted); }}
.metric strong {{ display: block; font-size: 1.9rem; margin: 8px 0 4px; overflow-wrap: anywhere; }}
.metric.ok strong {{ color: var(--ok); }}
.metric.warn strong {{ color: var(--warn); }}
.metric.bad strong {{ color: var(--bad); }}
.section-title {{ margin: 32px 0 12px; font-size: 1.35rem; }}
.artifact-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
.artifact, .library-card, .queue-item {{ padding: 14px 16px; text-decoration: none; display: flex; flex-direction: column; gap: 4px; }}
.artifact span {{ color: var(--muted); font-size: .85rem; }}
.artifact.ready strong::before {{ content: "● "; color: var(--ok); }}
.artifact.missing strong::before {{ content: "● "; color: var(--bad); }}
.hero-card {{ border: 1px solid var(--line); background: linear-gradient(180deg, rgba(125,211,252,.14), var(--panel)); border-radius: 24px; padding: 22px; }}
.hero-card h2 {{ margin: 0 0 8px; font-size: 1.5rem; }}
.hero-card p {{ color: var(--text); }}
.conclusion-list {{ margin: 0; padding-left: 20px; display: grid; gap: 8px; }}
.conclusion-list li {{ color: var(--text); }}
.queue-grid, .decision-grid, .insight-grid, .library-grid {{ display: grid; gap: 12px; }}
.queue-grid {{ grid-template-columns: repeat(5, minmax(0, 1fr)); }}
.queue-item, .decision, .insight, .library-card, .ticker-card {{
  border: 1px solid var(--line);
  background: linear-gradient(180deg, var(--panel-strong), var(--panel));
  border-radius: 18px;
}}
.queue-item b {{ width: 30px; height: 30px; border-radius: 50%; background: rgba(125,211,252,.18); color: var(--accent); display: inline-grid; place-items: center; }}
.queue-item span, .library-card span, .decision span, .insight span {{ color: var(--muted); font-size: .85rem; }}
.queue-item strong, .library-card strong {{ color: var(--text); }}
.lane {{ margin-top: 18px; }}
.lane-head {{ display: flex; align-items: end; justify-content: space-between; gap: 12px; margin-bottom: 10px; }}
.lane-head h2 {{ margin: 0; }}
.ticker-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
.ticker-card {{ padding: 16px; border-left: 4px solid var(--accent); text-decoration: none; color: inherit; display: block; transition: transform .15s ease, border-color .15s ease; }}
.ticker-card:hover {{ transform: translateY(-2px); border-color: var(--accent); }}
.ticker-card.risk-high {{ border-left-color: var(--bad); }}
.ticker-card.risk-watch {{ border-left-color: var(--warn); }}
.ticker-card h3 {{ margin: 8px 0 2px; font-size: 1.2rem; }}
.ticker-card h3 small {{ color: var(--muted); font-weight: 500; }}
.ticker-card p, .ticker-card em {{ color: var(--muted); margin: 0; font-style: normal; }}
.ticker-top {{ display: flex; justify-content: space-between; align-items: center; }}
.rank, .grade {{ border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; color: var(--muted); }}
.mini-stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin: 12px 0; }}
.mini-stats span {{ background: rgba(255,255,255,.07); border-radius: 10px; padding: 7px; color: var(--text); }}
.mini-stats b {{ display: block; color: var(--muted); font-size: .72rem; }}
.decision-grid {{ grid-template-columns: repeat(5, minmax(0, 1fr)); }}
.decision, .insight {{ padding: 16px; }}
.decision strong, .insight strong {{ display: block; font-size: 1.35rem; margin: 4px 0; }}
.decision.ok strong {{ color: var(--ok); }}
.decision.warn strong {{ color: var(--warn); }}
.decision.bad strong {{ color: var(--bad); }}
.decision p, .insight p {{ color: var(--muted); margin: 0; font-size: .92rem; }}
.insight-grid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
.insight.warn {{ border-left: 4px solid var(--warn); }}
.library-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
.library-card small {{ color: var(--accent); }}
.kv-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
.kv-grid span {{ background: rgba(255,255,255,.07); border-radius: 12px; padding: 10px; }}
.kv-grid b {{ display: block; color: var(--muted); font-size: .78rem; margin-bottom: 3px; }}
.panel {{ margin-top: 14px; padding: 18px; overflow: hidden; }}
.panel h2 {{ margin: 0 0 12px; }}
.report {{ margin: 12px 0; overflow: hidden; }}
.report summary {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 20px; cursor: pointer; }}
.report summary strong {{ display: block; font-size: 1.1rem; }}
.report summary small {{ display: block; color: var(--muted); margin-top: 3px; }}
.report summary a {{ color: var(--accent); text-decoration: none; white-space: nowrap; }}
.report > .muted {{ margin: 0; padding: 0 20px 12px; }}
.markdown-body {{ padding: 0 20px 22px; max-height: 760px; overflow: auto; border-top: 1px solid var(--line); }}
.markdown-body h1, .markdown-body h2, .markdown-body h3 {{ scroll-margin-top: 24px; }}
.markdown-body code, pre code {{ background: rgba(255,255,255,.1); border-radius: 7px; padding: 1px 5px; }}
pre {{ overflow: auto; background: rgba(0,0,0,.25); padding: 14px; border-radius: 14px; }}
.muted {{ color: var(--muted); }}
.table-wrap {{ overflow: auto; border: 1px solid var(--line); border-radius: 14px; }}
table {{ width: 100%; border-collapse: collapse; min-width: 720px; }}
th, td {{ padding: 9px 10px; border-bottom: 1px solid rgba(255,255,255,.1); text-align: left; vertical-align: top; }}
th {{ color: #dbeafe; background: rgba(255,255,255,.08); position: sticky; top: 0; }}
td {{ color: #dbe7ff; }}
.footer {{ margin-top: 28px; color: var(--muted); }}
@media (max-width: 980px) {{
  .grid, .artifact-grid, .ticker-grid, .decision-grid, .insight-grid, .library-grid, .queue-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
@media (max-width: 620px) {{
  .grid, .artifact-grid, .ticker-grid, .decision-grid, .insight-grid, .library-grid, .queue-grid {{ grid-template-columns: 1fr; }}
  .report summary {{ align-items: flex-start; flex-direction: column; }}
}}
</style>
</head>
<body>
<main class="shell">
  <section class="hero">
    <div class="pill-row">
      <span class="pill">Generated {html.escape(generated_at)}</span>
      <span class="pill">daily_rank rows: {daily_rows}</span>
      <span class="pill">outcome rows: {outcome_rows}</span>
    </div>
    <h1>Stock Watch Local Dashboard</h1>
    <p>把 daily、portfolio、verification、weekly、doctor、shadow tuning、watchlist expansion 跟 runbook 收到同一個本機入口。這頁是靜態 HTML，可以直接開檔，也可以用本機 HTTP server 看。</p>
    <div class="pill-row">
      <a class="pill" href="#reports">Report Library</a>
      <a class="pill" href="#previews">Data Previews</a>
      <a class="pill" href="#artifacts">Raw Artifacts</a>
    </div>
  </section>

  <section class="grid">{metrics_html}</section>

  <h2 class="section-title">Read First</h2>
  <section class="hero-card">
    <h2>今天的重點</h2>
    {render_daily_conclusion(conclusion_lines)}
  </section>

  <h2 class="section-title">Reading Queue</h2>
  <section class="queue-grid">{render_reading_queue(outdir, theme_outdir, verification_outdir)}</section>

  {render_ticker_lane("Short Attack — 先看這些", attach_ticker_hrefs(short_rows, outdir), mode="short", empty="No A/B short-attack rows today.")}
  {render_ticker_lane("Mid-Long Core — 可追蹤主線", attach_ticker_hrefs(midlong_rows, outdir), mode="midlong", empty="No A/B mid-long rows today.")}
  {render_ticker_lane("Spec Risk — 不要追太快", attach_ticker_hrefs(risk_rows, outdir), mode="risk", empty="No non-normal spec-risk rows today.")}

  <h2 class="section-title">Rule Decisions</h2>
  <section class="decision-grid">{render_decision_cards(weekly_decisions)}</section>

  <h2 class="section-title">Research Signals</h2>
  <section class="insight-grid">{render_research_cards(weekly)}</section>

  <h2 id="artifacts" class="section-title">Raw Artifacts</h2>
  <section class="artifact-grid">{artifact_cards(outdir, theme_outdir, verification_outdir)}</section>

  <h2 id="previews" class="section-title">Data Previews</h2>
  {daily_preview}
  {outcomes_preview}
  {shadow_preview}

  <h2 id="reports" class="section-title">Report Library</h2>
  <section class="library-grid">{report_library}</section>

  <p class="footer">Serve locally with <code>python3.11 -m http.server 8765 --directory {html.escape(str(outdir))}</code>, then open <code>http://localhost:8765</code>.</p>
</main>
</body>
</html>
"""


def write_local_website(*, outdir: Path = DEFAULT_SITE_DIR, theme_outdir: Path = THEME_OUTDIR, verification_outdir: Path = VERIFICATION_OUTDIR) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    copy_site_artifacts(outdir, theme_outdir, verification_outdir)
    write_review_pages(outdir, theme_outdir, verification_outdir)
    rank_rows = sorted(load_all_csv_rows(theme_outdir / "daily_rank.csv"), key=_rank)
    outcome_rows = load_all_csv_rows(verification_outdir / "reco_outcomes.csv")
    write_ticker_pages(outdir, rank_rows, outcome_rows)
    index_path = outdir / "index.html"
    index_path.write_text(
        build_site_html(outdir=outdir, theme_outdir=theme_outdir, verification_outdir=verification_outdir),
        encoding="utf-8",
    )
    return index_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    index_path = write_local_website(outdir=args.outdir, theme_outdir=args.theme_outdir, verification_outdir=args.verification_outdir)
    print(f"Wrote {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
