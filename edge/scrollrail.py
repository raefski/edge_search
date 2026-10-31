"""An always-visible, finger-sized scrollbar for a long Streamlit page.

Added 2026-10-31 for the arbitrage page, after Adam (on his phone) could not
find the scrollbar from the top of a 20,000px board, let alone grab it.

Two separate causes, so two separate fixes:

  * Streamlit's own theme CSS sets `* { scrollbar-color: transparent
    transparent }` and only paints the thumb on `:hover` -- so on a desktop
    browser the page's scrollbar is invisible until the pointer is over it.
    A more specific rule on the scroll container (`section[data-testid=
    "stMain"]` in 1.62; the document itself does not scroll) undoes that.
  * A phone browser draws an overlay indicator no CSS can pin open -- iOS
    Safari ignores scrollbar styling outright, Android Chrome fades it after
    each scroll -- and neither can be dragged with a finger in any useful
    way. There the only fix is to draw our own: a fixed rail on the right
    edge with a 48px-minimum thumb that tracks the scroll position, where a
    touch anywhere on the rail jumps there and a drag scrubs the page.
    Shown only for a coarse pointer, so a desktop does not get two bars.

The rail lives on document.body, which outlives a page switch in a
multi-page app, so it shows only while this page's own marker element is in
the DOM -- leave the page and Streamlit removes the marker, and the rail
hides itself.
"""
from __future__ import annotations

_CSS = """
<style>
section[data-testid="stMain"] {
  scrollbar-width: auto !important;
  scrollbar-color: rgba(128, 128, 128, 0.7) transparent !important;
}
@media (pointer: coarse) {
  section[data-testid="stMain"] { padding-right: 26px; }
}
</style>
"""

_RAIL = """
<span id="edge-rail-marker"></span>
<script>
(() => {
  const doc = document;
  if (doc.getElementById("edge-rail")) return;      // one rail per tab; reruns re-send this
  const scroller = () => doc.querySelector('section[data-testid="stMain"]')
                        || doc.querySelector("section.main") || doc.scrollingElement;
  const rail = doc.createElement("div");
  rail.id = "edge-rail";
  rail.style.cssText = "position:fixed;top:56px;right:0;bottom:8px;width:26px;z-index:999990;"
    + "touch-action:none;display:none;background:rgba(128,128,128,0.10);border-radius:13px;";
  const thumb = doc.createElement("div");
  thumb.style.cssText = "position:absolute;left:4px;right:4px;min-height:48px;border-radius:9px;"
    + "background:rgba(128,128,128,0.75);";
  rail.appendChild(thumb);
  doc.body.appendChild(rail);
  const coarse = matchMedia("(pointer: coarse)");

  let thumbH = 48;
  const update = () => {
    const el = scroller();
    const on = coarse.matches && doc.getElementById("edge-rail-marker")
               && el && el.scrollHeight > el.clientHeight + 4;
    rail.style.display = on ? "block" : "none";
    if (!on) return;
    const railH = rail.clientHeight;
    thumbH = Math.max(48, railH * el.clientHeight / el.scrollHeight);
    const frac = el.scrollTop / (el.scrollHeight - el.clientHeight);
    thumb.style.height = thumbH + "px";
    thumb.style.top = (frac * (railH - thumbH)) + "px";
  };
  const scrubTo = (clientY) => {
    const el = scroller();
    const r = rail.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientY - r.top - thumbH / 2) / (r.height - thumbH)));
    el.scrollTop = frac * (el.scrollHeight - el.clientHeight);
  };
  rail.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    rail.setPointerCapture(e.pointerId);
    scrubTo(e.clientY);
  });
  rail.addEventListener("pointermove", (e) => {
    if (rail.hasPointerCapture(e.pointerId)) scrubTo(e.clientY);
  });
  // capture: the scroll event does not bubble, and the container element can
  // be replaced under us by a page switch
  doc.addEventListener("scroll", () => requestAnimationFrame(update), true);
  addEventListener("resize", update);
  setInterval(update, 700);                          // a rerun changes the page height
  update();
})();
</script>
"""


def scroll_rail() -> None:
    """Call once near the top of a page. Safe on every rerun."""
    import streamlit as st

    try:
        st.html(_CSS + _RAIL, unsafe_allow_javascript=True)
    except TypeError:
        # Streamlit older than the `unsafe_allow_javascript` keyword: the CSS
        # still fixes desktop; a phone keeps its native indicator.
        st.html(_CSS)
