# ifc-graphx

A browser tool for building ground-truth scene graphs from IFC building models.

**[Open the annotator →](https://ashwin-ned.github.io/ifc-graphx/)**

An IFC file usually says which rooms exist. It almost never says which rooms
*connect* — that a door in this wall joins the kitchen to the hallway. That
connectivity is what a robot needs and what no existing dataset records, so it
has to be established by hand.

Annotating from scratch is impractical, so this tool works by correction: a
pipeline proposes the answer, draws it over the real floor plan, and the
annotator adjudicates it. An open-ended drawing task becomes a sequence of
one-key decisions, and the result is a hierarchical building graph —
building → storey → room, with the storeys chained together through their
stairs and lifts.

![no build step, no dependencies](https://img.shields.io/badge/build-none-brightgreen)

## Using it

Read **[annotator/ANNOTATION_GUIDE.md](annotator/ANNOTATION_GUIDE.md)** — it is
written for someone who has never seen an IFC file. The tool also shows it on
first run and behind the <kbd>?</kbd> key.

The short version: judge each room and each link with <kbd>1</kbd>–<kbd>5</kbd>,
add what the model missed with <kbd>A</kbd> and <kbd>M</kbd>, chain the floors
together with <kbd>V</kbd>, and use <kbd>3</kbd> (unsure) whenever you are not
certain — a guessed verdict is worse than an honest "unsure".

## Three ways to run it

The app is the same in all three; they differ only in where the work is kept.

| | work is saved | needs | best for |
|---|---|---|---|
| **Dataset folder** | the annotator's own folder, on every save | Chrome or Edge | handing someone a folder; nothing to download and nothing to lose |
| **GitHub Pages** | that browser, until downloaded | any browser | remote annotators, nothing to install |

The 3D IFC view works in all three: from the hosted site it streams the model
over HTTP, and from a folder it reads the file directly.
| **Local server** | your machine, over HTTP | Python + Flask | a team on one network |

```bash
# hand a colleague a folder of plans and IFC files
python annotator/prepare_dataset.py --plans annotator/data \
    --ifc /path/to/ifc --out ~/annotation-set

# or run the collecting server
python annotator/app.py --host 0.0.0.0 --port 8000

# or build the static site yourself
python annotator/build_site.py --out dist && python -m http.server -d dist 8080
```

In **dataset folder** mode the tool writes `bimsg-annotations.json` into that
folder after every save, so the file to send back is already there and
reopening the folder restores everything.

## Collecting the results

```bash
python annotator/build_gt.py --inbox ~/returned --out annotated_gt
```

This writes, per finished building, a hierarchical `*.graph.json` and a
`*.gt.json` of room pairs, and reports inter-annotator agreement where two
people annotated the same building. That agreement is the ceiling on any score
measured against the data, so it is reported whether or not it flatters.

Two deliberate choices: `unsure` is recorded and held out rather than quietly
becoming a negative, and buildings that are not fully judged are skipped rather
than half-counted.

## How it is built

No bundler, no npm, no framework — the app is a handful of scripts with no
imports, so there is no toolchain to maintain. three.js and web-ifc are the one
exception and are fetched from a CDN on demand, only if the 3D view is opened.

Composition of a building graph exists twice, in `annotator/compose.py` and
`annotator/static/compose.js`, because the browser must show an annotator the
graph their verdicts produce while the collection script must build the same
graph server-side. Two copies of one rule is a liability, so
`test_compose_parity.py` runs both over every plan under randomised verdicts and
fails on the smallest difference. It gates the deploy, along with tests that the
published site works from a project subpath and that the folder backend cannot
truncate an annotation file.

```bash
python annotator/test_compose_parity.py
python annotator/test_static_build.py
python annotator/test_dir_mode.py
```

## Data

`annotator/data/` holds 25 buildings, each as a `*.plan.json` floor plan (room
polygons, wall footprints, door positions and any room names the file stated)
and the `*.ifc` model it was extracted from. The IFC files are published so the
3D check works from the hosted site and not only from a folder on disk; their
headers were anonymised upstream (`FILE_NAME` carries `Organization_0`,
`Redacted`) and carry no author or organisation.

They are 247 MB, which is why `build_site.py --no-ifc` exists: it publishes the
plans alone, leaving the 3D view available only to annotators working from their
own folder.

## Acknowledgements

The Pages deployment follows the pattern in
[nbharathik/ifc-viewx](https://github.com/nbharathik/ifc-viewx), whose author
also pointed the way on loading web-ifc's WASM at runtime.

Uses [three.js](https://threejs.org) (MIT) and
[web-ifc](https://github.com/ThatOpen/engine_web-ifc) (MPL-2.0), both loaded
from a CDN at runtime and neither vendored here.
