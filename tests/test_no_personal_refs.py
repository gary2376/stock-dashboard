import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# generic leaks only: absolute home-directory paths, e-mail addresses, Tailscale (CGNAT 100.64/10) addresses
PATTERN = re.compile(r"/Users/[A-Za-z0-9._-]+|/home/(?!user\b)[A-Za-z0-9._-]+|[\w.+-]+@(?:gmail|outlook|yahoo|hotmail)\.com"
                     r"|\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b|\.ts\.net\b")


def test_tracked_source_has_no_personal_paths_emails_or_private_network_addresses():
    files = subprocess.run(["git", "ls-files", "backend", "scripts", "frontend", "README.md", "docs", "SECURITY.md", "NOTICE"],
                           cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    hits = [f"{f}:{i}" for f in files for i, line in enumerate((ROOT / f).read_text(errors="ignore").splitlines(), 1)
            if PATTERN.search(line)]
    assert not hits, hits
