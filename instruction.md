# 夜班排队网复核

分拣中心换线后，十台计数器被重新装到输送网的不同节点，标签顺序没有更新。六次试运行留下了每台计数器的离开量、累计占用时间和首波到达层级。设备台账仍保存候选节点的阶段、服务等级和边关系，但换线时的路由矩阵已经丢失。值班组需要恢复计数器归属、各条边的分流概率、出口率和各节点服务率，再做一次无随机项的稳态通量校准。

## 网络输入

换线快照由 `--input` 指向的 UTF-8 JSON 提供，默认演练快照为 `/app/data/queue_case.json`。快照根成员是 `schema_version`、`case_id`、`candidates`、`experiments`、`edges`、`probes`。

`candidates` 保存设备台账，每行由 `node_code`、`phase`、`service_class`、`entry` 组成；`experiments` 保存试运行表，每行由 `experiment_code`、`external_jobs` 组成；`edges` 记录有向连接，每行是 `from_node` 与 `to_node`；`probes` 保存计数器现场观测，每行含 `probe_code`、`departures`、`busy_s`、`first_wave`。

候选节点数为 8 到 12，试运行固定为 6 次，计数器数与候选节点数相同。`departures` 与 `busy_s` 各有 6 个有限正数。`service_class` 取 `quick`、`normal`、`slow` 之一。`entry` 是布尔值，并且只有一个节点为真。边的端点存在于候选节点集合，且不允许自环。

若同一对象内同名键再次出现，流程按规定停机且不发布报告。键集合不完整或夹带未声明条目时，本批数据不得进入求解。文件版本仅允许数值一；布尔值属于非法输入。

计数器清单最多容纳十二台；第十三台触发范围拒收。

## 反演与仿真

计数器归属由首波层级和服务等级共同确定，候选的服务率则从离开量与占用时间的比值恢复。映射必须是一一对应，不能把两台计数器归到同一个节点。

已知边关系后，用六次运行中相邻节点的离开量解出分流概率。每个源节点的边概率加上出口概率等于一。仿真从唯一入口注入累计外部作业量，按刚恢复的路由做确定性通量传播，并比较总离开量。

## 输出和历史

换线校准完成后不产生标准输出或标准错误文本。拒收时只允许在标准错误出现一行以 `queue: ` 开头的说明，状态码二，调用栈不落盘。

报告键集合为 `schema_version`、`case_id`、`node_map`、`routing`、`exit_probabilities`、`service_rates`、`simulation`、`status`。`node_map` 元素键集合为 `probe_code`、`node_code`，按 `node_code` 排序。`routing` 元素键集合为 `from_node`、`to_node`、`probability`，按起终点字典序排列。`exit_probabilities` 元素键集合为 `node_code`、`probability`。`service_rates` 元素键集合为 `node_code`、`jobs_per_second`。`simulation` 键集合为 `predicted_total`、`observed_total`、`relative_gap`。`status` 为 `calibrated`。

`--history` 是追加式校准历史，每行键集合为 `case_id`、`fingerprint`、`routing_sha256`。首次输入追加一条。同一原始文件的再次校准不得改动历史文件。同案号换输入追加新记录。失败输入保持旧字节。

报告和历史都是紧凑 JSON 并以一个换行结束。写入采用同目录临时文件再切换目标文件。

## 验收

节点映射、边概率、出口率、服务率和确定性通量都需要同时成立。一次换线校准的预算为十二秒；看门狗在十五秒时强制结束。
