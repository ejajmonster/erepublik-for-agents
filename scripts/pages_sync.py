#!/usr/bin/env python3
"""Sync public game state into the GitHub Pages site.

Run by systemd timer (erepublik-pages.timer, every 5 min). Fetches state,
verify, turn window and the log from the local game server, writes them to
the repo's public/ dir, and commits+pushes if anything changed.

No secrets: only public endpoints of the local server are used.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("EREP_BASE", "http://127.0.0.1:8451")
REPO = os.environ.get("EREP_PAGES_REPO", "/home/piotr/.openclaw/workspace/erepublik-repo")
PUBLIC = os.path.join(REPO, "public")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return r.read().decode()


def main():
    os.makedirs(PUBLIC, exist_ok=True)
    state = json.loads(get("/api/state"))
    verify = json.loads(get("/api/verify"))
    turn = json.loads(get("/api/turn"))
    log = get("/api/log")

    # public state: strip pending (private: it contains queued actions of real agents
    # before the turn closes — harmless but keep the dashboard payload lean)
    pub_state = {k: v for k, v in state.items() if k not in ("secrets", "pending")}

    meta = {
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "closes_at": (turn.get("window") or {}).get("closes_at"),
        "closes_at_utc": (turn.get("window") or {}).get("closes_at_utc"),
    }

    def write(name, content):
        p = os.path.join(PUBLIC, name)
        content = content if content.endswith("\n") else content + "\n"
        if os.path.exists(p) and open(p).read() == content:
            return False
        with open(p, "w") as f:
            f.write(content)
        return True

    changed = False
    changed |= write("state.json", json.dumps(pub_state, indent=1))
    changed |= write("verify.json", json.dumps(verify, indent=1))
    changed |= write("meta.json", json.dumps(meta, indent=1))
    changed |= write("log.jsonl", log)

    if not changed:
        print("no changes")
        return

    env = dict(os.environ)
    def git(*args):
        subprocess.run(["git", "-C", REPO, *args], check=True, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    git("add", "public")
    try:
        git("commit", "-m", f"pages sync {meta['updated_utc']} (turn {state['turn']})")
    except subprocess.CalledProcessError:
        pass  # nothing staged (identical content race)
    git("pull", "--rebase", "-q", "origin", "main")
    git("push", "-q", "origin", "main")
    print(f"synced turn {state['turn']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"sync failed: {e}", file=sys.stderr)
        sys.exit(1)
