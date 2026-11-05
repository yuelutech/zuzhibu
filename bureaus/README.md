# bureaus（各局）

七局的资料原料、导入和重核脚本，以及一局流水线。人物记录不在这里，统一放 `data/people/`，由 `python -m zuzhibu.importer` 导入（docs/PRODUCT.md 第十节）。

| 目录 | 局 | 内容 |
|---|---|---|
| dispatch | 一局·干部调配局 | 人事动态流水线：线索、研究、核查、写回 |
| personnel-sv | 二局·AI干部局（原人事一局） | 2026年10月版名册 97 人 |
| personnel-whitehouse | 三局·白宫干部局（原人事二局） | 名册待建 |
| personnel-congress | 四局·国会干部局（原人事三局） | 538 人导入、reverify.py 逐人重核 |
| personnel-others | 五局·美军干部局、六局·公检法干部局（原人事四局） | 2026年10月版名册 34 人，按机构归五局、六局、三局 |
| retired | 七局·老干部局 | 只有说明，人物由一局迁入 |
| secretariat、publicity | 秘书局、宣传教育局 | 保留成果，暂缓推进 |
