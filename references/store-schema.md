# 存储 JSON Schema

底层用单个 JSON 文件（机器精确、结构化友好），可用 `store.py export` 导出为 md 供人阅读。

## 文件位置

按优先级（`store.py` 的 `store_path()`）：
1. `PMEM_FILE` —— 完整文件路径（最高）
2. `PMEM_DIR` —— 目录，文件名固定 `store.json`
3. 默认 `~/.personal-memory/store.json`（本机用户目录，跨项目通用）

`PMEM_PROJECT_DIR` 已弃用并被忽略，避免个人档案被意外写入项目或短期沙箱。使用 `doctor` 检查实际路径；临时目录默认拒绝写入。每次成功覆盖前会在同目录保留上一版 `store.json.bak`。

## 结构

```json
{
  "schema_version": 1,
  "updated_at": 1721600000,
  "items": {
    "邮箱": {
      "type": "single",
      "value": "abc@example.com"
    },
    "实习经历": {
      "type": "entries",
      "value": [
        { "公司": "字节跳动", "岗位": "AI Infra 实习生", "时间": "2024.06-2024.09" },
        { "公司": "某某科技", "岗位": "后端开发实习生", "时间": "2023.07-2023.09" }
      ]
    }
  }
}
```

## 字段说明

| 字段 | 取值 | 说明 |
|------|------|------|
| `schema_version` | `1` | schema 版本 |
| `updated_at` | int | 最后修改的 Unix 时间戳 |
| `items` | object | key → 条目。key 即用户记忆的名字（邮箱/学号/实习经历…） |
| `items[key].type` | `single` \| `entries` | 单值 或 结构化条目列表 |
| `items[key].value` | string（single）\| array（entries） | single 为一段文本；entries 为对象数组，每对象是"字段名→值"的自由字典 |

## 持久化限制

默认路径只有在 `HOME` 对应用户本机或已挂载的持久卷时才可跨会话保留。远程容器或临时沙箱无法访问宿主机时，必须用 `PMEM_DIR` / `PMEM_FILE` 指向已挂载的持久目录；skill 不会假装已永久保存。

## 为什么用 JSON 而非纯 md 作底层

- **结构化条目**（经历/获奖）是一对多、多字段的嵌套数据，JSON 天然表达；纯 md 嵌套后脚本难以精确定位"第 2 条的岗位字段"。
- **逐字保真**：JSON 字符串精确保存任意字符（引号、括号、&、换行），读出不失真。
- **人可读诉求**由 `export` 命令用 md 满足——两全其美。
