/* Which build this is.
 *
 * "server" — served by `annotator/app.py`; annotations POST to that machine.
 * "local"  — the static GitHub Pages build; annotations live in the browser.
 *
 * This file is the only difference between the two. `annotator/build_site.py`
 * overwrites it with mode "local" when assembling the static site, so the
 * choice is decided at build time and never guessed at runtime.
 */
window.BIMSG_CONFIG = { mode: "server" };
