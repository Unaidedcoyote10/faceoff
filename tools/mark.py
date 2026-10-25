"""Single source of the Faceoff 'faceless' mark (64x64 grid): a featureless head + shoulders, an eye-line
censor slit, and one horizontal band of the head torn sideways (glitch). Used by tools/make_icons.py for the
static icons and inlined (as <symbol>s) into index.html for the animated logo."""
SILHOUETTE = ('<ellipse cx="32" cy="25" rx="12.5" ry="14.5"/>'
              '<path d="M10 60c0-13 9.5-19.5 22-19.5S54 47 54 60z"/>')
CUT = '<rect x="18" y="18.6" width="28" height="5.2" rx="1.2"/>'          # eye-line censor slit
BAND = (28.6, 4.4)                                                         # y, height of the torn band
GRAD = ('<linearGradient id="{id}" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#8b5cf6"/><stop offset="1" stop-color="#ec4899"/></linearGradient>')

def static_svg(size=512, bg=True, pad=0.0, rgb=True):
    s = 64 / (1 - 2 * pad); o = -(s - 64) / 2
    by, bh = BAND
    lines = "".join(f'<rect x="{o}" y="{y}" width="{s}" height=".5" fill="#fff" opacity=".035"/>' for y in range(1, 64, 2))
    ghost = (f'<g fill="#22e6ff" opacity=".5" transform="translate(-1.5 0)">{SILHOUETTE}</g>'
             f'<g fill="#ff2bd6" opacity=".5" transform="translate(1.5 0)">{SILHOUETTE}</g>') if rgb else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="{o} {o} {s} {s}">'
            f'<defs>{GRAD.format(id="g")}<clipPath id="b"><rect x="0" y="{by}" width="64" height="{bh}"/></clipPath>'
            f'<mask id="m"><rect x="-99" y="-99" width="300" height="300" fill="#fff"/><rect x="0" y="{by}" width="64" height="{bh}" fill="#000"/>{CUT.replace("<rect", "<rect fill=\"#000\"")}</mask></defs>'
            + (f'<rect x="{o}" y="{o}" width="{s}" height="{s}" rx="{0 if pad else 14}" fill="#0b0b10"/>' if bg else "")
            + f'<g mask="url(#m)">{ghost}<g fill="url(#g)">{SILHOUETTE}</g></g>'
            f'<g clip-path="url(#b)" transform="translate(5 0)"><g fill="#22e6ff" opacity=".75" transform="translate(-1.5 0)">{SILHOUETTE}</g><g fill="url(#g)">{SILHOUETTE}</g></g>'
            f'{lines}</svg>')
