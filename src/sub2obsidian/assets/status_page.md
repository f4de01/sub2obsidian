# 来源状态

> 由 sub2obsidian 初始化时生成，用 Dataview 实时汇总 `$raw_dir/` 中每个来源元数据里的来源状态。
> 命令行中执行 `sub2obsidian status` 可看到同样的统计（含数量为 0 的状态）。

## 各状态数量

```dataview
TABLE WITHOUT ID 状态 AS "来源状态", length(rows) AS "数量"
FROM "$raw_dir"
WHERE 来源状态
GROUP BY 来源状态 AS 状态
SORT length(rows) DESC
```

## 待编译

文章、图文已采集，或视频已转写：在本知识库中对 agent 说「编译」即可处理。

```dataview
TABLE WITHOUT ID link(file.path, default(标题, 平台内ID)) AS "来源", 平台, 类型, 作者, 发布时间
FROM "$raw_dir"
WHERE (类型 = "视频" AND 来源状态 = "已转写") OR (类型 != "视频" AND 来源状态 = "已采集")
SORT 采集时间 ASC
```

## 待转写

没有平台字幕的视频：执行 `sub2obsidian transcribe`。

```dataview
TABLE WITHOUT ID link(file.path, default(标题, 平台内ID)) AS "来源", 平台, 时长 AS "时长（秒）", 失败原因
FROM "$raw_dir"
WHERE 类型 = "视频" AND 来源状态 = "已采集"
SORT 采集时间 ASC
```

## 待筛

```dataview
TABLE WITHOUT ID link(file.path, default(标题, 平台内ID)) AS "来源", 平台, 作者, 筛选建议
FROM "$raw_dir"
WHERE 来源状态 = "待筛"
SORT 采集时间 ASC
```

## 采集失败

可重试的失败：来源保持原状态，下次同步或转写时自动重试。

```dataview
TABLE WITHOUT ID link(file.path, default(标题, 平台内ID)) AS "来源", 平台, 来源状态, 失败原因
FROM "$raw_dir"
WHERE 失败原因 AND 来源状态 != "已失效"
SORT 采集时间 ASC
```

## 已失效

平台上已删除或不可见；之前采集到的原始材料照常保留。

```dataview
TABLE WITHOUT ID link(file.path, default(标题, 平台内ID)) AS "来源", 平台, 失败原因
FROM "$raw_dir"
WHERE 来源状态 = "已失效"
SORT 采集时间 DESC
```
