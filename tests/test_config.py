import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_env_redirects_every_module_and_repo_data_untouched(tmp_path):
    code = (
        "import sys; sys.path[:0]=['backend','scripts'];"
        "import app, data_provider, market_health, news_feed, snapshots, generate_analysis as ga;"
        "d=app.DATA_DIR;"
        "assert app.TICKERS_FILE.parent==d and app.BACKUPS_DIR.parent==d;"
        "assert data_provider.CACHE_DIR.parent==d and market_health.CACHE_DIR.parent==d;"
        "assert news_feed.CACHE_DIR.parent==d and ga.ANALYSIS_DIR.parent==d and snapshots.HISTORY_DIR.parent==d;"
        "print(d)"
    )
    before = {p: p.stat().st_mtime for p in (ROOT / "data").rglob("*") if p.is_file()} if (ROOT / "data").exists() else {}
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                       env={"STOCK_DASHBOARD_DATA": str(tmp_path / "x"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    assert Path(r.stdout.strip()) == (tmp_path / "x").resolve()
    assert (tmp_path / "x" / "price_cache").is_dir()
    after = {p: p.stat().st_mtime for p in (ROOT / "data").rglob("*") if p.is_file()} if (ROOT / "data").exists() else {}
    assert before == after
