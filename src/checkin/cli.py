"""CLI entry point."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from checkin import __version__
from checkin.core import run_checkin, summarize
from checkin.models import CheckInStatus


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@click.command()
@click.option(
    "--config",
    "-c",
    "config_path",
    default=None,
    help="配置文件路径（默认 config.yaml 或环境变量 CHECKIN_CONFIG）",
)
@click.option("--verbose", "-v", is_flag=True, help="调试日志")
@click.version_option(__version__, prog_name="checkin")
def main(config_path: str | None, verbose: bool) -> None:
    """多站点每日自动签到工具。"""
    load_dotenv()
    _setup_logging(verbose)

    path = config_path or os.environ.get("CHECKIN_CONFIG") or "config.yaml"
    if not Path(path).exists():
        click.echo(
            f"找不到配置文件: {path}\n"
            "请复制 config.example.yaml 为 config.yaml，并填写站点与密钥。",
            err=True,
        )
        sys.exit(2)

    results = run_checkin(path)
    summary = summarize(results)

    click.echo("---")
    click.echo(f"完成: {summary['ok']}/{summary['total']} 成功或可忽略，失败 {summary['failed']}")
    for r in results:
        mark = "OK" if r.ok else "FAIL"
        click.echo(f"  [{mark}] {r.site_name}: {r.status.value} — {r.message}")

    if any(r.status == CheckInStatus.FAILED for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
