#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    errors = sorted(v.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        print(f"\n❌ Validation failed for {label}: {schema_path.name}")
        for e in errors[:30]:
            loc = ".".join([str(x) for x in e.path]) or "<root>"
            print(f" - {loc}: {e.message}")
        raise SystemExit(2)

def which(cmd: str) -> Optional[str]:
    from shutil import which as _which
    return _which(cmd)

def resolve_asset_path(project_root: Path, base_dir: Path, p: str) -> Path:
    cand = Path(p)
    if cand.is_absolute():
        return cand
    pr = project_root / cand
    if pr.exists():
        return pr
    bd = base_dir / cand
    return bd

def tex_escape_minimal(s: Any) -> str:
    # We assume most strings are already LaTeX-safe (math etc.)
    return str(s).replace("\r\n", "\n")


# -------------------------
# LaTeX rendering
# -------------------------

def latex_preamble() -> str:
    # XeLaTeX-friendly preamble (works with pdflatex too, but fontspec needs XeLaTeX/LuaLaTeX)
    return r"""
\documentclass[12pt,a4paper]{article}
\usepackage[margin=2cm]{geometry}
\usepackage{graphicx}
\usepackage{tabularx}
\usepackage{array}
\usepackage{enumitem}
\usepackage{hyperref}
\usepackage{float}

% Unicode friendly (XeLaTeX)
\usepackage{fontspec}
\defaultfontfeatures{Ligatures=TeX}

% For checkbox symbol
\usepackage{amssymb}
\providecommand{\checkbox}{$\square$}

% For grids
\usepackage{tikz}
\usetikzlibrary{calc}

\setlength{\parindent}{0pt}
\setlength{\parskip}{6pt}

% A line field
\newcommand{\linefield}[1]{\rule{#1}{0.4pt}}

% Task heading
\newcommand{\tasktitle}[2]{\vspace{6pt}\textbf{#1}\hfill /#2\par\vspace{4pt}}

\begin{document}
""".lstrip()

def latex_end() -> str:
    return r"\end{document}"


def render_header_template(
    project_root: Path,
    school_base_dir: Path,
    exam: Dict[str, Any],
    school: Dict[str, Any],
    total_points: float,
) -> str:
    header_path = project_root / "templates" / "header.tex"
    if not header_path.exists():
        raise FileNotFoundError(f"Header template not found: {header_path}")

    header = header_path.read_text(encoding="utf-8")

    logo_path = resolve_asset_path(project_root, school_base_dir, school["logo"])
    if not logo_path.exists():
        raise FileNotFoundError(f"School logo not found: {logo_path}")

    # Make points pretty
    tp = int(total_points) if float(total_points).is_integer() else total_points

    defs = "\n".join([
        rf"\def\SchoolLogo{{{logo_path.as_posix()}}}",
        rf"\def\ExamTitle{{{tex_escape_minimal(exam['title'])}}}",
        rf"\def\ExamSubject{{{tex_escape_minimal(exam['subject'])}}}",
        rf"\def\ExamDate{{{tex_escape_minimal(exam['date'])}}}",
        rf"\def\ExamClass{{{tex_escape_minimal(exam['class'])}}}",
        rf"\def\TotalPoints{{{tp}}}",
    ])

    return defs + "\n\n" + header + "\n"


