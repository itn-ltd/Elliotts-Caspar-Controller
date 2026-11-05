"""Parse CasparCG INFO replies."""

import os
import xml.etree.ElementTree as ET

EMPTY_LAYER = {"clip": None, "time": 0.0, "duration": 0.0, "loop": False, "producer": "empty"}


def parse_layer_info(raw: str) -> dict:
    """Parse the raw AMCP INFO 1-1 response into a layer info dict."""
    empty = dict(EMPTY_LAYER)
    try:
        nl = raw.find("\n")
        if nl < 0 or not raw.startswith("201"):
            return empty
        xml_str = raw[nl + 1 :].strip()
        if not xml_str:
            return empty
        root = ET.fromstring(xml_str)
        fg = root.find(".//foreground/producer")
        if fg is None:
            return empty
        ptype = fg.get("type", "")
        if "empty" in ptype.lower():
            return empty

        fn_el = fg.find("filename")
        clip = None
        if fn_el is not None and fn_el.text:
            clip = os.path.basename(fn_el.text.replace("\\", "/"))
            # Strip numeric suffix CasparCG sometimes appends (e.g. "clip.mp4_0")
            if clip and "_" in clip:
                stem, _, tail = clip.rpartition("_")
                if tail.isdigit():
                    clip = stem

        def _f(tag: str) -> float:
            el = fg.find(tag)
            try:
                return float(el.text) if el is not None and el.text else 0.0
            except ValueError:
                return 0.0

        loop_el = fg.find("loop")
        loop = (
            (loop_el.text or "0").strip() not in ("0", "false", "False")
            if loop_el is not None
            else False
        )

        return {
            "clip": clip,
            "time": round(_f("time"), 2),
            "duration": round(_f("duration"), 2),
            "loop": loop,
            "producer": ptype,
        }
    except Exception:
        return empty
