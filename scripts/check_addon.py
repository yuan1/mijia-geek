#!/usr/bin/env python3
"""校验 HA Add-on 仓库元数据，避免提交了装不上的版本。

检查项：
  1. repository.yaml 存在且含 name/url/maintainer
  2. bridge/config.yaml 必填字段齐全
  3. version 是 x.y.z 字符串（HA 靠它判断有无更新）
  4. slug 只含小写字母/数字/下划线
  5. arch 取值合法
  6. options 与 schema 键**完全一致**（不一致会让 Add-on 无法启动）
  7. schema 类型串合法
  8. ingress 开启时必须给 ingress_port
  9. Dockerfile 的每条 COPY 源路径在构建上下文（bridge/）中真实存在
 10. HA 自定义集成的 manifest 版本与 addon 版本保持一致

用法：python scripts/check_addon.py
退出码：0 通过 / 1 有问题
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "bridge"
CONFIG = ADDON_DIR / "config.yaml"
REPO_YAML = ROOT / "repository.yaml"
MANIFEST = ROOT / "ha_integration" / "mijia_geek" / "manifest.json"

VALID_ARCH = {"amd64", "aarch64", "armv7", "armhf", "i386"}
TYPE_RE = re.compile(
    r"^(str|password|bool|int|float|list|url|port|device|email|match|date|time|"
    r"(?:int|float)\([^)]*\)|list\([^)]*\))$"
)

errors: list[str] = []
warnings: list[str] = []


def check_repository_yaml() -> dict:
    if not REPO_YAML.exists():
        errors.append("缺少 repository.yaml —— 没有它仓库无法被 HA 添加为附加组件仓库")
        return {}
    try:
        import yaml
    except ImportError:
        errors.append("需要 PyYAML：pip install pyyaml")
        return {}
    data = yaml.safe_load(REPO_YAML.read_text(encoding="utf-8")) or {}
    for key in ("name", "url", "maintainer"):
        if not data.get(key):
            errors.append(f"repository.yaml 缺少字段 {key}")
    return data


def check_config() -> dict:
    try:
        import yaml
    except ImportError:
        errors.append("需要 PyYAML：pip install pyyaml")
        return {}
    if not CONFIG.exists():
        errors.append("缺少 bridge/config.yaml")
        return {}

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}

    for key in ("name", "version", "slug", "description", "arch"):
        if key not in cfg:
            errors.append(f"config.yaml 缺少必填字段 {key}")

    version = cfg.get("version")
    if not isinstance(version, str):
        errors.append(f"version 必须是字符串（当前 {type(version).__name__}）；"
                      f"写成数字会让 HA 无法正确比较版本")
    elif not re.fullmatch(r"\d+\.\d+\.\d+", version):
        errors.append(f"version 应形如 x.y.z，当前 {version!r}")

    slug = str(cfg.get("slug", ""))
    if not re.fullmatch(r"[a-z0-9_]+", slug):
        errors.append(f"slug 只能含小写字母/数字/下划线，当前 {slug!r}")

    bad_arch = set(cfg.get("arch") or []) - VALID_ARCH
    if bad_arch:
        errors.append(f"arch 含非法值 {bad_arch}，合法值 {sorted(VALID_ARCH)}")

    options = cfg.get("options") or {}
    schema = cfg.get("schema") or {}
    if set(options) != set(schema):
        only_opt = set(options) - set(schema)
        only_sch = set(schema) - set(options)
        errors.append(f"options 与 schema 键不一致"
                      f"（仅 options: {only_opt or '无'}；仅 schema: {only_sch or '无'}）")

    for key, spec in schema.items():
        if not isinstance(spec, str):
            errors.append(f"schema.{key} 必须是字符串")
            continue
        if not TYPE_RE.fullmatch(spec.rstrip("?")):
            errors.append(f"schema.{key} 类型不合法：{spec!r}")

    if cfg.get("ingress") and not cfg.get("ingress_port"):
        errors.append("ingress: true 时必须设置 ingress_port")

    hostname = cfg.get("hostname")
    if hostname and not re.fullmatch(r"[a-z0-9-]+", str(hostname)):
        errors.append(f"hostname 只能含小写字母/数字/连字符，当前 {hostname!r}")

    return cfg


def check_dockerfile() -> None:
    dockerfile = ADDON_DIR / "Dockerfile"
    if not dockerfile.exists():
        errors.append("缺少 bridge/Dockerfile")
        return
    text = dockerfile.read_text(encoding="utf-8")
    if "BUILD_FROM" not in text:
        warnings.append("Dockerfile 未使用 BUILD_FROM —— HA 无法按架构替换基础镜像")
    if not re.search(r"^CMD\s*\[", text, re.M):
        errors.append("Dockerfile 缺少 CMD（JSON 数组形式）")

    for match in re.finditer(r"^COPY\s+(.+)$", text, re.M):
        parts = match.group(1).split()
        for src in parts[:-1]:
            if src.startswith("--"):          # 跳过 --from 等参数
                continue
            # 构建上下文是 bridge/
            if not (ADDON_DIR / src).exists():
                errors.append(f"Dockerfile COPY 源不存在于构建上下文 bridge/：{src}")

    ignored = ADDON_DIR / ".dockerignore"
    if not ignored.exists():
        warnings.append("缺少 bridge/.dockerignore —— __pycache__ 等会被打进镜像")


def check_manifest(addon_version: str | None) -> None:
    if not MANIFEST.exists():
        warnings.append("未找到 HA 集成 manifest.json，跳过版本一致性检查")
        return
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        errors.append(f"manifest.json 不是合法 JSON：{err}")
        return
    manifest_version = data.get("version")
    if addon_version and manifest_version and manifest_version != addon_version:
        errors.append(f"版本不一致：addon={addon_version} manifest={manifest_version}")


def main() -> int:
    repo = check_repository_yaml()
    cfg = check_config()
    check_dockerfile()
    check_manifest(cfg.get("version"))

    print("=== HA Add-on 仓库元数据校验 ===")
    if repo:
        print(f"仓库名   : {repo.get('name')}")
        print(f"仓库地址 : {repo.get('url')}")
    if cfg:
        print(f"Add-on   : {cfg.get('name')} ({cfg.get('slug')})")
        print(f"版本     : {cfg.get('version')}")
        print(f"架构     : {', '.join(cfg.get('arch') or [])}")
        print(f"Ingress  : {cfg.get('ingress')} → 端口 {cfg.get('ingress_port')}")
        print(f"Hostname : {cfg.get('hostname')}")
    print()

    for warning in warnings:
        print(f"⚠️  {warning}")
    for error in errors:
        print(f"❌ {error}")

    if errors:
        print(f"\n结果：{len(errors)} 个错误，{len(warnings)} 个警告")
        return 1
    print(f"结果：通过 ✅（{len(warnings)} 个警告）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
