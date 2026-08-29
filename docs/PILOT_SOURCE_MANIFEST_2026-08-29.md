# TST pilot 源文件清单（2026-08-29）

> 源目录：`/Users/dylanchen2000/Work/heavy/depression/悬尾`
> 用途：迁移前的只读身份基线。本清单不表示文件已通过 V2 契约或 pilot 验收。

SHA-256 使用原文件字节计算。迁移、修复或归档禁止覆盖这些原文件。

| 文件 | SHA-256 | 当前分类 |
|---|---|---|
| `10mg 2周.mp4` | `8a9770847d48493103fa234f13aa0790670f35150cadb8f1ed4364f6f1c3ba3b` | TST 视频 |
| `20mg 1周 1-3+20 2周1.mp4` | `11b2361a85c27cc5e1a023b7396b1438032524d51623aa24d47f9b77ac80fd55` | TST 视频 |
| `20mg 2周.mp4` | `016009f1f59098562d7010f19dee29517d08e0b571a82ec383bce82e24790e61` | TST 视频 |
| `20mg 3周.mp4` | `6322504a20a0d8c4a755def75468a691cad1a1d03d615bfdb25ad37573ab334d` | TST 视频 |
| `30mg 2周 1-3+20 1周1.mp4` | `55f74089039961af175df424bc7afb0d38b3c26cbcd38e0dae1b5f74e9888ede` | TST 视频 |
| `30mg 2周 2+20 2周2.mp4` | `f5fef21679c67995ccb78d62ecd0b2f7a60c5cc32d75a7577d6ef6facde95e9c` | TST 视频 |
| `30mg 2周.mp4` | `d4e7d93334b46cc9dd2f4b1056e2fdae7d29951fed34e4fe0f20e913ef616dda` | TST 视频 |
| `10mg 2周_陈璇_mouse1.csv` | `86edaf857869f91b826a5f634a62b7a596922be89e83092f9ae728afcbafdc93` | legacy V2 7 列 CSV；单标；待显式迁移 |
| `20mg 1周_张咸明 (3).json` | `4289556ee52602f42a9f1756e7680fdfc99c94e5502f9bda8ab3258ee8da5041` | legacy schema 2 TST；双标诊断对 A |
| `20mg 一周_徐乐彤.json` | `2da08b46a090480b7022c376bd4ed80156bce9fc94c9cc71b21d95f2299eeace` | legacy schema 2 TST；双标诊断对 B；含重复/重叠 |
| `10mg 2周_张咸明 (1).json` | `44392d582ba98ac82163f97b4eb98a2a890766201fb5af9465754290eaf65a7b` | FST 局部/练习标注；错放 TST 目录 |
| `10mg 2周_徐乐彤.json` | `b59528c9f31f6d3ec50b337ca09881e15c783a9b966ce5420377ef903c5636b2` | FST 局部/练习标注；错放 TST 目录 |

## V2.2 恢复附录（2026-08-30；取代原“正式迁移门”）

本文件的 SHA-256 表仍是原始字节身份基线，不对任何一行做改写。表内 2026-08-29 的
“待显式迁移”等分类文字仅记录当时状态；当前处置以
[`LEGACY_RECOVERY_POLICY_V2_2.md`](LEGACY_RECOVERY_POLICY_V2_2.md) 和
[`PILOT_MIGRATION_AUDIT_2026-08-30.md`](PILOT_MIGRATION_AUDIT_2026-08-30.md) 为准：

- 本批 TST 恢复源为 1 CSV + 2 JSON；另 2 JSON 内容自报 `assay=FST`，只从
  TST 队列隔离，原文件不移动、不改名。
- 恢复器绑定已核验的实际视频后，使用全长 `[0,n_frames)` 作为窗口：本批为
  9,661 或 11,470 帧。不要求标注员裁剪或重标；如需标准 360 秒，由软件以版本化
  规则从全长结果派生 9,000 帧子窗。
- 同一 track/mouse 中同值的完全重复、重叠或相邻 interval 由恢复器确定性 union；
  只有异值重叠需人工裁决。本批异值冲突仅位于已隔离的 FST 文件，不阻塞 TST。
- 恢复结果必须使用新文件名，写 `metadata_repaired=true` 和完整 `provenance`，
  并引用本表的 source SHA-256；禁止覆盖原件。
- legacy 恢复结果固定为 `pool=train`、`annotator_role=legacy_rater`、`blind=false`，
  可用于训练与诊断，不计入 12 例正式独立盲标 pilot。
- 2026-08-30 已将 3/3 份 TST 源恢复到
  `/Users/dylanchen2000/Work/heavy/depression/recovered_annotations_v2.2/tst`；本表中源 SHA 复核未变。
