# 变更说明：把仓库变成真正的 HA Add-on 仓库

**目标**：让 `https://github.com/yuan1/mijia-geek` 能被 HA 直接添加为
「附加组件仓库」，之后在商店里安装、点「更新」即可，不必再手动拷源码 + 重建。

---

## 一、新增了什么

| 文件 | 作用 | 必要性 |
|---|---|---|
| `repository.yaml` | **核心**。HA 靠它识别仓库 | 缺了就无法添加仓库 |
| `scripts/check_addon.py` | addon 元数据校验器（10 项检查） | 防止提交装不上的版本 |
| `.github/workflows/ci.yml` | push 时自动跑测试 + 元数据校验 | 同上，防回归 |
| `bridge/.dockerignore` | 构建上下文为 `bridge/`，排除 `__pycache__` 等 | 避免脏文件进镜像 |

## 二、改了什么

### `bridge/config.yaml`

- **新增 `hostname: mijia-geek-bridge`** —— 让 HA 自定义集成能稳定发现内部地址
  `http://mijia-geek-bridge:8099`，不再依赖 Supervisor 自动生成的容器名。
- 把「界面显示名」和「实际容器名」解耦（slug 仍是 `mijia_geek_bridge`，
  hostname 是 `mijia-geek-bridge`）。注释里说明了这一点。
- 补了 `mcp_http_port` 与 `api_token` 的联动说明（非 0 端口时强制要求 token）。

### `bridge/Dockerfile`

- 显式声明 `ARG BUILD_FROM` 的用途，并补充注释：**构建上下文是 `bridge/`**（这点最容易搞错）。
- 追加 `PYTHONDONTWRITEBYTECODE=1`、`DATA_DIR=/data`。
- 保持 `CMD ["/run.sh"]` 的 JSON 数组形式（CI 会检查）。

### `docs/DEPLOY.md`

新增 **3.0 节：把仓库添加为附加组件仓库**，包含：
- A-1（仓库方式）与 A-2（本地 addon）的对比表
- 添加仓库的 5 步操作
- 发布新版本的完整流程
- 「必须递增 version，否则不会提示更新」的说明

### `docs/README.md`

开头加「安装（HA Add-on 仓库方式）」章节，目录结构补 `repository.yaml` / `scripts/` / `.github/`。

---

## 三、替换步骤

```bash
cd /path/to/your/mijia-geek
tar -xzf mijia-geek-addon-repo.tar.gz -C /tmp
cp -r /tmp/mijia-geek-addon-repo/. .

# 确认
git status --porcelain
python scripts/check_addon.py     # 应输出「结果：通过 ✅」
python -m pytest -q               # 应 88 passed
```

预期 `git status`：

```
 M bridge/Dockerfile
 M bridge/config.yaml
 M docs/DEPLOY.md
 M docs/README.md
?? .github/
?? bridge/.dockerignore
?? repository.yaml
?? scripts/
```

提交并推送：

```bash
git add -A
git commit -m "feat: make repo a proper HA add-on repository"
git push
```

---

## 四、推送后在 HA 里怎么用

1. **设置 → 加载项 → 附加组件商店**
2. 右上角 **⋮ → 仓库**
3. 填 `https://github.com/yuan1/mijia-geek` → 添加
4. 回商店刷新，找到 **Mijia Geek Bridge** → 安装

首次安装 Supervisor 会编译镜像，约 3–10 分钟。

> ⚠️ **重要**：你现在跑的实例是「本地 addon」（slug `local_mijia_geek_bridge`）。
> 从仓库安装出来的 slug 是 `mijia_geek_bridge`，**是另一个 Add-on**，配置不会自动带过来。
> 迁移步骤：
> 1. 记下旧 addon 的配置（gateway_url / api_token / scan_interval 等）
> 2. 从仓库安装新 addon，填入相同配置
> 3. 在「登录码」里输入 6 位码连上网关（一次性）
> 4. 确认新 addon 工作正常后，把旧的本地 addon 卸载
>
> 两者不能同时运行（都要占 8099），务必先停旧的再启新的。

---

## 五、验证记录

| 检查项 | 结果 |
|---|---|
| 单元测试 | **88 passed** |
| `scripts/check_addon.py` | 通过 ✅（0 警告） |
| YAML/JSON 语法 | 全部合法 |
| Dockerfile 每条 COPY 源路径 | 均存在于 `bridge/` 上下文 |
| `run.sh` 解析 options.json | 7 个字段全部正确 |
| 校验器负向测试 | 注入 5 个错误，**全部拦下**（退出码 1） |

负向测试注入的错误：`version` 写成数字、`slug` 含大写连字符、
`options`/`schema` 键不一致、`ingress_port` 为空、manifest 与 addon 版本不一致 —— 均被准确报出。

---

## 六、以后发版的固定动作

```bash
# 1. 两处版本号必须同步（CI 会检查不一致）
#    bridge/config.yaml                       version: "2.3.1"
#    ha_integration/mijia_geek/manifest.json  "version": "2.3.1"

# 2. 本地校验
python scripts/check_addon.py

# 3. 推送
git add -A && git commit -m "chore: bump to 2.3.1" && git push

# 4. HA → 加载项商店 → ⋮ → 检查更新 → 点「更新」
```

只要 `version` 变了，HA 就会显示「更新」按钮，点一下就自动拉取 + 编译 + 替换，
这才是「真正的 addon 仓库」该有的体验。
