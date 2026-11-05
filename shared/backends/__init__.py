"""shared.backends：模型适配与秘书局、宣传教育局共用的流水线。

- codex_cli：封装本机 ``codex exec``
- ai_api：使用者自己的 AI 接口直连，带联网检索（一局立即更新）
- schema_lite：标准库 JSON Schema 子集校验
- glossary：译名表加载、确定性匹配、生成后一致性检查
- lint_hook：tools/lint 的调用点
- pipeline：读原文 → 译名表匹配 → 调后端 → lint → 输出 Markdown
"""
