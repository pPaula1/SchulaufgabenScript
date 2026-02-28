#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Optional validation
try:
    from jsonschema import Draft202012Validator
    HAS_JSONSCHEMA = True
except Exception:
    HAS_JSONSCHEMA = False


# -------------------------
# Helpers
# -------------------------

def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def validate_json(instance: Dict[str, Any], schema_path: Path, label: str) -> None:
    if not HAS_JSONSCHEMA:
        return
    schema = read_json(schema_path)
    v = Draft202012Validator(schema)
    errors = sorted(v.iter_errors(instance), key=lambda e: e.path)
    if errors:
        print(f"\n❌ Validation failed for {label}: {schema_path.name}")
        for e in errors[:20]:
            loc = ".".join([str(x) for x in e.path]) or "<root>"
            print(f" - {loc}: {e.message}")
        raise SystemExit(2)

def which(cmd: str) -> Optional[str]:
    from shutil import which as _which
    return _which(cmd)

def resolve_asset_path(project_root: Path, base_dir: Path, p: str) -> Path:
    """
    Resolve asset paths robustly:
    - If p is absolute: use it
    - Else try project_root / p
    - Else try base_dir / p
    """
    cand = Path(p)
    if cand.is_absolute():
        return cand
    pr = project_root / cand
    if pr.exists():
        return pr
    bd = base_dir / cand
    return bd

def tex_escape_minimal(s: str) -> str:
    """
    We assume most text is LaTeX-safe already (because you embed math etc.).
    So we do NOT hard-escape everything.
    This only normalizes Windows newlines and ensures it’s a str.
    """
    return str(s).replace("\r\n", "\n")


# -------------------------
# LaTeX rendering
# -------------------------

def latex_preamble() -> str:
    return r"""
\documentclass[12pt,a4paper]{article}
\usepackage[margin=2cm]{geometry}
\usepackage{graphicx}
\usepackage{tabularx}
\usepackage{array}
\usepackage{enumitem}
\usepackage{hyperref}
\usepackage{float}

\setlength{\parindent}{0pt}
\setlength{\parskip}{6pt}

% A simple checkbox
\newcommand{\checkbox}{\(\square\)}

% A line field
\newcommand{\linefield}[1]{\rule{#1}{0.4pt}}

% Task heading
\newcommand{\tasktitle}[2]{\vspace{6pt}\textbf{#1}\hfill /#2\par\vspace{4pt}}

\usepackage{amssymb}
\providecommand{\checkbox}{$\square$}

\begin{document}
""".lstrip()

def latex_end() -> str:
    return r"\end{document}"

def render_header(project_root: Path, exam: Dict[str, Any], school: Dict[str, Any]) -> str:
    # Resolve logo path
    school_file_dir = None  # will be passed by caller if needed, but keep simple
    logo_path = resolve_asset_path(project_root, project_root, school["logo"])
    if not logo_path.exists():
        raise FileNotFoundError(f"School logo not found: {logo_path}")

    title = tex_escape_minimal(exam["title"])
    subject = tex_escape_minimal(exam["subject"])
    clazz = tex_escape_minimal(exam["class"])
    date = tex_escape_minimal(exam["date"])

    # Header fields from school.json
    fields = school.get("header_fields", [])

    # We render a simple table like:
    # [Logo] [Title | Subject | Date]
    #       [Name line | Class | Note]
    #       [Parents signature line]
    #       [Checkbox group LRSt IRSt ILSt]
    #
    # This is intentionally "no fancy layout settings", just generic rendering.
    rows: List[str] = []

    # First fixed row: title/subject/date
    rows.append(rf"\textbf{{{title}}} & Fach: {subject} & Datum: {date} \\ \hline")

    # Then render school header_fields in a generic way.
    # We will place "student_name", "class", "note" into one row if present.
    def find_field(key: str) -> Optional[Dict[str, Any]]:
        for f in fields:
            if f.get("key") == key:
                return f
        return None

    student = find_field("student_name")
    cls_f = find_field("class")
    note = find_field("note")

    if student or cls_f or note:
        student_label = student["label"] if student else "Name"
        cls_label = cls_f["label"] if cls_f else "Klasse"
        note_label = note["label"] if note else "Nr./Note"
        rows.append(
            rf"{student_label}: \linefield{{7cm}} & {cls_label}: \linefield{{3cm}} & {note_label}: \linefield{{3cm}} \\ \hline"
        )

    # Remaining text_line fields (excluding the three above) each get full-width row
    skip = {"student_name", "class", "note"}
    for f in fields:
        if f.get("kind") == "text_line" and f.get("key") not in skip:
            label = tex_escape_minimal(f.get("label", f.get("key", "")))
            rows.append(rf"\multicolumn{{3}}{{|l|}}{{{label}: \linefield{{12cm}}}} \\ \hline")

    # Checkbox groups
    for f in fields:
        if f.get("kind") == "checkbox_group":
            opts = f.get("options", [])
            label = tex_escape_minimal(f.get("label", ""))
            if label.strip():
                prefix = label + " "
            else:
                prefix = ""
            boxes = " \quad ".join([rf"{tex_escape_minimal(o)} {r'\checkbox'}" for o in opts])
            rows.append(rf"\multicolumn{{3}}{{|l|}}{{{prefix}{boxes}}} \\ \hline")

    header = rf"""
\begin{{tabular}}{{p{{0.22\textwidth}} p{{0.76\textwidth}}}}
  \includegraphics[width=\linewidth]{{{logo_path.as_posix()}}} &
  \renewcommand{{\arraystretch}}{{1.3}}
  \begin{{tabular}}{{|p{{0.40\textwidth}}|p{{0.25\textwidth}}|p{{0.25\textwidth}}|}}
    \hline
    {("\n    ".join(rows))}
  \end{{tabular}}
\end{{tabular}}

\vspace{{8pt}}
""".lstrip()
    return header

