# BIM-Graphs Ground-Truth Annotator

A browser tool for adjudicating the pipeline's output so we can compute real
precision and recall.

## Why this exists

`main/validate.py` checks *necessary conditions* — every door joins exactly two
rooms, every room has one storey parent, dimensions are physical. Those can
**falsify** a graph but never **confirm** it. Nothing in them establishes that a
recovered corridor corresponds to a real corridor.

So we annotate. But labelling 25 buildings from scratch is prohibitive, and
unnecessary: the pipeline already proposes an answer. This tool draws that
proposal over the real floor plan and asks you to judge it. An open-ended
drawing task becomes a sequence of one-key decisions.

## Tonight's task: a stratified subset

We have connectivity accuracy on synthetic buildings (F1 0.996 clean / 0.868
damaged) and label accuracy on held-out IFC-stated labels (0.580), but **no
real accuracy on the actual real corpus** — everything measured so far is a
proxy. This subset is chosen to close that gap efficiently rather than by
annotating all 25 models:

| model | why it's here | rooms | edges | predicted labels |
|---|---|---|---|---|
| `model_6`  | worst connectivity failure (intra-storey 0.259) | 74 | ~70 | 13 |
| `model_12` | mid failure (0.648) + heaviest labelling load | 68 | ~35 | 46 |
| `model_1`  | connectivity *passes* (0.900) — control | 68 | ~65 | 45 |
| `model_20` | severe failure (0.424), smaller/faster | 29 | ~20 | 25 |
| `model_9`  | connectivity is perfect (1.000), tiny — quick sanity check | 7 | 4 | 3 |

Two connectivity failures, one severe and one moderate; one passing control so
we're not only looking at bad cases; one small model to do first as a warm-up.
Rooms marked with a trailing `?` on the plan (`storage?`, `circulation?`) are
the model's unconfirmed guess — that's the semantic-labelling half of the
task, and doesn't need a separate pass: judge connectivity and labels for the
same room in one visit.

If you only have time for one, do `model_12` — it has the most labelling
volume and a real connectivity failure to look at.

## Three ways to run it

The app is the same in all three; they differ only in where the work is kept.
`static/config.js` picks the default at build time rather than guessing at
runtime, and folder mode (B) can be turned on from inside either of the others.

| | where work is saved | needs | best for |
|---|---|---|---|
| **A** server | your machine, via HTTP | annotators can reach your host | a team on one network |
| **B** dataset folder | the annotator's own folder, every save | Chrome or Edge | handing someone a folder; the only mode with the 3D IFC view |
| **C** GitHub Pages | that browser, until downloaded | any browser | remote annotators, nothing to install |

### A. Local server — central collection

```bash
# once per corpus — pre-extracts plans so the browser loads instantly
python main/export_plans.py 'dataset/test_set/*.ifc' --out annotator/data

python annotator/app.py --port 8000            # http://localhost:8000
python annotator/app.py --host 0.0.0.0 --port 8000   # share on your network
```

Annotations POST to the machine running the server and land in
`annotator/annotations/<model>__<name>.json`. Best when annotators can reach
your machine: you collect the work automatically and nobody can lose it.

### B. Dataset folder — the tool writes back into it (recommended)

Hand over a folder and the annotator points the browser at it. Work is written
into `bimsg-annotations.json` inside that same folder after every save, so the
file to send back is already there — no export step to forget — and reopening
the folder restores everything.

```bash
python annotator/prepare_dataset.py --plans annotator/data \
    --ifc dataset/test_set --out ~/bimsg-annotation-set
```

That folder holds the plans, the matching IFC files and a copy of the guide.
With the IFC present the annotator also gets an **IFC model** tab that renders
the source file in 3D, with a cut slider labelled by storey — the way to catch a
plan that was extracted wrongly, which a 2D-only view cannot show.

`--no-ifc` drops the IFC files (10 MB each against 45 KB for a plan) when the
3D check is not needed; `--models a b c` restricts the set.

