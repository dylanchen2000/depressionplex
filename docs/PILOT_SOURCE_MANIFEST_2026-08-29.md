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

## 正式迁移门

- 未确认的 `analysis_window` 禁止从视频长度或文件名静默推测。
- 两份 FST 文件只在新 manifest 中更正归属，原文件不移动、不改名。
- 含重复/重叠的 JSON 必须由标注员裁决，迁移器不选“第一条”。
- 所有迁移结果使用新文件名，写 `metadata_repaired=true` 和完整 `provenance`，并重新计算 SHA-256。