def render_workspace_block(block: Dict[str, Any]) -> str:
    t = block["type"]
    if t == "lines":
        n = int(block["lines"])
        # simple: n lines
        lines = "\n".join([r"\linefield{16cm}\\[6pt]" for _ in range(n)])
        return lines + "\n"
    if t == "blank":
        h = float(block["height_cm"])
        # blank box
        return rf"\vspace{{{h}cm}}" + "\n"
    if t == "box":
        h = float(block["height_cm"])
        title = tex_escape_minimal(block.get("box_title", ""))
        if title:
            return rf"\textbf{{{title}}}\par\vspace{{2pt}}\fbox{{\parbox[t][{h}cm][t]{{\linewidth}}{{}}}}\n"
        return rf"\fbox{{\parbox[t][{h}cm][t]{{\linewidth}}{{}}}}\n"
    # grid/coord could be done with tikz later; for v1 treat like blank.
    if t in ("grid", "coord"):
        h = float(block.get("height_cm", 6))
        return rf"\vspace{{{h}cm}}" + "\n"
    return ""

def render_task(project_root: Path, task_dir: Path, task: Dict[str, Any], index: int) -> str:
    name = tex_escape_minimal(task["name"])
    points = task["points"]
    statement = tex_escape_minimal(task["statement"])
    mode = (task.get("render") or {}).get("mode", "text")

    out: List[str] = []
    out.append(rf"\tasktitle{{Aufgabe {index}: {name}}}{{{points}}}")
    out.append(statement + "\n")

    assets = task.get("assets", [])
    workspace = task.get("workspace", [])

    # Layout mode: include the layout image big
    if mode == "layout":
        layout_asset = None
        for a in assets:
            if a.get("role") == "layout":
                layout_asset = a
                break
        if layout_asset is None and assets:
            layout_asset = assets[0]

        if not layout_asset:
            raise ValueError(f"Task {task['id']} is render.mode=layout but has no assets.")

        p = resolve_asset_path(project_root, task_dir, layout_asset["path"])
        if not p.exists():
            raise FileNotFoundError(f"Task layout asset not found: {p}")

        width = layout_asset.get("width", r"\linewidth")
        out.append(rf"\begin{{center}}\includegraphics[width={width}]{{{p.as_posix()}}}\end{{center}}")
        out.append("\n")
        return "\n".join(out)

    # Text mode: render figure assets (role=figure) below statement
    for a in assets:
        if a.get("role", "figure") != "figure":
            continue
        p = resolve_asset_path(project_root, task_dir, a["path"])
        if not p.exists():
            raise FileNotFoundError(f"Task figure asset not found: {p}")
        width = a.get("width", r"0.8\linewidth")
        cap = a.get("caption")
        if cap:
            out.append(rf"\begin{{figure}}[H]\centering\includegraphics[width={width}]{{{p.as_posix()}}}\caption{{{tex_escape_minimal(cap)}}}\end{{figure}}")
        else:
            out.append(rf"\begin{{center}}\includegraphics[width={width}]{{{p.as_posix()}}}\end{{center}}")

    # Parts
    parts = task.get("parts", [])
    if parts:
        out.append(r"\begin{enumerate}[label=\alph*)]")
        for part in parts:
            out.append(r"\item " + tex_escape_minimal(part["text"]))
            # per-part workspace
            for wb in part.get("workspace", []):
                out.append(render_workspace_block(wb))
        out.append(r"\end{enumerate}")

    # Workspace after task
    for wb in workspace:
        out.append(render_workspace_block(wb))

    out.append(r"\vspace{6pt}\hrule\vspace{6pt}")
    return "\n".join(out)


