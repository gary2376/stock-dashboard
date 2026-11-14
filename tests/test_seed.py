import json
import os
import threading

import pytest
from fastapi.testclient import TestClient

import app as appmod
import config
import seed
import strategy


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(seed, "DATA_DIR", d)
    monkeypatch.delenv("SD_NO_SEED", raising=False)
    monkeypatch.setattr(config, "DEMO", False)
    return d


def snapshot(d):
    """Every entry with its kind (symlink target / dir / file bytes): byte-identical comparison of a whole tree."""
    out = []
    for p in sorted(d.rglob("*")):
        rel = str(p.relative_to(d))
        if p.is_symlink():
            out.append((rel, "link", os.readlink(p)))
        elif p.is_dir():
            out.append((rel, "dir", None))
        else:
            out.append((rel, "file", p.read_bytes()))
    return out


# ---- happy path

def test_fresh_dir_seeds_once_and_second_run_does_nothing(fresh):
    done = seed.maybe_seed()
    assert done == ["tickers.json", "strategy/新手入門策略.md"]
    assert json.loads((fresh / "tickers.json").read_text()) == ["NVDA"]
    marker = json.loads((fresh / ".seeded").read_text())
    assert marker["what"] == done and marker["seeded_at"]
    notes, warnings = strategy.load_notes(fresh / "strategy")
    assert [n.name for n in notes] == ["新手入門策略.md"] and warnings == []
    assert any(strategy.chunk(n) for n in notes)
    before = snapshot(fresh)
    assert seed.maybe_seed() is None and snapshot(fresh) == before


def test_shipped_fresh_clone_shape_seeds(fresh):
    (fresh / "backups").mkdir()
    (fresh / "backups" / "README.md").write_text("shipped")
    (fresh / ".DS_Store").write_text("junk")
    assert seed.maybe_seed()
    assert (fresh / "backups" / "README.md").read_text() == "shipped" and (fresh / ".DS_Store").read_text() == "junk"


def test_missing_data_dir_is_created_and_seeded(tmp_path, monkeypatch):
    d = tmp_path / "new" / "data"
    monkeypatch.setattr(seed, "DATA_DIR", d)
    monkeypatch.delenv("SD_NO_SEED", raising=False)
    monkeypatch.setattr(config, "DEMO", False)
    assert seed.maybe_seed() and (d / "tickers.json").exists()


# ---- anything else blocks seeding and leaves the dir byte-identical

def make(fresh, tmp_path, kind):
    target = tmp_path / "outside"
    target.mkdir(exist_ok=True)
    (target / "f.txt").write_text("outside")
    f = fresh
    if kind in ("favorites", "holdings", "pins", "llm", "chat", "unknown", "corrupt", "env", "usage"):
        name = {"favorites": "favorites.json", "holdings": "holdings.json", "pins": "pins.json", "llm": "llm.json",
                "chat": "chat_history.json", "unknown": "unknown.txt", "corrupt": "tickers.json.corrupt-1",
                "env": ".env", "usage": "llm_usage.jsonl"}[kind]
        (f / name).write_text("mine")
    elif kind == "tickers":
        (f / "tickers.json").write_text('["MINE"]')
    elif kind == "tickers_same_text":
        (f / "tickers.json").write_text(json.dumps(["NVDA"], indent=2))
    elif kind == "tickers_dir":
        (f / "tickers.json").mkdir()
    elif kind == "strategy":
        (f / "strategy").mkdir()
        (f / "strategy" / "mine.md").write_text("mine")
    elif kind == "strategy_empty":
        (f / "strategy").mkdir()
    elif kind == "analysis":
        (f / "analysis").mkdir()
        (f / "analysis" / "A.json").write_text("{}")
    elif kind == "locks":
        (f / ".locks").mkdir()
    elif kind == "locks_file":
        (f / ".locks").write_text("x")
    elif kind == "seeding":
        (f / ".seeding").write_text('{"tickers": null, "notes": {}}')  # even a perfectly shaped record
    elif kind == "seeding_tmp":
        (f / ".seeding.tmp.0").write_text("x")
    elif kind == "seeded":
        (f / ".seeded").write_text("")
    elif kind == "price_cache":
        (f / "price_cache").mkdir()
        (f / "price_cache" / "X.json").write_text("{}")
    elif kind == "profiles":
        (f / "profiles").mkdir()
        (f / "profiles" / "p.json").write_text("{}")
    elif kind == "backups_with_file":
        (f / "backups").mkdir()
        (f / "backups" / "README.md").write_text("r")
        (f / "backups" / "mine.json").write_text("{}")
    elif kind == "backups_readme_is_dir":
        (f / "backups" / "README.md").mkdir(parents=True)
    elif kind == "backups_readme_symlink":
        (f / "backups").mkdir()
        (f / "backups" / "README.md").symlink_to(target / "f.txt")
    elif kind == "backups_symlink":
        (f / "backups").symlink_to(target, target_is_directory=True)
    elif kind == "dsstore_dir":
        (f / ".DS_Store").mkdir()
    elif kind == "dsstore_symlink":
        (f / ".DS_Store").symlink_to(target / "f.txt")
    elif kind == "symlink_file":
        (f / "favorites.json").symlink_to(target / "f.txt")
    elif kind == "symlink_dir":
        (f / "strategy").symlink_to(target, target_is_directory=True)
    elif kind == "symlink_dangling":
        (f / "tickers.json").symlink_to(target / "missing")
    elif kind == "symlink_locks":
        (f / ".locks").symlink_to(target, target_is_directory=True)
    elif kind == "symlink_marker":
        (f / ".seeded").symlink_to(target / "f.txt")
    else:
        raise AssertionError(kind)
    return target


