"""GitHub integration: read issues/PRs/commits, create issues, open draft PRs.

Auth: fine-grained PAT (FGPAT) at GITHUB_TOKEN. Repo scope is limited to the
allowlist configured at token-creation time — adding a new repo requires
re-issuing the token.

GITHUB_DEFAULT_REPO (env, format `owner/name`) is used when --repo is omitted.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import now_brt  # noqa: E402

NAME = "github"
RATE_LIMIT_FLOOR = 50  # abort batch ops below this remaining
AGENT_LABEL = "agent-drafted"


@dataclass(frozen=True)
class Issue:
    repo: str
    number: int
    title: str
    url: str
    assignee: str | None
    labels: tuple[str, ...]
    updated_at: str


@dataclass(frozen=True)
class PullRequest:
    repo: str
    number: int
    title: str
    url: str
    draft: bool
    base: str
    head: str
    updated_at: str


@dataclass(frozen=True)
class Commit:
    repo: str
    sha: str
    message: str
    author: str
    url: str
    committed_at: str


_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set in environment (.claude/.env)")
    from github import Auth, Github

    _CLIENT = Github(auth=Auth.Token(token), per_page=100)
    return _CLIENT


def _resolve_repo(repo_arg: str | None) -> str:
    repo = (repo_arg or os.environ.get("GITHUB_DEFAULT_REPO", "")).strip()
    if not repo:
        raise RuntimeError(
            "No repo specified. Pass --repo owner/name or set GITHUB_DEFAULT_REPO."
        )
    if "/" not in repo:
        raise RuntimeError(f"Invalid repo format: {repo!r} (expected owner/name)")
    return repo


def _check_rate(g) -> None:
    rl = g.get_rate_limit().core
    if rl.remaining < RATE_LIMIT_FLOOR:
        raise RuntimeError(
            f"GitHub rate limit nearly exhausted ({rl.remaining}/{rl.limit}); "
            f"resets at {rl.reset.isoformat()}"
        )


def _iso(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def assigned_to_me(g, repo_full_name: str) -> list[Issue]:
    repo = g.get_repo(repo_full_name)
    me = g.get_user().login
    out: list[Issue] = []
    for i in repo.get_issues(state="open", assignee=me):
        if i.pull_request is not None:
            continue
        out.append(
            Issue(
                repo=repo_full_name,
                number=i.number,
                title=i.title,
                url=i.html_url,
                assignee=i.assignee.login if i.assignee else None,
                labels=tuple(lbl.name for lbl in i.labels),
                updated_at=_iso(i.updated_at),
            )
        )
    return out


def open_prs(g, repo_full_name: str) -> list[PullRequest]:
    repo = g.get_repo(repo_full_name)
    out: list[PullRequest] = []
    for pr in repo.get_pulls(state="open", sort="updated", direction="desc"):
        out.append(
            PullRequest(
                repo=repo_full_name,
                number=pr.number,
                title=pr.title,
                url=pr.html_url,
                draft=bool(pr.draft),
                base=pr.base.ref,
                head=pr.head.ref,
                updated_at=_iso(pr.updated_at),
            )
        )
    return out


def recent_commits(g, repo_full_name: str, days: int = 7) -> list[Commit]:
    repo = g.get_repo(repo_full_name)
    since = now_brt() - timedelta(days=days)
    out: list[Commit] = []
    for c in repo.get_commits(since=since):
        msg = (c.commit.message or "").splitlines()[0][:200]
        author = ""
        if c.author:
            author = c.author.login
        elif c.commit.author:
            author = c.commit.author.name or ""
        out.append(
            Commit(
                repo=repo_full_name,
                sha=c.sha[:7],
                message=msg,
                author=author,
                url=c.html_url,
                committed_at=_iso(c.commit.author.date) if c.commit.author else "",
            )
        )
    return out


def open_issue(
    g,
    repo_full_name: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> Issue:
    _check_rate(g)
    repo = g.get_repo(repo_full_name)
    final_labels = sorted({*(labels or []), AGENT_LABEL})
    issue = repo.create_issue(title=title, body=body, labels=final_labels)
    return Issue(
        repo=repo_full_name,
        number=issue.number,
        title=issue.title,
        url=issue.html_url,
        assignee=issue.assignee.login if issue.assignee else None,
        labels=tuple(lbl.name for lbl in issue.labels),
        updated_at=_iso(issue.updated_at),
    )


def open_draft_pr(
    g,
    repo_full_name: str,
    branch_slug: str,
    title: str,
    body: str,
    files: dict[str, str],
) -> PullRequest:
    """Open a draft PR. Falls back to [WIP]+label on private-free 422.

    files: {path: content}. All files are created on a fresh branch off default.
    """
    from github import GithubException

    _check_rate(g)
    repo = g.get_repo(repo_full_name)
    base_ref = repo.default_branch
    base = repo.get_branch(base_ref)
    head_ref = f"agent/{branch_slug}"
    repo.create_git_ref(ref=f"refs/heads/{head_ref}", sha=base.commit.sha)

    for path, content in files.items():
        repo.create_file(
            path=path,
            message=f"Draft: {title}",
            content=content,
            branch=head_ref,
        )

    try:
        pr = repo.create_pull(
            title=f"Draft: {title}",
            body=body,
            head=head_ref,
            base=base_ref,
            draft=True,
        )
    except GithubException as e:
        if e.status != 422:
            raise
        pr = repo.create_pull(
            title=f"[WIP] {title}",
            body=body,
            head=head_ref,
            base=base_ref,
            draft=False,
        )
        try:
            pr.add_to_labels("draft")
        except GithubException:
            pass

    return PullRequest(
        repo=repo_full_name,
        number=pr.number,
        title=pr.title,
        url=pr.html_url,
        draft=bool(pr.draft),
        base=pr.base.ref,
        head=pr.head.ref,
        updated_at=_iso(pr.updated_at),
    )


def format_for_context(
    issues: list[Issue] | None = None,
    prs: list[PullRequest] | None = None,
    commits: list[Commit] | None = None,
) -> str:
    parts: list[str] = ["### GitHub", ""]
    issues = issues or []
    prs = prs or []
    commits = commits or []
    if issues:
        parts.append("**Issues assigned**")
        for i in issues:
            labels = f" [{', '.join(i.labels)}]" if i.labels else ""
            parts.append(f"- #{i.number} {i.title}{labels} — {i.url}")
        parts.append("")
    if prs:
        parts.append("**Open PRs**")
        for p in prs:
            tag = "draft" if p.draft else "ready"
            parts.append(f"- #{p.number} ({tag}) {p.title} — {p.url}")
        parts.append("")
    if commits:
        parts.append("**Recent commits**")
        for c in commits:
            parts.append(f"- {c.sha} {c.message} (@{c.author})")
        parts.append("")
    if not (issues or prs or commits):
        parts.append("_No GitHub items._")
    return "\n".join(parts) + "\n"


# --- CLI ---


def add_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(NAME, help="GitHub integration")
    sp = p.add_subparsers(dest="cmd", required=True)

    pi = sp.add_parser("issues", help="Open issues assigned to you")
    pi.add_argument("--repo", default=None)

    pp = sp.add_parser("prs", help="Open PRs in a repo")
    pp.add_argument("--repo", default=None)

    pr = sp.add_parser("recent", help="Recent commits in a repo")
    pr.add_argument("--repo", default=None)
    pr.add_argument("--days", type=int, default=7)

    poi = sp.add_parser("open-issue", help="Create a new issue (label: agent-drafted)")
    poi.add_argument("--repo", default=None)
    poi.add_argument("--title", required=True)
    poi.add_argument("--body-file", required=True)

    p.set_defaults(_handler=cli)


def cli(args: argparse.Namespace) -> int:
    g = _client()
    repo = _resolve_repo(args.repo)
    cmd = args.cmd
    if cmd == "issues":
        out = assigned_to_me(g, repo)
        print(format_for_context(issues=out))
        return 0
    if cmd == "prs":
        out = open_prs(g, repo)
        print(format_for_context(prs=out))
        return 0
    if cmd == "recent":
        out = recent_commits(g, repo, days=args.days)
        print(format_for_context(commits=out))
        return 0
    if cmd == "open-issue":
        body_path = Path(args.body_file)
        body = body_path.read_text(encoding="utf-8")
        issue = open_issue(g, repo, title=args.title, body=body)
        print(f"Created: {issue.url}")
        return 0
    return 2
