# 共享工装履历的寿命核算与换型交接仓

跨线共用工装、独立寿命部件及构型维修凭证的资料格式。 资料为本地合成样本。

## 源交付

`sources/` 存放 JSON 原件，文件名前两位是到达序号，同一序号内的各表属于同次交付。每个文件有 `intake`、`table`、`records`，记录 `id` 在所属表内唯一。`source-fields.json` 列出各表字段类型，空值用 `null`，空集合用 `[]`。业务时间按下方约定解释。

| 数据表 | 说明 |
| --- | --- |
| `parts` | 工装部件唯一身份及设计寿命。 |
| `legs` | 按确证时段实际装到的构型。 |
| `cycle` | 来源计数端点对。mirror 明确引用被转发的源行，mode 为工况。 |
| `rates` | 每次循环的工况扣减。 |
| `repairs` | 恢复与允许使用记录，configuration 使用实际部件对。 |
| `work_orders` | 工单拟使用区段和耗用。 |
| `loans` | 借用对象包括确切的预约构型和候选请求。 |
| `acks` | 实际的部件交收及拒绝说明。 |

## 来源约定

使用线端时间统一记录的分钟区间，均左闭右开。leg 的某条已封定装配关系对当时实际安装的 part 生效，未证实完整迁移时视为归属待核。tool 由基座 base 与夹头 jaw 组成，有固定工艺级别。cycle 的 before、after 是某代累计端点，来源间相同 run 加重叠范围需按覆盖证明处理。独立 run 才可直接加；跨拆装且不能证明分配时保留寿命扣减范围。耗用是一种无量纲寿命点，rates 每次循环对各角色分别扣相应的点。
repair 中 restore 在核定范围内增加可用余量且不得超出部件设计上限，replace 为新实物更换，release 针对签名构型和工况，惟其覆盖的工单配置才可派用。借用给的是一套配置经编号确认的时段安排。回执可将 part 逐个 accepted、returned 或 refused。实际没有接到的件不等于已可用，得到释放或返还证据后才可复用。

## 资料盘点

运行环境为 Python 3.11，盘点与资料校验仅使用标准库。盘点结果按到达批次显示表名及行数。

```sh
python source_inventory.py
python -E -m unittest discover -s tests -v
```

## 履历仓实现（`toolhistory/`，仅标准库）

| 模块 | 职责 |
| --- | --- |
| `loader.py` | 按 `source-fields.json` 逐字段校验、引用完整性与 leg 互斥，支持 `upto(n)` 批次视图。 |
| `reconcile.py` | mirror 去重、同 run 覆盖证明（重叠不相加）、端点倒退/世代重置、按 leg 时间片做构件归因，跨界用量保留 best/worst 界限与出处。 |
| `ledger.py` | replace 开新实物世代（新件从新品起算、历史不重置）、restore 受限恢复点（按生效时点与签名构型封顶、不超设计上限）、release 构型/工况凭证。 |
| `loans.py` | confirmed/requested 借用、accepted/returned/refused 回执状态机、占用切片、重复承诺检测与时点可承诺库存。 |
| `dispatch.py` | 工单合套核查：构型门、逐件余量门（不整套抵消）、凭证工况门、占用/签收门，逐构型给出可派用理由。 |
| `backfill.py` | 迟到补录的 as-of 差额、受影响工单/借用、不确定用量/等待签收/凭证确证三分类，已执行借用只保留不回滚。 |
| `repository.py` / `cli.py` | 统一查询门面与命令行。 |

### 隔离运行约定

源 JSON 是不可信数据。`python -I` 会同时移除 cwd 与 `PYTHONPATH`，导致项目包无法导入；
本仓统一用 `python -E`（忽略环境变量、隔离用户 site，但保留项目根作为脚本/模块目录），
且始终从项目根运行、把数据路径作为参数传入，不进入 `sources/` 执行解释器。

### 查询命令

```sh
python -E -m toolhistory.cli summary                 # 各部件 best/worst 余量
python -E -m toolhistory.cli part jaw1               # 构件历次核算/凭证/占用
python -E -m toolhistory.cli runs                    # run 覆盖证明与 mirror 出处
python -E -m toolhistory.cli conflicts               # 计数/装配/凭证冲突清单
python -E -m toolhistory.cli orders                  # 工单可派用理由（逐构型）
python -E -m toolhistory.cli inventory --at 120      # 时点可承诺库存
python -E -m toolhistory.cli backfill 3              # 第 3 批补录的影响与三分类
```

### 关键判定口径

- 同 run 同代计数范围重叠时，重叠窗的物理循环被两台计数器各记一次，循环上界取较大累计读数而非相加；独立区间才相加。
- 计数区间跨过 leg 换件边界时，界内时间片照常归属，跨界部分对新件下界为 0、上界为该行有效读数，绝不臆断归新件。
- restore 恢复点仅在签名 `configuration` + `modes` 下可用，按凭证生效时点该构型内的累计已耗封顶；后续补录不会挤掉已入账恢复点。
- 逐件余量不过线即整套不可派，`fits_possible` 但 `fits_certain=False` 的边际件只能条件承诺；release 只对签名构型/工况背书。
- confirmed 借用在 accepted 后实际占用、returned 后释放；requested 不硬占用；refused 不形成占用，后补 accepted 以最后回执为准。

## 验证

`tests/` 共 42 个用例，覆盖镜像去重、同 run 覆盖、跨装配界限、工况/等级冲突、世代换件、计数重置、受限恢复点、凭证工况门、借用交收、合套不抵消与补录回放的相互制约：

```sh
python -E -m unittest discover -s tests -v
```
