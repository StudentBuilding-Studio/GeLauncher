"""命令行入口：所有能力先在这里可用，UI 只是它的另一种皮肤。

用法示例：
    python -m gelauncher versions
    python -m gelauncher doctor
    python -m gelauncher launch --version 1.21.8 --print-command
    python -m gelauncher launch --version 1.21.8
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import Config
from .core.command import Account
from .core.inherit import InheritError
from .core.install_loader import (
    LoaderInstallError,
    install_fabric,
    install_forge,
)
from .core.install_vanilla import InstallError, install_version, missing_report, remote_versions
from .core.java import JavaError, find_java, installed_javas
from .core.launch import LaunchError, LaunchOptions, prepare, run
from .core.layout import GameLayout, current_env
from .core.models import VersionJsonError
from .core.net import Fetcher
from .core.rules import allow
from .core import libraries


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--game-dir", type=Path, help="游戏目录（默认读配置，再默认 %%APPDATA%%\\.minecraft）")
    parser.add_argument("--java", type=Path, help="指定 java.exe，跳过自动查找")
    parser.add_argument("--username", help="离线玩家名")


def _make_layout(args: argparse.Namespace, config: Config) -> GameLayout:
    return GameLayout(args.game_dir or config.game_dir_path())


def detect_loader(version) -> str:
    """从 mainClass / 库名猜是哪家的加载器（只用于显示）。"""
    main_class = (version.main_class or "").lower()
    names = " ".join(version.library_names()).lower()
    if "fabric" in main_class or "net.fabricmc" in names:
        return "Fabric"
    if "quilt" in main_class or "org.quiltmc" in names:
        return "Quilt"
    if "neoforge" in main_class or "net.neoforged" in names:
        return "NeoForge"
    if (
        "forge" in main_class
        or "bootstraplauncher" in main_class
        or "minecraftforge" in names
        or "cpw.mods" in names
    ):
        return "Forge"
    if "optifine" in names:
        return "OptiFine"
    return "原版"


def cmd_versions(args: argparse.Namespace, config: Config) -> int:
    layout = _make_layout(args, config)
    versions = layout.installed_versions()
    if not versions:
        print(f"{layout.root} 下没有安装任何版本（versions/ 为空）")
        return 1
    env = current_env()
    print(f"游戏目录：{layout.root}")
    print(f"本机环境：{env.os_name} / {env.arch}")
    for version_id in versions:
        try:
            version = layout.load_json(version_id)
        except VersionJsonError as exc:
            print(f"  {version_id:<32} [JSON 有问题] {exc}")
            continue
        loader = detect_loader(version)
        parent = f"，继承自 {version.inherits_from}" if version.inherits_from else ""
        print(
            f"  {version_id:<32} {loader:<7} java={version.java_version or '?':<3} "
            f"libs={len(version.libraries):<4}{parent}"
        )
    return 0


def cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    layout = _make_layout(args, config)
    env = current_env()
    print("== Ge 启动器体检 ==")
    print(f"版本      : {__version__}")
    print(f"Python    : {sys.version.split()[0]}")
    print(f"配置      : {config_path_hint()}")
    print(f"游戏目录  : {layout.root}  {'存在' if layout.root.is_dir() else '不存在'}")
    print(f"系统      : {env.os_name} / {env.arch}")
    print(f"版本数    : {len(layout.installed_versions())}")
    print(f"assets    : {layout.assets_dir}  {'有' if layout.assets_dir.is_dir() else '缺'}")
    print(f"libraries : {layout.libraries_dir}  {'有' if layout.libraries_dir.is_dir() else '缺'}")
    print("\n== 可用的 Java ==")
    javas = installed_javas()
    if not javas:
        print("  （没找到，请安装 Java 21 或用 --java 指定）")
    for runtime in javas:
        print(f"  Java {runtime.major:<3} {runtime.path}")
    return 0


def config_path_hint() -> str:
    from .config import config_path

    return str(config_path())


def cmd_launch(args: argparse.Namespace, config: Config) -> int:
    layout = _make_layout(args, config)
    try:
        account = _launch_account(args, config)
    except LaunchError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    options = LaunchOptions(
        layout=layout,
        version_id=args.version,
        account=account,
        java_path=args.java or (Path(config.java_path) if config.java_path else None),
        max_memory_mb=args.max_memory or config.max_memory_mb,
        width=args.width or config.width,
        height=args.height or config.height,
        extra_jvm_args=tuple(args.jvm_arg or ()),
        extra_game_args=tuple(args.game_arg or ()),
        isolated=not args.no_isolate,
        dry_run=args.dry_run,
    )
    try:
        plan = prepare(options)
    except (LaunchError, InheritError, VersionJsonError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    for warning in plan.warnings:
        print(f"[警告] {warning}", file=sys.stderr)

    if args.print_command or args.dry_run:
        print(plan.printable_command())
    if args.dry_run:
        return 0

    print(f"启动 {plan.version_id}（Java {plan.java.major}）")
    print(f"工作目录：{plan.cwd}")
    log_path = layout.root / "logs" / f"gelauncher-{_stamp()}.log"
    print(f"日志：{log_path}")
    if args.print_command:
        print()

    def echo(line: str) -> None:
        if not args.quiet:
            print(line, flush=True)

    try:
        result = run(plan, log_path=log_path, on_line=echo, wait=not args.detach)
    except LaunchError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    if args.detach:
        print("游戏已在后台启动（--detach），启动器退出。")
        return 0
    print(f"游戏退出，退出码 {result.returncode}")
    return 0 if result.returncode == 0 else result.returncode


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _launch_account(args: argparse.Namespace, config: Config):
    """--username 显式给了就用离线账号，否则用配置里的当前账号（可能是微软）。"""
    if getattr(args, "username", None):
        return Account(name=args.username)
    from .core.accounts import resolve_account
    from .core.microsoft import MSAuthError

    try:
        return resolve_account(config)
    except MSAuthError as exc:
        raise LaunchError(str(exc)) from exc


def cmd_verify(args: argparse.Namespace, config: Config) -> int:
    """机械验收（方案 §6.4）：进程存活 N 秒 + 游戏日志里出现关键字。"""
    layout = _make_layout(args, config)
    try:
        account = _launch_account(args, config)
    except LaunchError as exc:
        print(f"验收失败：{exc}", file=sys.stderr)
        return 1
    options = LaunchOptions(
        layout=layout,
        version_id=args.version,
        account=account,
        java_path=args.java or (Path(config.java_path) if config.java_path else None),
        max_memory_mb=args.max_memory or config.max_memory_mb,
        extra_jvm_args=tuple(args.jvm_arg or ()),
        extra_game_args=tuple(args.game_arg or ()),
        isolated=not args.no_isolate,
    )
    try:
        plan = prepare(options)
    except (LaunchError, InheritError, VersionJsonError) as exc:
        print(f"验收失败：{exc}", file=sys.stderr)
        return 1
    for warning in plan.warnings:
        print(f"[警告] {warning}", file=sys.stderr)
    print(f"命令：{plan.printable_command()[:400]}{'…' if len(plan.printable_command()) > 400 else ''}")

    log_path = layout.root / "logs" / f"gelauncher-verify-{_stamp()}.log"
    print(f"启动器日志：{log_path}")
    captured: list[str] = []
    try:
        process = run(
            plan,
            log_path=log_path,
            on_line=captured.append,
            wait=False,
        )
    except LaunchError as exc:
        print(f"验收失败：{exc}", file=sys.stderr)
        return 1

    started = time.time()
    print(f"等待 {args.seconds} 秒，看进程是否还活着…")
    alive = True
    deadline = started + args.seconds
    while time.time() < deadline:
        if process.poll() is not None:
            alive = False
            break
        time.sleep(0.5)

    game_log = plan.cwd / "logs" / "latest.log"
    keywords = args.keyword or ["OpenAL initialized", "Created:", "Setting user"]
    # 只认"这次启动之后写过"的游戏日志，避免把上一次的成绩算进来
    text = ""
    if game_log.is_file() and game_log.stat().st_mtime >= started - 2:
        text = game_log.read_text(encoding="utf-8", errors="replace")
    haystack = text + "\n" + "\n".join(captured)
    found = [keyword for keyword in keywords if keyword in haystack]

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except Exception:  # noqa: BLE001 - 收尾失败不影响结论
            process.kill()

    print()
    print(f"进程存活：{'是' if alive else '否（提前退出）'}")
    print(f"游戏日志：{game_log}{'（本次有写入）' if text else '（本次没写/不存在）'}")
    print(f"命中关键字：{', '.join(found) if found else '（无）'}")
    if alive and found:
        print("\n验收通过 ✔")
        return 0
    if not alive:
        print(f"\n验收失败 ✘：进程在 {args.seconds} 秒内退出了，先看 {log_path}")
    else:
        print(f"\n验收失败 ✘：进程活着，但日志里没有 {keywords}")
    return 1


def _fetcher(args: argparse.Namespace, config: Config) -> Fetcher:
    """命令行没显式指定 --source 时，跟随设置里的下载源。"""
    from .core.net import MIRROR_FIRST_SOURCES, Fetcher, sources_for

    mode = getattr(args, "source", None) or config.download_source
    if mode == "mirror-first":
        sources = MIRROR_FIRST_SOURCES
    else:
        sources = sources_for(mode)
    return Fetcher(sources=sources, threads=args.threads)


def cmd_available(args: argparse.Namespace, config: Config) -> int:
    fetcher = _fetcher(args, config)
    try:
        versions = remote_versions(fetcher, kind=None if args.type == "all" else args.type)
    except Exception as exc:  # noqa: BLE001 - 网络错误直接给人话
        print(f"拿不到版本列表：{exc}", file=sys.stderr)
        return 1
    for version in versions[: args.limit]:
        layout = _make_layout(args, config)
        installed = " [已安装]" if layout.version_json_path(version.id).is_file() else ""
        print(f"  {version.id:<24} {version.type:<10} {version.release_time or ''}{installed}")
    print(f"\n共 {len(versions)} 个，显示前 {min(args.limit, len(versions))} 个")
    return 0


def _print_progress(stage: str, done: int, total: int, message: str) -> None:
    if stage in ("assets", "libraries") and total > 0:
        if done % 100 == 0 or done == total:
            print(f"\r  {stage:<10} {done}/{total}  {message[:60]}", end="", flush=True)
            if done == total:
                print(flush=True)
        return
    print(f"  [{stage}] {message}", flush=True)


def cmd_install(args: argparse.Namespace, config: Config) -> int:
    layout = _make_layout(args, config)
    fetcher = _fetcher(args, config)
    instance = args.instance_name or args.version
    print(f"安装 {args.version} → 实例「{instance}」({layout.root})")
    try:
        report = install_version(
            layout,
            args.version,
            instance_name=instance,
            fetcher=fetcher,
            with_assets=not args.no_assets,
            progress=_print_progress,
        )
    except InstallError as exc:
        print(f"安装失败：{exc}", file=sys.stderr)
        return 1

    for message in report.messages:
        print(f"  [提示] {message}")
    if report.downloads.failures:
        print(f"有 {len(report.downloads.failures)} 个文件没下下来：", file=sys.stderr)
        print(missing_report(report), file=sys.stderr)
    print(
        f"完成：下载 {report.downloads.downloaded} 个 / 跳过 {report.downloads.skipped} 个，"
        f"共 {report.downloads.bytes_written / 1024 / 1024:.1f} MB"
    )
    if report.ok:
        version = report.version
        if version and version.java_version:
            print(f"这个版本需要 Java {version.java_version}")
        print(f"\n可以启动了：python -m gelauncher launch --version-id {instance}")
        return 0
    return 1


def cmd_install_loader(args: argparse.Namespace, config: Config) -> int:
    layout = _make_layout(args, config)
    fetcher = _fetcher(args, config)
    java = find_java(None, args.java) if args.java else None

    def progress(stage: str, message: str) -> None:
        if stage == "log":
            print(f"  | {message}", flush=True)
        else:
            print(f"  [{stage}] {message}", flush=True)

    try:
        if args.loader == "fabric":
            if args.separate_version:
                report = install_fabric(
                    layout,
                    args.mc,
                    loader_version=args.loader_version,
                    fetcher=fetcher,
                    java=java,
                    progress=progress,
                    use_installer=args.use_installer,
                )
            else:
                from .core.install_loader import attach_fabric

                report = attach_fabric(
                    layout,
                    args.mc,
                    loader_version=args.loader_version,
                    fetcher=fetcher,
                    progress=progress,
                )
        elif args.loader == "neoforge":
            from .core.install_loader import install_neoforge

            report = install_neoforge(
                layout,
                args.mc,
                neoforge_version=args.loader_version,
                fetcher=fetcher,
                java=java,
                progress=progress,
                attach=not args.separate_version,
            )
        else:
            report = install_forge(
                layout,
                args.mc,
                forge_version=args.forge_version or args.loader_version,
                fetcher=fetcher,
                java=java,
                progress=progress,
                attach=not args.separate_version,
            )
    except (LoaderInstallError, JavaError) as exc:
        print(f"安装失败：{exc}", file=sys.stderr)
        return 1

    print(f"\n装好了：{report.version_id}")
    print(f"可以启动了：python -m gelauncher launch --version-id {report.version_id}")
    return 0


def cmd_remove_loader(args: argparse.Namespace, config: Config) -> int:
    from .core.install_loader import LOADER_LABELS, detach_loader

    layout = _make_layout(args, config)
    if detach_loader(layout, args.mc):
        print(f"{args.mc} 已还原成原版（卸载了 {LOADER_LABELS.get(args.loader, '加载器')}）")
        return 0
    print(f"{args.mc} 没有加载器安装记录（找不到 .vanilla-backup 备份）", file=sys.stderr)
    return 1


def cmd_ui(args: argparse.Namespace, config: Config) -> int:
    if args.game_dir:
        config.game_dir = str(args.game_dir)
    from .ui.app import run_ui

    return run_ui(config)


def cmd_config(args: argparse.Namespace, config: Config) -> int:
    if args.action == "set":
        if args.key == "max-memory":
            config.max_memory_mb = int(args.value)
        elif args.key == "username":
            config.username = args.value
        elif args.key == "java":
            config.java_path = args.value
        elif args.key == "game-dir":
            config.game_dir = str(Path(args.value).expanduser())
        elif args.key == "width":
            config.width = int(args.value)
        elif args.key == "height":
            config.height = int(args.value)
        elif args.key == "msa-client-id":
            config.msa_client_id = args.value
        elif args.key == "show-log-window":
            config.show_log_window = str(args.value).lower() in ("1", "true", "yes", "on")
        else:
            print(f"不认识的配置项：{args.key}", file=sys.stderr)
            return 2
        path = config.save()
        print(f"已保存到 {path}")
    print(f"游戏目录  : {config.game_dir}")
    print(f"Java      : {config.java_path or '（自动查找）'}")
    print(f"玩家名    : {config.username}")
    print(f"账号类型  : {'微软' if config.selected_account_type == 'msa' else '离线'}")
    print(f"微软中已登录: {len(config.msa_accounts)} 个")
    print(f"微软 client id: {config.msa_client_id or '（未配置，见 msa-setup.md）'}")
    print(f"最大内存  : {config.max_memory_mb} MB")
    print(f"窗口尺寸  : {config.width or '默认'} x {config.height or '默认'}")
    return 0


def cmd_info(args: argparse.Namespace, config: Config) -> int:
    """打印一个版本的解析结果：合并 inheritance 后到底有哪些库生效。"""
    layout = _make_layout(args, config)
    try:
        version = layout.load_json(args.version)
    except VersionJsonError as exc:
        print(f"读不了版本 JSON：{exc}", file=sys.stderr)
        return 1
    from .core.inherit import resolve

    env = current_env()
    merged = resolve(layout, args.version)
    print(f"版本       : {merged.id}")
    print(f"mainClass  : {merged.main_class}")
    print(f"jar        : {merged.jar}")
    print(f"java       : {merged.java_version}")
    print(f"assetIndex : {merged.asset_index.id if merged.asset_index else merged.assets}")
    print(f"libraries  : {len(version.libraries)}（原始）→ {len(merged.libraries)}（合并后）")
    classpath, natives = libraries.effective_libraries(merged, env)
    print(f"生效的库   : {len(classpath)} 个进 classpath，{len(natives)} 个解压")
    if args.list_libraries:
        for lib in classpath:
            print(f"  cp  {libraries.library_relative_path(lib)}")
        for lib in natives:
            artifact, classifier = libraries.native_artifact(lib, env)
            if artifact:
                print(f"  nat {artifact.path}  ({classifier})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ge", description="Ge 启动器（只给几个朋友用的极简 Minecraft 启动器）"
    )
    parser.add_argument("--version", action="version", version=f"GeLauncher {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    versions = sub.add_parser("versions", help="列出已安装的版本")
    _add_common(versions)
    versions.set_defaults(func=cmd_versions)

    doctor = sub.add_parser("doctor", help="检查环境（游戏目录 / Java / assets）")
    _add_common(doctor)
    doctor.set_defaults(func=cmd_doctor)

    launch = sub.add_parser("launch", help="启动一个已安装的版本")
    _add_common(launch)
    launch.add_argument("--version-id", dest="version", required=True, help="版本 id，例如 1.21.8")
    launch.add_argument("--max-memory", type=int, help="最大内存（MB）")
    launch.add_argument("--width", type=int)
    launch.add_argument("--height", type=int)
    launch.add_argument("--jvm-arg", action="append", help="追加 JVM 参数，可重复")
    launch.add_argument("--game-arg", action="append", help="追加游戏参数，可重复")
    launch.add_argument("--print-command", action="store_true", help="打印拼好的命令再启动")
    launch.add_argument("--dry-run", action="store_true", help="只做检查并打印命令，不启动")
    launch.add_argument("--detach", action="store_true", help="启动后立即返回")
    launch.add_argument("--quiet", action="store_true", help="不把游戏输出打到终端")
    launch.add_argument("--no-isolate", action="store_true", help="不隔离游戏目录（用 .minecraft 根）")
    launch.set_defaults(func=cmd_launch)

    verify = sub.add_parser("verify", help="机械验收：启动后看进程存活 + 日志关键字")
    _add_common(verify)
    verify.add_argument("--version-id", dest="version", required=True)
    verify.add_argument("--seconds", type=float, default=25, help="进程需要存活的秒数")
    verify.add_argument("--keyword", action="append", help="日志里必须出现的关键字，可重复")
    verify.add_argument("--max-memory", type=int)
    verify.add_argument("--jvm-arg", action="append")
    verify.add_argument("--game-arg", action="append")
    verify.add_argument("--no-isolate", action="store_true")
    verify.set_defaults(func=cmd_verify)

    install = sub.add_parser("install", help="安装一个原版版本（库 / assets / 客户端 jar）")
    _add_common(install)
    install.add_argument("--version-id", dest="version", required=True, help="要装的版本，例如 1.21.8")
    install.add_argument(
        "--instance-name",
        dest="instance_name",
        help="实例名（也就是文件夹名），默认和版本号相同；重名会拒绝安装",
    )
    install.add_argument("--no-assets", action="store_true", help="跳过 assets（能启动但没声音）")
    install.add_argument(
        "--source",
        choices=["auto", "official", "mirror", "mirror-first"],
        default=None,
        help="下载源；不传就跟随设置里的「下载源」（auto=官方优先+自动切换+并行赛跑）",
    )
    install.add_argument("--threads", type=int, default=8, help="并发下载数，默认 8")
    install.set_defaults(func=cmd_install)

    available = sub.add_parser("available", help="列出官方有哪些版本可装")
    _add_common(available)
    available.add_argument("--type", choices=["release", "snapshot", "all"], default="release")
    available.add_argument("--limit", type=int, default=25)
    available.add_argument("--source", choices=["auto", "official", "mirror", "mirror-first"], default=None)
    available.add_argument("--threads", type=int, default=4)
    available.set_defaults(func=cmd_available)

    loader = sub.add_parser("install-loader", help="装 Fabric / Forge / NeoForge（默认装进现有版本）")
    _add_common(loader)
    loader.add_argument("--loader", choices=["fabric", "forge", "neoforge"], required=True)
    loader.add_argument("--mc", required=True, help="原版版本，例如 1.21.8（必须是已经装好的）")
    loader.add_argument("--loader-version", help="加载器版本（Fabric/NeoForge 都认），默认取最新稳定版")
    loader.add_argument(
        "--separate-version",
        action="store_true",
        help="Fabric 装成单独的新版本（默认是装进现有版本，和 HMCL 一样）",
    )
    loader.add_argument(
        "--use-installer",
        action="store_true",
        help="配合 --separate-version：坚持走官方 installer",
    )
    loader.add_argument("--forge-version", help="Forge 版本，例如 47.4.0 或 1.20.1-47.4.0")
    loader.add_argument(
        "--source",
        choices=["auto", "official", "mirror", "mirror-first"],
        default=None,
    )
    loader.add_argument("--threads", type=int, default=4)
    loader.set_defaults(func=cmd_install_loader)

    remove_loader = sub.add_parser("remove-loader", help="卸载装进现有版本的加载器（还原原版）")
    _add_common(remove_loader)
    remove_loader.add_argument("--loader", choices=["fabric", "forge", "neoforge"], required=True)
    remove_loader.add_argument("--mc", required=True, help="装过加载器的版本 id，例如 1.21.8")
    remove_loader.set_defaults(func=cmd_remove_loader)

    info = sub.add_parser("info", help="看一个版本的解析结果")
    _add_common(info)
    info.add_argument("--version-id", dest="version", required=True)
    info.add_argument("--list-libraries", action="store_true")
    info.set_defaults(func=cmd_info)

    ui = sub.add_parser("ui", help="打开图形界面")
    ui.add_argument("--game-dir", type=Path, help="临时指定游戏目录")
    ui.set_defaults(func=cmd_ui)

    cfg = sub.add_parser("config", help="查看 / 修改配置")
    cfg.add_argument("action", nargs="?", choices=["show", "set"], default="show")
    cfg.add_argument("key", nargs="?")
    cfg.add_argument("value", nargs="?")
    cfg.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config.load()
    if args.command == "config":
        return args.func(args, config)
    try:
        return args.func(args, config)
    except (VersionJsonError, InheritError) as exc:
        print(f"出错了：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
