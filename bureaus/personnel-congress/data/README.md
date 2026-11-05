# data（人事三局事实包）

由 ../import_ceo_profiles.py 从 ../import/ceo-profiles/ 生成，不手改；重跑会覆盖 bundles/、index.csv、report.md、report.json。

- bundles/：538 份事实包，gold 60、draft 478，结构按 shared/schema/bundle.schema.json。
- index.csv：名册索引。
- report.md、report.json：校验通过率、字段转换统计、留给本机复核的清单。
- verified/：../reverify.py 对 draft 逐人重新取证的结果（事实包、review.md、进度.csv），见上级 README「重新取证」一节。导入脚本重跑不动这个目录。

真实人物资料，公开前整目录移出。
