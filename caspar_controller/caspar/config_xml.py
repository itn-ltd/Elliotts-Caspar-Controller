"""Write each instance's casparcg.config and the test-pattern clips."""

import logging
import os
import shutil
from xml.sax.saxutils import escape

from caspar_controller.settings import instance_amcp_port

log = logging.getLogger(__name__)


def caspar_instance_config_path(config: dict, inst: dict) -> str:
    """Return the full path for a per-instance casparcg config file."""
    filename = f"casparcg_inst_{inst['id']}.config"
    exe = config.get("caspar_exe_path", "")
    if exe and os.path.isabs(exe):
        return os.path.join(os.path.dirname(exe), filename)
    return filename


def regenerate_instance_config(config: dict, inst: dict) -> str:
    """Write a single-channel casparcg.config for one instance. Returns the path written.

    Each instance gets its own log/inst_N and data/inst_N subdirectory to prevent file-locking
    conflicts when multiple CasparCG processes run from the same folder simultaneously.
    """
    port = instance_amcp_port(config, inst)
    inst_id = int(inst["id"])
    media_path_xml = escape(config.get("media_path", "").strip() or "media\\")
    video_mode = escape(config["video_mode"])
    ndi_name = escape(inst["ndi_name"])

    exe = config.get("caspar_exe_path", "")
    if exe and os.path.isabs(exe):
        exe_dir = os.path.dirname(exe)
        for subdir in (f"log\\inst_{inst_id}", f"data\\inst_{inst_id}"):
            os.makedirs(os.path.join(exe_dir, subdir), exist_ok=True)
        # Wipe the CEF session cache before each start so Chromium never shows
        # a "Restore pages?" dialog or navigates to the config file on startup.
        html_cache = os.path.join(exe_dir, f"html_cache\\inst_{inst_id}")
        shutil.rmtree(html_cache, ignore_errors=True)
        os.makedirs(html_cache, exist_ok=True)

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <log-level>info</log-level>

  <channels>
    <!-- Instance {inst_id} -->
    <channel>
      <video-mode>{video_mode}</video-mode>
      <consumers>
        <ndi>
          <name>{ndi_name}</name>
          <allow-fields>false</allow-fields>
        </ndi>
      </consumers>
    </channel>
  </channels>

  <paths>
    <media-path>{media_path_xml}</media-path>
    <log-path>log\\inst_{inst_id}\\</log-path>
    <data-path>data\\inst_{inst_id}\\</data-path>
    <template-path>template\\</template-path>
  </paths>

  <controllers>
    <tcp>
      <port>{port}</port>
      <protocol>AMCP</protocol>
    </tcp>
  </controllers>

  <html>
    <remote-debugging-port>0</remote-debugging-port>
    <enable-gpu>false</enable-gpu>
    <cache-path>html_cache\\inst_{inst_id}\\</cache-path>
  </html>

</configuration>
"""
    out_path = caspar_instance_config_path(config, inst)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)
    return out_path


def regenerate_all_instance_configs(config: dict) -> list:
    """Regenerate config files for every instance. Returns list of paths written."""
    return [regenerate_instance_config(config, inst) for inst in config.get("instances", [])]


TEST_PATTERNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_patterns")


def ensure_test_pattern_images(config: dict) -> None:
    """Copy the TEST_* clips into the CasparCG media folder, keeping any already there.

    Skips if there's no media path and no absolute exe path to find one from.
    """
    media_dir = config.get("media_path", "").strip()
    if not media_dir:
        exe = config.get("caspar_exe_path", "")
        if not exe or not os.path.isabs(exe):
            return
        media_dir = os.path.join(os.path.dirname(exe), "media")
    os.makedirs(media_dir, exist_ok=True)
    for name in os.listdir(TEST_PATTERNS_DIR):
        target = os.path.join(media_dir, name)
        try:
            if not os.path.exists(target):
                shutil.copyfile(os.path.join(TEST_PATTERNS_DIR, name), target)
        except OSError as e:
            log.warning("Couldn't copy %s into %s: %s", name, media_dir, e)