def render_workspace_block(block: Dict[str, Any]) -> str:
    t = block["type"]

    if t == "lines":
        n = int(block["lines"])
        # simple writing lines
        lines = "\n".join([r"\linefield{16cm}\\[6pt]" for _ in range(n)])
        return lines + "\n"

    if t == "blank":
        h = float(block["height_cm"])
        return rf"\vspace{{{h}cm}}" + "\n"

    if t == "box":
        h = float(block["height_cm"])
        title = tex_escape_minimal(block.get("box_title", ""))
        if title.strip():
            return rf"\textbf{{{title}}}\par\vspace{{2pt}}\fbox{{\parbox[t][{h}cm][t]{{\linewidth}}{{}}}}\n"
        return rf"\fbox{{\parbox[t][{h}cm][t]{{\linewidth}}{{}}}}\n"

    if t == "grid":
        # IMPORTANT: TikZ cannot use \linewidth as a coordinate length directly.
        # We draw using a fixed width that matches typical text width (~16cm with 2cm margins).
        # Adjust GRID_WIDTH_CM if you change margins.
        GRID_WIDTH_CM = 16.0

        h = float(block.get("height_cm", 4.0))
        grid = block.get("grid", "karo_5mm")

        if grid == "karo_5mm":
            step = "0.5cm"
        elif grid == "karo_1cm":
            step = "1cm"
        elif grid == "millimeter":
            step = "0.1cm"
        else:
            step = "0.5cm"

        return rf"""
\begin{{center}}
\begin{{tikzpicture}}
  \draw[step={step}, very thin] (0,0) grid ({GRID_WIDTH_CM}cm, {h}cm);
  \draw[line width=0.4pt] (0,0) rectangle ({GRID_WIDTH_CM}cm, {h}cm);
\end{{tikzpicture}}
\end{{center}}
""".lstrip()

    if t == "coord":
        GRID_WIDTH_CM = 16.0
        h = float(block.get("height_cm", 6.0))
        return rf"""
\begin{{center}}
\begin{{tikzpicture}}
  \draw[step=0.5cm, very thin] (0,0) grid ({GRID_WIDTH_CM}cm, {h}cm);
  \draw[line width=0.4pt] (0,0) rectangle ({GRID_WIDTH_CM}cm, {h}cm);
\end{{tikzpicture}}
\end{{center}}
""".lstrip()

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

    # Layout mode: include a layout image big
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

    # Text mode: render figure assets
    for a in assets:
        if a.get("role", "figure") != "figure":
            continue
        p = resolve_asset_path(project_root, task_dir, a["path"])
        if not p.exists():
            raise FileNotFoundError(f"Task figure asset not found: {p}")
        width = a.get("width", r"0.8\linewidth")
        cap = a.get("caption")
        if cap:
            out.append(
                rf"\begin{{figure}}[H]\centering\includegraphics[width={width}]{{{p.as_posix()}}}\caption{{{tex_escape_minimal(cap)}}}\end{{figure}}"
            )
        else:
            out.append(rf"\begin{{center}}\includegraphics[width={width}]{{{p.as_posix()}}}\end{{center}}")

    # Parts
    parts = task.get("parts", [])
    if parts:
        out.append(r"\begin{enumerate}[label=\alph*)]")
        for part in parts:
            out.append(r"\item " + tex_escape_minimal(part["text"]))
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

    if not args.no_validate and HAS_JSONSCHEMA and exam_schema.exists():
        validate_json(exam, exam_schema, f"exam ({exam_path.name})")

    # Load school
    school_id = exam["school_id"]
    school_path = next((project_root / "schools").glob(f"**/{school_id}.json"), None)
    if school_path is None or not school_path.exists():
        raise FileNotFoundError(f"School file not found for id '{school_id}' inside schools/")

    school = read_json(school_path)
    if not args.no_validate and HAS_JSONSCHEMA and school_schema.exists():
        validate_json(school, school_schema, f"school ({school_path.name})")

    school_base_dir = school_path.parent

    # Precompute total points (so header can show it)
    total_points = 0.0
    for tref in exam["tasks"]:
        task_id = tref["id"]
        task_path = project_root / "tasks" / task_id / "task.json"
        if not task_path.exists():
            raise FileNotFoundError(f"Task JSON not found: {task_path}")
        task = read_json(task_path)
        pts = float(tref.get("points_override", task["points"]))
        total_points += pts

    # Build LaTeX
    tex_parts: List[str] = []
    tex_parts.append(latex_preamble())
    tex_parts.append(render_header_template(project_root, school_base_dir, exam, school, total_points))

    # Render tasks
    for i, tref in enumerate(exam["tasks"], start=1):
        task_id = tref["id"]
        task_dir = project_root / "tasks" / task_id
        task_path = task_dir / "task.json"
        task = read_json(task_path)

        if not args.no_validate and HAS_JSONSCHEMA and task_schema.exists():
            validate_json(task, task_schema, f"task ({task_id})")

        pts = float(tref.get("points_override", task["points"]))
        task_for_render = dict(task)
        task_for_render["points"] = pts

        if tref.get("page_break_before") or (task_for_render.get("render") or {}).get("page_break_before"):
            tex_parts.append(r"\newpage")

        tex_parts.append(render_task(project_root, task_dir, task_for_render, i))

    tex_parts.append(latex_end())

    exam_id = exam["id"]
    tex_path = outdir / f"{exam_id}.tex"
    tex_path.write_text("\n".join(tex_parts), encoding="utf-8")
    print(f"✅ Wrote TeX: {tex_path}")

    # Compile (prefer xelatex for Unicode)
    compiler = which("xelatex") or which("pdflatex")
    if compiler is None:
        raise RuntimeError("Neither xelatex nor pdflatex found in PATH. Install MiKTeX/TeX Live and add to PATH.")

    # Run twice for references
    for _ in range(2):
        cmd = [
            compiler,
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={outdir.as_posix()}",
            tex_path.as_posix(),
        ]
        print("▶ Running:", " ".join(cmd))
        subprocess.run(cmd, check=True)

    pdf_path = outdir / f"{exam_id}.pdf"
    if pdf_path.exists():
        print(f"🎉 PDF created: {pdf_path}")
    else:
        print("⚠️ Build finished but PDF not found. Check LaTeX logs.", file=sys.stderr)
        raise SystemExit(3)


if __name__ == "__main__":
    main()