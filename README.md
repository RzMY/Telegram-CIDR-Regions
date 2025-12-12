# Telegram-CIDR-Regions

每日更新的 Telegram CIDR 分流规则。通过 RIPE Stat API 拉取指定 ASN 的前缀列表，按地区 (SG/US/EU) 归类后进行去重与 CIDR 合并，并生成适用于网络代理工具的规则文件。

**Highlights**
- 数据源：RIPE NCC RIPE Stat API（权威 BGP 宣告数据）
- 并发拉取：多 ASN 并发请求，失败自动重试
- 精确归属：跨 Region/ASN 的重叠网段严格处理，保留更具体路由，并对较大网段进行拆分后正确归属
- 自动化：GitHub Actions 每天 UTC 00:00 自动生成并推送

## 区域与 ASN 映射
- SG (DC5): `AS44907`, `AS62014`
- US (DC1, DC3): `AS59930`
- EU (DC2, DC4): `AS62041`, `AS211157`

## 数据接口
- RIPE Stat API: `https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{asn}`
- 取值字段：`data.prefixes[].prefix`

## 输出文件
生成 3 个列表文件：
- `TelegramSG.list`
- `TelegramUS.list`
- `TelegramEU.list`

文件头信息示例：
```
# NAME: TelegramSG
# AUTHOR: RzMY
# REPO: https://github.com/RzMY/Telegram-CIDR-Regions
# UPDATED: 2025-06-06 09:17:51
# IP-CIDR: 2
# IP6-CIDR: 1
# TOTAL: 3
```

条目格式：
- IPv4：`IP-CIDR,x.x.x.x/xx,Telegram{Region}`
- IPv6：`IP6-CIDR,xxxx:xxxx::/xx,Telegram{Region}`

## 关键合并与去重规则
- 仅在同一 Region 内进行 `cidr_merge` 合并，不跨 Region 合并
- 对跨 Region/ASN 的重叠前缀：
	- 保留更具体的网段（更长掩码，如 `/23` 优先于 `/22`）
	- 对较大网段使用拆分 (`cidr_exclude`) 后，将剩余部分归属到其原 Region
- 示例：
	- SG 宣告 `91.108.56.0/23`，EU 宣告 `91.108.56.0/22`
	- 结果：`91.108.56.0/23` 归属 SG；`91.108.58.0/23`（由 `/22` 拆分）归属 EU

## 本地运行
环境要求：Python 3.10+，依赖 `requests`, `netaddr`

```
pip install -r requirements.txt
python main.py
```

运行成功后，将在仓库根目录生成 `TelegramSG.list`, `TelegramUS.list`, `TelegramEU.list`。

## GitHub Actions 自动化
工作流文件：`.github/workflows/update.yml`
- 触发：每天 UTC `0 0 * * *`，支持 `workflow_dispatch`
- 环境：`ubuntu-latest`
- 步骤：Checkout → 设置 Python → 安装依赖 → 运行脚本 → 若有变更则 Commit & Push

## 错误处理与健壮性
- 请求失败自动重试（指数回退）
- 无效前缀自动跳过
- 详细日志输出：包含每个 ASN 的前缀列表与重叠处理过程（拆分、跳过等）

## 许可证
此项目的规则数据来自公开网络资源（RIPE Stat）。本仓库脚本与生成的列表文件仅用于网络分流学习与研究用途。
