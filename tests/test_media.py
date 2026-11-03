"""/api/media clip listing."""


def make_media(root, names):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def test_lists_media_next_to_exe(client, write_config, tmp_path):
    exe = tmp_path / "caspar" / "casparcg.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    make_media(
        tmp_path / "caspar" / "media",
        [
            "amb.mp4",
            "Logo.PNG",
            "notes.txt",
            "sub/Intro Clip.mov",
            "TEST_RED.png",
            "z.mxf",
        ],
    )
    write_config({"caspar_exe_path": str(exe), "media_path": ""})

    data = client.get("/api/media").json()

    assert data["clips"] == ["AMB", "LOGO", "SUB/INTRO CLIP", "TEST_RED", "Z"]
    assert data["media_dir"] == str(tmp_path / "caspar" / "media")


def test_custom_media_path_wins(client, write_config, tmp_path):
    make_media(tmp_path / "custom", ["a.mp4"])
    write_config({"caspar_exe_path": "", "media_path": str(tmp_path / "custom")})
    assert client.get("/api/media").json()["clips"] == ["A"]


def test_exe_not_set(client, write_config):
    write_config({"caspar_exe_path": "", "media_path": ""})
    assert client.get("/api/media").json() == {"clips": [], "error": "CasparCG exe path not set"}


def test_missing_custom_media_path_falls_back_to_exe(client, write_config, tmp_path):
    write_config({"caspar_exe_path": "", "media_path": str(tmp_path / "missing")})
    assert client.get("/api/media").json()["error"] == "CasparCG exe path not set"


def test_media_folder_missing(client, write_config, tmp_path):
    exe = tmp_path / "casparcg.exe"
    exe.write_bytes(b"")
    write_config({"caspar_exe_path": str(exe), "media_path": ""})
    data = client.get("/api/media").json()
    assert data["clips"] == []
    assert data["error"].startswith("Media folder not found: ")
