# GitHub 发布隐私检查

## 2026-11-04：公开授权后的复核

用户已明确批准将 [Craft-s/FinSight-AI](https://github.com/Craft-s/FinSight-AI) 公开，并委托选择开源方案。本次采用 MIT 许可原创源码与文档，保留第三方财报、依赖和数据服务各自权利及原始署名。

- 当前 296 个 Git 候选文件扫描通过；与内存中 3 个实际本地秘密值、个人用户名及联系人比对无命中。
- 既有历史的 322 个 blob 与提交元数据，以及 4 次 Actions 的 99 份日志完成上述已知隐私值比对，无发现。历史没有 `.env`、`work/`、`outputs/` 或数据库被提交记录。
- 既有提交作者与提交者均为公开 GitHub 账号和 noreply 邮箱。上游 EdgarTools 的公开版权署名属于应保留的第三方许可内容。
- 发布保护 10 项回归通过；新增文档的 51 个本地链接均有效。干净源码 ZIP 重新生成并通过成员哈希、CRC、排除项和已知隐私值核对，ZIP 与运行资料保持不入 Git。

以上检查有明确范围；未知凭据、图片中的未识别内容等仍不能仅由文本扫描保证。没有改动实际本地凭据、数据库或运行配置。具体测试与环境处理见[测试记录](TEST_RESULTS.md)。

## 历史：最初上传前的检查记录

以下保留初次上传前的事实记录，其中“没有提交”“尚未推送”等只描述当时状态。

检查日期：2026-11-04。范围是当前项目的 Git 待上传文件、本地配置保护边界、截图，以及既有交付 ZIP 的已知秘密比对。本轮没有创建远程仓库、提交或推送，也没有修改本机实际凭据。

## 结果

| 检查项 | 实际结果 |
|---|---|
| 个人身份与路径 | 候选源码、配置示例、原始开发说明、README 和 docs 中未发现本机用户名、个人绝对目录、个人联系方式或个人账户信息 |
| 真实凭据比对 | 从本地配置在内存中取得 3 个实际秘密值，逐一比对全部候选文件；没有命中，未打印或写入公开报告 |
| 凭据格式检查 | 常见模型/云/GitHub Key、私钥块及个人用户目录格式没有待处理命中；测试凭据、CI 一次性凭据和示例占位符单独复核 |
| 配置示例 | `.env.example` 中生产密码、管理员令牌、SEC 身份和主密钥为空；只有通用本地连接默认值 |
| 截图内容 | 全部 47 张 JPEG 做视觉检查，其中 8 张设置截图按原尺寸复查；双语离线 OCR 共 94 份，未发现个人账号、路径或明文凭据；OCR 再与本机实际秘密精确比对也未命中 |
| 图片元数据 | 47 张 JPEG 无 EXIF、XMP、IPTC、ICC、注释或 GPS/作者元数据；原图保留 |
| 历史提交 | 当前仓库没有提交、暂存文件或已追踪文件，没有本轮需要清理的历史；本机尚未配置提交作者邮箱 |
| 本地保护 | `.env`、`work/`、`outputs/`、依赖、构建及浏览器诊断被忽略；本轮补充忽略 TypeScript 缓存、签名私钥、本机配置、数据库备份和压缩包 |
| 旧源码 ZIP | 对 7 个历史源码 ZIP 的成员内容和名称做已知秘密/本机用户名/本地联系人比对，无命中；仍保留在被忽略的 `outputs/`，不作为本轮待提交文件 |

没有发现需要删除的真实个人路径或凭据。实际修正是将此前可能被加入 Git 的 `frontend/tsconfig.tsbuildinfo` 排除，并加固后续文件的忽略与检查规则。公开上市公司财报、原始来源哈希、第三方许可署名、保留示例域名和明确的隔离测试数据予以保留。

## 可重复运行的检查

从项目根目录执行，使用 Python 3.12 和 Git；这些检查不依赖数据库或模型服务：

```sh
python scripts/check_public_files.py
python -m unittest discover -s scripts/tests
git add --dry-run .
```

`check_public_files.py` 使用 `git ls-files --cached --others --exclude-standard` 枚举工作树文件，还直接读取实际暂存区 blob。因此未追踪源码、被强制追踪的忽略文件，以及暂存后工作树已改干净或删除的旧内容都能被检查。输出仅包含相对文件名、行号和问题类别，不打印匹配值。发现问题返回非零退出码；Git 不可用或无法完成检查也不会伪装通过。

`.github/workflows/ci.yml` 新增 `public-files` 任务，执行本轮 10 项安全回归及候选扫描。本地测试与 Ruff 均通过，当前 271 个候选文件扫描无待处理发现；远程 GitHub Actions 尚未执行。本轮未修改金融业务代码，未重新运行完整财务/浏览器测试；此前结果见 [测试记录](TEST_RESULTS.md)。

该脚本是项目发布保护措施，不能替代完整秘密扫描、图片审查或历史审查；未知秘密、编码/压缩内容和 OCR 识别遗漏仍有边界。后续新增截图、真实配置或生成物时应再次检查。GitHub 的 [secret scanning](https://docs.github.com/en/code-security/concepts/secret-security/secret-scanning) 可提供额外检测。

## 上传范围与作者隐私

Git 推送应在根目录检查后正常添加源码，不使用强制添加忽略文件。也可解压本轮干净源码包 `outputs/FinSight_AI_GitHub_Ready_Source.zip` 后，在解压目录初始化并上传；不要将整个开发工作目录打包上传，也不要把 ZIP 本身当作源码仓库。

源码包仅按通过检查的 Git 候选清单生成，不含 `.git`、真实 `.env`、数据库、备份、运行日志、OCR 原文、旧交付文件、依赖或构建产物。ZIP 不附带个人文件所有者或绝对路径。

首次提交前，作者名和邮箱是需要主动选择的公开身份。若希望隐藏真实邮箱，在 GitHub 邮箱设置中取得自己的 `noreply` 地址，再仅为此仓库设置：

```sh
git config user.name "YOUR_PUBLIC_NAME"
git config user.email "YOUR_GITHUB_NOREPLY_ADDRESS"
git add .
python scripts/check_public_files.py
git diff --cached --stat
git diff --cached --check
```

以上身份是占位符，本轮没有替用户设置。Git 提交邮箱会随提交公开，修改未来的设置不会改写已提交的邮箱，详见 [GitHub 提交邮箱说明](https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address)。仓库名称、可见性和远程地址由用户决定。
