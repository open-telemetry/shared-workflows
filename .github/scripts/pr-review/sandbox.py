"""Prepare a clean PR checkout and run each Copilot pass through AWF."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import progress, run, write_json
from usage import proxy_logs_dir


AGENT_TIMEOUT_MINUTES = 60


def checkout_ref(repo: str, ref: str, checkout: Path, *, expected_head: str | None = None) -> str:
    checkout.mkdir()
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }

    def git(*args: str) -> str:
        return run(
            ["git", "-c", f"core.hooksPath={os.devnull}", *args], cwd=checkout, env=env, timeout=120
        ).stdout.strip()

    git("init", "--quiet", "--template=")
    git("fetch", "--quiet", "--depth=1", "--no-tags", f"https://github.com/{repo}.git", ref)
    head = git("rev-parse", "FETCH_HEAD")
    if expected_head is not None and head != expected_head:
        raise RuntimeError("The PR head changed before checkout; start a new run")
    git("checkout", "--quiet", "--detach", head)
    return head


def checkout_pr(repo: str, pr: int, head: str, checkout: Path) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}", head):
        raise ValueError("Invalid PR head SHA")
    checkout_ref(repo, f"refs/pull/{pr}/head", checkout, expected_head=head)


def checkout_target_branch(repo: str, branch: str, target: Path) -> None:
    head = checkout_ref(repo, f"refs/heads/{branch}", target)
    progress(f"Target branch {branch} checked out at {head}")


def run_in_sandbox(
    checkout: Path, inputs: Path, args: list[str], *, target: Path, phase: str
) -> subprocess.CompletedProcess[str]:
    copilot = shutil.which("copilot")
    if copilot is None:
        raise RuntimeError("Copilot CLI is not installed")
    awf = shutil.which("awf")
    if awf is None:
        raise RuntimeError("Agentic Workflow Firewall is not installed")
    cli_dir = Path(copilot).parent.parent
    # AWF creates root-owned mount points beside its work directory. The disposable runner removes them.
    awf_work = Path(tempfile.mkdtemp(prefix="pr-review-awf-", dir=os.environ.get("RUNNER_TEMP"))) / "work"
    with tempfile.TemporaryDirectory(prefix="pass-", dir=checkout.parent) as temp:
        root = Path(temp)
        workspace = root / "checkout"
        home = root / "home"
        home.mkdir()
        shutil.copytree(checkout, workspace, symlinks=True)
        config = root / "awf.json"
        write_json(config, {
            "network": {"isolation": True, "allowDomains": [
                # Expanded instruction-audit network groups: defaults, github, and all language ecosystems.
                "*.githubusercontent.com", "*.gradle-enterprise.cloud", "*.pythonhosted.org",
                "*.rvm.io", "*.vsblob.vsassets.io", "adoptium.net", "anaconda.org",
                "api.adoptium.net", "api.foojay.io", "api.npms.io", "api.nuget.org", "api.rubygems.org",
                "archive.apache.org", "archive.ubuntu.com", "azure.archive.ubuntu.com",
                "azuresearch-usnc.nuget.org", "azuresearch-ussc.nuget.org", "bcr.bazel.build",
                "binstar.org", "bitbucket.org", "blog.bazel.build", "bootstrap.pypa.io",
                "builds.dotnet.microsoft.com", "builds.hex.pm", "bun.sh", "bundler.rubygems.org",
                "cache.ruby-lang.org", "cdn.azul.com", "cdn.cocoapods.org", "cdn.hex.pm", "cdn.jsdelivr.net",
                "central.sonatype.com", "ci.dot.net", "cocoapods.org", "codeload.github.com",
                "conda.anaconda.org", "conda.binstar.org", "crates.io", "crl.geotrust.com",
                "crl.globalsign.com", "crl.identrust.com", "crl.sectigo.com", "crl.thawte.com",
                "crl.usertrust.com", "crl.verisign.com", "crl3.digicert.com", "crl4.digicert.com",
                "crls.ssl.com", "deb.nodesource.com", "deno.land", "develocity.apache.org",
                "dist.nuget.org", "dl.google.com", "dlcdn.apache.org", "docs.github.com", "dot.net",
                "dotnet.microsoft.com", "dotnetcli.blob.core.windows.net", "download.eclipse.org",
                "download.java.net", "download.jetbrains.com", "download.oracle.com", "download.swift.org",
                "downloads.gradle-dn.com", "esm.sh", "fastly.hex.pm", "files.pythonhosted.org",
                "ge.jetbrains.com", "ge.spockframework.org", "gems.rubyforge.org", "gems.rubyonrails.org",
                "get.pnpm.io", "getcomposer.org", "github-cloud.githubusercontent.com",
                "github-cloud.s3.amazonaws.com", "github.blog", "github.com", "github.githubassets.com",
                "go.dev", "golang.org", "googleapis.deno.dev", "googlechromelabs.github.io", "goproxy.io",
                "gradle.org", "hex.pm", "index.crates.io", "index.rubygems.org", "jcenter.bintray.com",
                "jdk.java.net", "json-schema.org", "json.schemastore.org", "jsr.io", "keyserver.ubuntu.com",
                "kotlin.bintray.com", "lfs.github.com", "maven-central.storage-download.googleapis.com",
                "maven.apache.org", "maven.google.com", "maven.oracle.com", "maven.pkg.github.com",
                "maven.pkg.jetbrains.space", "mirror.bazel.build", "nodejs.org", "npm.pkg.github.com",
                "npmjs.com", "npmjs.org", "nuget.org", "nuget.pkg.github.com",
                "nugetregistryv2prod.blob.core.windows.net", "objects.githubusercontent.com",
                "ocsp.digicert.com", "ocsp.geotrust.com", "ocsp.globalsign.com", "ocsp.identrust.com",
                "ocsp.sectigo.com", "ocsp.ssl.com", "ocsp.thawte.com", "ocsp.usertrust.com",
                "ocsp.verisign.com", "oneocsp.microsoft.com", "opentelemetry.io", "packages.jetbrains.team",
                "packagist.org", "patch-diff.githubusercontent.com", "patchdiff.githubusercontent.com",
                "pip.pypa.io", "pkg.go.dev", "pkgs.dev.azure.com", "plugins-artifacts.gradle.org",
                "plugins.gradle.org", "proxy.golang.org", "pub.dartlang.org", "pub.dev", "pypi.org",
                "pypi.python.org", "raw.githubusercontent.com", "registry.bower.io", "registry.npmjs.com",
                "registry.npmjs.org", "registry.yarnpkg.com", "release-assets.githubusercontent.com",
                "releases.bazel.build", "repo.anaconda.com", "repo.continuum.io", "repo.gradle.org",
                "repo.grails.org", "repo.hex.pm", "repo.maven.apache.org", "repo.packagist.org",
                "repo.spring.io", "repo.yarnpkg.com", "repo1.maven.org", "repository.apache.org",
                "rubygems.org", "rubygems.pkg.github.com", "s.symcb.com", "s.symcd.com", "scans-in.gradle.com",
                "security.ubuntu.com", "services.gradle.org", "sh.rustup.rs", "skimdb.npmjs.com",
                "static.crates.io", "static.rust-lang.org", "storage.googleapis.com", "sum.golang.org",
                "swift.org", "ts-crl.ws.symantec.com", "ts-ocsp.ws.symantec.com", "www.java.com",
                "www.microsoft.com", "www.npmjs.com", "www.npmjs.org", "yarnpkg.com",
            ]},
            "apiProxy": {"enabled": True, "modelFallback": {"enabled": False}},
            "chroot": {"identity": {"home": str(home)}},
        })
        env = {
            key: os.environ[key] for key in (
                "LANG", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "COPILOT_GITHUB_TOKEN",
            ) if key in os.environ
        }
        env["HOME"] = str(home)
        env["GITHUB_WORKSPACE"] = str(workspace)
        capture = awf_work.parent / "output"
        stdout = capture / "attempt-001.stdout.bin"
        stderr = capture / "attempt-001.stderr.bin"
        logs = proxy_logs_dir(phase)
        logs.mkdir(parents=True)
        command = [
            "/usr/bin/sudo", "--non-interactive", f"--preserve-env={','.join(env)}",
            str(Path(awf).resolve()), "--config", str(config), "--work-dir", str(awf_work),
            "--image-tag", os.environ["AWF_IMAGE_TAG"], "--container-workdir", str(workspace),
            "--mount", f"{cli_dir}:{cli_dir}:ro", "--mount", f"{inputs}:{inputs}:ro",
            "--mount", f"{target}:{target}:ro",
            "--env", "GITHUB_COPILOT_PROMPT_MODE_EXTENSIONS=false",
            "--capture-command-output", str(capture),
            "--proxy-logs-dir", str(logs),
            "--agent-timeout", str(AGENT_TIMEOUT_MINUTES),
            "--log-level", "info",
            "--", "/bin/bash", str(inputs / "launch-copilot.sh"),
            str(workspace), str(inputs), str(config), (checkout / ".git" / "HEAD").read_text().strip(),
            str(target), (target / ".git" / "HEAD").read_text().strip(),
            copilot, *args,
        ]
        try:
            result = run(command, cwd=workspace, env=env, check=False, timeout=(AGENT_TIMEOUT_MINUTES + 2) * 60)
        except subprocess.TimeoutExpired as error:
            error.output = stdout.read_bytes() if stdout.is_file() else None
            raise
        if stdout.is_file():
            return subprocess.CompletedProcess(
                result.args, result.returncode,
                stdout.read_text(encoding="utf-8", errors="replace"),
                result.stderr + ("\n" + stderr.read_text(encoding="utf-8", errors="replace") if stderr.is_file() else ""),
            )
        return result
