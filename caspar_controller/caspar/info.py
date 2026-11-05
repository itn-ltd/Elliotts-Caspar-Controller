"""Parse CasparCG INFO replies: the 2.3 layout and the 2.0/2.1 one."""

import os
import re
import xml.etree.ElementTree as ET

EMPTY_LAYER = {"clip": None, "time": 0.0, "duration": 0.0, "loop": False, "producer": "empty"}


def _num(text) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _truthy(text) -> bool:
    return (text or "").strip().lower() not in ("", "0", "false")


def _clip_name(path: str) -> str:
    name = os.path.basename(path.replace("\\", "/"))
    # 2.0 can report "clip.mp4_0"; only strip after an extension, so PROMO_2024 stays whole.
    return re.sub(r"(\.\w+)_\d+$", r"\1", name)


def _layer(clip, time: float, duration: float, loop: bool, producer: str) -> dict:
    return {
        "clip": clip or None,
        "time": round(time, 2),
        "duration": round(duration, 2),
        "loop": loop,
        "producer": producer,
    }


def _from_23(fg: ET.Element) -> dict:
    """2.3: foreground/file/{name,path,time="elapsed total"}, producer as text."""
    file = fg.find("file")
    elapsed, _, total = (file.findtext("time") or "").partition(" ")
    clip = file.findtext("name") or _clip_name(file.findtext("path") or "")
    return _layer(
        clip, _num(elapsed), _num(total), _truthy(fg.findtext("loop")), fg.findtext("producer", "")
    )


def _from_20(producer: ET.Element) -> dict:
    """2.0/2.1: producer type as an attribute or child, times as seconds or frames."""
    ptype = producer.get("type") or producer.findtext("type") or (producer.text or "").strip()
    if "empty" in ptype.lower() or not ptype:
        return dict(EMPTY_LAYER)
    fps = _num(producer.findtext("fps"))

    def seconds(tag: str, frames_tag: str) -> float:
        frames = _num(producer.findtext(frames_tag))
        return _num(producer.findtext(tag)) or (frames / fps if fps else 0.0)

    return _layer(
        _clip_name(producer.findtext("filename") or ""),
        seconds("time", "file-frame-number"),
        seconds("duration", "file-nb-frames"),
        _truthy(producer.findtext("loop")),
        ptype,
    )


def parse_layer_info(raw: str) -> dict:
    """Parse the raw AMCP INFO 1-1 response into a layer info dict."""
    status, _, xml_str = raw.partition("\n")
    if not status.startswith("201"):
        return dict(EMPTY_LAYER)
    try:
        fg = ET.fromstring(xml_str.strip()).find(".//foreground")
    except ET.ParseError:
        return dict(EMPTY_LAYER)
    if fg is None:
        return dict(EMPTY_LAYER)
    if fg.find("file") is not None:
        return _from_23(fg)
    producer = fg.find("producer")
    return _from_20(producer) if producer is not None else dict(EMPTY_LAYER)
