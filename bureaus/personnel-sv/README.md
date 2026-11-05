# personnel-sv（二局·AI干部局，原人事一局）

本局名册是 Codex 名册任务的英文原文，人物记录由 `python -m zuzhibu.importer` 导入 `data/people/`，中文名取译名表 v1.4，表里没有的标“待译”。

## Personnel Bureau I: Silicon Valley and Global AI Leadership (roster notes)

Verified on 2026-10-17. The v0.1 roster contains 97 unique people across 32 of the 33 requested institution groups, with 76 official portrait URLs and 40 SEC biography links.

## Files and scope

- [roster/roster.csv](roster/roster.csv): one person per row, in English, with the eight fixed columns below.
- [roster/sources.md](roster/sources.md): evidence for every person, source page types, filing sections, portrait provenance, access dates, and gaps.

The roster covers company leadership, research and AI leadership, founders, and the named venture firms' partners. Alphabet is included as Google's parent. Amazon and AWS, Google and Google DeepMind, and SpaceX and xAI follow the requested institution groupings. Venture firms retain their published partner titles.

Elon Musk and Peter Thiel each appear once. Their affiliations are separated by semicolons in `organization_en`; their roles identify the corresponding organization. They count toward each relevant group's coverage, giving 99 group memberships for 97 people.

| Institution group | People |
| --- | ---: |
| OpenAI | 5 |
| Anthropic | 6 |
| Google and Google DeepMind | 6 |
| Microsoft | 3 |
| Meta | 3 |
| Apple | 2 |
| Amazon and AWS | 4 |
| NVIDIA | 3 |
| AMD | 3 |
| Intel | 3 |
| TSMC | 2 |
| Oracle | 3 |
| Tesla | 2 |
| SpaceX and xAI | 2 |
| Palantir | 4 |
| Broadcom | 3 |
| Qualcomm | 3 |
| Arm | 3 |
| Samsung | 3 |
| SK hynix | 3 |
| Mistral AI | 3 |
| Hugging Face | 3 |
| Scale AI | 2 |
| Databricks | 6 |
| CoreWeave | 4 |
| Perplexity | 2 |
| Safe Superintelligence | 2 |
| Thinking Machines Lab | 2 |
| Andreessen Horowitz | 2 |
| Sequoia | 2 |
| Founders Fund | 3 |
| Thrive | 0 |
| Khosla Ventures | 2 |

## CSV fields

| Column | Definition |
| --- | --- |
| `english_name` | Name used in the cited official material; original Latin spelling and diacritics retained. |
| `position_en` | Current published role or founder affiliation. Multiple roles are separated by semicolons. |
| `organization_en` | Institution or requested institution group. |
| `since` | Documented start of the listed principal role, as `YYYY-MM-DD` or `YYYY-MM`. Notes identify a narrower role or founding affiliation when applicable. A year alone or an unspecified effective date produces a blank. |
| `official_bio_url` | Official leadership or board page; an official company profile or announcement where a directory is unavailable. The Thinking Machines Lab CTO record uses the CEO's official appointment post. |
| `official_portrait_url` | Individual portrait image published on an official source, including its image CDN. A blank means no individual portrait URL was identified in the examined official sources. |
| `sec_filing_url` | Direct SEC Archives link to the latest annual report or definitive proxy of that type containing an individual biography. |
| `notes` | Factual role/date qualifications and the reason for a missing SEC biography at a listed company. |

The CSV is UTF-8 without a BOM. It has one header and one physical line per person. Empty fields are unfilled, not inferred dates or substitute URLs.

## Evidence and gaps

Company pages establish current roles. SEC biographies supply a second official source for listed-company personnel; an earlier filing can describe a previous role when a later company appointment is documented. Filing selection uses the SEC submissions records through 2026-10-17. Compensation tables, ownership tables, signatures, and passing mentions do not qualify as personal biographies. Private institutions have blank SEC fields. Listed-company records without a qualifying biography retain an individual reason in `notes`.

The roster has 56 blank start dates, 21 blank portrait fields, and 57 blank SEC fields. The per-person source entries identify the missing fields. Thrive has no roster entries: its website does not publish individual leadership biographies. The original Thrive appointment announcement hosted by Harvard Business School is outside the company-website source rule.

## Maintenance

For an update, reopen the official role source, check dated leadership announcements, and review the issuer's latest SEC annual report and proxy. Retain one row per person. Copy portrait URLs only from official publications. Record source types, accessed dates, and biography sections in `sources.md`; update this README's date and counts after validating the CSV.

The bureau uses the private `aipolis` pipeline for resume, review, portrait, and card exports. It references that pipeline without copying its implementation. Shared dependencies remain `glossary`, `skills/cadre-resume`, `schema`, `portraits`, and `render/card_3x4`.