KINDS = ["favorites", "holdings", "pins", "llm", "chat", "unknown", "corrupt", "env", "usage", "tickers",
         "tickers_same_text", "tickers_dir", "strategy", "strategy_empty", "analysis", "locks", "locks_file", "seeding",
         "seeding_tmp", "seeded", "price_cache", "profiles", "backups_with_file",
         "backups_readme_is_dir", "backups_readme_symlink", "backups_symlink", "dsstore_dir", "dsstore_symlink",
         "symlink_file", "symlink_dir", "symlink_dangling", "symlink_locks", "symlink_marker"]


@pytest.mark.parametrize("kind", KINDS)
def test_any_other_entry_alone_blocks_seeding(fresh, tmp_path, kind):
    target = make(fresh, tmp_path, kind)
    before, outside = snapshot(fresh), snapshot(target)
    assert seed.maybe_seed() is None
    assert snapshot(fresh) == before and snapshot(target) == outside  # nothing written, replaced or followed


def test_symlinked_data_dir_refused(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(seed, "DATA_DIR", link)
    monkeypatch.delenv("SD_NO_SEED", raising=False)
    monkeypatch.setattr(config, "DEMO", False)
    assert seed.maybe_seed() is None and not list(real.iterdir())


def test_unsupported_platform_does_nothing(fresh, monkeypatch):
    monkeypatch.setattr(seed, "SUPPORTED", False)
    assert seed.maybe_seed() is None and not list(fresh.iterdir())


# ---- the claim is exclusive

def test_concurrent_attempts_exactly_one_proceeds(fresh):
    results, barrier = [], threading.Barrier(8)

    def go():
        barrier.wait()
        results.append(seed.maybe_seed())
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    winners = [r for r in results if r]
    assert len(winners) == 1 and winners[0][0] == "tickers.json"
    assert json.loads((fresh / "tickers.json").read_text()) == ["NVDA"]
    assert sorted(p.name for p in fresh.iterdir()) == [".seeded", "strategy", "tickers.json"]


def test_claim_losing_stops_without_touching_anything(fresh, monkeypatch):
    real = seed._create

    def lost_race(fd, name, text, mode=0o644):
        if name == ".seeded":
            raise FileExistsError(name)  # someone else claimed between our check and our claim
        return real(fd, name, text, mode)
    monkeypatch.setattr(seed, "_create", lost_race)
    assert seed.maybe_seed() is None and not list(fresh.iterdir())


# ---- user data appearing after the claim is never overwritten

def test_user_files_appearing_after_the_claim_are_untouched(fresh, monkeypatch):
    real = seed._create
    state = {}

    def racing(fd, name, text, mode=0o644):
        if name == "tickers.json" and not state:
            state["x"] = 1  # right before our first create: the user creates data
            (fresh / "holdings.json").write_text('{"MINE": 1}')
            (fresh / "tickers.json").write_text('["MY","OWN"]')
        return real(fd, name, text, mode)
    monkeypatch.setattr(seed, "_create", racing)
    seed.maybe_seed()
    assert (fresh / "holdings.json").read_text() == '{"MINE": 1}'
    assert (fresh / "tickers.json").read_text() == '["MY","OWN"]'  # O_EXCL: not overwritten
    assert not (fresh / "strategy").exists()  # seeding stopped at the failure


def test_user_strategy_dir_appearing_is_skipped(fresh, monkeypatch):
    real = seed._create

    def racing(fd, name, text, mode=0o644):
        if name == "tickers.json":
            (fresh / "strategy").mkdir()
            (fresh / "strategy" / "mine.md").write_text("mine")
        return real(fd, name, text, mode)
    monkeypatch.setattr(seed, "_create", racing)
    seed.maybe_seed()
    assert [p.name for p in (fresh / "strategy").iterdir()] == ["mine.md"]  # their dir: no notes added


def test_strategy_swapped_for_symlink_writes_nothing_through_it(fresh, tmp_path, monkeypatch):
    victim = tmp_path / "victim"
    victim.mkdir()
    real = seed._create

    def racing(fd, name, text, mode=0o644):
        if name.endswith(".md"):  # after our mkdir('strategy'), before the first note
            (fresh / "strategy").rmdir()
            (fresh / "strategy").symlink_to(victim, target_is_directory=True)
        return real(fd, name, text, mode)
    monkeypatch.setattr(seed, "_create", racing)
    seed.maybe_seed()
    assert not list(victim.iterdir())
    # and the dir fd is re-opened by name with O_NOFOLLOW, so a symlink swapped in before the open is refused
    swapped = fresh / "strategy"
    assert swapped.is_symlink()


def test_dir_fd_open_refuses_symlinks(tmp_path):
    real = tmp_path / "r"
    real.mkdir()
    (tmp_path / "l").symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        seed._open_dir(tmp_path / "l")


def test_failures_after_the_claim_just_stop(fresh, monkeypatch):
    real = seed._create

    def broken(fd, name, text, mode=0o644):
        if name == "tickers.json":
            raise OSError("disk full")
        return real(fd, name, text, mode)
    monkeypatch.setattr(seed, "_create", broken)
    assert seed.maybe_seed() == []  # claimed, then stopped: no exception, nothing else created
    assert sorted(p.name for p in fresh.iterdir()) == [".seeded"]
    assert seed.maybe_seed() is None  # and no retry ever


def test_nothing_is_ever_replaced_deleted_or_renamed(fresh, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("seed must not replace/rename/unlink")
    for fn in ("replace", "rename", "unlink", "remove", "rmdir"):
        monkeypatch.setattr(seed.os, fn, boom)
    assert seed.maybe_seed()


# ---- switches and hook

def test_demo_and_env_switch(fresh, monkeypatch):
    monkeypatch.setattr(config, "DEMO", True)
    assert seed.maybe_seed() is None and not list(fresh.iterdir())
    monkeypatch.setattr(config, "DEMO", False)
    monkeypatch.setenv("SD_NO_SEED", "1")
    assert seed.maybe_seed() is None and not list(fresh.iterdir())


def test_bad_examples_dir_tolerated(fresh, tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "EXAMPLES", tmp_path / "missing")
    assert seed.maybe_seed() == ["tickers.json"]
    ex = tmp_path / "ex"
    ex.mkdir()
    (ex / "ok.md").write_text("# ok\n")
    (ex / "bad name!.md").write_text("x")
    (ex / "bin.md").write_bytes(b"\xff\xfe\x00")
    (ex / "big.md").write_text("x" * (strategy.MAX_FILE + 1))
    (ex / "sub.md").mkdir()
    (ex / "link.md").symlink_to(ex / "ok.md")
    (ex / "x.exe").write_text("x")
    other = tmp_path / "data2"
    other.mkdir()
    monkeypatch.setattr(seed, "DATA_DIR", other)
    monkeypatch.setattr(seed, "EXAMPLES", ex)
    assert seed.maybe_seed() == ["tickers.json", "strategy/ok.md"]


def test_only_the_startup_hook_seeds(fresh):
    TestClient(appmod.app).get("/api/info")  # no context manager: no lifespan, no seeding
    assert not list(fresh.iterdir())
    with TestClient(appmod.app) as c:
        c.get("/api/info")
    assert json.loads((fresh / "tickers.json").read_text()) == ["NVDA"]


def test_conftest_disables_seeding_by_default():
    assert os.environ.get("SD_NO_SEED") == "1"


def test_empty_backups_dir_created_by_the_app_at_import_does_not_block_seeding(fresh):
    """app.py mkdirs an EMPTY backups/ at import time, before the startup hook runs seeding."""
    (fresh / "backups").mkdir()
    assert seed.maybe_seed() is not None
    assert (fresh / "tickers.json").read_text().strip().startswith("[")
    assert (fresh / ".seeded").exists()


def test_the_empty_dirs_the_app_creates_at_import_do_not_block_seeding(fresh):
    for name in ("analysis", "price_cache", "market_cache", "backups"):
        (fresh / name).mkdir()
    assert seed.maybe_seed() is not None
    assert (fresh / "tickers.json").exists() and (fresh / "strategy").is_dir()


def test_non_empty_app_dirs_still_block(fresh):
    (fresh / "market_cache").mkdir()
    (fresh / "market_cache" / "us.json").write_text("{}")
    assert seed.maybe_seed() is None
    assert not (fresh / "tickers.json").exists()


def test_real_startup_path_seeds_a_brand_new_data_dir(tmp_path):
    """The app mkdirs analysis/, price_cache/, market_cache/, backups/ at import time; seeding must still happen."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    data = tmp_path / "brand_new"
    env = {k: v for k, v in os.environ.items() if k not in ("SD_NO_SEED", "STOCK_DASHBOARD_DEMO")}
    env.update(STOCK_DASHBOARD_DATA=str(data), STOCK_DASHBOARD_ALLOWED_HOSTS="testserver")
    code = ("import sys; sys.path[:0]=['backend','scripts'];"
            "from fastapi.testclient import TestClient; import app;"
            "c=TestClient(app.app); c.__enter__(); c.__exit__(None,None,None)")
    r = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    assert (data / "tickers.json").exists() and (data / ".seeded").exists() and (data / "strategy").is_dir()
