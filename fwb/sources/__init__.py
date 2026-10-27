"""数据源适配器。每个模块：请求（经 fwb.http 合规客户端）→ 解析 → 规范化为 DataFrame，不直接写存储。

| 模块                | 数据类别        | 合规依据（2026-09 核实）                                     |
|---------------------|-----------------|--------------------------------------------------------------|
| football_data_uk    | 历史 + 准实时   | robots.txt 对通用 UA 无限制（仅屏蔽 AI 爬虫）；免责声明未限制使用 |
| openfootball        | 准实时（赛程）  | CC0-1.0 公有领域；raw.githubusercontent.com 无 robots 限制     |
| football_data_org   | 实时（可选）    | 官方 REST API，需免费 token，遵守其限流                        |
| statsbomb_open      | 历史（事件级）  | StatsBomb 开放数据条款：发布成果须注明来源并使用其 logo        |

明确不接入：Understat（robots.txt: Disallow: /）、FBref（robots.txt 对脚本返回 403）、
FotMob（robots.txt 禁止 /api/*）、Sofascore/WhoScored/Transfermarkt（服务条款禁止自动抓取）。
"""