# -------------------------
# Main build
# -------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("exam_json", type=str, help="Path to exams/<exam>.json")
    ap.add_argument("--project-root", type=str, default=".", help="Project root (default: current dir)")
    ap.add_argument("--outdir", type=str, default="out", help="Output directory (default: out/)")
    ap.add_argument("--no-validate", action="store_true", help="Skip jsonschema validation")
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    exam_path = Path(args.exam_json).resolve()
    outdir = (project_root / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    exam = read_json(exam_path)

    # Schemas
    schemas_dir = project_root / "schemas"
    task_schema = schemas_dir / "task.schema.json"
    school_schema = schemas_dir / "school.schema.json"
    exam_schema = schemas_dir / "exam.schema.json"

    # Validate exam if schema exists and jsonschema installed
    if not args.no_validate and HAS_JSONSCHEMA and exam_schema.exists():
        validate_json(exam, exam_schema, f"exam ({exam_path.name})")

    # Load school
    school_id = exam["school_id"]
    school_path = next(
        (project_root / "schools").glob(f"**/{school_id}.json"),
        None
    )

    if school_path is None:
        raise FileNotFoundError(
            f"School file not found for id '{school_id}' inside schools/"
        )
    if not school_path.exists():
        raise FileNotFoundError(f"School file not found: {school_path}")
    school = read_json(school_path)

    if not args.no_validate and HAS_JSONSCHEMA and school_schema.exists():
        validate_json(school, school_schema, f"school ({school_path.name})")

    # Build LaTeX
    tex_parts: List[str] = []
    tex_parts.append(latex_preamble())
    tex_parts.append(render_header(project_root, exam, school))

    # Load and render tasks
    total_points = 0.0
    for i, tref in enumerate(exam["tasks"], start=1):
        task_id = tref["id"]
        task_dir = project_root / "tasks" / task_id
        task_path = task_dir / "task.json"
        if not task_path.exists():
            raise FileNotFoundError(f"Task JSON not found: {task_path}")

        task = read_json(task_path)

        if not args.no_validate and HAS_JSONSCHEMA and task_schema.exists():
            validate_json(task, task_schema, f"task ({task_id})")

        pts = float(tref.get("points_override", task["points"]))
        total_points += pts

        # If override, apply to render output
        task_for_render = dict(task)
        task_for_render["points"] = pts

        if tref.get("page_break_before") or (task_for_render.get("render") or {}).get("page_break_before"):
            tex_parts.append(r"\newpage")

        tex_parts.append(render_task(project_root, task_dir, task_for_render, i))

    # Total points line at end (optional)
    tex_parts.append(rf"\par\textbf{{Gesamtpunkte:}} {total_points}\par")

    tex_parts.append(latex_end())

    exam_id = exam["id"]
    tex_path = outdir / f"{exam_id}.tex"
    tex_path.write_text("\n".join(tex_parts), encoding="utf-8")

    # Compile
    pdf_path = outdir / f"{exam_id}.pdf"
    print(f"✅ Wrote TeX: {tex_path}")

    latexmk = which("latexmk")
    pdflatex = which("pdflatex")

    latexmk = which("latexmk")
    pdflatex = which("pdflatex")

    # On Windows, latexmk often requires Perl; prefer pdflatex.
    if pdflatex:
        # run twice for references (simple approach)
        for _ in range(2):
            cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
                   f"-output-directory={outdir.as_posix()}", tex_path.as_posix()]
            print("▶ Running:", " ".join(cmd))
            subprocess.run(cmd, check=True)
    elif latexmk:
        cmd = ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error",
               f"-outdir={outdir.as_posix()}", tex_path.as_posix()]
        print("▶ Running:", " ".join(cmd))
        subprocess.run(cmd, check=True)
    else:
        raise RuntimeError("Neither latexmk nor pdflatex found in PATH. Install TeX Live/MiKTeX.")

    if pdf_path.exists():
        print(f"🎉 PDF created: {pdf_path}")
    else:
        print("⚠️ Build finished but PDF not found. Check LaTeX logs.", file=sys.stderr)
        raise SystemExit(3)


if __name__ == "__main__":
    main()