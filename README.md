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
| **Local server** | your machine, over HTTP | Python + Flask | a team on one network |

The 3D IFC view works in all three: from the hosted site it streams the model
over HTTP, and from a folder it reads the file directly.

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

## Turning a missing-room pin into a node

A pin is a point, a label and a storey. That records a real recall failure, but
it cannot enter the graph: a node needs an extent, and inventing edges from a
click position would be guessing.

```bash
python annotator/resolve_pins.py --inbox ~/returned --out annotator/data-resolved
```

Two strategies. **project** carries a vertical shaft up from the nearest storey
that models it — a stair on the ground floor pinned on the floor above is the
same shaft, so the polygon comes from the file and the position was confirmed by
a person. It is gated on the room being a shaft, because only a shaft repeats
its footprint from floor to floor; without that gate it happily "projected" a
67 m² office and a 133 m² hallway out of unrelated neighbours. **enclose**
flood-fills free space around the pin, bounded by that storey's walls, for
rooms recovery rejected as too small or merged into a corridor.

Anything neither resolves stays a pin — still a recorded recall miss, still not
a node. Refusing is the point.

Nothing is asserted: resolved rooms carry `source: "projected"`/`"recovered"`
plus the evidence they came from, draw in amber, and their links are marked
`proposed`. The evidence rides through onto the graph node, so a room can be
audited later against what justified it.

Hand the output back for a second pass by pointing either build at it:

```bash
python annotator/app.py --plans annotator/data-resolved
python annotator/build_site.py --plans annotator/data-resolved --out dist
```

## The verdict vocabulary

A room and a link fail in different ways, so they do not share words — a saved
file says what was meant without the reader having to know which dictionary the
entry came from.

| | words |
|---|---|
| rooms | `real`, `not_a_room`, `unsure`, `merge`, `split` |
| links | `passable`, `not_passable`, `unsure` |

An added link also records `kind`: `connected_by_door` or `open_passage`. A door
can be shut and an archway cannot, which is the whole difference to anything
planning a route, so the tool asks rather than guessing.

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

## Looking at the graph

```bash
python annotator/export_graph.py annotated_gt/*.graph.json --out graphs
```

Three files per building, because "open it in a viewer" means different things.

| | opens in | keeps |
|---|---|---|
| `.html` | any browser, nothing installed | the storeys as floor plates, rooms on their real coordinates |
| `.graphml` | yEd, Cytoscape, Gephi, networkx | every node and edge attribute, plus position and colour |
| `.gexf` | Gephi, networkx | the same, with `viz:position` and `viz:color` Gephi reads directly |

The HTML page is the one to hand someone. Rooms sit at their true centroids on
their own floor plate, so it reads as a building rather than a hairball: green
links are doors, brown are open passages, and the dotted purple ones are the
stairs and lifts chaining the floors. Colour says where a room came from — blue
from the IFC, purple from pipeline recovery, amber from an annotator's pin —
and hovering one gives its area, provenance and verdict.

It also states the number of connected pieces, and says so loudly when that is
more than one. A finished building should be a single component; four means
either the annotation missed the links between them or the building really is
that way, and both are worth seeing before the graph is used for anything.

`export_graph.py` reads what `build_gt.py` writes and nothing else, so the
export can be regenerated at any point without re-running the annotation.

## How it is built

No bundler, no npm, no framework — the app is a handful of scripts with no
imports, so there is no toolchain to maintain. three.js and web-ifc are the one
exception and are fetched from a CDN on demand, only if the 3D view is opened.

Only self-contained modules are fetched. three's addons, `OrbitControls` among
them, are published with a bare `import ... from "three"` that a browser cannot
resolve without an import map — loading one is what broke the viewer on first
release — so the orbit/pan/dolly controls are ~60 lines in `viewer3d.js`
instead. `test_viewer_deps.py` asserts that no module the viewer imports carries
a bare specifier, and drives those controls against the real three.js build.

Composition of a building graph exists twice, in `annotator/compose.py` and
`annotator/static/compose.js`, because the browser must show an annotator the
graph their verdicts produce while the collection script must build the same
graph server-side. Two copies of one rule is a liability, so
`test_compose_parity.py` runs both over every plan under randomised verdicts and
fails on the smallest difference. It gates the deploy, along with tests that the
published site works from a project subpath and that the folder backend cannot
truncate an annotation file.

```bash
python annotator/test_compose_parity.py   # the two composers agree
python annotator/test_export_graph.py     # the export opens in networkx
python annotator/test_static_build.py     # the published site works from /<repo>/
python annotator/test_dir_mode.py         # folder mode saves and restores
python annotator/test_viewer_deps.py      # CDN modules resolve; controls behave
python annotator/test_e2e.py              # the whole app, in a real browser
```

`test_e2e.py` is the one that matters. This is a browser application, and every
regression it has shipped -- a name collision in the state object, a block of
code removed by an over-wide edit, a section cutting the wrong axis, a control
that quietly stopped being wired -- was invisible to module tests and obvious
the moment someone opened the page. It loads the built site in Chromium and
clicks through: judging a room, undo and redo, adding and deleting a link,
switching storeys and layouts, loading the IFC, and checking that the canvas is
not blank and the section planes are horizontal. Run it before pushing anything
under `static/`.

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
