# AIDD / CADD 文献订阅看板

每天自动抓取 **38 种期刊**的 AI 药物设计（AIDD）与计算机辅助药物设计（CADD）相关文献，
生成可交互的网页看板并发布到 GitHub Pages。全流程在 GitHub Actions 上跑，本地电脑无需开机。

- **看板网址**：部署后见仓库 Settings → Pages 页面顶部的链接
- **数据源**：Europe PMC 公开接口（支持跨域，看板打开时也会自行补拉最新数据）
- **更新频率**：每天北京时间 09:30（UTC 01:30）

## 覆盖的期刊

药物化学与分子模拟：J Med Chem、J Chem Inf Model、Eur J Med Chem、J Chem Theory Comput、
J Comput Aided Mol Des、J Cheminform、Mol Inform、ChemMedChem、RSC Med Chem、Digital Discovery

AI 与计算方法：Nat Mach Intell、Nat Comput Sci、Mach Learn Sci Technol、Patterns、Chem Sci

生物信息与药物发现：Bioinformatics、Brief Bioinform、BMC Bioinformatics、Bioinform Adv、
Comput Struct Biotechnol J、Drug Discov Today、Expert Opin Drug Discov、Future Med Chem、
Comput Biol Chem、J Mol Graph Model、Chem Biol Drug Des、SAR QSAR Environ Res

综合刊（需命中 AIDD/CADD 关键词才收录）：Nat Commun、Nat Biotechnol、Nat Methods、Sci Adv、
PNAS、JACS、Angew Chem、ACS Cent Sci、J Phys Chem B、WIREs Comput Mol Sci、J Chem Phys

## 保底窗口机制

不同期刊出刊频率差异极大。若用统一的 3 天窗口，月刊/季刊永远抓不到东西。
因此每种期刊在 `fetch_lit.py` 的 `JOURNALS` 里配了「保底窗口天数」：常规窗口返回 0 篇时，
自动按该刊的保底窗口扩窗重试（如 Nat Mach Intell 150 天、Mach Learn Sci Technol 180 天、
WIREs Comput Mol Sci 365 天）。

## 数据存放在哪

| 内容 | 存放方式 | 原因 |
|---|---|---|
| 文献库 `data/papers.json` | Actions 缓存 | 避免仓库膨胀；缓存被回收时自动 90 天冷启动重建 |
| 看板 `dashboard/` | Pages 制品 | 官方部署方式，不写入 git 历史 |
| Markdown 日报 `reports/` | git 提交 | 文本文件小，便于在线查阅与追溯 |

## 手动跑一次

仓库页面 → Actions → 「AIDD/CADD 文献每日更新」→ Run workflow。
首次部署建议把 `days` 填 **90** 做冷启动回填，之后保持默认 3 即可。

## 配置邮件推送（可选）

不配置也能正常运行，只是不发邮件。要开启请在
**Settings → Secrets and variables → Actions** 添加：

| Secret | 说明 | 示例 |
|---|---|---|
| `EMAIL_TO` | 收件邮箱 | `you@qq.com` |
| `MAIL_USERNAME` | 发件邮箱 | `you@qq.com` |
| `MAIL_PASSWORD` | SMTP 授权码（不是登录密码） | QQ 邮箱在设置里生成 |
| `SMTP_SERVER` | SMTP 服务器 | `smtp.qq.com` |
| `SMTP_PORT` | 端口 | `465` |

## 本地运行

```bash
python fetch_lit.py --days 3        # 抓取最近 3 天并重建看板
python fetch_lit.py --days 90       # 冷启动回填
python fetch_lit.py --rebuild       # 不联网，用本地库重建看板
python fetch_lit.py --verify --days 3   # 逐刊校验是否都有命中
python fetch_if.py                  # 更新期刊影响因子（每年 JCR 发布后跑一次）
```

无任何第三方依赖，Python 3.8+ 标准库即可运行。
