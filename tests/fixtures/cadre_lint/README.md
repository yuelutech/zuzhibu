# cadre_lint 测试样本

全部是虚构的人名、公司和机构，只用来测 tools/lint/cadre_lint.py。

- glossary/：三张虚构译名表，列名和 shared/glossary 的定稿一样，文件名以 `_org.csv`、`_title.csv`、`_person.csv` 结尾即可被找到。
- pass/：按 shared/skills/cadre-resume/SKILL.md（aipolis rules/cadre.md）写法写的样本，用 glossary/ 检查应当零违规。
- fail/one_per_rule.txt：九条规则各至少触发一次。
- allow.txt：放行词表的样例。