**Needs Chrome or Edge.** The File System Access API is not implemented in
Firefox or Safari; the tool detects this and says so rather than failing later.
Works in either build below — folder mode is offered whether the page came from
Flask or from Pages.

### C. GitHub Pages — nothing but a browser

```bash
python annotator/build_site.py --out dist
python -m http.server -d dist 8080             # preview before pushing
```

This is the fallback for annotators on Firefox or Safari, or when handing over
a folder is inconvenient. `.github/workflows/deploy-pages.yml` does this on every push to `main` that
touches `annotator/`. Enable it once at **Settings → Pages → Source → GitHub
Actions**. The plans are committed under `annotator/data/` (~1.1 MB) rather
than extracted in CI, which would need the 2 GB IFC corpus; re-run
`main/export_plans.py` and commit when they change.

**The trade-off is real and annotators must be told about it.** There is no
server, so work lives in that browser's `localStorage`. Clearing site data,
using a private window, or switching machine loses it. The tool therefore
tracks what has not been downloaded and nags in the sidebar, and annotators
finish by pressing **Download all my work** and sending you the file:

```bash
python annotator/build_gt.py --inbox ~/returned --out dataset/annotated_gt
```

`--inbox` accepts single annotations and whole bundles, and reads the model and
annotator from inside each file rather than trusting its name. **Load work from
file…** puts a returned bundle back into a browser, which is how someone
resumes on a different machine or how you review another person's work.

Everyone enters their own name in the top-right box in both modes, so two
people can label the same building independently and agreement can be measured.

## What to annotate

Full instructions for annotators are in
**[ANNOTATION_GUIDE.md](ANNOTATION_GUIDE.md)** — send that file (or the in-app
**Guide** button / `?` key) to anyone you distribute this to. The summary:

Work storey by storey, then chain the storeys together. Four kinds of judgement.

### 1. Rooms — is this region a real room?

Click a room, then press a key:

| key | verdict | meaning |
|---|---|---|
| `1` | **correct** | a real room, correctly delimited |
| `2` | **spurious** | not a room — a shaft, void, or artefact |
| `3` | **unsure** | genuinely cannot tell |
| `4` | **merge** | this and a neighbour are really one room |
| `5` | **split** | this covers several real rooms |
| `0` | clear | undo the verdict |

Violet dashed rooms were **recovered geometrically**; blue rooms were **stated
by the IFC**. Both need judging — that split is what lets us report the recovery
stage's contribution separately, which is the number the paper turns on.

You can also correct the **label** in the right panel. This doubles as ground
truth for semantic labelling, so fix names that are wrong, generic
("Room Name"), or missing.

### 2. Links — is this passage real?

Click a line between two rooms and press `1` correct, `2` spurious, `3` unsure.
Amber dashed lines are `open_passage` (a wall-free threshold); green solid lines
are `connected_by_door`.

**These are the most valuable judgements in the tool.** Connectivity is exactly
what IFC omits — the corpus contains zero `IfcRelSpaceBoundary` — so every link
is inferred and none of it has ever been checked.

### 3. Floor-to-floor links — does the building hold together?

Listed in the bottom-right panel and drawn as violet markers on the rooms they
touch. Judge the predicted ones the same way; press `V` to add one the pipeline
missed (click a room, change floor with `[` / `]`, click the room the stair
arrives in).

Without these the annotation is a stack of disconnected floor plans rather than
a building. A stairwell recorded as a separate room per floor with nothing
joining them is common, and has to be linked by hand.

### 4. What's missing

- Press `A`, then click two rooms to **add a link** the pipeline missed.
- Press `M`, then click inside a room the pipeline **failed to find**, and name
  it. A point is enough; we need it for recall, not for its boundary.

Missing items matter as much as wrong ones: precision alone is easy to game by
predicting less.

Anything you add can be selected and removed with `Del`, and
`Ctrl`+`Z` undoes any action at all. Predicted items cannot be deleted — that
the pipeline proposed them and was wrong is the data we need, so they are marked
spurious instead.

## Shortcuts

| key | action |
|---|---|
| `1`–`5`, `0` | verdicts |
| `Esc` | judge mode |
| `A` | add-link mode |
| `V` | link-floors mode |
| `M` | missing-room mode |
| `Del` | delete the thing you added |
| `Ctrl`/`Cmd` + `Z` | undo |
| `Ctrl`/`Cmd` + `Shift` + `Z` | redo |
| `[` / `]` | previous / next storey |
| `F` | fit plan to window |
| `Ctrl`/`Cmd` + `S` | save |
| `T` | switch between the plan and the 3D IFC view |
| `Ctrl`/`Cmd` + `←` / `→` | previous / next building |
| `?` | in-app guide |

Scroll to zoom, drag to pan. Shortcuts are ignored while typing in a text box —
click the plan first. Work autosaves every 20 seconds.

## Guidance on hard cases

- **Where does a corridor end?** If two annotators would plausibly draw the
  boundary differently, that is genuine ambiguity, not pipeline error. Judge it
  `correct` and let the agreement statistics record the uncertainty.
- **Open-plan floors.** A large undivided floor plate is one room, not several.
  Only mark `split` if there is a real physical division the pipeline missed.
- **Exterior circulation.** Several buildings in this corpus are open-corridor
  apartment blocks whose flats open onto an outdoor deck. That deck is genuinely
  navigable and should be judged `correct`, not `spurious`.
- **Don't guess.** Press `3` for unsure. It is a recorded verdict, not a gap:
  those items land in `held_out` rather than becoming silent negatives, so they
  can be routed to a second annotator. Wrong ground truth is worse than none,
  because everything downstream inherits it.

## Collecting the results

```bash
# per finished building: a hierarchical graph (storeys chained) and a .gt.json
# in the pair form main/eval_connectivity.py reads; plus agreement statistics
python annotator/build_gt.py --out dataset/annotated_gt

python annotator/build_gt.py --agreement-only   # agreement, write nothing
python main/evaluate.py                         # precision/recall, ifc vs inferred
```

Only buildings where **every** room, link and floor-to-floor link is judged are
written; partial work is reported and skipped (`--include-incomplete` overrides).
`unsure` never becomes a silent negative — those items go to `held_out` so they
can be routed to a second annotator.

`merge` and `split` are reported as **partial** — the region is real but wrongly
delimited, which is neither a clean hit nor a false positive, so it is not
silently folded into either.

Cohen's kappa collapses toward 0 when one annotator uses essentially a single
verdict, which happens whenever the pipeline is mostly right. `build_gt.py`
detects that case and says so; quote **raw agreement** when it fires.

## Files

```
annotator/app.py              Flask server (mode A)
annotator/build_site.py       assembles the static Pages build (mode C)
annotator/prepare_dataset.py  builds the folder to hand to an annotator (mode B)
annotator/compose.py          verdicts -> hierarchical building graph
annotator/static/compose.js   the same rules in JS, for the serverless build
annotator/static/store.js     server vs localStorage storage backends
annotator/static/fsdir.js     dataset-folder backend (File System Access API)
annotator/static/viewer3d.js  IFC 3D view, three.js + web-ifc loaded on demand
annotator/test_compose_parity.py  proves the two composers agree
annotator/test_static_build.py    proves the Pages build works from /<repo>/
annotator/test_dir_mode.py        proves folder mode saves and restores work
annotator/build_gt.py         batch: ground truth + inter-annotator agreement
annotator/ANNOTATION_GUIDE.md the guide to hand to annotators
annotator/static/             UI (no build step, no dependencies)
annotator/data/*.plan.json    pre-extracted plans (generated)
annotator/annotations/        one JSON per (model, annotator)
```

Annotation files are keyed by the pipeline's own node ids, so they join straight
back onto the graphs in `results/`.
